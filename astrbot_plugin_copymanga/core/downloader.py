"""章节图片下载器。"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .api import CopyMangaClient
from .auth import AccountPool
from .errors import (
    AllAccountsLimitedError,
    CopyMangaError,
    IncompleteDownloadError,
    RiskControlError,
    TokenExpiredError,
)
from .models import ChapterMeta, ComicMeta
from .utils import build_chapter_dir, page_numbers

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]


@dataclass
class DownloadResult:
    chapter: ChapterMeta
    path: Path
    pages: int
    skipped: int
    existed: bool


def extract_pages(chapter: dict) -> list[tuple[int, str]]:
    """从章节详情中提取 (页码, 图片地址)，并切换到高清地址。"""
    contents = chapter.get("contents") or []
    words = chapter.get("words") or []

    urls: list[str] = []
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if url:
                urls.append(url)

    if not urls:
        return []

    numbers = page_numbers(list(words) if isinstance(words, list) else [], len(urls))
    high_quality = [
        url.replace(".c800x.", ".c1500x.").replace(".c400x.", ".c1500x.")
        for url in urls
    ]
    return sorted(zip(numbers, high_quality), key=lambda item: item[0])


def find_existing_page(directory: Path, page: int) -> Path | None:
    for path in directory.glob(f"{page:03d}.*"):
        if not path.is_file() or path.suffix == ".part":
            continue
        try:
            if path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


class ChapterDownloader:
    def __init__(
        self,
        client: CopyMangaClient,
        pool: AccountPool,
        img_concurrency: int = 8,
    ) -> None:
        self.client = client
        self.pool = pool
        self.img_concurrency = max(1, min(20, int(img_concurrency)))

    async def fetch_chapter(self, path_word: str, chapter_uuid: str) -> dict:
        """带账号池轮换地获取章节详情。"""
        last_error: Exception | None = None
        for _ in range(4):
            account = await self.pool.acquire()
            try:
                return await self.client.get_chapter(
                    path_word, chapter_uuid, account.token
                )
            except RiskControlError as exc:
                self.pool.mark_limited(account)
                last_error = exc
            except TokenExpiredError as exc:
                self.pool.invalidate_token(account)
                last_error = exc

        if isinstance(last_error, CopyMangaError):
            raise last_error
        raise AllAccountsLimitedError()

    async def download(
        self,
        comic: ComicMeta,
        chapter: ChapterMeta,
        chapter_data: dict,
        root: Path,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        """下载一个章节的全部图片。"""
        info = chapter_data.get("chapter")
        if not isinstance(info, dict):
            raise CopyMangaError("章节详情数据不正确。")

        title = str(info.get("name") or chapter.title or chapter.uuid)
        try:
            order = float(info.get("ordered")) / 10.0
        except (TypeError, ValueError):
            order = chapter.order

        pages = extract_pages(info)
        if not pages:
            raise CopyMangaError(
                "该章节没有可下载的图片，可能被锁定或需要更高权限。"
            )

        actual = ChapterMeta(
            uuid=chapter.uuid,
            title=title,
            order=order,
            size=len(pages),
            index=chapter.index,
        )
        chapter_dir = build_chapter_dir(root, comic.title, order, title)
        chapter_dir.mkdir(parents=True, exist_ok=True)

        # 所有页面已经存在时直接返回，避免重复请求图片 CDN
        if all(
            find_existing_page(chapter_dir, page) is not None
            for page, _url in pages
        ):
            return DownloadResult(
                chapter=actual,
                path=chapter_dir,
                pages=len(pages),
                skipped=0,
                existed=True,
            )

        skipped = await self._download_pages(
            pages, chapter_dir, progress
        )
        return DownloadResult(
            chapter=actual,
            path=chapter_dir,
            pages=len(pages),
            skipped=skipped,
            existed=False,
        )

    async def _download_pages(
        self,
        pages: list[tuple[int, str]],
        directory: Path,
        progress: ProgressCallback | None,
    ) -> int:
        total = len(pages)
        semaphore = asyncio.Semaphore(self.img_concurrency)
        lock = asyncio.Lock()
        done = 0
        skipped = 0

        async def report(current: int) -> None:
            if progress is None:
                return
            try:
                await progress(current, total)
            except Exception:
                logger.exception("发送下载进度失败")

        async def worker(page: int, url: str) -> None:
            nonlocal done, skipped

            async with semaphore:
                existing = find_existing_page(directory, page)
                if existing is not None:
                    async with lock:
                        skipped += 1
                        done += 1
                        current = done
                    await report(current)
                    return

                data, extension = await self.client.get_image(url)
                target = directory / f"{page:03d}.{extension}"
                temp_path = directory / (
                    f".{page:03d}.{uuid.uuid4().hex}.part"
                )
                try:
                    temp_path.write_bytes(data)
                    os.replace(temp_path, target)
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError:
                            pass

            async with lock:
                done += 1
                current = done
            await report(current)

        results = await asyncio.gather(
            *(worker(page, url) for page, url in pages),
            return_exceptions=True,
        )

        failed_pages = [
            page
            for (page, _url), result in zip(pages, results)
            if isinstance(result, Exception)
        ]
        if failed_pages:
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("图片下载失败：%s", result)
            raise IncompleteDownloadError(
                total=total,
                success=total - len(failed_pages),
                failed_pages=failed_pages,
            )

        return skipped

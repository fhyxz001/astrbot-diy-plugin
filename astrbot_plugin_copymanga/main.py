"""AstrBot 拷贝漫画搜索下载插件。"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

if __package__:
    # AstrBot 以 data.plugins.<插件目录>.main 的形式导入插件
    from .core.api import DEFAULT_API_DOMAIN, CopyMangaClient
    from .core.auth import AccountPool
    from .core.downloader import (
        ChapterDownloader,
        DownloadResult,
        extract_pages,
    )
    from .core.errors import ApiError, CopyMangaError
    from .core.models import (
        ChapterMeta,
        ComicMeta,
        normalize_chapter,
        normalize_comic,
        normalize_search_results,
    )
    from .core.utils import (
        extract_command_arg,
        extract_from_url,
        filename_filter,
        format_order,
        is_url,
        is_valid_path_word,
        looks_like_chapter_selector,
        parse_chapter_selector,
    )
else:  # 兼容直接运行/独立调试
    from core.api import DEFAULT_API_DOMAIN, CopyMangaClient
    from core.auth import AccountPool
    from core.downloader import (
        ChapterDownloader,
        DownloadResult,
        extract_pages,
    )
    from core.errors import ApiError, CopyMangaError
    from core.models import (
        ChapterMeta,
        ComicMeta,
        normalize_chapter,
        normalize_comic,
        normalize_search_results,
    )
    from core.utils import (
        extract_command_arg,
        extract_from_url,
        filename_filter,
        format_order,
        is_url,
        is_valid_path_word,
        looks_like_chapter_selector,
        parse_chapter_selector,
    )

try:
    from astrbot.core.utils.astrbot_path import (
        get_astrbot_plugin_data_path,
        get_astrbot_root,
    )
except Exception:  # pragma: no cover - 兼容旧版 AstrBot

    def get_astrbot_root() -> str:
        return os.getcwd()

    def get_astrbot_plugin_data_path() -> str:
        return os.path.join(get_astrbot_root(), "data", "plugin_data")


PLUGIN_NAME = "astrbot_plugin_copymanga"
SEARCH_CACHE_TTL = 10 * 60
SEARCH_DISPLAY_LIMIT = 10


@dataclass
class CachedSearch:
    expires_at: float
    results: list[dict]


class _ProgressReporter:
    """把下载进度限制为最多每 5 秒提示一次。"""

    def __init__(
        self,
        plugin: "CopyMangaPlugin",
        umo: str,
        comic_title: str,
        chapter_title: str,
    ) -> None:
        self.plugin = plugin
        self.umo = umo
        self.comic_title = comic_title
        self.chapter_title = chapter_title
        self.last_sent = time.monotonic()
        self._lock = asyncio.Lock()

    async def __call__(self, done: int, total: int) -> None:
        if done >= total:
            return

        async with self._lock:
            now = time.monotonic()
            if now - self.last_sent < 5:
                return
            self.last_sent = now
            await self.plugin._send(
                self.umo,
                f"《{self.comic_title}》 {self.chapter_title} "
                f"下载中 {done}/{total}",
            )


@register(PLUGIN_NAME, "DIY", "拷贝漫画搜索与章节下载", "0.1.0")
class CopyMangaPlugin(Star):
    def __init__(
        self, context: Context, config: AstrBotConfig | None = None
    ) -> None:
        super().__init__(context)
        self.config = config or {}

        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)

        api_domain = str(
            self.config.get("api_domain") or DEFAULT_API_DOMAIN
        ).strip()
        timeout = self._config_int("request_timeout", 15, minimum=5)
        max_accounts = self._config_int("max_accounts", 5, minimum=1)
        img_concurrency = self._config_int("img_concurrency", 8, minimum=1)
        self.max_chapters_per_task = self._config_int(
            "max_chapters_per_task", 0, minimum=0
        )

        self.client = CopyMangaClient(api_domain, timeout=timeout)
        self.pool = AccountPool(
            self.client, self.data_dir, max_accounts=max_accounts
        )
        self.downloader = ChapterDownloader(
            self.client, self.pool, img_concurrency=img_concurrency
        )
        self.download_root = self._resolve_download_root()

        self._search_cache: dict[str, CachedSearch] = {}
        self._tasks: dict[str, asyncio.Task] = {}

        logger.info("拷贝漫画插件已加载，下载目录：%s", self.download_root)

    def _config_int(self, key: str, default: int, minimum: int = 1) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    def _resolve_download_root(self) -> Path:
        raw = str(self.config.get("download_dir") or "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = Path(get_astrbot_root()) / candidate
        else:
            candidate = self.data_dir / "downloads"

        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except OSError as exc:
            logger.warning("下载目录 %s 不可用：%s", candidate, exc)
            fallback = self.data_dir / "downloads"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback.resolve()

    @filter.command_group("cp")
    def cp(self):
        """拷贝漫画指令组"""

    @cp.command("搜索")
    async def search(self, event: AstrMessageEvent):
        """搜索漫画。用法：/cp 搜索 <关键词>"""
        if not self._is_private(event):
            yield event.plain_result("该插件仅支持私聊使用。")
            return

        keyword = extract_command_arg(event.message_str, "搜索")
        if not keyword:
            yield event.plain_result("用法：/cp 搜索 <关键词>")
            return

        try:
            data = await self.client.search(keyword)
        except CopyMangaError as exc:
            yield event.plain_result(f"搜索失败：{exc}")
            return
        except Exception:
            logger.exception("搜索漫画异常")
            yield event.plain_result("搜索失败：服务异常。")
            return

        results = normalize_search_results(
            data, limit=SEARCH_DISPLAY_LIMIT
        )
        if not results:
            yield event.plain_result("没有找到相关漫画，换个关键词试试。")
            return

        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            total = len(results)

        lines = [f"共找到 {total} 部，显示前 {len(results)} 部："]
        for index, item in enumerate(results, 1):
            author = f" 作者：{item['author']}" if item["author"] else ""
            lines.append(f"{index}. 《{item['name']}》{author}")
        lines.append("")
        lines.append("查看章节总数：/cp 详情 <序号>")
        lines.append("下载最新话：/cp 下载 <序号>")
        lines.append("下载指定章节：直接发送章节链接")

        self._search_cache[self._session_key(event)] = CachedSearch(
            expires_at=time.monotonic() + SEARCH_CACHE_TTL,
            results=results,
        )
        yield event.plain_result("\n".join(lines))

    @cp.command("详情")
    async def detail(self, event: AstrMessageEvent):
        """查看章节总数。用法：/cp 详情 <序号|path_word|链接|关键词>"""
        if not self._is_private(event):
            yield event.plain_result("该插件仅支持私聊使用。")
            return

        target = extract_command_arg(event.message_str, "详情")
        if not target:
            yield event.plain_result(
                "用法：/cp 详情 <序号|path_word|链接|关键词>"
            )
            return

        try:
            path_word, _chapter_uuid = await self._resolve_target(
                self._session_key(event), target
            )
            path_word, comic_data = await self._load_comic(
                path_word, target
            )
            comic = normalize_comic(comic_data, path_word)
            chapters = await self._get_chapters(comic)
        except (CopyMangaError, ValueError) as exc:
            yield event.plain_result(f"查询失败：{exc}")
            return
        except Exception:
            logger.exception("查询漫画详情异常")
            yield event.plain_result("查询失败：服务异常。")
            return

        lines = [f"《{comic.title}》", f"章节总数：{len(chapters)} 章"]
        if chapters:
            latest = chapters[-1]
            lines.append(
                f"最新章：{format_order(latest.order)} {latest.title}"
            )
        lines.append(f"漫画 ID：{comic.path_word}")
        lines.append("")
        lines.append("下载全部：/cp 下载 <序号|漫画 ID> all")
        lines.append("下载最新章：/cp 下载 <序号|漫画 ID>")
        yield event.plain_result("\n".join(lines))

    @cp.command("下载")
    async def download(self, event: AstrMessageEvent):
        """下载漫画。用法：/cp 下载 <序号|path_word|链接|关键词> [latest|all|序号|范围]"""
        if not self._is_private(event):
            yield event.plain_result("该插件仅支持私聊使用。")
            return

        raw = extract_command_arg(event.message_str, "下载")
        if not raw:
            yield event.plain_result(
                "用法：/cp 下载 <序号|path_word|链接|关键词> "
                "[latest|all|序号|范围]"
            )
            return

        parts = raw.split()
        first_is_target = (
            parts[0].isdigit()
            or is_valid_path_word(parts[0])
            or is_url(parts[0])
        )
        last_is_selector = looks_like_chapter_selector(parts[-1])
        if (
            len(parts) >= 2
            and last_is_selector
            and (
                first_is_target
                or parts[-1].lower() in {"all", "latest", "最新"}
            )
        ):
            target = " ".join(parts[:-1]).strip()
            selector = parts[-1]
        else:
            target = raw.strip()
            selector = "latest"

        if not target:
            yield event.plain_result("下载目标不能为空。")
            return

        umo = getattr(event, "unified_msg_origin", "") or ""
        key = self._session_key(event)
        running = self._tasks.get(key)
        if running is not None and not running.done():
            yield event.plain_result("当前会话已有下载任务，请等待完成。")
            return

        task = asyncio.create_task(
            self._run_download(umo, key, target, selector)
        )
        self._tasks[key] = task

        def _cleanup(done_task: asyncio.Task) -> None:
            if self._tasks.get(key) is done_task:
                self._tasks.pop(key, None)

        task.add_done_callback(_cleanup)
        yield event.plain_result("已开始下载，完成后会发送保存目录。")

    async def _run_download(
        self, umo: str, key: str, target: str, selector: str
    ) -> None:
        try:
            path_word, chapter_uuid = await self._resolve_target(
                key, target
            )
            path_word, comic_data = await self._load_comic(
                path_word, target
            )
            comic = normalize_comic(comic_data, path_word)

            if chapter_uuid:
                selected = [
                    ChapterMeta(
                        uuid=chapter_uuid,
                        title="",
                        order=0.0,
                    )
                ]
                await self._send(
                    umo, f"开始下载《{comic.title}》指定章节。"
                )
            else:
                chapters = await self._get_chapters(comic)
                if not chapters:
                    raise CopyMangaError("没有找到可下载的章节。")

                indices = parse_chapter_selector(selector, len(chapters))
                if (
                    self.max_chapters_per_task > 0
                    and len(indices) > self.max_chapters_per_task
                ):
                    raise CopyMangaError(
                        f"单次最多下载 {self.max_chapters_per_task} 章，"
                        "请缩小章节范围，或把 max_chapters_per_task 设为 0。"
                    )
                selected = [chapters[index - 1] for index in indices]
                await self._send(
                    umo,
                    f"开始下载《{comic.title}》，共 {len(selected)} 章。",
                )

            batch = len(selected) > 1
            downloaded = 0
            existed = 0
            failed: list[str] = []
            for chapter in selected:
                try:
                    result = await self._download_one(
                        umo, comic, chapter, announce=not batch
                    )
                except (CopyMangaError, ValueError) as exc:
                    if not batch:
                        raise
                    failed.append(f"{chapter.title or chapter.uuid}：{exc}")
                    continue
                if result.existed:
                    existed += 1
                else:
                    downloaded += 1

            if not batch:
                await self._send(umo, f"《{comic.title}》下载任务结束。")
                return

            lines = [
                f"《{comic.title}》下载任务结束：共 {len(selected)} 章，"
                f"新下载 {downloaded} 章，已存在 {existed} 章。"
            ]
            if failed:
                lines.append(f"失败 {len(failed)} 章：")
                lines.extend(f"- {item}" for item in failed[:10])
                if len(failed) > 10:
                    lines.append(f"- 其余 {len(failed) - 10} 章省略")
                lines.append("重新发送相同命令可继续下载，已完成的章节会跳过。")
            lines.append(
                "保存目录："
                f"{self.download_root / filename_filter(comic.title)}"
            )
            await self._send(umo, "\n".join(lines))
        except asyncio.CancelledError:
            raise
        except (CopyMangaError, ValueError) as exc:
            await self._send(umo, f"下载中止：{exc}")
        except Exception as exc:
            logger.exception("下载任务异常")
            await self._send(umo, f"下载中止：{exc}")

    async def _load_comic(
        self, path_word: str, target: str
    ) -> tuple[str, dict]:
        """获取漫画详情；纯字母关键词被误判为 path_word 时回退到搜索。"""
        try:
            return path_word, await self.client.get_comic(path_word)
        except ApiError:
            if target.isdigit() or is_url(target):
                raise
            data = await self.client.search(target)
            results = normalize_search_results(data, limit=1)
            if not results or results[0]["path_word"] == path_word:
                raise
            fallback = results[0]["path_word"]
            return fallback, await self.client.get_comic(fallback)

    async def _resolve_target(
        self, key: str, target: str
    ) -> tuple[str, str | None]:
        target = target.strip()
        if not target:
            raise CopyMangaError("下载目标不能为空。")

        if is_url(target):
            path_word, chapter_uuid = extract_from_url(target)
            if not path_word:
                raise CopyMangaError("无法从链接中解析漫画 ID。")
            return path_word, chapter_uuid

        if target.isdigit():
            cached = self._search_cache.get(key)
            if cached is None or cached.expires_at <= time.monotonic():
                raise CopyMangaError(
                    "搜索结果已过期，请先使用 /cp 搜索 <关键词>。"
                )
            index = int(target)
            if index < 1 or index > len(cached.results):
                raise CopyMangaError(
                    f"序号超出范围（1-{len(cached.results)}）。"
                )
            return cached.results[index - 1]["path_word"], None

        if is_valid_path_word(target):
            return target, None

        # 不是序号、链接或 path_word 时，按关键词搜索并使用第一条结果
        data = await self.client.search(target)
        results = normalize_search_results(data, limit=1)
        if not results:
            raise CopyMangaError(f"没有找到漫画：{target}")
        return results[0]["path_word"], None

    async def _get_chapters(self, comic: ComicMeta) -> list[ChapterMeta]:
        raw = await self.client.get_group_chapters(
            comic.path_word, comic.default_group
        )
        chapters = [
            normalize_chapter(item, comic.default_group)
            for item in raw
            if isinstance(item, dict) and item.get("uuid")
        ]
        chapters.sort(key=lambda chapter: (chapter.order, chapter.index))
        return chapters

    async def _download_one(
        self,
        umo: str,
        comic: ComicMeta,
        chapter: ChapterMeta,
        announce: bool = True,
    ) -> DownloadResult:
        chapter_data = await self.downloader.fetch_chapter(
            comic.path_word, chapter.uuid
        )
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
        if announce:
            await self._send(
                umo,
                f"《{comic.title}》 {title}：共 {len(pages)} 页，开始下载。",
            )

        reporter = _ProgressReporter(self, umo, comic.title, title)
        result = await self.downloader.download(
            comic, actual, chapter_data, self.download_root, reporter
        )

        if not announce:
            return result

        if result.existed:
            text = (
                f"《{comic.title}》 {title}：已存在，跳过下载。\n"
                f"目录：{result.path}"
            )
        else:
            text = (
                f"《{comic.title}》 {title}：下载完成，共 {result.pages} 页"
            )
            if result.skipped:
                text += f"，跳过 {result.skipped} 页"
            text += f"。\n保存目录：{result.path}"
        await self._send(umo, text)
        return result

    async def _send(self, umo: str, text: str) -> None:
        if not umo:
            logger.warning("无法发送消息：unified_msg_origin 为空")
            return
        try:
            result = await self.context.send_message(
                umo, MessageChain().message(text)
            )
            if result is False:
                logger.warning("发送消息失败：%s", text[:120])
        except Exception:
            logger.exception("发送消息异常")

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        return (
            getattr(event, "unified_msg_origin", "")
            or event.get_sender_id()
        )

    @staticmethod
    def _is_private(event: AstrMessageEvent) -> bool:
        return not event.get_group_id()

    async def terminate(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        await self.client.close()

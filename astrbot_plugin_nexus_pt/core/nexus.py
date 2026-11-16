"""NexusPHP 站点客户端。"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .errors import CookieInvalidError, NexusError, PtError, TorrentFileError
from .models import TorrentDetail, TorrentItem
from .parser import (
    is_login_page,
    parse_torrent_detail,
    parse_torrent_list,
)
from .utils import normalize_base_url, normalize_cookie

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class NexusClient:
    """通过 Cookie 访问 NexusPHP 站点。"""

    def __init__(
        self,
        base_url: str,
        cookie: str,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.cookie = normalize_cookie(cookie)
        self.timeout = max(5.0, float(timeout))

        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.base_url:
            headers["Referer"] = f"{self.base_url}/"

        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            follow_redirects=True,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.cookie)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def _request(
        self,
        url: str,
        action: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.get(
                    url, params=params, headers=headers
                )
                if (
                    response.status_code in {429, 500, 502, 503, 504}
                    and attempt < 2
                ):
                    logger.debug(
                        "%s返回 %s，准备重试", action, response.status_code
                    )
                    await asyncio.sleep(1 + attempt)
                    continue
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)

        raise NexusError(f"网络请求失败：{last_error}") from last_error

    def _checked_html(self, response: httpx.Response, action: str) -> str:
        final_url = str(response.url).lower()
        if "login.php" in final_url or "takelogin.php" in final_url:
            raise CookieInvalidError("Cookie 已失效，请更新站点 Cookie。")
        if response.status_code in {401, 403}:
            raise CookieInvalidError(
                f"{action}失败（HTTP {response.status_code}），"
                "Cookie 可能已失效或账号被限制。"
            )
        if response.status_code != 200:
            raise NexusError(f"{action}失败（HTTP {response.status_code}）。")

        text = response.text
        if is_login_page(text):
            raise CookieInvalidError("Cookie 已失效，请更新站点 Cookie。")
        return text

    async def latest(self, page: int = 1) -> list[TorrentItem]:
        """获取首页（torrents.php）最新种子列表。"""
        page = max(1, int(page))
        response = await self._request(
            self._url("torrents.php"),
            "获取首页",
            params={"page": page - 1},
        )
        page_html = self._checked_html(response, "获取首页")
        return parse_torrent_list(page_html, self.base_url)

    async def search(self, keyword: str, page: int = 1) -> list[TorrentItem]:
        """按标题搜索种子。"""
        page = max(1, int(page))
        params = {
            "search": keyword,
            "search_area": 0,
            "search_mode": 0,
            "page": page - 1,
        }
        response = await self._request(
            self._url("torrents.php"), "搜索种子", params=params
        )
        page_html = self._checked_html(response, "搜索种子")
        return parse_torrent_list(page_html, self.base_url)

    async def detail(self, torrent_id: str) -> TorrentDetail:
        """获取种子详情页。"""
        response = await self._request(
            self._url("details.php"),
            "获取种子详情",
            params={"id": torrent_id, "hit": 1},
        )
        page_html = self._checked_html(response, "获取种子详情")
        return parse_torrent_detail(
            page_html, self.base_url, str(torrent_id)
        )

    async def fill_covers(
        self, items: list[TorrentItem], concurrency: int = 5
    ) -> None:
        """为列表中缺少封面的种子并发补齐封面。"""
        targets = [item for item in items if not item.cover]
        if not targets:
            return

        semaphore = asyncio.Semaphore(max(1, int(concurrency)))

        async def fill(item: TorrentItem) -> None:
            async with semaphore:
                try:
                    detail = await self.detail(item.id)
                except PtError as exc:
                    logger.debug("获取种子 %s 封面失败：%s", item.id, exc)
                    return
                item.cover = detail.cover

        await asyncio.gather(*(fill(item) for item in targets))

    async def fetch_torrent(self, url: str, referer: str = "") -> bytes:
        """下载 .torrent 文件内容。"""
        if not url:
            raise TorrentFileError("没有找到种子下载链接。")

        target = (
            url
            if url.lower().startswith(("http://", "https://"))
            else self._url(url)
        )
        headers = {"Accept": "application/x-bittorrent,*/*"}
        if referer:
            headers["Referer"] = referer

        response = await self._request(target, "下载种子", headers=headers)
        final_url = str(response.url).lower()
        if "login.php" in final_url or "takelogin.php" in final_url:
            raise CookieInvalidError("Cookie 已失效，请更新站点 Cookie。")
        if response.status_code in {401, 403}:
            raise CookieInvalidError(
                f"下载种子失败（HTTP {response.status_code}），"
                "Cookie 可能已失效或没有下载权限。"
            )
        if response.status_code != 200:
            raise NexusError(f"下载种子失败（HTTP {response.status_code}）。")

        data = response.content
        if not data:
            raise TorrentFileError("下载种子失败：返回内容为空。")

        head = data[:512].lstrip().lower()
        if head.startswith(b"<") or b"<html" in head:
            if is_login_page(data.decode("utf-8", "ignore")):
                raise CookieInvalidError(
                    "Cookie 已失效，请更新站点 Cookie。"
                )
            raise TorrentFileError(
                "站点返回的是网页而不是种子文件，可能没有下载权限。"
            )
        if not data.startswith(b"d"):
            raise TorrentFileError("返回内容不是有效的 .torrent 文件。")
        return data

    async def close(self) -> None:
        await self._http.aclose()

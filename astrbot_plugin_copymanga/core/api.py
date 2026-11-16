"""拷贝漫画接口客户端。"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
from urllib.parse import quote

import httpx

from .errors import ApiError, RiskControlError, TokenExpiredError
from .utils import detect_image_ext

logger = logging.getLogger(__name__)

DEFAULT_API_DOMAIN = "api.copy202601.com"
SEARCH_LIMIT = 20
CHAPTER_LIMIT = 100

API_HEADERS = {
    "User-Agent": "COPY/3.0.0",
    "Accept": "application/json",
    "version": "2025.08.15",
    "platform": "1",
    "webp": "1",
    "region": "1",
}


class CopyMangaClient:
    """只依赖 httpx 的拷贝漫画客户端。"""

    def __init__(
        self,
        api_domain: str = DEFAULT_API_DOMAIN,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        domain = (api_domain or DEFAULT_API_DOMAIN).strip()
        domain = domain.removeprefix("https://").removeprefix("http://").strip("/")
        self.api_domain = domain or DEFAULT_API_DOMAIN
        self.timeout = max(5.0, float(timeout))

        self._api = httpx.AsyncClient(
            headers=API_HEADERS,
            timeout=self.timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._img = httpx.AsyncClient(
            headers={"User-Agent": "COPY/3.0.0", "Accept": "image/*"},
            timeout=max(self.timeout, 30.0),
            follow_redirects=True,
            transport=transport,
        )

    def _url(self, path: str) -> str:
        return f"https://{self.api_domain}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._api.request(
                    method,
                    self._url(path),
                    params=params,
                    data=data,
                    headers=headers,
                )
                if (
                    response.status_code in {429, 500, 502, 503, 504}
                    and attempt < 2
                ):
                    logger.debug(
                        "接口返回 %s，准备重试：%s", response.status_code, path
                    )
                    await asyncio.sleep(1 + attempt)
                    continue
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)

        raise ApiError(f"网络请求失败：{last_error}") from last_error

    def _results(self, response: httpx.Response, action: str):
        if response.status_code == 210:
            raise RiskControlError(f"{action}触发风控（HTTP 210）。")
        if response.status_code == 401:
            raise TokenExpiredError(f"{action}失败：Token 已失效。")
        if response.status_code != 200:
            body = response.text[:200]
            raise ApiError(f"{action}失败（HTTP {response.status_code}）：{body}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(f"{action}失败：接口返回的不是 JSON。") from exc

        if not isinstance(payload, dict):
            raise ApiError(f"{action}失败：接口返回格式不正确。")

        code = payload.get("code")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = None

        if code_int == 210:
            raise RiskControlError(f"{action}触发风控（code=210）。")
        if code_int == 401:
            raise TokenExpiredError(f"{action}失败：Token 已失效。")
        if code_int != 200:
            message = payload.get("message") or payload.get("msg") or ""
            suffix = f"：{message}" if message else ""
            raise ApiError(f"{action}失败（code={code}）{suffix}")

        return payload.get("results")

    async def search(self, keyword: str, page: int = 1) -> dict:
        page = max(1, int(page))
        params = {
            "limit": SEARCH_LIMIT,
            "offset": (page - 1) * SEARCH_LIMIT,
            "q": keyword,
            "q_type": "",
            "platform": 1,
        }
        response = await self._request("GET", "/api/v3/search/comic", params=params)
        results = self._results(response, "搜索漫画")
        return results if isinstance(results, dict) else {}

    async def get_comic(self, path_word: str) -> dict:
        response = await self._request(
            "GET",
            f"/api/v3/comic2/{quote(path_word, safe='')}",
            params={"platform": 1},
        )
        results = self._results(response, "获取漫画详情")
        if not isinstance(results, dict):
            raise ApiError("获取漫画详情失败：数据结构不正确。")
        return results

    async def get_chapters(
        self,
        path_word: str,
        group_path_word: str,
        limit: int = CHAPTER_LIMIT,
        offset: int = 0,
    ) -> dict:
        response = await self._request(
            "GET",
            f"/api/v3/comic/{quote(path_word, safe='')}"
            f"/group/{quote(group_path_word, safe='')}/chapters",
            params={"limit": limit, "offset": offset},
        )
        results = self._results(response, "获取章节目录")
        return results if isinstance(results, dict) else {}

    async def get_group_chapters(
        self, path_word: str, group_path_word: str
    ) -> list[dict]:
        first = await self.get_chapters(path_word, group_path_word)
        chapters = list(first.get("list") or [])

        try:
            total = int(first.get("total") or 0)
        except (TypeError, ValueError):
            total = 0

        if total > CHAPTER_LIMIT:
            pages = math.ceil(total / CHAPTER_LIMIT)
            semaphore = asyncio.Semaphore(5)

            async def fetch_page(page: int) -> dict:
                async with semaphore:
                    return await self.get_chapters(
                        path_word,
                        group_path_word,
                        limit=CHAPTER_LIMIT,
                        offset=(page - 1) * CHAPTER_LIMIT,
                    )

            results = await asyncio.gather(
                *(fetch_page(page) for page in range(2, pages + 1)),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    raise result
                chapters.extend(result.get("list") or [])

        # 分页边界可能重复，按 uuid 去重并保持接口顺序
        seen: set[str] = set()
        unique: list[dict] = []
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            uuid = str(chapter.get("uuid") or "")
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)
            unique.append(chapter)
        return unique

    async def get_chapter(
        self, path_word: str, chapter_uuid: str, token: str
    ) -> dict:
        if not token:
            raise TokenExpiredError("获取章节失败：Token 为空。")

        response = await self._request(
            "GET",
            f"/api/v3/comic/{quote(path_word, safe='')}"
            f"/chapter2/{quote(chapter_uuid, safe='')}",
            params={"platform": 1},
            headers={"authorization": f"Token {token}"},
        )
        results = self._results(response, "获取章节")
        if not isinstance(results, dict):
            raise ApiError("获取章节失败：数据结构不正确。")
        return results

    async def register(self, username: str, password: str) -> None:
        response = await self._request(
            "POST",
            "/api/v3/register",
            data={
                "username": username,
                "password": password,
                "source": "freeSite",
            },
        )
        self._results(response, "注册账号")

    async def login(self, username: str, password: str) -> str:
        encoded = base64.b64encode(
            f"{password}-1729".encode("utf-8")
        ).decode("ascii")
        response = await self._request(
            "POST",
            "/api/v3/login",
            data={
                "username": username,
                "password": encoded,
                "salt": 1729,
            },
        )
        results = self._results(response, "登录账号")
        if not isinstance(results, dict) or not results.get("token"):
            raise ApiError("登录账号失败：接口没有返回 Token。")
        return str(results["token"])

    async def get_user_profile(self, token: str) -> dict:
        if not token:
            raise TokenExpiredError("校验 Token 失败：Token 为空。")

        response = await self._request(
            "GET",
            "/api/v3/member/info",
            headers={"authorization": f"Token {token}"},
        )
        results = self._results(response, "校验 Token")
        return results if isinstance(results, dict) else {}

    async def get_image(self, url: str) -> tuple[bytes, str]:
        if url.startswith("//"):
            url = f"https:{url}"

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._img.get(url)
                if response.status_code == 200:
                    data = response.content
                    if not data:
                        raise ApiError("下载图片失败：图片内容为空。")
                    content_type = response.headers.get("content-type", "")
                    return data, detect_image_ext(data, content_type)

                if (
                    response.status_code in {429, 500, 502, 503, 504}
                    and attempt < 2
                ):
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise ApiError(f"下载图片失败（HTTP {response.status_code}）。")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))

        raise ApiError(f"下载图片失败：{last_error}") from last_error

    async def close(self) -> None:
        await self._api.aclose()
        await self._img.aclose()

"""排行榜海报服务客户端。

接口文档（RANK_POSTER_API.md）：

- ``GET /api/rank-poster/image?type=&groups=`` 返回 JPEG 图片流
- ``GET /api/rank-poster/links?type=&groups=`` 返回同序号对应的视频直链

两个接口共用服务端按 ``(type, groups)`` 缓存 10 分钟的同一份榜单快照。
为了严格保证“图上的序号 == 下载链接的序号”，本客户端每次都用相同参数
**先取图片、紧接着取链接**（接口文档推荐的成对调用方式），任一失败则整次
查询失败，不返回半份数据。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .config import PluginSettings
from .parsing import TYPE_LABELS

IMAGE_PATH = "/api/rank-poster/image"
LINKS_PATH = "/api/rank-poster/links"


class RankPosterError(Exception):
    """查询排行榜失败，message 可以直接展示给用户。"""


@dataclass(frozen=True)
class RankItem:
    group: int
    index: int
    video_url: str


@dataclass
class RankSnapshot:
    type_key: str
    groups: int
    label: str
    image: bytes
    items: list[RankItem] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def urls(self) -> dict[int, str]:
        return {item.index: item.video_url for item in self.items}


class RankPosterClient:
    """一个轻量客户端，每个请求周期内复用插件的 aiohttp session。"""

    def __init__(self, session: aiohttp.ClientSession, settings: PluginSettings):
        self.session = session
        self.base_url = settings.server_url.rstrip("/")
        self.timeout = settings.request_timeout
        self.ssl = settings.verify_ssl
        self.headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, image/jpeg, */*",
        }

    async def fetch(self, type_key: str, groups: int) -> RankSnapshot:
        """依次请求图片和链接接口，失败则抛出 RankPosterError。

        先请求图片接口：服务端会为 ``(type, groups)`` 生成/命中榜单快照，
        紧接着用完全相同的参数请求链接接口即可复用同一份快照，避免两边
        拿到不同批次的榜单（接口文档也建议先图后链接、10 分钟内成对调用）。
        """
        if not self.base_url:
            raise RankPosterError("未配置排行榜海报服务地址，请在插件配置里填写 server_url。")
        groups = max(1, min(10, int(groups)))
        params = {"type": type_key, "groups": str(groups)}

        image = await self._fetch_image(params)
        try:
            links = await self._fetch_links(params)
        except RankPosterError as exc:
            raise RankPosterError(f"海报已获取，但下载链接获取失败：{exc}") from exc

        items: list[RankItem] = []
        raw_items = links.get("items")
        if not isinstance(raw_items, list):
            raw_items = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                index = int(raw["index"])
            except (KeyError, TypeError, ValueError):
                continue
            video_url = str(raw.get("video_url") or "").strip()
            if not video_url:
                continue
            group = int(raw.get("group") or ((index - 1) // 10 + 1))
            items.append(RankItem(group=group, index=index, video_url=video_url))

        if not items:
            raise RankPosterError("链接接口没有返回可下载的视频，榜单可能暂时没有数据。")

        label = str(links.get("label") or "").strip() or TYPE_LABELS.get(type_key, type_key)
        return RankSnapshot(
            type_key=type_key,
            groups=groups,
            label=label,
            image=image,
            items=sorted(items, key=lambda item: item.index),
        )

    async def _fetch_image(self, params: dict[str, str]) -> bytes:
        url = f"{self.base_url}{IMAGE_PATH}"
        try:
            async with self.session.get(
                url,
                params=params,
                headers=self.headers,
                ssl=self.ssl,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                body = await response.read()
                content_type = (response.headers.get("Content-Type") or "").lower()
                if response.status != 200:
                    raise RankPosterError(self._http_error(response.status, body, "海报图片接口"))
                if "json" in content_type:
                    raise RankPosterError(self._json_error(body, "海报图片接口"))
                if not body:
                    raise RankPosterError("海报图片接口返回了空响应。")
                if "image" not in content_type and not body.startswith(b"\xff\xd8"):
                    raise RankPosterError("海报图片接口返回的内容不是有效的图片。")
                return body
        except RankPosterError:
            raise
        except asyncio.TimeoutError as exc:
            raise RankPosterError(f"请求海报图片接口超时（{self.timeout:g} 秒），可调大 request_timeout。") from exc
        except aiohttp.ClientError as exc:
            raise RankPosterError(f"无法连接排行榜服务 {self.base_url}：{exc}") from exc

    async def _fetch_links(self, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}{LINKS_PATH}"
        try:
            async with self.session.get(
                url,
                params=params,
                headers=self.headers,
                ssl=self.ssl,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                body = await response.read()
                if response.status != 200:
                    raise RankPosterError(self._http_error(response.status, body, "下载链接接口"))
                try:
                    data = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RankPosterError("下载链接接口返回的不是合法 JSON。") from exc
                if not isinstance(data, dict):
                    raise RankPosterError("下载链接接口返回格式不正确。")
                if data.get("ok") is False:
                    detail = str(data.get("error") or "未知错误")
                    raise RankPosterError(f"下载链接接口返回错误：{detail}")
                return data
        except RankPosterError:
            raise
        except asyncio.TimeoutError as exc:
            raise RankPosterError(f"请求下载链接接口超时（{self.timeout:g} 秒）。") from exc
        except aiohttp.ClientError as exc:
            raise RankPosterError(f"无法连接排行榜服务 {self.base_url}：{exc}") from exc

    @staticmethod
    def _decode(body: bytes) -> str:
        try:
            return body.decode("utf-8", "replace").strip()
        except Exception:  # pragma: no cover - decode 已带 replace
            return ""

    def _json_error(self, body: bytes, name: str) -> str:
        text = self._decode(body)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return f"{name}返回异常：{text[:200] or '空响应'}"
        if isinstance(data, dict):
            detail = data.get("error") or data.get("detail") or data.get("message")
            if detail:
                return f"{name}返回异常：{detail}"
        return f"{name}返回异常。"

    def _http_error(self, status: int, body: bytes, name: str) -> str:
        text = self._decode(body)
        detail = ""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                detail = str(data.get("error") or data.get("detail") or "")
        except json.JSONDecodeError:
            detail = text[:200]

        hints = {
            400: "参数不合法",
            404: "该榜单暂无数据",
            422: "参数类型错误",
            502: "上游榜单拉取失败或拼图生成失败",
        }
        message = f"{name}返回 HTTP {status}"
        hint = hints.get(status)
        if hint:
            message += f"（{hint}）"
        if detail:
            message += f"：{detail}"
        return message

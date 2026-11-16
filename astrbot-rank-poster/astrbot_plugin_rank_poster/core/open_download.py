"""对外开放下载接口客户端。

接口文档（OPEN_DOWNLOAD_API.md）：

    POST {server_url}/api/open/download
    {"url": "https://.../video.mp4", "path": "astrbot/day_p001_xxx.mp4", "key": "<key>"}

服务端收到后立即返回 ``task_id``，由后台线程下载；``path`` 是相对路径时
基于服务端的下载根目录展开，父目录会自动创建。任务状态可通过
``GET /api/open/download/{task_id}`` 查询（本插件只负责提交）。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .config import PluginSettings

SAFE_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".ts", ".flv"}
SUBMIT_PATH = "/api/open/download"


class OpenDownloadError(Exception):
    """提交下载任务失败，message 可以直接展示给用户。"""


def build_target_path(subdir: str, type_key: str, index: int, url: str) -> str:
    """生成提交给服务端的相对路径，例如 ``astrbot/week_p012_abc.mp4``。"""
    try:
        raw_path = urlsplit(url).path
    except ValueError:
        raw_path = ""

    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", Path(raw_path).stem).strip("_")[-40:].strip("_")
    if not stem:
        stem = "video"

    extension = Path(raw_path).suffix.lower()
    if extension not in SAFE_EXTENSIONS:
        extension = ".mp4"

    filename = f"{type_key}_p{index:03d}_{stem}{extension}"
    clean_subdir = (subdir or "").strip().strip("/\\")
    return f"{clean_subdir}/{filename}" if clean_subdir else filename


class OpenDownloadClient:
    def __init__(self, session: aiohttp.ClientSession, settings: PluginSettings):
        self.session = session
        self.base_url = (settings.server_url or "").rstrip("/")
        self.key = (settings.open_download_key or "").strip()
        self.ssl = settings.verify_ssl
        self.timeout = min(60.0, max(10.0, settings.request_timeout))

    @property
    def submit_url(self) -> str:
        return f"{self.base_url}{SUBMIT_PATH}"

    async def _post(self, url: str, path: str) -> tuple[int, str, dict | None]:
        payload = {"url": url, "path": path, "key": self.key}
        try:
            async with self.session.post(
                self.submit_url,
                json=payload,
                ssl=self.ssl,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                status = response.status
                text = (await response.text()).strip()
        except asyncio.TimeoutError as exc:
            raise OpenDownloadError(f"连接下载服务超时：{self.base_url}") from exc
        except aiohttp.ClientError as exc:
            raise OpenDownloadError(f"无法连接下载服务 {self.base_url}：{exc}") from exc
        except ValueError as exc:
            raise OpenDownloadError(f"下载服务地址不合法：{self.base_url}（{exc}）") from exc

        data = None
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        return status, text, data

    @staticmethod
    def _detail(status: int, text: str, data: dict | None) -> str:
        if isinstance(data, dict):
            error = data.get("error")
            if error:
                return str(error)
        return text[:200] or f"HTTP {status}"

    async def submit(self, url: str, path: str) -> dict:
        """提交一个下载任务，成功返回 task 字典（含 task_id/path/status）。"""
        if not self.base_url:
            raise OpenDownloadError("未配置下载服务地址，请在插件配置里填写 server_url。")

        status, text, data = await self._post(url, path)
        if status == 401:
            raise OpenDownloadError(
                "开放下载 Key 无效或未在服务端启用，"
                "请检查 open_download_key（服务端「任务设置 → 开放下载 Key」）。"
            )
        if status == 422:
            raise OpenDownloadError(f"下载服务参数校验失败：{self._detail(status, text, data)}")
        if status != 200:
            raise OpenDownloadError(f"下载服务返回 HTTP {status}：{self._detail(status, text, data)}")
        if not isinstance(data, dict) or not data.get("ok"):
            raise OpenDownloadError(f"下载服务返回异常：{self._detail(status, text, data)}")

        task = data.get("task")
        if not isinstance(task, dict) or not task.get("task_id"):
            raise OpenDownloadError("下载服务返回缺少 task_id。")
        return task

    async def probe(self) -> None:
        """检查服务与 key 是否可用：用空 URL 探测，服务端会返回 400 而不会创建任务。"""
        if not self.base_url:
            raise OpenDownloadError("未配置下载服务地址，请在插件配置里填写 server_url。")
        status, text, data = await self._post("", "__astrbot_probe__")
        if status == 401:
            raise OpenDownloadError(
                "开放下载 Key 无效，请检查 open_download_key。"
            )
        if status in (200, 400):
            # 400 = 空 URL 被拒绝，说明 key 已通过；200 = 服务端未校验空 URL。
            return
        raise OpenDownloadError(
            f"开放下载接口异常（HTTP {status}）：{self._detail(status, text, data)}"
        )

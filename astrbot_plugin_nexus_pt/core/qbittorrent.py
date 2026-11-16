"""qBittorrent Web API 客户端（API Key 认证）。"""

from __future__ import annotations

import httpx

from .errors import QbittorrentError
from .utils import normalize_base_url


class QbittorrentClient:
    """只实现上传种子文件所需的最小接口。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url, default_scheme="http")
        self.api_key = (api_key or "").strip()
        self.timeout = max(5.0, float(timeout))

        headers = {"Referer": f"{self.base_url}/"} if self.base_url else {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            follow_redirects=True,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def add_torrent(
        self,
        filename: str,
        content: bytes,
        savepath: str = "",
        category: str = "",
        paused: bool = False,
    ) -> None:
        """上传 .torrent 文件并添加到 qBittorrent。"""
        if not self.configured:
            raise QbittorrentError(
                "未配置 qBittorrent 地址或 API Key。"
            )
        if not content:
            raise QbittorrentError("种子内容为空，无法添加。")

        data: dict[str, str] = {"paused": "true" if paused else "false"}
        if savepath:
            data["savepath"] = savepath
            data["autoTMM"] = "false"
        if category:
            data["category"] = category

        try:
            response = await self._http.post(
                f"{self.base_url}/api/v2/torrents/add",
                data=data,
                files={
                    "torrents": (
                        filename,
                        content,
                        "application/x-bittorrent",
                    )
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise QbittorrentError(f"连接 qBittorrent 失败：{exc}") from exc

        if response.status_code in {401, 403}:
            raise QbittorrentError(
                f"qBittorrent 认证失败（HTTP {response.status_code}），"
                "请检查 API Key 是否正确，并确认 qBittorrent 版本不低于 5.2。"
            )
        if response.status_code != 200:
            raise QbittorrentError(
                f"qBittorrent 添加种子失败（HTTP {response.status_code}）。"
            )

        text = response.text.strip()
        if text == "Ok." or self._is_json_success(response):
            return

        message = text[:200] or "无返回内容"
        raise QbittorrentError(
            f"qBittorrent 添加种子失败：{message}"
        )

    @staticmethod
    def _is_json_success(response: httpx.Response) -> bool:
        """兼容 qBittorrent 5.2+ 返回的 JSON 结果。"""
        try:
            payload = response.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False

        try:
            success = int(payload.get("success_count") or 0)
            failure = int(payload.get("failure_count") or 0)
            pending = int(payload.get("pending_count") or 0)
        except (TypeError, ValueError):
            return False

        if failure > 0:
            return False
        added = payload.get("added_torrent_ids")
        if isinstance(added, list) and added:
            return True
        return success > 0 or pending > 0

    async def close(self) -> None:
        await self._http.aclose()

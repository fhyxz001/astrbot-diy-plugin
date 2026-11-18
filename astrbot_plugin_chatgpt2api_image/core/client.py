"""ChatGPT2API / OpenAI 兼容图片生成客户端。"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import ApiError, ConfigError
from .utils import (
    MAX_IMAGE_BYTES,
    build_endpoint,
    guess_image_extension,
    normalize_api_key,
    normalize_base_url,
    sniff_image_mime,
    truncate,
)

logger = logging.getLogger(__name__)

USER_AGENT = "AstrBot-ChatGPT2API-Image/0.2.0"


@dataclass(frozen=True)
class GeneratedImage:
    """一张生成好的图片。"""

    data: bytes
    mime_type: str = "image/png"
    source: str = ""


class ChatGpt2ApiClient:
    """调用 ``/v1/images/generations`` 与 ``/v1/images/edits`` 接口。"""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "gpt-image-2.6",
        timeout: float = 120.0,
        size: str = "",
        quality: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = normalize_api_key(api_key)
        self.model = str(model or "").strip()
        self.timeout = max(10.0, float(timeout))
        self.size = str(size or "").strip()
        self.quality = str(quality or "").strip()

        headers = {
            "Accept": "application/json, image/*;q=0.9, */*;q=0.8",
            "User-Agent": USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        timeout_config = httpx.Timeout(self.timeout, connect=10.0)
        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=timeout_config,
            follow_redirects=True,
            transport=transport,
        )
        # 下载图片使用独立客户端，避免把 API Key 带给第三方图片地址。
        self._download_http = httpx.AsyncClient(
            timeout=httpx.Timeout(min(self.timeout, 60.0), connect=10.0),
            follow_redirects=True,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def endpoint(self) -> str:
        return build_endpoint(self.base_url)

    @property
    def edit_endpoint(self) -> str:
        return build_endpoint(self.base_url, "edits")

    async def close(self) -> None:
        await self._http.aclose()
        await self._download_http.aclose()

    async def generate(
        self,
        prompt: str,
        n: int = 1,
        size: str | None = None,
        quality: str | None = None,
    ) -> list[GeneratedImage]:
        """生成图片，返回解码后的图片数据。"""
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ConfigError("提示词不能为空。")
        if not self.configured:
            raise ConfigError(
                "尚未配置 ChatGPT2API 服务地址或模型，请先在插件配置中填写。"
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": max(1, min(int(n), 8)),
        }
        final_size = str(size if size is not None else self.size).strip()
        final_quality = str(
            quality if quality is not None else self.quality
        ).strip()
        if final_size:
            payload["size"] = final_size
        if final_quality:
            payload["quality"] = final_quality

        response = await self._post(
            self.endpoint,
            label="生图",
            json=payload,
        )
        return await self._parse_response(response)

    async def edit(
        self,
        prompt: str,
        image: bytes,
        mime_type: str = "",
        filename: str = "",
        n: int = 1,
        size: str | None = None,
        quality: str | None = None,
    ) -> list[GeneratedImage]:
        """编辑图片（multipart/form-data），返回解码后的图片数据。"""
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ConfigError("提示词不能为空。")
        if not image:
            raise ConfigError("没有可编辑的图片。")
        if len(image) > MAX_IMAGE_BYTES:
            raise ConfigError(
                "图片体积超过 "
                f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB，已拒绝上传。"
            )
        if not self.configured:
            raise ConfigError(
                "尚未配置 ChatGPT2API 服务地址或模型，请先在插件配置中填写。"
            )

        final_mime = sniff_image_mime(image) or str(mime_type or "").strip()
        if not final_mime:
            final_mime = "image/png"
        final_name = str(filename or "").strip() or (
            f"input.{guess_image_extension(image, final_mime)}"
        )

        form: dict[str, str] = {
            "model": self.model,
            "prompt": prompt,
        }
        final_size = str(size if size is not None else self.size).strip()
        final_quality = str(
            quality if quality is not None else self.quality
        ).strip()
        if final_size:
            form["size"] = final_size
        if final_quality:
            form["quality"] = final_quality
        count = max(1, min(int(n), 8))
        # 与官方 curl 保持一致：单张时不额外发送 n 参数。
        if count > 1:
            form["n"] = str(count)

        response = await self._post(
            self.edit_endpoint,
            label="图片编辑",
            data=form,
            files={"image": (final_name, image, final_mime)},
        )
        return await self._parse_response(response)

    async def _post(
        self,
        url: str,
        *,
        label: str,
        json: Any = None,
        data: Any = None,
        files: Any = None,
    ) -> httpx.Response:
        headers = (
            {"Content-Type": "application/json"} if json is not None else None
        )
        try:
            response = await self._http.post(
                url,
                json=json,
                data=data,
                files=files,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise ApiError(
                f"请求{label}接口超时（{self.timeout:g} 秒），"
                "可调大插件配置中的请求超时。"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"无法连接{label}接口：{exc}") from exc

        if response.status_code >= 400:
            raise ApiError(
                self._http_error_message(response, label),
                status_code=response.status_code,
            )
        return response

    async def _parse_response(
        self, response: httpx.Response
    ) -> list[GeneratedImage]:
        content_type = response.headers.get("content-type", "")
        if content_type.lower().startswith("image/"):
            return [
                self._image_from_bytes(
                    response.content, content_type, "response"
                )
            ]

        # 少数网关会直接返回二进制图片，而不是 JSON。
        sniffed = sniff_image_mime(response.content)
        if sniffed:
            return [
                self._image_from_bytes(response.content, sniffed, "response")
            ]

        try:
            body = response.json()
        except ValueError as exc:
            raise ApiError(
                "接口返回了非 JSON 内容（HTTP "
                f"{response.status_code}）：{truncate(response.text)}"
            ) from exc

        if isinstance(body, dict) and body.get("error"):
            raise ApiError(self._json_error_message(body))

        images = await self._images_from_body(body)
        if not images:
            raise ApiError(
                "接口没有返回可用图片，返回内容："
                f"{truncate(response.text)}"
            )
        return images

    async def _images_from_body(self, body: Any) -> list[GeneratedImage]:
        candidates: list[str] = []
        for value in _iter_image_values(body):
            text = value.strip()
            if not text:
                continue
            # 有些接口会同时返回 revised_prompt 等长文本，只保留像图片数据的值。
            if not (
                text.startswith(("http://", "https://", "data:", "/"))
                or _looks_like_base64(text)
                or _looks_like_relative_image_url(text)
            ):
                continue
            candidates.append(text)
            if len(candidates) >= 8:
                break

        images: list[GeneratedImage] = []
        errors: list[str] = []
        for candidate in candidates:
            try:
                image = await self._load_candidate(candidate)
            except ApiError as exc:
                errors.append(str(exc))
                continue
            images.append(image)

        if not images and errors:
            raise ApiError(errors[0])
        return images

    async def _load_candidate(self, value: str) -> GeneratedImage:
        if value.startswith("data:"):
            return self._image_from_data_uri(value)
        if value.startswith(("http://", "https://")):
            return await self._download_image(value)

        if value.startswith("/") or _looks_like_relative_image_url(value):
            return await self._download_image(self._absolute_url(value))

        data = _decode_base64(value)
        if not data:
            raise ApiError("接口返回的 base64 图片数据无法解码。")
        return self._image_from_bytes(data, "", "base64")

    def _image_from_data_uri(self, value: str) -> GeneratedImage:
        header, separator, payload = value.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ApiError("接口返回了不支持的 data URI 图片格式。")
        data = _decode_base64(payload)
        if not data:
            raise ApiError("接口返回的 data URI 图片数据无法解码。")
        mime = header[5:].split(";", 1)[0].strip()
        return self._image_from_bytes(data, mime, "data-uri")

    def _image_from_bytes(
        self,
        data: bytes,
        mime_type: str,
        source: str,
    ) -> GeneratedImage:
        if not data:
            raise ApiError("接口返回了空图片。")
        if len(data) > MAX_IMAGE_BYTES:
            raise ApiError(
                "图片体积超过 "
                f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB，已拒绝保存。"
            )

        sniffed = sniff_image_mime(data)
        declared = (mime_type or "").split(";", 1)[0].strip().lower()
        if not sniffed and not declared.startswith("image/"):
            raise ApiError("接口返回的数据不是可识别的图片。")
        return GeneratedImage(
            data=data,
            mime_type=sniffed or declared or "image/png",
            source=source,
        )

    def _absolute_url(self, value: str) -> str:
        parts = urlsplit(self.base_url)
        origin = f"{parts.scheme}://{parts.netloc}/"
        return urljoin(origin, value.lstrip("/"))

    async def _download_image(self, url: str) -> GeneratedImage:
        headers = {"User-Agent": USER_AGENT}
        if self.api_key and _same_origin(url, self.base_url):
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = await self._download_http.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise ApiError("下载生成图片超时。") from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"下载生成图片失败：{exc}") from exc

        if response.status_code != 200:
            raise ApiError(
                f"下载生成图片失败（HTTP {response.status_code}）。"
            )
        return self._image_from_bytes(
            response.content,
            response.headers.get("content-type", ""),
            url,
        )

    @staticmethod
    def _http_error_message(
        response: httpx.Response, label: str = "生图"
    ) -> str:
        detail = _response_error_detail(response)
        code = response.status_code
        if code in {401, 403}:
            prefix = "接口鉴权失败，请检查 API Key"
        elif code == 404:
            prefix = "接口地址不存在（HTTP 404），请检查服务地址"
        elif code == 429:
            prefix = "请求过于频繁或额度不足（HTTP 429）"
        elif code >= 500:
            prefix = f"{label}服务异常（HTTP {code}）"
        else:
            prefix = f"接口返回 HTTP {code}"
        return f"{prefix}：{detail}" if detail else prefix

    @staticmethod
    def _json_error_message(body: Any) -> str:
        return _message_from_error(body) or truncate(str(body))


def _iter_image_values(value: Any, depth: int = 0) -> Iterator[str]:
    """从常见响应结构中惰性提取图片字段。"""
    if depth > 5:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_image_values(item, depth + 1)
        return
    if not isinstance(value, dict):
        return

    # 单个图片对象：只取一个字段，避免 b64 和 url 重复出图。
    for key in ("b64_json", "b64", "base64", "image_base64"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            yield item
            return
    for key in ("url", "image_url"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            yield item
            return
    image = value.get("image")
    if isinstance(image, str) and image.strip():
        yield image
        return
    if isinstance(image, (dict, list)):
        yield from _iter_image_values(image, depth + 1)
        return

    for key in ("data", "images", "output", "result"):
        if key in value:
            yield from _iter_image_values(value[key], depth + 1)


def _looks_like_base64(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 24:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact))


def _looks_like_relative_image_url(value: str) -> bool:
    return bool(
        re.search(
            r"\.(?:png|jpe?g|webp|gif|bmp|avif)(?:\?.*)?$",
            value,
            flags=re.IGNORECASE,
        )
    )


def _decode_base64(value: str) -> bytes:
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return b""
    if len(compact) > MAX_IMAGE_BYTES * 4 // 3 + 16:
        return b""
    compact = compact.replace("-", "+").replace("_", "/")
    compact += "=" * (-len(compact) % 4)
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return b""


def _same_origin(url: str, base_url: str) -> bool:
    left = _origin(url)
    right = _origin(base_url)
    return bool(left and right and left == right)


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    return parts.scheme.lower(), parts.hostname.lower(), port


def _response_error_detail(response: httpx.Response) -> str:
    try:
        return _message_from_error(response.json()) or truncate(response.text)
    except ValueError:
        return truncate(response.text)


def _message_from_error(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        if message:
            return truncate(message)
    elif isinstance(error, str) and error.strip():
        return truncate(error)
    for key in ("message", "detail", "msg"):
        message = body.get(key)
        if isinstance(message, str) and message.strip():
            return truncate(message)
    return ""

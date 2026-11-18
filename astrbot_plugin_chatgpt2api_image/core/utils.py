"""与 AstrBot 无关的通用工具。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

DEFAULT_COMMANDS = ("st", "生图", "绘图")
EDIT_COMMANDS = ("改图", "修图", "图片编辑", "edit")
MAX_IMAGE_BYTES = 30 * 1024 * 1024

_TRIM_CHARS = " \t\r\n\u3000"
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
}
_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/avif": "avif",
}


def normalize_base_url(value: object) -> str:
    """规范化服务地址，允许只填写 IP:端口。"""
    text = str(value or "").strip().strip(_TRIM_CHARS)
    if not text:
        return ""
    if not _SCHEME_RE.match(text):
        text = f"http://{text}"

    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def build_endpoint(base_url: object, action: str = "generations") -> str:
    """根据用户填写的地址拼接 OpenAI 兼容的图片接口。

    ``action`` 为 ``generations``（文生图）或 ``edits``（图片编辑）。
    """
    base = normalize_base_url(base_url)
    if not base:
        return ""

    action = "edits" if action == "edits" else "generations"
    path = base.rstrip("/")
    for suffix in ("/images/generations", "/images/edits"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path.endswith("/images"):
        return f"{path}/{action}"
    if path.endswith("/v1"):
        return f"{path}/images/{action}"
    return f"{path}/v1/images/{action}"


def extract_prompt(
    message: object,
    commands: tuple[str, ...] = DEFAULT_COMMANDS,
) -> str:
    """从 AstrBot 的 message_str 中取出 /st 后面的提示词。

    兼容三类输入：
    - ``/st 一只猫``（AstrBot 常见形式）
    - ``st 一只猫``（适配器去掉了斜杠）
    - ``一只猫``（适配器已剥离指令，直接给参数）
    """
    text = str(message or "").strip(_TRIM_CHARS)
    if not text:
        return ""

    text = re.sub(r"^[/!！]+", "", text).strip(_TRIM_CHARS)

    for command in commands:
        match = re.match(
            rf"^{re.escape(command)}(?=$|[\s\u3000])",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            text = text[match.end() :].strip(_TRIM_CHARS)
            break

    if len(text) >= 2 and _QUOTE_PAIRS.get(text[0]) == text[-1]:
        text = text[1:-1].strip(_TRIM_CHARS)
    return text


def sniff_image_mime(data: bytes) -> str:
    """通过文件头识别常见的图片格式。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if (
        len(data) >= 12
        and data[4:8] == b"ftyp"
        and data[8:12] in {b"avif", b"avis"}
    ):
        return "image/avif"
    return ""


def guess_image_extension(
    data: bytes,
    mime_type: str = "",
    url: str = "",
) -> str:
    """推断图片扩展名，默认 png。"""
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if mime in _MIME_EXTENSIONS:
        return _MIME_EXTENSIONS[mime]

    sniffed = sniff_image_mime(data)
    if sniffed:
        return _MIME_EXTENSIONS[sniffed]

    path = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    for ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp", "avif"):
        if path.endswith(f".{ext}"):
            return "jpg" if ext == "jpeg" else ext
    return "png"


def normalize_api_key(value: object) -> str:
    """允许配置里误填 Bearer 前缀。"""
    key = str(value or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


def truncate(text: object, limit: int = 300) -> str:
    """压缩空白并截断，避免把整段 HTML/JSON 发到聊天里。"""
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}…"

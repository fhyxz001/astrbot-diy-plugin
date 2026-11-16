"""与 AstrBot 无关的通用工具。"""

from __future__ import annotations

import re


def normalize_base_url(value: str, default_scheme: str = "https") -> str:
    """补全协议并去掉结尾斜杠。"""
    text = (value or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = f"{default_scheme}://{text}"
    return text.rstrip("/")


def normalize_cookie(value: str) -> str:
    """把浏览器复制的 Cookie 整理成单行请求头格式。"""
    text = (value or "").strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()

    parts: list[str] = []
    for line in text.splitlines():
        for part in line.split(";"):
            part = part.strip()
            if part and "=" in part:
                parts.append(part)
    return "; ".join(parts)


def extract_command_arg(message: str, subcommand: str) -> str:
    """从 message_str 中取出子指令后面的参数。"""
    raw = (message or "").strip()
    # 去掉 /pt 之类的指令前缀
    raw = re.sub(r"^/\S+\s*", "", raw)
    # 有些适配器可能不带 /，直接给 "pt 搜索 ..."
    raw = re.sub(r"^(?:pt|PT)\s+", "", raw)
    if raw.startswith(subcommand):
        return raw[len(subcommand) :].strip()
    return ""


def parse_page(value: str) -> int:
    """解析页码，非法时回到第 1 页。"""
    try:
        page = int((value or "").strip())
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def extract_torrent_id(value: str) -> str:
    """从种子 ID、id:123、详情页链接中提取种子 ID。"""
    text = (value or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith("id:"):
        text = text[3:].strip()
        return text if text.isdigit() else ""

    if text.isdigit():
        return text

    match = re.search(r"[?&]id=(\d+)", text)
    if match:
        return match.group(1)
    return ""

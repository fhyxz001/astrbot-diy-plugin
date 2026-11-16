"""与 AstrBot 无关的通用工具。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

_FILENAME_TRANSLATION = str.maketrans(
    {
        "\\": " ",
        "/": " ",
        ":": "：",
        "*": "⭐",
        "?": "？",
        '"': "'",
        "<": "《",
        ">": "》",
        "|": "丨",
    }
)


def filename_filter(value: object) -> str:
    """把漫画/章节标题转换成 Windows/Linux 都能使用的目录名。"""
    text = str(value or "")
    text = text.translate(_FILENAME_TRANSLATION)
    text = "".join(ch for ch in text if ord(ch) >= 32)
    # 防止标题本身是 .. 造成路径穿越
    text = text.replace("..", "__")
    text = text[:120].strip().strip(" .")
    return text or "未命名"


def format_order(order: object) -> str:
    """章节序号格式化：1 -> 0001，1.5 -> 0001.5。"""
    try:
        value = max(0.0, float(order))
    except (TypeError, ValueError):
        return "0000"

    text = f"{value:g}"
    if "." in text:
        integer, fraction = text.split(".", 1)
        return f"{int(integer):04d}.{fraction}"
    return f"{int(text):04d}"


def is_url(value: str) -> bool:
    text = (value or "").strip()
    return bool(re.match(r"^https?://", text, re.IGNORECASE)) or "/comic/" in text


def is_valid_path_word(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{2,100}", (value or "").strip()))


def extract_from_url(value: str) -> tuple[str, str | None]:
    """从漫画/章节链接中提取 path_word 和可选的章节 uuid。"""
    match = re.search(r"/comic/([^/?#]+)(?:/chapter/([^/?#]+))?", value or "")
    if not match:
        return "", None

    path_word = unquote(match.group(1)).strip()
    chapter_uuid = unquote(match.group(2)).strip() if match.group(2) else None
    if not is_valid_path_word(path_word):
        return "", None
    if chapter_uuid and not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", chapter_uuid):
        chapter_uuid = None
    return path_word, chapter_uuid


def extract_command_arg(message: str, subcommand: str) -> str:
    """从 AstrBot 的 message_str 中取出子指令后面的参数。"""
    raw = (message or "").strip()
    # 去掉 /cp、/copymanga 之类的指令前缀
    raw = re.sub(r"^/[^\s]+\s*", "", raw)
    # 有些适配器可能不带 /，直接给 "cp 搜索 ..."
    raw = re.sub(r"^(?:cp|拷贝漫画)\s+", "", raw, flags=re.IGNORECASE)
    if raw.startswith(subcommand):
        return raw[len(subcommand) :].strip()
    return ""


def looks_like_chapter_selector(value: str) -> bool:
    """判断一个参数是否像章节选择器，用于支持带空格的搜索关键词。"""
    text = (value or "").strip().lower()
    if text in {"latest", "最新", "all"}:
        return True
    return bool(re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text))


def parse_chapter_selector(selector: str, total: int) -> list[int]:
    """解析 latest、all、单个序号或范围，返回从 1 开始的章节下标。"""
    if total <= 0:
        raise ValueError("没有可下载的章节。")

    text = (selector or "latest").strip().lower()
    if text in {"", "latest", "最新"}:
        return [total]
    if text == "all":
        return list(range(1, total + 1))

    indices: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                left, right = part.split("-", 1)
                start, end = int(left), int(right)
                if start > end:
                    start, end = end, start
                indices.update(range(start, end + 1))
            else:
                indices.add(int(part))
        except ValueError as exc:
            raise ValueError(
                "章节参数支持 latest、单个序号（如 3）或范围（如 3-5）。"
            ) from exc

    if not indices:
        raise ValueError("章节参数不能为空。")

    for index in indices:
        if index < 1 or index > total:
            raise ValueError(f"章节序号超出范围（1-{total}）。")
    return sorted(indices)


def page_numbers(words: list[object], count: int) -> list[int]:
    """把接口的 words 转换成连续的 1..N 页码。"""
    if count <= 0:
        return []

    numbers: list[int] = []
    for index in range(count):
        word = words[index] if index < len(words) else None
        if isinstance(word, (int, float)) and not isinstance(word, bool):
            numbers.append(int(word) + 1)
        else:
            numbers.append(index + 1)

    # 接口返回的 words 可能是 1 起始或存在重复，统一回退到位置序号
    if sorted(numbers) != list(range(1, count + 1)):
        numbers = list(range(1, count + 1))
    return numbers


def detect_image_ext(data: bytes, content_type: str | None) -> str:
    """根据 Content-Type 或文件头判断图片扩展名。"""
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "image/webp": "webp",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/avif": "avif",
    }
    if ctype in mapping:
        return mapping[ctype]
    if ctype.startswith("image/"):
        ext = ctype.split("/", 1)[1].strip().replace("jpeg", "jpg")
        if re.fullmatch(r"[a-z0-9]+", ext):
            return ext

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"GIF":
        return "gif"
    return "img"


def ensure_inside(root: Path, path: Path) -> Path:
    """确保目标路径位于下载根目录内。"""
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    if path_resolved != root_resolved and root_resolved not in path_resolved.parents:
        raise ValueError("下载路径越界。")
    return path_resolved


def build_chapter_dir(
    root: Path,
    comic_title: str,
    order: object,
    chapter_title: str,
) -> Path:
    """生成 漫画/序号 章节名 的保存目录。"""
    path = (
        root
        / filename_filter(comic_title)
        / f"{format_order(order)} {filename_filter(chapter_title)}"
    )
    return ensure_inside(root, path)

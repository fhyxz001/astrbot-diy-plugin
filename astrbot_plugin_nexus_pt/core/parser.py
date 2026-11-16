"""NexusPHP 页面解析。"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from .models import TorrentDetail, TorrentItem

_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_DETAILS_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']*details\.php\?(?P<query>[^\"']*))[\"'][^>]*>"
    r"(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_CATEGORY_RE = re.compile(
    r"<a\b[^>]*href=[\"'][^\"']*torrents\.php\?cat=\d+[^\"']*[\"'][^>]*>"
    r"(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_DOWNLOAD_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']*download\.php\?[^\"']*)[\"']",
    re.IGNORECASE | re.DOTALL,
)
_FIELD_RE = re.compile(
    r"<td\b[^>]*class=[\"'][^\"']*rowhead[^\"']*[\"'][^>]*>(?P<label>.*?)</td>\s*"
    r"<td\b[^>]*>(?P<value>.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_SIZE_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(TiB|GiB|MiB|KiB|TB|GB|MB|KB|B)\b",
    re.IGNORECASE,
)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
_META_IMAGE_RE = re.compile(
    r"<meta\b[^>]*property=[\"']og:image[\"'][^>]*content=[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
_META_IMAGE_RE_ALT = re.compile(
    r"<meta\b[^>]*content=[\"']([^\"']+)[\"'][^>]*property=[\"']og:image[\"']",
    re.IGNORECASE | re.DOTALL,
)
_USERNAME_RE = re.compile(r"name=[\"']username[\"']", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"name=[\"']password[\"']", re.IGNORECASE)


def strip_tags(value: str) -> str:
    """去掉 HTML 标签、反转义实体并压缩空白。"""
    text = re.sub(
        r"<script\b.*?</script>", " ", value or "", flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_size(text: str) -> str:
    """从一段文本中提取第一个大小（如 2.15 GB）。"""
    match = _SIZE_RE.search(text or "")
    if not match:
        return ""
    return f"{match.group(1)} {match.group(2).upper()}"


def is_login_page(page_html: str) -> bool:
    """判断页面是否是登录页。"""
    text = (page_html or "").lower()
    if "takelogin.php" in text:
        return True
    return bool(_USERNAME_RE.search(text) and _PASSWORD_RE.search(text))


def parse_torrent_list(page_html: str, base_url: str) -> list[TorrentItem]:
    """解析 torrents.php 的种子列表。"""
    items: list[TorrentItem] = []
    seen: set[str] = set()

    for row in _ROW_RE.findall(page_html or ""):
        item = _parse_row(row, base_url)
        if item is None or item.id in seen:
            continue
        seen.add(item.id)
        items.append(item)

    if items:
        return items

    # 少数主题不使用表格，退化为全页扫描
    for match in _DETAILS_RE.finditer(page_html or ""):
        torrent_id = _query_id(match.group("query"))
        title = strip_tags(match.group("body"))
        if not torrent_id or not title or torrent_id in seen:
            continue
        seen.add(torrent_id)
        items.append(_build_item(torrent_id, title, "", "", base_url))
    return items


def parse_torrent_detail(
    page_html: str, base_url: str, torrent_id: str
) -> TorrentDetail:
    """解析 details.php 的种子详情。"""
    fields = _parse_fields(page_html)
    return TorrentDetail(
        id=str(torrent_id),
        title=_page_title(page_html) or f"种子 {torrent_id}",
        size=fields.get("size", ""),
        category=fields.get("category", ""),
        subtitle=fields.get("subtitle", ""),
        seeders=fields.get("seeders", ""),
        leechers=fields.get("leechers", ""),
        snatched=fields.get("snatched", ""),
        added=fields.get("added", ""),
        description=_description(page_html),
        cover=find_cover_url(page_html, base_url),
        download_url=find_download_url(page_html, base_url, torrent_id),
        page_url=_page_url(base_url, torrent_id),
    )


def find_download_url(page_html: str, base_url: str, torrent_id: str) -> str:
    """从详情页提取下载链接，失败时回退到 download.php?id=。"""
    fallback = ""
    for match in _DOWNLOAD_RE.finditer(page_html or ""):
        href = html.unescape(match.group("href")).strip()
        if not href:
            continue
        if not fallback:
            fallback = href
        if re.search(
            rf"[?&]id={re.escape(str(torrent_id))}(?:&|$)", href
        ):
            return _absolute(base_url, href)
    if fallback:
        return _absolute(base_url, fallback)
    return _absolute(base_url, f"download.php?id={torrent_id}")


def find_cover_url(page_html: str, base_url: str) -> str:
    """从详情页提取封面图片链接。"""
    text = page_html or ""

    for pattern in (_META_IMAGE_RE, _META_IMAGE_RE_ALT):
        match = pattern.search(text)
        if not match:
            continue
        url = html.unescape(match.group(1)).strip()
        if _is_cover_candidate(url):
            return _absolute(base_url, url)

    for tag in _IMG_RE.findall(text):
        attrs, urls = _img_attrs(tag)
        if not _is_cover_marked(attrs):
            continue
        for url in urls:
            if _is_cover_candidate(url):
                return _absolute(base_url, url)

    for match in _FIELD_RE.finditer(text):
        label = strip_tags(match.group("label")).lower()
        if "海报" in label or "封面" in label or "poster" in label:
            url = _first_image(match.group("value"), base_url)
            if url:
                return url

    match = re.search(
        r"<(div|td)\b[^>]*id=[\"']kdescr[\"'][^>]*>(.*?)</\1>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        url = _first_image(match.group(2), base_url)
        if url:
            return url

    return _first_image(text, base_url)


def _row_cover(row: str, base_url: str) -> str:
    fallback = ""
    for tag in _IMG_RE.findall(row):
        attrs, urls = _img_attrs(tag)
        marked = _is_cover_marked(attrs)
        for url in urls:
            if not _is_cover_candidate(url):
                continue
            absolute = _absolute(base_url, url)
            if marked:
                return absolute
            if not fallback:
                fallback = absolute
    return fallback


def _first_image(fragment: str, base_url: str) -> str:
    fallback = ""
    for tag in _IMG_RE.findall(fragment or ""):
        attrs, urls = _img_attrs(tag)
        marked = _is_cover_marked(attrs)
        for url in urls:
            if not _is_cover_candidate(url):
                continue
            absolute = _absolute(base_url, url)
            if marked:
                return absolute
            if not fallback:
                fallback = absolute
    return fallback


def _img_attrs(tag: str) -> tuple[dict[str, str], list[str]]:
    attrs = {
        key.lower(): html.unescape(value).strip()
        for key, value in re.findall(
            r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", tag, re.IGNORECASE
        )
    }
    urls = [
        attrs.get("data-src", ""),
        attrs.get("data-original", ""),
        attrs.get("data-lazy-src", ""),
        attrs.get("src", ""),
    ]
    return attrs, [url for url in urls if url]


def _is_cover_marked(attrs: dict[str, str]) -> bool:
    text = " ".join(
        attrs.get(key, "") for key in ("id", "class", "alt", "title")
    ).lower()
    return any(
        word in text
        for word in ("poster", "cover", "海报", "封面", "torrent_img")
    )


def _is_cover_candidate(url: str) -> bool:
    lower = (url or "").strip().lower()
    if not lower or lower.startswith(
        ("data:", "javascript:", "#", "about:")
    ):
        return False
    if ".gif" in lower.split("?", 1)[0]:
        return False
    blocked = (
        "/pic/",
        "logo",
        "icon",
        "smilies",
        "emoticon",
        "spacer",
        "blank.",
    )
    return not any(token in lower for token in blocked)


def _query_id(query: str) -> str:
    match = re.search(r"(?:^|&)id=(\d+)", query or "")
    return match.group(1) if match else ""


def _parse_row(row: str, base_url: str) -> TorrentItem | None:
    for match in _DETAILS_RE.finditer(row):
        torrent_id = _query_id(match.group("query"))
        title = strip_tags(match.group("body"))
        if not torrent_id or not title:
            continue
        size = extract_size(strip_tags(row))
        category = _row_category(row)
        cover = _row_cover(row, base_url)
        return _build_item(
            torrent_id, title, size, category, base_url, cover
        )
    return None


def _row_category(row: str) -> str:
    match = _CATEGORY_RE.search(row)
    if not match:
        return ""
    body = match.group("body")
    text = strip_tags(body)
    if text:
        return text
    attr = re.search(
        r"(?:alt|title)=[\"']([^\"']+)[\"']", body, re.IGNORECASE
    )
    return html.unescape(attr.group(1)).strip() if attr else ""


def _page_title(page_html: str) -> str:
    match = re.search(
        r"<title[^>]*>(.*?)</title>", page_html or "", re.IGNORECASE | re.DOTALL
    )
    if match:
        title = strip_tags(match.group(1))
        if " :: " in title:
            title = title.split(" :: ", 1)[0].strip()
        if title:
            return title

    match = re.search(
        r"<h1[^>]*>(.*?)</h1>", page_html or "", re.IGNORECASE | re.DOTALL
    )
    if not match:
        return ""
    title = strip_tags(match.group(1))
    if title:
        return title
    value = re.search(
        r"<input[^>]*value=[\"']([^\"']+)[\"']", match.group(1), re.IGNORECASE
    )
    return html.unescape(value.group(1)).strip() if value else ""


def _parse_fields(page_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(page_html or ""):
        label = strip_tags(match.group("label")).lower()
        raw_value = match.group("value")
        value = strip_tags(raw_value)
        if not label or not value:
            continue

        if _has(label, "大小", "size"):
            fields.setdefault("size", value)
        elif _has(label, "副标题", "subtitle"):
            fields.setdefault("subtitle", value)
        elif _has(label, "类型", "type"):
            fields.setdefault("category", value)
        elif _has(label, "添加时间", "发布时间", "added"):
            fields.setdefault("added", value)
        elif _has(label, "种子", "做种", "seeder"):
            _set_count(fields, "seeders", value)
        elif _has(label, "下载", "leecher"):
            # “下载”这一行也可能是种子下载按钮，此时值里含有 download.php
            if "download.php" in raw_value.lower():
                continue
            _set_count(fields, "leechers", value)
        elif _has(label, "完成", "snatch"):
            _set_count(fields, "snatched", value)
    return fields


def _has(label: str, *keys: str) -> bool:
    return any(key in label for key in keys)


def _set_count(fields: dict[str, str], key: str, value: str) -> None:
    if key in fields:
        return
    match = re.search(r"\d[\d,]*", value)
    if match:
        fields[key] = match.group(0)


def _description(page_html: str) -> str:
    match = re.search(
        r"<(div|td)\b[^>]*id=[\"']kdescr[\"'][^>]*>(.*?)</\1>",
        page_html or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    text = strip_tags(match.group(2))
    if len(text) > 300:
        text = text[:300].rstrip() + "…"
    return text


def _build_item(
    torrent_id: str,
    title: str,
    size: str,
    category: str,
    base_url: str,
    cover: str = "",
) -> TorrentItem:
    return TorrentItem(
        id=torrent_id,
        title=title,
        size=size,
        category=category,
        page_url=_page_url(base_url, torrent_id),
        cover=cover,
    )


def _page_url(base_url: str, torrent_id: str) -> str:
    return _absolute(base_url, f"details.php?id={torrent_id}")


def _absolute(base_url: str, href: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", href)

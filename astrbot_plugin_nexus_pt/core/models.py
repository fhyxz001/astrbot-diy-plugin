"""数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TorrentItem:
    """种子列表中的一条记录。"""

    id: str
    title: str
    size: str = ""
    category: str = ""
    page_url: str = ""
    cover: str = ""


@dataclass
class TorrentDetail:
    """种子详情页解析结果。"""

    id: str
    title: str
    size: str = ""
    category: str = ""
    subtitle: str = ""
    seeders: str = ""
    leechers: str = ""
    snatched: str = ""
    added: str = ""
    description: str = ""
    cover: str = ""
    download_url: str = ""
    page_url: str = ""

"""接口数据标准化。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComicMeta:
    path_word: str
    title: str
    group_names: dict[str, str] = field(default_factory=dict)
    default_group: str = "default"


@dataclass
class ChapterMeta:
    uuid: str
    title: str
    order: float = 0.0
    size: int = 0
    index: int = 0


def normalize_search_results(
    results: dict | None, limit: int | None = None
) -> list[dict]:
    """把搜索接口的 results 转成插件内部使用的列表。"""
    if not isinstance(results, dict):
        return []

    items = results.get("list") or []
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        path_word = str(item.get("path_word") or "").strip()
        if not path_word:
            continue

        authors = item.get("author") or []
        author_names = []
        if isinstance(authors, list):
            for author in authors:
                if isinstance(author, dict) and author.get("name"):
                    author_names.append(str(author["name"]))

        normalized.append(
            {
                "name": str(item.get("name") or path_word),
                "path_word": path_word,
                "author": "、".join(author_names),
                "popular": item.get("popular") or 0,
            }
        )

    if limit is not None:
        return normalized[: max(0, limit)]
    return normalized


def normalize_comic(results: dict | None, path_word: str) -> ComicMeta:
    """把漫画详情接口的 results 转成 ComicMeta。"""
    if not isinstance(results, dict):
        raise ValueError("漫画详情数据为空。")

    detail = results.get("comic")
    if not isinstance(detail, dict):
        detail = {}

    groups = results.get("groups")
    if not isinstance(groups, dict):
        groups = {}

    group_names: dict[str, str] = {}
    for key, value in groups.items():
        group_key = str(key)
        if isinstance(value, dict):
            group_names[group_key] = str(value.get("name") or group_key)
        else:
            group_names[group_key] = group_key

    # 没有分组信息时退回 default，后续章节接口仍可能可用
    if not group_names:
        group_names = {"default": "默认"}

    if "default" in group_names:
        default_group = "default"
    else:
        default_group = next(iter(group_names))

    return ComicMeta(
        path_word=path_word,
        title=str(detail.get("name") or path_word),
        group_names=group_names,
        default_group=default_group,
    )


def normalize_chapter(item: dict, group_key: str) -> ChapterMeta:
    """把章节目录中的一项转成 ChapterMeta。"""
    ordered = item.get("ordered")
    try:
        order = float(ordered) / 10.0
    except (TypeError, ValueError):
        order = 0.0

    try:
        size = int(item.get("size") or 0)
    except (TypeError, ValueError):
        size = 0

    try:
        index = int(item.get("index") or 0)
    except (TypeError, ValueError):
        index = 0

    return ChapterMeta(
        uuid=str(item.get("uuid") or ""),
        title=str(item.get("name") or "未命名章节"),
        order=order,
        size=size,
        index=index,
    )

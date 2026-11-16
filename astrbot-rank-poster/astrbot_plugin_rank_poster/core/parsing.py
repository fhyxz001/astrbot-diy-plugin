"""把用户的中文/英文自然语言转换成榜单类型、页码和下载序号。"""

from __future__ import annotations

import re
from dataclasses import dataclass

TYPE_LABELS: dict[str, str] = {
    "day": "日榜",
    "week": "周榜",
    "month": "月榜",
    "year": "总榜",
}

# 顺序很重要：先匹配更具体的榜单词，避免 "月榜" 被当成别的类型。
_TYPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "year",
        (
            r"总榜",
            r"年榜",
            r"总排行",
            r"(?<![a-z])all(?![a-z])",
            r"(?<![a-z])year(?:ly)?(?![a-z])",
            r"^(总|年)$",
        ),
    ),
    (
        "month",
        (
            r"月榜",
            r"月度榜",
            r"月排行",
            r"(?<![a-z])month(?:ly)?(?![a-z])",
            r"^月$",
        ),
    ),
    (
        "week",
        (
            r"周榜",
            r"每周榜",
            r"周排行",
            r"(?<![a-z])week(?:ly)?(?![a-z])",
            r"^周$",
        ),
    ),
    (
        "day",
        (
            r"日榜",
            r"每日榜",
            r"今日榜",
            r"日排行",
            r"(?<![a-z])daily(?![a-z])",
            r"(?<![a-z])day(?![a-z])",
            r"^(今天|今日|日)$",
        ),
    ),
)

_ABS_PAGE_PATTERNS: tuple[str, ...] = (
    r"第\s*(\d{1,3})\s*页",
    r"(?<![a-z])pages?\s*(\d{1,3})(?!\d)",
    r"翻到\s*(\d{1,3})\s*页",
    r"(?<!\d)(\d{1,3})\s*页",
)
_NEXT_PAGE_PATTERN = (
    r"下一页|下页|下一张|翻页|翻一页|再翻一页|往后翻|"
    r"(?<![a-z])(?:next|nxt)(?![a-z])"
)
_PREV_PAGE_PATTERN = r"上一页|上页|上一张|往前翻|(?<![a-z])(?:prev|previous)(?![a-z])"
_FIRST_PAGE_PATTERN = r"首页|第一页|回到开头|(?<![a-z])first(?![a-z])"
_CN_PAGE_PATTERN = r"第\s*([一二两三四五六七八九十])\s*页"
_CN_PAGE_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_CANCEL_WORDS = {
    "0",
    "不",
    "不用",
    "不需要",
    "不要",
    "不下载",
    "不下",
    "取消",
    "算了",
    "没了",
    "没有",
    "没有了",
    "结束",
    "no",
    "nope",
    "cancel",
    "none",
}
_ALL_WORDS = {"全部", "都要", "全下", "所有", "全要", "all", "全部下载"}

_FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９，、。！？：；（）", "0123456789,,.!?:;()"
)
_CN_DIGITS = {
    "零": "0",
    "〇": "0",
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}

# 长词在前，避免 "下载" 被 "下" 提前拆掉。
_STRIP_WORDS: tuple[str, ...] = (
    "下载",
    "下載",
    "保存",
    "推送",
    "视频",
    "視頻",
    "帮我",
    "帮忙",
    "麻烦",
    "给我",
    "我要",
    "我想",
    "就要",
    "选择",
    "一下",
    "看看",
    "看",
    "要",
    "请",
    "选",
    "第",
    "个",
    "号",
    "条",
    "的",
    "了",
    "吧",
    "呀",
    "啊",
    "呢",
    "哦",
    "下",
    "来",
)


@dataclass(frozen=True)
class PageHint:
    """页码提示：absolute 表示绝对页码，delta 表示相对翻页。"""

    absolute: int | None = None
    delta: int = 0


@dataclass(frozen=True)
class Selection:
    """kind: cancel / all / numbers / none。"""

    kind: str = "none"
    numbers: tuple[int, ...] = ()


def detect_type(text: str) -> str | None:
    """从任意文本中识别榜单类型，返回 day/week/month/year。"""
    value = (text or "").strip()
    if not value:
        return None
    for type_key, patterns in _TYPE_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, value, flags=re.IGNORECASE):
                return type_key
    return None


def detect_page(text: str) -> PageHint | None:
    """识别翻页/指定页码。"""
    value = (text or "").strip()
    if not value:
        return None

    lowered = value.lower()
    if re.search(_FIRST_PAGE_PATTERN, lowered):
        return PageHint(absolute=1)

    for pattern in _ABS_PAGE_PATTERNS:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            page = int(match.group(1))
            if 1 <= page <= 100:
                return PageHint(absolute=page)

    cn_match = re.search(_CN_PAGE_PATTERN, value)
    if cn_match:
        return PageHint(absolute=_CN_PAGE_DIGITS[cn_match.group(1)])

    if re.search(_NEXT_PAGE_PATTERN, lowered):
        return PageHint(delta=1)
    if re.search(_PREV_PAGE_PATTERN, lowered):
        return PageHint(delta=-1)
    return None


def _strip_particles(text: str) -> str:
    result = text.strip(" \t\r\n。.!！?？~～,，、")
    result = re.sub(r"(了|吧|呀|啊|呢|哦|啦|嘛|谢谢|thank you)+$", "", result, flags=re.IGNORECASE)
    return result.strip()


def parse_selection(text: str) -> Selection:
    """解析“3”、“1,3,5”、“1-3”、“第三个”、“全部”、“0”等选择。"""
    raw = (text or "").strip()
    if not raw:
        return Selection("none")

    value = _strip_particles(raw.translate(_FULLWIDTH_MAP))
    lowered = value.lower()

    if lowered in _CANCEL_WORDS or value in _CANCEL_WORDS:
        return Selection("cancel")
    if lowered.startswith(("不下载", "不用", "不需要", "不要", "取消", "算了", "没有了", "没了", "结束")):
        return Selection("cancel")
    if lowered in _ALL_WORDS:
        return Selection("all")

    stripped = value
    for word in _STRIP_WORDS:
        stripped = stripped.replace(word, "")

    stripped = "".join(_CN_DIGITS.get(char, char) for char in stripped)
    stripped = stripped.replace("和", ",").replace("与", ",").replace("及", ",")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if not stripped or not re.search(r"\d", stripped):
        return Selection("none")
    if not re.fullmatch(r"[0-9,\s\-~～到至+]+", stripped):
        # 例如 "我今天买了3个苹果" 这类正常聊天，不能当成下载指令。
        return Selection("none")

    stripped = stripped.replace("～", "~").replace("到", "-").replace("至", "-")
    numbers: list[int] = []
    for token in re.split(r"[,+\s]+", stripped):
        token = token.strip()
        if not token:
            continue
        range_match = re.fullmatch(r"(\d{1,3})\s*-\s*(\d{1,3})", token)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if start > end:
                start, end = end, start
            if end - start > 20:
                continue
            numbers.extend(range(start, end + 1))
        elif re.fullmatch(r"\d{1,3}", token):
            numbers.append(int(token))

    if not numbers:
        return Selection("none")
    if 0 in numbers and len(numbers) == 1:
        return Selection("cancel")

    unique = sorted({number for number in numbers if 0 < number <= 100})
    if not unique:
        return Selection("cancel")
    return Selection("numbers", tuple(unique[:20]))

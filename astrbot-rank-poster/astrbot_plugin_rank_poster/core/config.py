"""插件配置的读取与规范化。

AstrBot 会把 ``_conf_schema.json`` 渲染成 WebUI 表单，并在实例化插件时把
配置对象（``AstrBotConfig``，本质上是 dict）传给 ``__init__``。这里做一层
容错转换，保证即使用户填了 ``192.168.1.13`` 这种不带协议/端口的地址也能用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

DEFAULT_SERVER = "http://192.168.1.13:2617"
DEFAULT_OPEN_DOWNLOAD_KEY = "912bdbc4a3354d5d86843309e3bc52b7"
SERVER_PORT = 2617

DEFAULT_TRIGGER_KEYWORDS: tuple[str, ...] = (
    "排行榜",
    "榜单",
    "海报",
    "看榜",
    "rank",
    "日榜",
    "周榜",
    "月榜",
    "总榜",
)

VALID_TYPES = ("day", "week", "month", "year")


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "是", "开", "启用"}:
        return True
    if text in {"0", "false", "no", "off", "否", "关", "禁用"}:
        return False
    return default


def _as_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_keywords(value: Any) -> tuple[str, ...]:
    """把 list / 逗号分隔字符串统一成去重后的关键词元组。"""
    if value is None:
        return DEFAULT_TRIGGER_KEYWORDS

    items: Iterable[Any]
    if isinstance(value, str):
        items = re.split(r"[,，、\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        return DEFAULT_TRIGGER_KEYWORDS

    result: list[str] = []
    for item in items:
        keyword = str(item or "").strip()
        if keyword and keyword not in result:
            result.append(keyword)
    # 用户显式清空列表时表示关闭关键词触发，不强行回填默认值。
    return tuple(result)


def _normalize_url(raw: Any, default_port: int | None = None) -> str:
    """补全协议和端口，并去掉末尾斜杠。"""
    text = _as_str(raw)
    if not text:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", text):
        text = "http://" + text

    try:
        parts = urlsplit(text)
        port = parts.port  # 非法端口会在这里抛 ValueError
    except ValueError:
        return ""

    host = parts.netloc
    if not host:
        return ""

    if default_port is not None and port is None:
        # IPv6 字面量 [::1] 直接追加端口即可，带端口的已经在上面的 port 判断里排除。
        host = f"{host}:{default_port}"

    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, host, path, parts.query, parts.fragment)).rstrip("/")


def normalize_base_url(raw: Any, default_port: int | None = None) -> str:
    """海报服务地址：``192.168.1.13`` -> ``http://192.168.1.13:2617``。"""
    return _normalize_url(raw, default_port)


@dataclass(frozen=True)
class PluginSettings:
    enabled: bool = True
    server_url: str = DEFAULT_SERVER
    default_type: str = "day"
    trigger_keywords: tuple[str, ...] = DEFAULT_TRIGGER_KEYWORDS
    group_require_at: bool = True
    allow_group: bool = True
    request_timeout: float = 60.0
    state_ttl: float = 600.0
    max_page: int = 10
    open_download_key: str = DEFAULT_OPEN_DOWNLOAD_KEY
    download_subdir: str = ""
    verify_ssl: bool = True
    user_agent: str = "AstrBot-RankPoster/2.1.0 (+https://gitee.com/hexbkyoma/astrbot-rank-poster)"

    @classmethod
    def from_config(cls, config: Any) -> "PluginSettings":
        data: dict[str, Any] = config if isinstance(config, dict) else {}
        default_type = _as_str(data.get("default_type"), "day").lower()
        if default_type not in VALID_TYPES:
            default_type = "day"

        # 配置项缺失时用默认值；用户显式清空（""）则视为未配置。
        server_raw = data.get("server_url")
        if server_raw is None:
            server_raw = DEFAULT_SERVER
        open_key_raw = data.get("open_download_key")
        if open_key_raw is None:
            open_key_raw = DEFAULT_OPEN_DOWNLOAD_KEY

        return cls(
            enabled=_as_bool(data.get("enabled"), True),
            server_url=normalize_base_url(server_raw, SERVER_PORT),
            default_type=default_type,
            trigger_keywords=_as_keywords(data.get("trigger_keywords")),
            group_require_at=_as_bool(data.get("group_require_at"), True),
            allow_group=_as_bool(data.get("allow_group"), True),
            request_timeout=max(5.0, _as_float(data.get("request_timeout"), 60.0)),
            state_ttl=max(30.0, _as_float(data.get("state_ttl"), 600.0)),
            max_page=_as_int(data.get("max_page"), 10, 1, 10),
            open_download_key=_as_str(open_key_raw),
            download_subdir=_as_str(data.get("download_subdir")).strip("/\\ "),
            verify_ssl=_as_bool(data.get("verify_ssl"), True),
        )

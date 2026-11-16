"""AstrBot NexusPHP PT 站点助手插件。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

if __package__:
    # AstrBot 以 data.plugins.<插件目录>.main 的形式导入插件
    from .core.errors import PtError
    from .core.models import TorrentDetail, TorrentItem
    from .core.nexus import NexusClient
    from .core.qbittorrent import QbittorrentClient
    from .core.utils import (
        extract_command_arg,
        extract_torrent_id,
        parse_page,
    )
else:  # 兼容直接运行/独立调试
    from core.errors import PtError
    from core.models import TorrentDetail, TorrentItem
    from core.nexus import NexusClient
    from core.qbittorrent import QbittorrentClient
    from core.utils import (
        extract_command_arg,
        extract_torrent_id,
        parse_page,
    )

PLUGIN_NAME = "astrbot_plugin_nexus_pt"
CACHE_TTL = 10 * 60

HELP_TEXT = """NexusPHP PT 助手
/pt 首页 [页码]          浏览最新种子
/pt 搜索 <关键词>        搜索种子
/pt 详情 <序号|ID|链接>  查看种子详情
/pt 下载 <序号|ID|链接>  推送到 qBittorrent

序号来自最近一次首页或搜索结果，10 分钟内有效；也可以直接使用种子 ID 或详情页链接。"""


@dataclass
class CachedList:
    expires_at: float
    items: list[TorrentItem]


@register(PLUGIN_NAME, "DIY", "NexusPHP PT 资源浏览与 qBittorrent 下载", "0.1.0")
class NexusPtPlugin(Star):
    def __init__(
        self, context: Context, config: AstrBotConfig | None = None
    ) -> None:
        super().__init__(context)
        self.config = config or {}

        base_url = str(self.config.get("base_url") or "").strip()
        cookie = str(self.config.get("cookie") or "")
        timeout = self._config_int("request_timeout", 20, minimum=5)
        self.list_limit = self._config_int("list_limit", 10, minimum=1)

        self.nexus = NexusClient(base_url, cookie, timeout=float(timeout))
        self.qb = QbittorrentClient(
            str(self.config.get("qb_url") or ""),
            str(self.config.get("qb_api_key") or ""),
            timeout=float(timeout),
        )
        self.qb_savepath = str(self.config.get("qb_savepath") or "").strip()
        self.qb_category = str(self.config.get("qb_category") or "").strip()
        self.qb_paused = bool(self.config.get("qb_paused", False))

        self._cache: dict[str, CachedList] = {}
        logger.info(
            "NexusPHP PT 插件已加载：%s",
            self.nexus.base_url or "未配置站点",
        )

    def _config_int(self, key: str, default: int, minimum: int = 1) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    @filter.command_group("pt")
    def pt(self):
        """NexusPHP PT 指令组"""

    @pt.command("帮助")
    async def help(self, event: AstrMessageEvent):
        """查看帮助。用法：/pt 帮助"""
        denied = self._private_denied(event)
        if denied:
            yield event.plain_result(denied)
            return
        yield event.plain_result(HELP_TEXT)

    @pt.command("首页")
    async def home(self, event: AstrMessageEvent):
        """浏览最新种子。用法：/pt 首页 [页码]"""
        yield event.plain_result(await self._latest_text(event, "首页"))

    @pt.command("最新")
    async def latest(self, event: AstrMessageEvent):
        """浏览最新种子。用法：/pt 最新 [页码]"""
        yield event.plain_result(await self._latest_text(event, "最新"))

    async def _latest_text(
        self, event: AstrMessageEvent, subcommand: str
    ) -> str:
        denied = self._private_denied(event)
        if denied:
            return denied
        missing = self._site_missing()
        if missing:
            return missing

        page = parse_page(extract_command_arg(event.message_str, subcommand))
        try:
            items = await self.nexus.latest(page=page)
        except PtError as exc:
            return f"获取首页失败：{exc}"
        except Exception:
            logger.exception("获取 PT 首页异常")
            return "获取首页失败：服务异常。"

        if not items:
            return "首页没有解析到种子，可能是 Cookie 失效或页面结构不兼容。"

        visible = items[: self.list_limit]
        await self._fill_covers(visible)
        self._remember(event, visible)

        header = f"最新种子（第 {page} 页）：共 {len(items)} 条"
        if len(visible) < len(items):
            header += f"，显示前 {len(visible)} 条"
        return self._format_list(header, visible)

    @pt.command("搜索")
    async def search(self, event: AstrMessageEvent):
        """搜索种子。用法：/pt 搜索 <关键词>"""
        denied = self._private_denied(event)
        if denied:
            yield event.plain_result(denied)
            return
        missing = self._site_missing()
        if missing:
            yield event.plain_result(missing)
            return

        keyword = extract_command_arg(event.message_str, "搜索")
        if not keyword:
            yield event.plain_result("用法：/pt 搜索 <关键词>")
            return

        try:
            items = await self.nexus.search(keyword)
        except PtError as exc:
            yield event.plain_result(f"搜索失败：{exc}")
            return
        except Exception:
            logger.exception("搜索 PT 种子异常")
            yield event.plain_result("搜索失败：服务异常。")
            return

        if not items:
            yield event.plain_result(
                f"没有找到与“{keyword}”相关的种子。"
            )
            return

        visible = items[: self.list_limit]
        await self._fill_covers(visible)
        self._remember(event, visible)

        header = f"搜索“{keyword}”：共 {len(items)} 条"
        if len(visible) < len(items):
            header += f"，显示前 {len(visible)} 条"
        yield event.plain_result(self._format_list(header, visible))

    @pt.command("详情")
    async def detail(self, event: AstrMessageEvent):
        """查看种子详情。用法：/pt 详情 <序号|ID|链接>"""
        denied = self._private_denied(event)
        if denied:
            yield event.plain_result(denied)
            return
        missing = self._site_missing()
        if missing:
            yield event.plain_result(missing)
            return

        target = extract_command_arg(event.message_str, "详情")
        if not target:
            yield event.plain_result(
                "用法：/pt 详情 <序号|种子 ID|详情页链接>"
            )
            return

        torrent_id = self._resolve_id(event, target)
        if not torrent_id:
            yield event.plain_result(
                "序号无效或搜索结果已过期，"
                "请先使用 /pt 首页 或 /pt 搜索。"
            )
            return

        try:
            detail = await self.nexus.detail(torrent_id)
        except PtError as exc:
            yield event.plain_result(f"查询详情失败：{exc}")
            return
        except Exception:
            logger.exception("查询 PT 种子详情异常")
            yield event.plain_result("查询详情失败：服务异常。")
            return

        yield event.plain_result(self._format_detail(detail))

    @pt.command("下载")
    async def download(self, event: AstrMessageEvent):
        """推送到 qBittorrent。用法：/pt 下载 <序号|ID|链接>"""
        denied = self._private_denied(event)
        if denied:
            yield event.plain_result(denied)
            return
        missing = self._site_missing()
        if missing:
            yield event.plain_result(missing)
            return
        if not self.qb.configured:
            yield event.plain_result(
                "请先在插件配置中填写 qBittorrent 地址和 API Key。"
            )
            return

        target = extract_command_arg(event.message_str, "下载")
        if not target:
            yield event.plain_result(
                "用法：/pt 下载 <序号|种子 ID|详情页链接>"
            )
            return

        torrent_id = self._resolve_id(event, target)
        if not torrent_id:
            yield event.plain_result(
                "序号无效或搜索结果已过期，"
                "请先使用 /pt 首页 或 /pt 搜索。"
            )
            return

        try:
            detail = await self.nexus.detail(torrent_id)
            content = await self.nexus.fetch_torrent(
                detail.download_url, referer=detail.page_url
            )
            await self.qb.add_torrent(
                f"{torrent_id}.torrent",
                content,
                savepath=self.qb_savepath,
                category=self.qb_category,
                paused=self.qb_paused,
            )
        except PtError as exc:
            yield event.plain_result(f"推送下载失败：{exc}")
            return
        except Exception:
            logger.exception("推送 PT 种子到 qBittorrent 异常")
            yield event.plain_result("推送下载失败：服务异常。")
            return

        lines = [f"已推送到 qBittorrent：《{detail.title}》"]
        if detail.size:
            lines.append(f"大小：{detail.size}")
        if self.qb_savepath:
            lines.append(f"保存路径：{self.qb_savepath}")
        if self.qb_category:
            lines.append(f"分类：{self.qb_category}")
        if self.qb_paused:
            lines.append("状态：已暂停，需要在 qBittorrent 中手动开始。")
        yield event.plain_result("\n".join(lines))

    def _resolve_id(self, event: AstrMessageEvent, target: str) -> str:
        text = (target or "").strip()
        if not text:
            return ""

        if text.isdigit():
            index = int(text)
            cached = self._get_cache(event)
            if cached and 1 <= index <= len(cached):
                return cached[index - 1].id
            # 纯数字且不像列表序号时，按种子 ID 处理
            return text if len(text) >= 4 else ""
        return extract_torrent_id(text)

    async def _fill_covers(self, items: list[TorrentItem]) -> None:
        try:
            await self.nexus.fill_covers(items)
        except Exception:
            logger.exception("补齐种子封面异常")

    def _remember(self, event: AstrMessageEvent, items: list[TorrentItem]) -> None:
        key = self._session_key(event)
        self._cache[key] = CachedList(
            expires_at=time.monotonic() + CACHE_TTL,
            items=items[: self.list_limit],
        )

    def _get_cache(self, event: AstrMessageEvent) -> list[TorrentItem]:
        key = self._session_key(event)
        cached = self._cache.get(key)
        if cached is None:
            return []
        if cached.expires_at <= time.monotonic():
            self._cache.pop(key, None)
            return []
        return cached.items

    @staticmethod
    def _format_list(header: str, items: list[TorrentItem]) -> str:
        lines = [header, ""]
        for index, item in enumerate(items, 1):
            lines.append(f"{index}.{item.cover or item.page_url}")
        lines.append("")
        lines.append("查看详情：/pt 详情 <序号>")
        lines.append("推送下载：/pt 下载 <序号>")
        return "\n".join(lines)

    @staticmethod
    def _format_detail(detail: TorrentDetail) -> str:
        lines = [f"《{detail.title}》"]
        if detail.subtitle:
            lines.append(f"副标题：{detail.subtitle}")

        meta: list[str] = []
        if detail.category:
            meta.append(f"类型：{detail.category}")
        if detail.size:
            meta.append(f"大小：{detail.size}")
        if meta:
            lines.append("  ".join(meta))

        stats: list[str] = []
        if detail.seeders:
            stats.append(f"做种：{detail.seeders}")
        if detail.leechers:
            stats.append(f"下载：{detail.leechers}")
        if detail.snatched:
            stats.append(f"完成：{detail.snatched}")
        if stats:
            lines.append("  ".join(stats))

        if detail.added:
            lines.append(f"发布时间：{detail.added}")
        if detail.description:
            lines.append(f"简介：{detail.description}")
        if detail.page_url:
            lines.append(f"页面：{detail.page_url}")
        lines.append(f"推送下载：/pt 下载 {detail.id}")
        return "\n".join(lines)

    @staticmethod
    def _private_denied(event: AstrMessageEvent) -> str:
        if event.get_group_id():
            return "该插件仅支持私聊使用。"
        return ""

    def _site_missing(self) -> str:
        if not self.nexus.configured:
            return "请先在插件配置中填写站点地址和 Cookie。"
        return ""

    @staticmethod
    def _session_key(event: AstrMessageEvent) -> str:
        return (
            getattr(event, "unified_msg_origin", "")
            or event.get_sender_id()
        )

    async def terminate(self) -> None:
        await self.nexus.close()
        await self.qb.close()

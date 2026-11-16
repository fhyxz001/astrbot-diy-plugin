"""AstrBot 插件：排行榜海报 + 按序号提交到开放下载接口。

对话流程：

1. 用户发送触发词（默认 ``排行榜`` / ``榜单`` / ``海报`` / ``看榜`` / ``rank``），
   插件用相同参数**成对**请求海报图片接口和下载链接接口（先图后链接），保证序号一致。
2. 插件发出海报并询问要下载哪个视频（回复 1-10，回复 0 表示不需要）。
3. 用户可以继续用 ``周榜`` / ``月榜`` / ``总榜`` 切换榜单，用 ``下一页`` /
   ``上一页`` / ``第2页`` 翻页（每次翻页同样成对请求两个接口）。
4. 用户回复序号后，插件把当前海报对应的链接逐条提交给服务端的
   ``POST /api/open/download``，由服务端后台下载，不会重新拉取链接。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core.config import PluginSettings
from .core.image_util import crop_page
from .core.open_download import (
    OpenDownloadClient,
    OpenDownloadError,
    build_target_path,
)
from .core.parsing import TYPE_LABELS, detect_page, detect_type, parse_selection
from .core.poster_api import RankPosterClient, RankPosterError

PLUGIN_NAME = "astrbot_plugin_rank_poster"
PAGE_SIZE = 10


@dataclass
class SessionState:
    """一次海报推送后保存的会话快照。"""

    type_key: str
    page: int
    label: str
    items: dict[int, str]
    created_at: float = field(default_factory=time.time)

    @property
    def indices(self) -> list[int]:
        return sorted(self.items)

    @property
    def min_index(self) -> int:
        return min(self.items) if self.items else 0

    @property
    def max_index(self) -> int:
        return max(self.items) if self.items else 0


@dataclass
class Followup:
    """用户在“已发海报”状态下的一句话被解析成的动作。"""

    kind: str  # cancel / download / type / page / refresh
    type_key: str | None = None
    page: int | None = None
    numbers: tuple[int, ...] = ()
    select_all: bool = False


class RankPosterPlugin(Star):
    """排行榜海报：查询日/周/月/总榜海报，按图片序号下载视频。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context, config)
        self.config = config if config is not None else {}
        self.settings = PluginSettings.from_config(self.config)
        self._session: aiohttp.ClientSession | None = None
        self._states: dict[str, SessionState] = {}

        self._data_dir = self._resolve_data_dir()
        self._poster_dir = self._data_dir / "posters"
        self._poster_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- lifecycle
    async def initialize(self) -> None:
        self.settings = PluginSettings.from_config(self.config)
        self._session = self._new_session()
        self.logger.info(
            "rank poster ready: server=%s open_download=%s",
            self.settings.server_url,
            f"{self.settings.server_url}/api/open/download",
        )

    async def terminate(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ---------------------------------------------------------------- config
    def _settings(self) -> PluginSettings:
        # 每次读取都重新解析，WebUI 改配置后无需重载插件。
        self.settings = PluginSettings.from_config(self.config)
        return self.settings

    @staticmethod
    def _resolve_data_dir() -> Path:
        try:
            path = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:  # pragma: no cover - 仅在非 AstrBot 环境或加载异常时
            import tempfile

            path = Path(tempfile.gettempdir()) / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _new_session(self) -> aiohttp.ClientSession:
        settings = self.settings
        return aiohttp.ClientSession(
            headers={"User-Agent": settings.user_agent},
            timeout=aiohttp.ClientTimeout(total=settings.request_timeout, connect=15),
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        )

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._new_session()
        return self._session

    # --------------------------------------------------------------- handler
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """关键词触发 + 海报后的多轮选择。"""
        try:
            text = (event.get_message_str() or "").strip()
            if not text:
                return

            settings = self._settings()
            if not settings.enabled:
                return
            if text.lower().startswith("rankcheck"):
                # 由 /rankcheck 指令处理，避免重复响应。
                return

            umo = event.unified_msg_origin
            group = self._is_group(umo)
            if group:
                if not settings.allow_group:
                    return
                if settings.group_require_at and not getattr(event, "is_at_or_wake_command", False):
                    return

            state = self._get_state(umo, settings)
            if state is not None:
                action = self._parse_followup(text, state, settings)
                if action is not None:
                    event.stop_event()
                    async for result in self._handle_followup(event, state, action, settings):
                        yield result
                    return

            if self._match_trigger(text, settings):
                event.stop_event()
                type_key = detect_type(text) or settings.default_type
                page = self._extract_initial_page(text, settings)
                async for result in self._show_rank(event, type_key, page, settings):
                    yield result
        except Exception:  # pragma: no cover - 防御性兜底
            self.logger.exception("rank poster handler failed")
            try:
                yield event.plain_result("❌ 插件处理消息时出错了，请查看 AstrBot 日志。")
            except Exception:
                pass

    @filter.command("rankcheck")
    async def rankcheck(self, event: AstrMessageEvent):
        """检查排行榜服务和开放下载接口是否可连接。"""
        event.stop_event()
        settings = self._settings()
        message = await self._build_check_message(settings)
        yield event.plain_result(message)

    # ------------------------------------------------------------- follow-up
    def _parse_followup(self, text: str, state: SessionState, settings: PluginSettings) -> Followup | None:
        selection = parse_selection(text)
        if selection.kind == "cancel":
            return Followup("cancel")

        type_key = detect_type(text)
        if type_key and selection.kind not in {"numbers", "all"}:
            page = 1
            hint = detect_page(text)
            if hint and hint.absolute:
                page = hint.absolute
            return Followup("type", type_key=type_key, page=page)

        hint = detect_page(text)
        if hint is not None:
            page = hint.absolute if hint.absolute else state.page + hint.delta
            return Followup("page", page=max(1, min(settings.max_page, page)))

        if selection.kind == "all":
            return Followup("download", select_all=True)
        if selection.kind == "numbers":
            return Followup("download", numbers=selection.numbers)

        if self._match_trigger(text, settings):
            return Followup("refresh")
        return None

    async def _handle_followup(self, event: AstrMessageEvent, state: SessionState, action: Followup, settings: PluginSettings):
        if action.kind == "cancel":
            self._states.pop(event.unified_msg_origin, None)
            yield event.plain_result("👌 好的，这次不下载了。")
            return

        if action.kind == "download":
            async for result in self._start_download(event, state, action, settings):
                yield result
            return

        type_key = action.type_key or state.type_key
        page = 1 if action.kind == "refresh" else (action.page or 1)
        async for result in self._show_rank(event, type_key, page, settings):
            yield result

    async def _start_download(self, event: AstrMessageEvent, state: SessionState, action: Followup, settings: PluginSettings):
        umo = event.unified_msg_origin

        if action.select_all:
            chosen = state.indices
            missing: list[int] = []
        else:
            chosen = [number for number in action.numbers if number in state.items]
            missing = [number for number in action.numbers if number not in state.items]

        if missing:
            yield event.plain_result(
                f"⚠️ 序号 {', '.join(str(n) for n in missing)} 不在当前页"
                f"（{state.min_index}-{state.max_index}），已忽略。"
            )
        if not chosen:
            yield event.plain_result(
                f"请回复 {state.min_index}-{state.max_index} 之间的序号，或回复 0 取消。"
            )
            return

        # 下载使用当前海报对应的快照，随后清除状态，避免重复提交。
        self._states.pop(umo, None)

        client = OpenDownloadClient(self._ensure_session(), settings)

        async def submit_one(number: int) -> tuple[int, dict | None, str | None]:
            url = state.items[number]
            target = build_target_path(
                settings.download_subdir, state.type_key, number, url
            )
            try:
                task = await client.submit(url, target)
            except OpenDownloadError as exc:
                self.logger.warning("submit #%s to open download failed: %s", number, exc)
                return number, None, str(exc)
            except Exception as exc:  # pragma: no cover - 防御性兜底
                self.logger.exception("submit #%s to open download failed unexpectedly", number)
                return number, None, f"未知错误：{exc}"
            self.logger.info(
                "submit #%s to open download: %s -> %s (task %s)",
                number,
                url,
                task.get("path") or target,
                task.get("task_id"),
            )
            return number, task, None

        results = await asyncio.gather(*(submit_one(number) for number in chosen))
        submitted = [(number, task) for number, task, error in results if error is None]
        failures = [(number, error) for number, task, error in results if error is not None]

        lines: list[str] = []
        if submitted:
            lines.append(f"✅ 已提交到下载服务 {len(submitted)} 个任务")
            for number, task in submitted:
                saved_path = task.get("path") or ""
                task_id = str(task.get("task_id") or "")[:8]
                suffix = f"（任务 {task_id}…）" if task_id else ""
                lines.append(f"· 序号 {number} → {saved_path}{suffix}")
            lines.append("服务端会在后台下载，可在「任务 → 日志」查看进度。")
        for number, message in failures:
            lines.append(f"❌ 序号 {number} 提交失败：{message}")
        if not lines:
            lines.append("没有可提交的视频。")

        yield event.plain_result("\n".join(lines))

    # --------------------------------------------------------------- display
    async def _show_rank(self, event: AstrMessageEvent, type_key: str, page: int, settings: PluginSettings, note: str | None = None):
        if type_key not in TYPE_LABELS:
            type_key = settings.default_type
        page = max(1, min(settings.max_page, int(page or 1)))

        client = RankPosterClient(self._ensure_session(), settings)
        try:
            snapshot = await client.fetch(type_key, page)
        except RankPosterError as exc:
            self.logger.warning("fetch rank poster failed: %s", exc)
            yield event.plain_result(f"❌ 获取榜单失败：{exc}")
            return
        except Exception as exc:  # pragma: no cover - 意外错误也要给用户反馈
            self.logger.exception("unexpected error when fetching rank poster")
            yield event.plain_result(f"❌ 获取榜单时出现未知错误：{exc}")
            return

        low = (page - 1) * PAGE_SIZE + 1
        high = page * PAGE_SIZE
        page_items = {
            item.index: item.video_url
            for item in snapshot.items
            if low <= item.index <= high
        }
        if not page_items:
            if page > 1:
                yield event.plain_result(
                    f"📭 {snapshot.label} 第 {page} 页没有更多视频了，回复「上一页」返回。"
                )
            else:
                yield event.plain_result(f"📭 {snapshot.label} 暂时没有数据。")
            return

        try:
            display, cropped = crop_page(snapshot.image, snapshot.groups, page)
        except Exception:  # pragma: no cover - Pillow 异常时退回原图
            display, cropped = snapshot.image, False

        try:
            image_path = self._save_poster(display)
        except OSError as exc:
            self.logger.warning("save poster failed: %s", exc)
            yield event.plain_result(f"❌ 保存海报失败：{exc}")
            return

        state = SessionState(
            type_key=type_key,
            page=page,
            label=snapshot.label or TYPE_LABELS.get(type_key, type_key),
            items=dict(sorted(page_items.items())),
        )
        self._states[event.unified_msg_origin] = state
        self._prune_states(settings)

        yield event.image_result(str(image_path))
        yield event.plain_result(
            self._build_prompt(
                state,
                settings,
                cropped=cropped,
                is_group=self._is_group(event.unified_msg_origin),
                note=note,
            )
        )

    def _build_prompt(
        self,
        state: SessionState,
        settings: PluginSettings,
        *,
        cropped: bool,
        is_group: bool,
        note: str | None = None,
    ) -> str:
        lines = [
            f"📊 {state.label} · 第 {state.page} 页（序号 {state.min_index}-{state.max_index}，"
            f"共 {len(state.items)} 个视频）"
        ]
        if note:
            lines.append(note)
        if state.page > 1 and not cropped:
            lines.append("（服务端返回的是整张多组拼图，请按上面的序号选择）")
        lines.extend(
            [
                "",
                "需要下载哪个视频？",
                f"· 单个：回复 {state.min_index}",
                "· 多个：回复 1,3,5",
                "· 不需要：回复 0",
                "",
                "切换榜单：日榜 / 周榜 / 月榜 / 总榜",
                "翻页：下一页 / 上一页 / 第2页",
            ]
        )
        if is_group and settings.group_require_at:
            lines.append("提示：群聊里请 @我 之后再回复。")
        return "\n".join(lines)

    # ----------------------------------------------------------------- utils
    def _get_state(self, umo: str, settings: PluginSettings) -> SessionState | None:
        state = self._states.get(umo)
        if state is None:
            return None
        if time.time() - state.created_at > settings.state_ttl:
            self._states.pop(umo, None)
            return None
        return state

    def _prune_states(self, settings: PluginSettings) -> None:
        now = time.time()
        expired = [
            key
            for key, state in self._states.items()
            if now - state.created_at > settings.state_ttl
        ]
        for key in expired:
            self._states.pop(key, None)
        if len(self._states) > 500:
            oldest = sorted(self._states.items(), key=lambda item: item[1].created_at)
            for key, _ in oldest[: len(self._states) - 500]:
                self._states.pop(key, None)

    def _save_poster(self, data: bytes) -> Path:
        name = f"{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
        path = self._poster_dir / name
        path.write_bytes(data)
        self._cleanup_posters()
        return path

    def _cleanup_posters(self) -> None:
        now = time.time()
        kept: list[Path] = []
        for item in self._poster_dir.glob("*.jpg"):
            try:
                if now - item.stat().st_mtime > 3600:
                    item.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            kept.append(item)
        if len(kept) > 200:
            kept.sort(key=lambda path: path.stat().st_mtime)
            for old in kept[:-200]:
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass

    def _extract_initial_page(self, text: str, settings: PluginSettings) -> int:
        hint = detect_page(text)
        if hint is not None and hint.absolute:
            return max(1, min(settings.max_page, hint.absolute))
        match = re.search(r"(?<![a-z])rank\b[^\d]{0,24}(\d{1,2})(?!\d)", text, re.IGNORECASE)
        if match:
            return max(1, min(settings.max_page, int(match.group(1))))
        return 1

    def _match_trigger(self, text: str, settings: PluginSettings) -> bool:
        value = text.lower()
        for keyword in settings.trigger_keywords:
            keyword = keyword.strip()
            if not keyword:
                continue
            if keyword.isascii() and re.fullmatch(r"[a-z0-9_]+", keyword):
                if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", value):
                    return True
            elif keyword.lower() in value:
                return True
        return False

    @staticmethod
    def _is_group(umo: str) -> bool:
        return "group" in (umo or "").lower()

    async def _build_check_message(self, settings: PluginSettings) -> str:
        lines: list[str] = [
            f"⚙️ 开放下载：{settings.server_url}/api/open/download"
            f"（Key：{'已配置' if settings.open_download_key else '未配置'}；"
            f"子目录：{settings.download_subdir or '下载根目录'}）"
        ]
        started = time.perf_counter()
        try:
            snapshot = await RankPosterClient(self._ensure_session(), settings).fetch(
                settings.default_type, 1
            )
            cost = (time.perf_counter() - started) * 1000
            lines.append(f"✅ 榜单服务正常：{snapshot.label} {snapshot.count} 条（{cost:.0f} ms）")
        except Exception as exc:
            lines.append(f"❌ 榜单服务异常：{exc}")

        if not settings.open_download_key:
            lines.append("ℹ️ 未配置开放下载 Key。")
        else:
            try:
                await OpenDownloadClient(self._ensure_session(), settings).probe()
                lines.append("✅ 开放下载接口正常（Key 有效）")
            except Exception as exc:
                lines.append(f"❌ 开放下载接口异常：{exc}")
        return "\n".join(lines)

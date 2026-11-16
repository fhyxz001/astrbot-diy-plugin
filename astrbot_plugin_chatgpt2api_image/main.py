"""AstrBot ChatGPT2API 生图插件。"""

from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

if __package__:
    # AstrBot 以 data.plugins.<插件目录>.main 的形式导入插件
    from .core.client import ChatGpt2ApiClient
    from .core.errors import ImageGenerationError
    from .core.storage import ImageStore
    from .core.utils import DEFAULT_COMMANDS, extract_prompt
else:  # 兼容直接运行/独立调试
    from core.client import ChatGpt2ApiClient
    from core.errors import ImageGenerationError
    from core.storage import ImageStore
    from core.utils import DEFAULT_COMMANDS, extract_prompt

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except Exception:  # pragma: no cover - 兼容旧版 AstrBot

    def get_astrbot_plugin_data_path() -> str:
        return str(Path.cwd() / "data" / "plugin_data")


PLUGIN_NAME = "astrbot_plugin_chatgpt2api_image"
MAX_PROMPT_LENGTH = 4000

HELP_TEXT = """ChatGPT2API 生图
/st <提示词>      根据提示词生成图片
/st 帮助          查看本说明

示例：
/st 一张极简产品海报，白色背景，留白充足

也可以使用 /生图 或 /绘图。生成数量、尺寸、质量、并发和冷却时间可在插件配置中调整。"""


@register(
    PLUGIN_NAME,
    "DIY",
    "通过 ChatGPT2API 的 OpenAI 兼容接口，用 /st 指令根据提示词生成图片",
    "0.1.0",
)
class ChatGpt2ApiImagePlugin(Star):
    def __init__(
        self, context: Context, config: AstrBotConfig | None = None
    ) -> None:
        super().__init__(context)
        self.config = config or {}

        self.image_count = self._config_int("image_count", 1, 1, 4)
        self.request_timeout = self._config_int(
            "request_timeout", 120, 10, 600
        )
        self.cooldown_seconds = self._config_int(
            "cooldown_seconds", 10, 0, 600
        )
        self.allow_group = bool(self.config.get("allow_group", True))
        retention_hours = self._config_int(
            "image_retention_hours", 24, 1, 24 * 30
        )
        max_concurrency = self._config_int("max_concurrency", 2, 1, 8)

        self.client = ChatGpt2ApiClient(
            base_url=self._config_text("base_url"),
            api_key=self._config_text("api_key"),
            model=self._config_text("model") or "gpt-image-2.6",
            timeout=float(self.request_timeout),
            size=self._config_text("size"),
            quality=self._config_text("quality"),
        )

        data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.store = ImageStore(
            data_dir / "images",
            retention_hours=retention_hours,
        )

        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._cooldown_lock = asyncio.Lock()
        self._cooldowns: dict[str, float] = {}

        logger.info(
            "ChatGPT2API 生图插件已加载：%s（模型：%s）",
            self.client.base_url or "未配置服务地址",
            self.client.model or "未配置模型",
        )

    def _config_text(self, key: str) -> str:
        return str(self.config.get(key) or "").strip()

    def _config_int(
        self,
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @filter.command("st", alias={"生图", "绘图"})
    async def generate_image(self, event: AstrMessageEvent):
        """根据提示词生成图片。用法：/st 提示词"""
        prompt = extract_prompt(event.message_str, DEFAULT_COMMANDS)
        if not prompt or prompt.lower() in {"帮助", "help", "?", "？"}:
            yield event.plain_result(HELP_TEXT)
            return

        if event.get_group_id() and not self.allow_group:
            yield event.plain_result(
                "该指令仅支持私聊使用，请联系管理员开启群聊权限。"
            )
            return

        if not self.client.configured:
            yield event.plain_result(
                "尚未配置 ChatGPT2API 服务地址或模型，"
                "请先在插件配置中填写 base_url 和 model。"
            )
            return

        if len(prompt) > MAX_PROMPT_LENGTH:
            yield event.plain_result(
                f"提示词过长（{len(prompt)} 字符），请控制在 {MAX_PROMPT_LENGTH} 字符以内。"
            )
            return

        remaining = await self._take_cooldown(event)
        if remaining:
            yield event.plain_result(f"生成冷却中，请 {remaining} 秒后再试。")
            return

        count_hint = f" {self.image_count} 张" if self.image_count > 1 else ""
        yield event.plain_result(
            f"🎨 正在生成{count_hint}图片，请稍候…"
        )

        started = time.monotonic()
        try:
            async with self._semaphore:
                images = await self.client.generate(prompt, n=self.image_count)
                paths = [
                    await self.store.save(image.data, image.mime_type)
                    for image in images
                ]
        except ImageGenerationError as exc:
            logger.warning("ChatGPT2API 生图失败：%s", exc)
            yield event.plain_result(f"生成失败：{exc}")
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ChatGPT2API 生图插件处理异常")
            yield event.plain_result(
                "生成失败：插件内部错误，请查看 AstrBot 日志。"
            )
            return

        try:
            await self.store.maybe_cleanup()
        except Exception:
            logger.warning("清理过期生图文件失败", exc_info=True)

        elapsed = time.monotonic() - started
        logger.info(
            "ChatGPT2API 生图完成：%s 张，耗时 %.1f 秒",
            len(paths),
            elapsed,
        )
        if len(paths) > 1:
            yield event.plain_result(
                f"已生成 {len(paths)} 张图片（耗时 {elapsed:.0f} 秒）"
            )
        for path in paths:
            yield event.image_result(str(path))

    async def _take_cooldown(self, event: AstrMessageEvent) -> int:
        """返回剩余冷却秒数；0 表示本次请求已放行。"""
        if self.cooldown_seconds <= 0:
            return 0

        key = (
            f"{event.get_group_id() or ''}:"
            f"{event.get_sender_id() or event.unified_msg_origin}"
        )
        now = time.monotonic()
        async with self._cooldown_lock:
            last = self._cooldowns.get(key)
            if last is not None:
                remaining = self.cooldown_seconds - (now - last)
                if remaining > 0:
                    return max(1, math.ceil(remaining))
            self._cooldowns[key] = now

            if len(self._cooldowns) > 2048:
                cutoff = now - self.cooldown_seconds - 3600
                self._cooldowns = {
                    item_key: value
                    for item_key, value in self._cooldowns.items()
                    if value >= cutoff
                }
        return 0

    async def terminate(self) -> None:
        await self.client.close()

import asyncio
import time
from contextlib import suppress
from pathlib import Path

import astrbot.core.message.components as Comp
from astrbot import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from .core.avatar import AvatarManager
from .core.meme import MemeManager
from .core.param import MissingTargetError, ParamsCollector
from .core.resources import ResourceManager
from .utils import compress_image

try:
    from astrbot.api.web import error_response, json_response, request

    WEB_API_AVAILABLE = True
except Exception:  # 旧版 AstrBot 未提供 astrbot.api.web
    error_response = json_response = request = None
    WEB_API_AVAILABLE = False

# [patcher:meme-trigger] 「指令唯一性」守卫缓存：其它插件已注册指令的首词集合。
_RESERVED_COMMANDS_CACHE: tuple[float, frozenset[str]] = (0.0, frozenset())
_RESERVED_COMMANDS_TTL = 60.0


def _plugin_name() -> str:
    """读取插件名，WebUI 接口路由需要与 AstrBot 记录的插件名一致。"""
    try:
        import yaml

        metadata = yaml.safe_load(
            (Path(__file__).parent / "metadata.yaml").read_text(encoding="utf-8")
        )
        name = str(metadata.get("name", "")).strip()
        if name:
            return name
    except Exception:
        pass
    return Path(__file__).resolve().parent.name.strip()


PLUGIN_NAME = _plugin_name()


def _reserved_first_words() -> frozenset[str]:
    """收集其它插件已注册的「首词」指令名（含别名与指令组名）。

    [patcher:meme-trigger] 本机改造后 meme 触发不再要求 @bot，任何「首词精确命中
    meme 关键词」的消息都会进入判定；因此先排除其它插件的指令名，保证一个词只会
    触发一个插件。结果带 60s 缓存，memelite 热重载会重建缓存。
    """
    global _RESERVED_COMMANDS_CACHE
    now = time.monotonic()
    cached_at, cached = _RESERVED_COMMANDS_CACHE
    if cached and now - cached_at < _RESERVED_COMMANDS_TTL:
        return cached

    names: set[str] = set()
    self_prefix = str(__package__ or "")
    try:
        handlers = star_handlers_registry.get_handlers_by_event_type(
            EventType.AdapterMessageEvent
        )
    except Exception:
        handlers = []
    for handler in handlers:
        module_path = str(getattr(handler, "handler_module_path", "") or "")
        if "memelite" in module_path or (self_prefix and module_path.startswith(self_prefix)):
            continue
        for event_filter in getattr(handler, "event_filters", []) or []:
            if isinstance(event_filter, CommandFilter):
                # 只收顶层指令：子指令的首词是所属指令组名，已由组名覆盖
                parents = [
                    p for p in (getattr(event_filter, "parent_command_names", None) or [""])
                    if p
                ]
                if parents:
                    continue
                names.add(str(event_filter.command_name))
                names.update(str(a) for a in (event_filter.alias or ()))
            elif isinstance(event_filter, CommandGroupFilter):
                # 嵌套指令组同理，只收根组名
                if getattr(event_filter, "parent_group", None) is not None:
                    continue
                names.add(str(event_filter.group_name))
                names.update(str(a) for a in (event_filter.alias or ()))

    result = frozenset(name for name in names if name)
    _RESERVED_COMMANDS_CACHE = (now, result)
    return result


class MemePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.avatar_manager = AvatarManager()
        self.collector = ParamsCollector(config, self.avatar_manager)
        self.manager = MemeManager(config, self.collector)
        self.resource_manager = ResourceManager(self.manager, config)
        self._resource_task: asyncio.Task | None = None
        self._register_web_apis(context)

    def _register_web_apis(self, context: Context) -> None:
        """注册 WebUI 面板（views/resource-manager）所使用的后端接口。"""
        if not WEB_API_AVAILABLE:
            logger.warning("当前 AstrBot 版本不支持插件 WebUI 页面，资源面板不可用")
            return

        prefix = f"/{PLUGIN_NAME}"
        context.register_web_api(
            f"{prefix}/overview", self.page_overview, ["GET"], "表情资源概览"
        )
        context.register_web_api(
            f"{prefix}/progress", self.page_progress, ["GET"], "表情资源任务进度"
        )
        context.register_web_api(
            f"{prefix}/scan", self.page_scan, ["POST"], "扫描缺失资源"
        )
        context.register_web_api(
            f"{prefix}/download", self.page_download, ["POST"], "下载缺失资源"
        )
        context.register_web_api(
            f"{prefix}/cancel", self.page_cancel, ["POST"], "停止资源任务"
        )
        context.register_web_api(
            f"{prefix}/mirror", self.page_save_mirror, ["POST"], "保存下载镜像"
        )
        context.register_web_api(
            f"{prefix}/images", self.page_images, ["GET"], "底图列表"
        )
        context.register_web_api(
            f"{prefix}/image", self.page_image, ["GET"], "底图原文"
        )

    async def page_overview(self):
        return json_response(self.resource_manager.overview())

    async def page_progress(self):
        return json_response({"task": self.resource_manager.snapshot()})

    async def page_images(self):
        try:
            offset = int(request.query.get("offset", 0))
            limit = int(request.query.get("limit", 24))
        except (TypeError, ValueError):
            offset, limit = 0, 24
        keyword = str(request.query.get("keyword", "") or "")
        return json_response(
            self.resource_manager.list_images(offset=offset, limit=limit, keyword=keyword)
        )

    async def page_image(self):
        name = str(request.query.get("file", "") or "").strip()
        if not name:
            return error_response("缺少 file 参数")
        try:
            return json_response(self.resource_manager.image_detail(name))
        except (OSError, ValueError) as exc:
            return error_response(str(exc), status_code=404)

    def _launch_resource_job(self, scan_only: bool, payload):
        mirror = payload.get("mirror") if isinstance(payload, dict) else None
        mirror = mirror.strip() if isinstance(mirror, str) else None
        started, message = self.resource_manager.start(
            scan_only=scan_only, mirror=mirror
        )
        if not started:
            return error_response(message)
        return json_response(
            {"started": True, "task": self.resource_manager.snapshot()}
        )

    async def page_scan(self):
        try:
            payload = await request.json(default={})
        except Exception:
            payload = {}
        return self._launch_resource_job(True, payload)

    async def page_download(self):
        try:
            payload = await request.json(default={})
        except Exception:
            payload = {}
        return self._launch_resource_job(False, payload)

    async def page_cancel(self):
        cancelled = self.resource_manager.cancel()
        return json_response(
            {"cancelled": cancelled, "task": self.resource_manager.snapshot()}
        )

    async def page_save_mirror(self):
        payload = await request.json(default={})
        value = payload.get("mirror") if isinstance(payload, dict) else None
        if not isinstance(value, str):
            return error_response("mirror 必须是字符串")
        value = value.strip()
        if len(value) > 500:
            return error_response("镜像地址过长")
        if value and not value.startswith(("http://", "https://")):
            return error_response("镜像地址必须以 http:// 或 https:// 开头")
        self.conf["resource_mirror"] = value
        self.conf.save_config()
        return json_response({"mirror": value})

    async def initialize(self):
        self._resource_task = asyncio.create_task(self.manager.check_resources())

    async def terminate(self):
        if self._resource_task and not self._resource_task.done():
            self._resource_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._resource_task
        await self.avatar_manager.close()

    @filter.command("meme列表", alias={"表情帮助", "meme菜单", "meme帮助"})
    async def memes_help(self, event):
        """显示meme列表"""
        if output := await self.manager.render_meme_list_image():
            yield event.chain_result([Comp.Image.fromBytes(output)])
        else:
            yield event.plain_result("图片生成失败，请稍后重试。")

    @filter.command("meme详情", alias={"表情详情", "meme信息"})
    async def meme_details_show(
        self, event: AstrMessageEvent, keyword: str | int | None = None
    ):
        """meme详情 <名称>"""
        if not keyword:
            yield event.plain_result("未指定表情名称。\n用法：/meme详情 <名称>\n发送 /meme列表 查看名称。")
            return
        keyword = str(keyword)

        result = self.manager.get_meme_info(keyword)
        if not result:
            yield event.plain_result("未找到该表情。发送 /meme列表 查看可用名称。")
            return

        meme_info, preview = result
        chain = [
            Comp.Plain(meme_info + "使用：发送表情名称，并附上所需的图片、文字或 @群友。"),
            Comp.Image.fromBytes(preview),
        ]
        yield event.chain_result(chain)

    @filter.command("禁用meme")
    async def add_supervisor(
        self, event: AstrMessageEvent, meme_name: str | None = None
    ):
        """禁用meme <名称>"""
        if not meme_name:
            yield event.plain_result("未指定表情名称。\n用法：/禁用meme <名称>\n发送 /meme列表 查看名称。")
            return
        if not self.manager.is_meme_keyword(meme_name):
            yield event.plain_result(f"未找到表情「{meme_name}」。发送 /meme列表 查看名称。")
            return
        if meme_name in self.conf["memes_disabled_list"]:
            yield event.plain_result(f"表情「{meme_name}」已经停用，无需重复操作。")
            return
        self.conf["memes_disabled_list"].append(meme_name)
        self.conf.save_config()
        yield event.plain_result(f"已停用表情「{meme_name}」。需要恢复时发送 /启用meme {meme_name}。")
        logger.info(f"当前禁用meme: {self.conf['memes_disabled_list']}")

    @filter.command("启用meme")
    async def remove_supervisor(
        self, event: AstrMessageEvent, meme_name: str | None = None
    ):
        """启用meme <名称>"""
        if not meme_name:
            yield event.plain_result("未指定表情名称。\n用法：/启用meme <名称>\n发送 /meme列表 查看名称。")
            return
        if not self.manager.is_meme_keyword(meme_name):
            yield event.plain_result(f"未找到表情「{meme_name}」。发送 /meme列表 查看名称。")
            return
        if meme_name not in self.conf["memes_disabled_list"]:
            yield event.plain_result(f"表情「{meme_name}」当前可用，无需重复操作。")
            return
        self.conf["memes_disabled_list"].remove(meme_name)
        self.conf.save_config()
        yield event.plain_result(f"已恢复表情「{meme_name}」。")

    @filter.command("meme黑名单")
    async def list_supervisors(self, event: AstrMessageEvent):
        """查看禁用的meme"""
        names = self.conf['memes_disabled_list']
        if not names:
            yield event.plain_result("暂无禁用的表情。\n禁用：/禁用meme <名称>")
            return
        yield event.plain_result(
            f"禁用表情 · 共 {len(names)} 项\n" + "\n".join(f"- {name}" for name in names)
            + "\n恢复：/启用meme <名称>"
        )

    @filter.event_message_type(EventMessageType.ALL)
    async def meme_handle(self, event: AstrMessageEvent):
        """处理 meme 生成的主流程。

        [patcher:meme-trigger] 本机三条改造：
        1. 免 @bot：由配置 `need_prefix=false` 关闭原「需要前缀或 @bot」门槛，
           触发形态固定为「关键词 [参数/目标]」；
        2. 严格参数：`require_target=true` 时必须显式给出目标（@某人 / @QQ号 /
           附图 / 引用）或文字参数，否则静默跳过，不再自动兜底发送者与 bot 头像；
        3. 指令唯一性：首词命中其它插件已注册指令名时直接放弃，避免一词双触发。
        """
        if self.conf["need_prefix"] and not event.is_at_or_wake_command:
            return
        if self.conf["extra_prefix"] and not event.message_str.startswith(
            self.conf["extra_prefix"]
        ):
            return

        param = event.message_str.removeprefix(self.conf["extra_prefix"])
        if not param:
            return

        # 指令唯一性守卫：首词是别的插件的指令名 -> 让位
        first_word = param.split()[0] if param.split() else ""
        if first_word and first_word in _reserved_first_words():
            logger.debug(f"meme放弃: 「{first_word}」是其它插件的指令名")
            return

        # 匹配 meme
        keyword = self.manager.match_meme_keyword(
            text=param, fuzzy_match=self.conf["fuzzy_match"]
        )
        if not keyword or keyword in self.conf["memes_disabled_list"]:
            return

        # 合成表情
        try:
            image = await asyncio.wait_for(
                self.manager.generate_meme(
                    event,
                    keyword,
                    require_explicit=bool(self.conf.get("require_target", True)),
                ),
                timeout=self.conf["meme_timeout"],
            )
        except MissingTargetError as e:
            # 没给目标 / 参数：静默跳过，不回复也不打扰
            logger.debug(f"meme跳过（缺少目标/参数）: {keyword} | {e}")
            return
        except asyncio.TimeoutError:
            logger.warning(f"meme生成超时: {keyword}")
            yield event.plain_result("meme生成超时")
            return
        except Exception as e:
            logger.error(f"meme生成异常: {e}")
            return

        if image and self.conf["is_compress_image"]:
            try:
                image = compress_image(image) or image
            except Exception:
                pass

        if image:
            yield event.chain_result([Comp.Image.fromBytes(image)])  # type: ignore

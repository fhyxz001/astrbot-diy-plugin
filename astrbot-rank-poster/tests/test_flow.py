"""本地端到端测试（不依赖真实 AstrBot / 下载服务）。

用 aiohttp 启动一个同时模拟「排行榜海报服务」和「开放下载接口」的服务器，
再用 tests/stubs 里的最小 AstrBot 桩件加载插件，验证：

1. 每次查询都成对请求图片和链接两个接口（参数一致）；
2. 海报、下载状态、序号范围正确；
3. 切换榜单 / 翻页 / 取消 / 非法序号；
4. 按序号提交开放下载任务：URL 对应序号、key、path 子目录；
5. key 错误时的提示；
6. 解析工具和配置默认值。

运行： python tests/test_flow.py
"""

from __future__ import annotations

import asyncio
import io
import os
import socket
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "stubs"))
sys.path.insert(0, str(ROOT))

TEMP_ROOT = Path(tempfile.mkdtemp(prefix="rank_poster_test_"))
os.environ["ASTRBOT_STUB_DATA_DIR"] = str(TEMP_ROOT / "plugin_data")

from aiohttp import web  # noqa: E402
from PIL import Image  # noqa: E402

from astrbot_plugin_rank_poster.core.config import (  # noqa: E402
    DEFAULT_OPEN_DOWNLOAD_KEY,
    PluginSettings,
)
from astrbot_plugin_rank_poster.core.open_download import (  # noqa: E402
    OpenDownloadClient,
    build_target_path,
)
from astrbot_plugin_rank_poster.core.parsing import (  # noqa: E402
    detect_page,
    detect_type,
    parse_selection,
)
from astrbot_plugin_rank_poster.main import RankPosterPlugin  # noqa: E402

GROUP_HEIGHT = 2560
GROUP_GAP = 12
COLORS = [(220, 40, 40), (40, 180, 60), (50, 80, 220), (200, 180, 40)]
LABELS = {"day": "日榜", "week": "周榜", "month": "月榜", "year": "总榜"}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class MockState:
    def __init__(self):
        self.image_calls: list[tuple[str, int]] = []
        self.link_calls: list[tuple[str, int]] = []
        self.submit_calls: list[dict] = []
        self.open_key = "test-key"
        self.download_root = "/data/downloads"


def build_app(state: MockState) -> web.Application:
    app = web.Application()

    async def image(request: web.Request):
        type_key = request.query.get("type", "day")
        groups = int(request.query.get("groups", "1"))
        state.image_calls.append((type_key, groups))

        height = groups * GROUP_HEIGHT + (groups - 1) * GROUP_GAP
        canvas = Image.new("RGB", (576, height), (0, 0, 0))
        for group in range(groups):
            block = Image.new("RGB", (576, GROUP_HEIGHT), COLORS[group % len(COLORS)])
            canvas.paste(block, (0, group * (GROUP_HEIGHT + GROUP_GAP)))
        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=95)
        return web.Response(body=buffer.getvalue(), content_type="image/jpeg")

    async def links(request: web.Request):
        type_key = request.query.get("type", "day")
        groups = int(request.query.get("groups", "1"))
        state.link_calls.append((type_key, groups))

        origin = str(request.url.origin())
        items = [
            {
                "group": (index - 1) // 10 + 1,
                "index": index,
                "video_url": f"{origin}/video/{index}?type={type_key}&groups={groups}",
            }
            for index in range(1, groups * 10 + 1)
        ]
        return web.json_response(
            {
                "ok": True,
                "type": type_key,
                "label": LABELS.get(type_key, type_key),
                "groups": groups,
                "count": len(items),
                "items": items,
            }
        )

    async def open_download(request: web.Request):
        payload = await request.json()
        url = str(payload.get("url") or "").strip()
        path = str(payload.get("path") or "").strip()
        key = str(payload.get("key") or "")
        state.submit_calls.append({"url": url, "path": path, "key": key})

        if key != state.open_key:
            return web.json_response(
                {"ok": False, "error": "key 无效或未在设置中启用"}, status=401
            )
        if not url.startswith(("http://", "https://")):
            return web.json_response(
                {"ok": False, "error": "下载链接必须是 http/https 直链"}, status=400
            )
        if not path:
            return web.json_response({"ok": False, "error": "下载路径不能为空"}, status=400)

        resolved = f"{state.download_root}/{path}"
        return web.json_response(
            {
                "ok": True,
                "task": {
                    "task_id": uuid.uuid4().hex,
                    "url": url,
                    "path": resolved,
                    "status": "pending",
                    "error": None,
                    "size": None,
                    "created_at": "2026-11-04 16:38:48",
                    "finished_at": None,
                },
            }
        )

    app.router.add_get("/api/rank-poster/image", image)
    app.router.add_get("/api/rank-poster/links", links)
    app.router.add_post("/api/open/download", open_download)
    return app


class FakeEvent:
    def __init__(self, text: str = "", umo: str = "aiocqhttp:GroupMessage:100"):
        self.message_str = text
        self.unified_msg_origin = umo
        self.is_at_or_wake_command = False
        self.stopped = False
        self.results: list[tuple[str, str]] = []

    def get_message_str(self) -> str:
        return self.message_str

    def plain_result(self, text: str):
        item = ("text", text)
        self.results.append(item)
        return item

    def image_result(self, path: str):
        item = ("image", path)
        self.results.append(item)
        return item

    def stop_event(self) -> None:
        self.stopped = True


class FakeContext:
    def __init__(self):
        self.messages: list[tuple[str, object]] = []


async def say(plugin: RankPosterPlugin, event: FakeEvent, text: str) -> list[tuple[str, str]]:
    event.message_str = text
    event.results = []
    results = []
    async for item in plugin.on_message(event):
        results.append(item)
    return results


def texts_of(results) -> list[str]:
    return [value for kind, value in results if kind == "text"]


def submit_calls(state: MockState) -> list[dict]:
    return [call for call in state.submit_calls if call["url"]]


def test_parsing() -> None:
    check(detect_type("看看周榜") == "week", "周榜解析失败")
    check(detect_type("月榜第2页") == "month", "月榜解析失败")
    check(detect_type("总榜") == "year", "总榜解析失败")
    check(detect_type("rank week") == "week", "英文 week 解析失败")
    check(detect_type("今天天气") is None, "普通文本不应识别榜单")

    check(detect_page("第2页").absolute == 2, "第2页解析失败")
    check(detect_page("第二页").absolute == 2, "第二页解析失败")
    check(detect_page("下一页").delta == 1, "下一页解析失败")
    check(detect_page("翻一页").delta == 1, "翻一页解析失败")
    check(detect_page("上一页").delta == -1, "上一页解析失败")

    check(parse_selection("3").numbers == (3,), "单个序号解析失败")
    check(parse_selection("1,3,5").numbers == (1, 3, 5), "多数序号解析失败")
    check(parse_selection("1-3").numbers == (1, 2, 3), "范围解析失败")
    check(parse_selection("第三个").numbers == (3,), "中文序号解析失败")
    check(parse_selection("第三个和第五个").numbers == (3, 5), "中文多序号解析失败")
    check(parse_selection("0").kind == "cancel", "0 应取消")
    check(parse_selection("不用了").kind == "cancel", "不用了应取消")
    check(parse_selection("全部").kind == "all", "全部解析失败")
    check(parse_selection("我今天买了3个苹果").kind == "none", "普通聊天不应触发下载")

    check(
        build_target_path("", "week", 12, "https://x/a/b.mp4") == "week_p012_b.mp4",
        "默认路径生成失败",
    )
    check(
        build_target_path("astrbot/", "day", 1, "https://x/a.mp4")
        == "astrbot/day_p001_a.mp4",
        "子目录路径生成失败",
    )
    check(
        build_target_path("", "day", 2, "https://x/no-ext")
        == "day_p002_no-ext.mp4",
        "无扩展名时未回退到 mp4",
    )

    default = PluginSettings.from_config({})
    check(default.open_download_key == DEFAULT_OPEN_DOWNLOAD_KEY, "默认开放下载 Key 异常")
    check(default.download_subdir == "", "默认下载子目录应为空")
    check(
        PluginSettings.from_config({"download_subdir": "/astrbot/"}).download_subdir == "astrbot",
        "下载子目录未规范化",
    )
    check(
        OpenDownloadClient._detail(400, '{"ok":false,"error":"x"}', {"ok": False, "error": "x"})
        == "x",
        "错误详情解析失败",
    )


async def test_flow() -> None:
    state = MockState()
    app = build_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    base = f"http://127.0.0.1:{port}"

    config = {
        "server_url": base,
        "open_download_key": "test-key",
        "download_subdir": "",
        "group_require_at": False,
        "request_timeout": 30,
        "state_ttl": 300,
    }

    context = FakeContext()
    plugin = RankPosterPlugin(context, config)
    await plugin.initialize()
    umo = "aiocqhttp:GroupMessage:100"

    try:
        # 1. 关键词触发：必须同时请求两个接口
        event = FakeEvent(umo=umo)
        results = await say(plugin, event, "排行榜")
        check(event.stopped, "触发后应终止事件传播")
        check(state.image_calls == [("day", 1)], f"图片接口调用异常：{state.image_calls}")
        check(state.link_calls == [("day", 1)], f"链接接口调用异常：{state.link_calls}")

        image_paths = [value for kind, value in results if kind == "image"]
        check(len(image_paths) == 1, "应发送一张海报")
        check(Path(image_paths[0]).exists(), "海报文件应存在")
        with Image.open(image_paths[0]) as poster:
            check(poster.size == (576, GROUP_HEIGHT), f"第1页海报尺寸异常：{poster.size}")
        prompt = "\n".join(texts_of(results))
        check("序号 1-10" in prompt, f"提示缺少序号范围：{prompt}")
        check("回复 0" in prompt, "提示缺少 0 取消说明")

        current = plugin._states[umo]
        check(current.type_key == "day" and current.page == 1, "状态错误")
        check(current.indices == list(range(1, 11)), "第1页序号应为 1-10")

        # 2. 切换榜单：重新同时请求两个接口
        await say(plugin, event, "周榜")
        check(state.image_calls[-1] == ("week", 1), f"切周榜失败：{state.image_calls}")
        check(state.link_calls[-1] == ("week", 1), f"切周榜链接失败：{state.link_calls}")
        check(plugin._states[umo].type_key == "week", "状态未切到周榜")

        # 3. 翻页：请求 groups=2 并裁剪出第 2 组
        before_images = len(state.image_calls)
        results = await say(plugin, event, "下一页")
        check(len(state.image_calls) == before_images + 1, "翻页应重新查询")
        check(state.image_calls[-1] == ("week", 2), f"翻页参数异常：{state.image_calls}")
        check(state.link_calls[-1] == ("week", 2), f"翻页链接参数异常：{state.link_calls}")

        page2 = plugin._states[umo]
        check(page2.page == 2, "页码未更新")
        check(page2.indices == list(range(11, 21)), f"第2页序号异常：{page2.indices}")
        prompt = "\n".join(texts_of(results))
        check("序号 11-20" in prompt, f"第2页提示异常：{prompt}")

        image_path = [value for kind, value in results if kind == "image"][0]
        with Image.open(image_path) as poster:
            check(poster.size == (576, GROUP_HEIGHT), f"裁剪后高度异常：{poster.size}")
            pixel = poster.convert("RGB").getpixel((20, 20))
        check(abs(pixel[1] - COLORS[1][1]) < 30 and pixel[0] < 90, f"裁剪到的不是第2组：{pixel}")

        # 4. 无关消息不应被处理
        check(await say(plugin, event, "今天天气不错") == [], "无关消息不应被插件处理")

        # 5. 按序号提交开放下载：使用当前快照，不重新请求榜单接口
        before_images, before_links = len(state.image_calls), len(state.link_calls)
        results = await say(plugin, FakeEvent(umo=umo), "12")
        check(len(state.image_calls) == before_images, "提交不应再请求图片接口")
        check(len(state.link_calls) == before_links, "提交不应再请求链接接口")
        check(umo not in plugin._states, "提交后应清除状态")

        reply = "\n".join(texts_of(results))
        check("已提交到下载服务" in reply and "序号 12" in reply, f"提交回复异常：{reply}")
        calls = submit_calls(state)
        check(len(calls) == 1, f"下载服务应收到 1 个任务：{calls}")
        check(calls[0]["url"].endswith("/video/12?type=week&groups=2"), "提交的 URL 与序号不匹配")
        check(calls[0]["path"] == "week_p012_12.mp4", f"提交路径异常：{calls[0]}")
        check(calls[0]["key"] == "test-key", f"key 未正确传递：{calls[0]}")
        check("/data/downloads/week_p012_12.mp4" in reply, f"回复未包含服务端保存路径：{reply}")

        # 6. 非法序号：提示范围且保留状态
        event3 = FakeEvent(umo=umo)
        await say(plugin, event3, "排行榜")
        check(umo in plugin._states, "重新查看榜单后应有状态")
        results = await say(plugin, event3, "15")
        check(any("不在当前页" in text for text in texts_of(results)), "非法序号应提示范围")
        check(umo in plugin._states, "非法序号不应清除状态")

        # 7. 取消
        results = await say(plugin, event3, "0")
        check(any("不下载" in text for text in texts_of(results)), "0 应取消")
        check(umo not in plugin._states, "取消后应清除状态")

        # 8. 没有状态时的裸数字不应触发
        check(await say(plugin, FakeEvent(umo=umo), "3") == [], "无状态时的数字不应触发")

        # 9. 指令风格：rank week 2
        await say(plugin, FakeEvent(umo="aiocqhttp:GroupMessage:200"), "rank week 2")
        check(state.image_calls[-1] == ("week", 2), f"rank week 2 解析失败：{state.image_calls}")

        # 10. 自检指令
        check_event = FakeEvent(umo="aiocqhttp:GroupMessage:300")
        check_results = [item async for item in plugin.rankcheck(check_event)]
        check_text = "\n".join(value for kind, value in check_results if kind == "text")
        check("榜单服务正常" in check_text, "自检未报告榜单正常")
        check("开放下载接口正常" in check_text, "自检未报告开放下载正常")

        # 11. 多选 + 子目录
        sub_plugin = RankPosterPlugin(FakeContext(), dict(config, download_subdir="astrbot"))
        await sub_plugin.initialize()
        sub_umo = "aiocqhttp:GroupMessage:400"
        sub_event = FakeEvent(umo=sub_umo)
        await say(sub_plugin, sub_event, "排行榜")
        results = await say(sub_plugin, sub_event, "1,3")
        check(any("已提交到下载服务 2 个任务" in text for text in texts_of(results)), "多选提交失败")
        calls = submit_calls(state)[-2:]
        check(
            {call["path"] for call in calls}
            == {"astrbot/day_p001_1.mp4", "astrbot/day_p003_3.mp4"},
            f"多选路径异常：{calls}",
        )
        await sub_plugin.terminate()

        # 12. key 错误时应提示
        bad_plugin = RankPosterPlugin(FakeContext(), dict(config, open_download_key="bad-key"))
        await bad_plugin.initialize()
        bad_umo = "aiocqhttp:GroupMessage:500"
        bad_event = FakeEvent(umo=bad_umo)
        await say(bad_plugin, bad_event, "排行榜")
        results = await say(bad_plugin, bad_event, "1")
        reply = "\n".join(texts_of(results))
        check("Key 无效" in reply, f"错误 key 未提示：{reply}")
        await bad_plugin.terminate()

        # 13. probe：空 URL 只校验 key，不创建任务
        probe_settings = PluginSettings.from_config(
            {"server_url": base, "open_download_key": "test-key"}
        )
        probe_client = OpenDownloadClient(plugin._ensure_session(), probe_settings)
        await probe_client.probe()
    finally:
        await plugin.terminate()
        await runner.cleanup()


async def main() -> None:
    test_parsing()
    await test_flow()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())

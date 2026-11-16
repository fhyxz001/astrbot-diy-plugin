# 排行榜海报下载（astrbot_plugin_rank_poster）

对接 `RANK_POSTER_API`：用对话拉取排行榜海报，按海报上的序号把对应视频提交给
本项目的 **开放下载接口**（`POST /api/open/download`），由服务端后台下载。

> v2.1.0 起改为调用 `OPEN_DOWNLOAD_API.md` 的开放下载接口，不再依赖 qBittorrent
> 或 Motrix。

## 功能

- **一次请求、两份数据**：每次查询都用相同参数**成对**请求
  `/api/rank-poster/image` 和 `/api/rank-poster/links`（先图后链接，复用同一份
  快照），保证「图片上的序号 == 下载链接的序号」。
- **对话式选择**：发海报后询问用户要下载哪个视频，回复 `1-10`，或回复 `0`
  表示不需要。
- **切换榜单**：日榜 / 周榜 / 月榜 / 总榜（`year` 对应站点总榜）。
- **翻页**：`下一页` / `上一页` / `第2页`，每页 10 个，最多 10 页（100 个）。
  翻页同样成对请求两个接口，并自动把多组拼图裁剪成当前页那一组。
- **按序号提交下载**：直接使用当前海报对应的链接快照，不会重新拉取，
  逐条提交到服务端的开放下载接口，返回服务端解析出的保存路径与任务 ID。
- **连接自检**：`/rankcheck` 检查榜单服务和开放下载 Key 是否可用。

## 支持的平台

插件只依赖 AstrBot 的通用消息接口，`metadata.yaml` 已声明支持：

- QQ：`aiocqhttp`、`qq_official`、`qq_official_webhook`
- 微信：个人微信 `weixin_oc`、微信公众号 `weixin_official_account`、
  企业微信 `wecom` / `wecom_ai_bot`
- Telegram、Discord、飞书 `lark`

下载由服务端后台线程执行，插件只需提交成功并返回文本结果，不依赖平台的
主动消息能力。群聊默认要求 @机器人 或唤醒前缀；若所用适配器无法识别 @，
可关闭 `group_require_at` 并改用唤醒前缀。

## 安装

1. 把 `astrbot_plugin_rank_poster` 整个目录放到 AstrBot 的 `data/plugins/` 下；
   或在 AstrBot WebUI「插件」页上传打包好的 zip。
2. 在 WebUI 插件配置里填写排行榜服务地址（默认 `192.168.1.13:2617`）、
   开放下载 Key（默认值与接口文档一致）和可选的下载子目录。
3. 重载插件。依赖 `aiohttp`、`Pillow` 会按 `requirements.txt` 自动安装。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `server_url` | `http://192.168.1.13:2617` | 海报/下载服务地址；只填 `192.168.1.13` 会自动补协议和 2617 端口 |
| `default_type` | `day` | 默认榜单：day/week/month/year |
| `trigger_keywords` | 排行榜、榜单、海报、看榜、rank、日榜、周榜、月榜、总榜 | 触发关键词，英文按整词匹配 |
| `group_require_at` | `true` | 群聊需 @机器人 或唤醒前缀才响应（后续回复序号同样需要） |
| `allow_group` | `true` | 是否允许群聊使用 |
| `request_timeout` | `60` | 接口请求超时（秒），大页码拼图慢时调大 |
| `state_ttl` | `600` | 海报发出后等待用户回复的有效期（秒） |
| `max_page` | `10` | 最大页码（接口 groups 上限 10） |
| `open_download_key` | `912bdbc4a3354d5d86843309e3bc52b7` | 服务端「任务 → 任务设置 → 开放下载 Key」 |
| `download_subdir` | 空 | 相对服务端下载根目录的子目录，如 `astrbot`；留空直接放下载根目录 |
| `verify_ssl` | `true` | 自签名 HTTPS 时关闭 |

### 开放下载接口

提交请求：

```json
{
  "url": "https://video.twimg.com/.../video.mp4",
  "path": "astrbot/week_p012_abc.mp4",
  "key": "912bdbc4a3354d5d86843309e3bc52b7"
}
```

- `path` 由插件生成：`<download_subdir>/<榜单类型>_p<序号>_<链接文件名>.<扩展名>`。
  它是**相对路径**，服务端会基于自身的下载根目录展开；不传子目录时直接落在根目录。
- 服务端收到后立即返回 `task_id`，由后台线程下载，失败自动重试；
  父目录不存在会自动创建，目标文件已存在会覆盖。
- 服务端下载使用其设置页里的 HTTP 代理，因此 AstrBot 所在机器不需要能直连视频站点。
- 任务状态可在服务端「任务 → 日志」查看，或调用
  `GET /api/open/download/{task_id}` 查询。

接口返回的 `task.path` 是服务端解析后的绝对路径，插件会在回复里展示。

## 对话示例

```
用户：排行榜
Bot：[海报图片]
     📊 日榜 · 第 1 页（序号 1-10，共 10 个视频）

     需要下载哪个视频？
     · 单个：回复 1
     · 多个：回复 1,3,5
     · 不需要：回复 0

     切换榜单：日榜 / 周榜 / 月榜 / 总榜
     翻页：下一页 / 上一页 / 第2页

用户：周榜
Bot：[周榜海报] ……

用户：下一页
Bot：[周榜第 2 页海报，序号 11-20] ……

用户：12
Bot：✅ 已提交到下载服务 1 个任务
     · 序号 12 → /data/downloads/week_p012_abc.mp4（任务 52eafaf3…）
     服务端会在后台下载，可在「任务 → 日志」查看进度。

用户：0
Bot：👌 好的，这次不下载了。
```

其他可用说法：`看日榜`、`月榜第2页`、`1-5`、`第三个`、`全部`、`不用了`。
`/rankcheck` 可检查榜单服务和开放下载接口是否连通。

## 一致性与翻页说明

- 服务端按 `(type, groups)` 缓存榜单 10 分钟，图片和链接接口共用同一份快照。
  插件每次查询都先请求图片接口、紧接着用相同参数请求链接接口，任一失败就整次失败，
  不会拿旧的链接去配新的图。
- 接口没有 offset 参数，所以“第 N 页”是通过请求 `groups=N` 再裁剪第 N 组实现的：
  每组高 2560px、组间 12px、宽 576px。若服务端返回的版式与文档不符，插件会
  退回发送整张拼图并在提示里说明。
- 用户回复序号后不会再请求榜单接口，直接把当前海报对应的链接提交给下载接口。

## 常见问题

- **图片发不出来**：确认 `server_url` 端口是 2617，先用浏览器打开
  `http://<host>:2617/api/rank-poster/links?type=day&groups=1` 验证。
- **提交失败：Key 无效**：`open_download_key` 与服务端「任务设置 → 开放下载 Key」
  不一致，或服务端该 key 为空（接口未启用）。
- **提交成功但没下载**：到服务端「任务 → 日志」看该 `task_id` 的状态；
  常见原因是视频链接防盗链（403/404）、磁盘权限或路径不可写。
- **文件保存位置不对**：`download_subdir` 是相对服务端下载根目录的路径；
  服务端返回的 `task.path` 才是最终绝对路径。
- **页码大时超时**：服务端要现场拼更多封面，调大 `request_timeout`。
- **群聊不响应**：默认需要 @机器人 或唤醒前缀；可关闭 `group_require_at`。

## 目录结构

```
astrbot_plugin_rank_poster/
├── main.py              # AstrBot 入口：对话状态机、指令
├── core/
│   ├── config.py        # 配置规范化
│   ├── parsing.py       # 榜单类型 / 页码 / 序号解析
│   ├── poster_api.py    # 两个接口的成对请求与校验
│   ├── open_download.py # 开放下载接口客户端
│   └── image_util.py    # 多组拼图裁剪
├── _conf_schema.json
├── metadata.yaml
└── requirements.txt
```

`core/` 不依赖 AstrBot，可以直接用 `python tests/test_flow.py` 跑本地模拟服务测试。

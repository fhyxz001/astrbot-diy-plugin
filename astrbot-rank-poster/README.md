# astrbot-rank-poster

AstrBot 插件：查询日榜 / 周榜 / 月榜 / 总榜海报，按图片上的序号把对应视频提交给
开放下载接口，由服务端后台下载。

> v2.1.0 起改为调用开放下载接口（`POST /api/open/download`），不再依赖
> qBittorrent 或 Motrix。

- 插件源码：[astrbot_plugin_rank_poster/](astrbot_plugin_rank_poster/)
- 插件说明：[astrbot_plugin_rank_poster/README.md](astrbot_plugin_rank_poster/README.md)
- 安装包：[astrbot_plugin_rank_poster.zip](astrbot_plugin_rank_poster.zip)
- 接口文档：[RANK_POSTER_API.md](RANK_POSTER_API.md)、[OPEN_DOWNLOAD_API.md](OPEN_DOWNLOAD_API.md)
- 本地端到端测试：`python tests/test_flow.py`

## 安装

1. 把 `astrbot_plugin_rank_poster/` 整个目录放到 AstrBot 的 `data/plugins/` 下；
   或在 AstrBot WebUI「插件」页上传 `astrbot_plugin_rank_poster.zip`。
2. 在 WebUI 插件配置中填写排行榜服务地址（默认 `192.168.1.13:2617`）、
   开放下载 Key（默认值与接口文档一致）和可选的下载子目录。
3. 重载插件，发送「排行榜」即可。

## 主要特性

- 支持 QQ、微信（个人微信 / 微信公众号 / 企业微信）、Telegram、Discord、飞书；
- 每次查询都成对请求海报图片接口与下载链接接口，保证图号与链接序号一致；
- 对话切换日/周/月/总榜，支持 `下一页`、`上一页`、`第2页` 翻页；
- 发海报后回复 `1-10` 选择下载，回复 `0` 取消，支持 `1,3,5` 多选；
- 通过开放下载接口把视频逐条提交给服务端后台下载，返回任务 ID 与服务端保存路径。

详细配置与说明见 [插件 README](astrbot_plugin_rank_poster/README.md)。

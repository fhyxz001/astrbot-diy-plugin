# astrbot_plugin_copymanga

AstrBot 的拷贝漫画搜索与下载插件。

第一版只做两件事：

- 在私聊中搜索漫画；
- 把章节图片下载到配置的本地目录。

不支持群聊、收藏夹、订阅更新、PDF、CBZ 打包或把文件直接发回聊天。

## 功能

- `/cp 搜索 <关键词>`：搜索漫画，返回前 10 条结果。
- `/cp 详情 <序号>`：查看漫画的章节总数和最新章，下载前可先确认。
- `/cp 下载 <序号>`：下载上一次搜索结果的指定漫画的最新一话。
- `/cp 下载 <path_word>`：按漫画 ID 下载最新一话。
- `/cp 下载 <漫画链接>`：按漫画链接下载最新一话。
- `/cp 下载 <章节链接>`：下载链接对应的章节。
- `/cp 下载 <序号> 3`：下载第 3 章（按章节列表从 1 开始排序）。
- `/cp 下载 <序号> 3-5`：下载第 3 到第 5 章。
- `/cp 下载 <序号> all`：下载默认分组的全部章节，可断点续传。
- `/cp 下载 <关键词>`：没有搜索结果时，按关键词搜索并使用第一条结果。

示例：

```text
/cp 搜索 迷宫饭
/cp 详情 1
/cp 下载 1
/cp 下载 1 3
/cp 下载 1 all
/cp 下载 https://www.mangacopy.com/comic/xxxx
/cp 下载 https://www.mangacopy.com/comic/xxxx/chapter/yyyy
```

## 安装

1. 把整个 `astrbot_plugin_copymanga` 目录放到 AstrBot 的 `data/plugins/` 下。
2. 在 AstrBot WebUI 的插件页重载插件。
3. 插件会读取同目录下的 `requirements.txt` 安装 `httpx`。

目录结构：

```text
data/plugins/astrbot_plugin_copymanga/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── requirements.txt
├── core/
└── tests/
```

## 配置

在 AstrBot WebUI 的插件配置中修改：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `download_dir` | 空 | 下载根目录。留空时使用 `data/plugin_data/astrbot_plugin_copymanga/downloads`；相对路径相对于 AstrBot 根目录 |
| `api_domain` | `api.copy202601.com` | 拷贝漫画 API 域名，不要带 `https://` |
| `img_concurrency` | 8 | 图片下载并发数 |
| `max_accounts` | 5 | 自动注册账号池上限 |
| `max_chapters_per_task` | 0 | 单次下载最多章节数，0 表示不限制。旧版本保存过的配置不会自动更新，需要手动改为 0 |
| `request_timeout` | 15 | API 请求超时秒数 |

下载目录示例：

```text
<download_dir>/
└── 漫画名/
    └── 0012 第12话/
        ├── 001.webp
        ├── 002.webp
        └── ...
```

## 账号说明

插件默认会自动注册账号并维护账号池，用于获取章节图片列表。账号信息保存在：

```text
data/plugin_data/astrbot_plugin_copymanga/accounts.json
```

遇到 HTTP 210 风控时，插件会把当前账号冷却 60 秒并尝试切换其他账号。账号池达到上限且全部受限时，会提示稍后重试。

`accounts.json` 中包含自动注册的用户名和密码，请勿公开或上传该文件。

## 限制

- 仅支持私聊；群聊消息会直接被拒绝。
- 只下载默认分组的最新章、指定章节或全部章节，不提供章节目录浏览。
- 下载的是原始图片格式（通常是 WebP/JPEG），不做格式转换。
- 不发送图片或文件到聊天，只保存到本地目录。
- 拷贝漫画接口域名、版本头或风控策略变化时，插件可能需要同步更新。

## 免责声明

本插件仅用于学习与个人备份，请遵守目标网站的服务条款和当地法律法规。请勿用于传播盗版内容或商业用途。自动注册账号可能带来账号或 IP 风险，请自行评估。

## 许可证

本项目基于 [lanyeeee/copymanga-downloader](https://github.com/lanyeeee/copymanga-downloader) 的接口逻辑移植，原项目使用 MIT License。相关版权声明保留在 [LICENSE](LICENSE) 中。

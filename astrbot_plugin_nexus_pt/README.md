# AstrBot NexusPHP PT 助手

通过 Cookie 访问 NexusPHP 架构的 PT 站点，在私聊中浏览首页种子、搜索资源、查看详情，并将种子推送到 qBittorrent 下载。

仓库地址：<https://gitee.com/hexbkyoma/astrbot_plugin_nexus_pt>

## 功能

- 浏览站点首页（`torrents.php`）最新种子，支持翻页
- 按标题搜索种子
- 查看种子详情：类型、大小、做种/下载/完成人数、发布时间、简介
- 服务端携带 Cookie 拉取 `.torrent` 文件，再上传到 qBittorrent
- 支持 qBittorrent 保存路径、分类、暂停添加
- 自动识别 Cookie 失效、跳转登录页、返回网页而非种子文件等异常

## 安装

1. 将整个 `astrbot_plugin_nexus_pt` 目录放入 AstrBot 的 `data/plugins/` 目录。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 重启 AstrBot，在插件配置页填写站点与 qBittorrent 信息。

## 配置项

| 配置项 | 说明 |
|---|---|
| `base_url` | NexusPHP 站点根地址，例如 `https://pt.example.com` |
| `cookie` | 浏览器复制的完整登录 Cookie |
| `request_timeout` | 请求超时（秒），默认 20 |
| `list_limit` | 首页/搜索列表最多显示条数，默认 10 |
| `qb_url` | qBittorrent WebUI 地址，例如 `http://127.0.0.1:8080` |
| `qb_api_key` | qBittorrent API Key |
| `qb_savepath` | qBittorrent 保存路径，留空使用默认路径 |
| `qb_category` | qBittorrent 分类，留空不设置 |
| `qb_paused` | 添加后是否暂停，默认关闭 |

### 获取站点 Cookie

在浏览器登录 PT 站点，打开开发者工具 → 网络（Network）→ 任意站点请求 → 请求头中的 `Cookie`，完整复制到插件配置。NexusPHP 通常包含 `c_secure_uid`、`c_secure_pass`、`c_secure_login` 等字段。

### qBittorrent API Key

API Key 认证需要 **qBittorrent 5.2.0 及以上版本**，可在 WebUI 的「选项 → WebUI」中查看或生成。插件使用 `Authorization: Bearer <API Key>` 请求头认证，不需要用户名和密码。

## 指令

| 指令 | 说明 |
|---|---|
| `/pt 首页 [页码]` | 浏览最新种子 |
| `/pt 最新 [页码]` | 同首页 |
| `/pt 搜索 <关键词>` | 按标题搜索种子 |
| `/pt 详情 <序号\|ID\|链接>` | 查看种子详情 |
| `/pt 下载 <序号\|ID\|链接>` | 推送到 qBittorrent |
| `/pt 帮助` | 查看指令说明 |

序号来自最近一次首页或搜索结果，10 分钟内有效。也可以直接使用种子 ID（如 `123456`）、`id:123` 或 `details.php?id=123` 形式的详情页链接。

首页与搜索结果以封面图片链接形式返回：

```text
1.https://example.com/poster1.jpg
2.https://example.com/poster2.jpg
```

列表页没有封面时会自动请求详情页补齐；仍然找不到封面时，使用详情页链接作为兜底。

## 使用示例

```text
/pt 首页
/pt 搜索 沙丘
/pt 详情 2
/pt 下载 2
/pt 下载 123456
/pt 下载 https://pt.example.com/details.php?id=123456
```

## 说明与限制

- 插件仅允许私聊使用，群聊请求会被拒绝。
- 下载流程为「服务端带 Cookie 获取种子 → 上传到 qBittorrent」，因此 qBittorrent 无需直接访问 PT 站点。
- 列表与详情解析基于 NexusPHP 常见页面结构；如果站点使用特殊主题，可能需要调整 `core/parser.py`。
- 仅支持单站点配置。

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

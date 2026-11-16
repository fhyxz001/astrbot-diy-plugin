# AstrBot ChatGPT2API 生图

通过 ChatGPT2API 容器项目提供的 OpenAI 兼容图片接口，在聊天中用 `/st` 指令根据提示词生成并发送图片。

## 功能

- 指令：`/st <提示词>`，也支持 `/生图`、`/绘图`
- 调用 `POST /v1/images/generations`，请求体与官方 OpenAI 图片接口一致
- 兼容多种响应格式：`b64_json`、图片 URL、data URI、直接返回二进制图片
- 返回 URL 时由 AstrBot 服务端下载并落盘后再发送，避免平台无法访问内网地址
- 支持生成数量、尺寸、质量、请求超时、并发数、用户冷却、群聊开关
- 图片临时保存在插件数据目录，默认 24 小时后自动清理
- 生成中先回复提示，完成后逐张发送图片

## 安装

1. 将整个 `astrbot_plugin_chatgpt2api_image` 目录放入 AstrBot 的 `data/plugins/` 目录。
2. 安装依赖（AstrBot 通常已自带 `httpx`，仍建议执行）：

```bash
pip install -r requirements.txt
```

3. 重启 AstrBot，在插件配置页填写服务地址、API Key 和模型。

## 配置项

| 配置项 | 说明 |
|---|---|
| `base_url` | ChatGPT2API 服务地址，例如 `http://192.168.1.13:4444` |
| `api_key` | 接口 Key，对应 `Authorization: Bearer <Key>` |
| `model` | 图片模型，默认 `gpt-image-2.6` |
| `image_count` | 每次生成数量，对应请求里的 `n`，默认 1 |
| `size` | 图片尺寸，留空则不发送该参数 |
| `quality` | 图片质量，留空则不发送该参数 |
| `request_timeout` | 请求超时（秒），默认 120 |
| `max_concurrency` | 同时处理的生图请求上限，默认 2 |
| `cooldown_seconds` | 同一用户冷却时间，默认 10 秒，0 表示不限制 |
| `allow_group` | 是否允许群聊使用，默认开启 |
| `image_retention_hours` | 本地图片保留时长，默认 24 小时 |

### base_url 怎么写

以下写法都可以，插件会自动补全接口路径：

```text
http://192.168.1.13:4444
http://192.168.1.13:4444/v1
http://192.168.1.13:4444/v1/images/generations
```

如果服务部署在反向代理的子路径下，请填写到该子路径为止，例如
`https://example.com/chatgpt2api`，插件会请求
`https://example.com/chatgpt2api/v1/images/generations`。

## 指令

| 指令 | 说明 |
|---|---|
| `/st <提示词>` | 生成图片 |
| `/st 帮助` | 查看帮助 |
| `/生图 <提示词>` | 同 `/st` |
| `/绘图 <提示词>` | 同 `/st` |

群聊中是否需要 @机器人 或唤醒前缀，由 AstrBot 的全局指令唤醒设置决定。

## 使用示例

```text
/st 一张极简产品海报，白色背景，留白充足
/st 赛博朋克风格的城市夜景，霓虹灯，电影感，16:9
/生图 一只戴眼镜的橘猫坐在书桌前看代码
```

插件实际发送的请求与下面等价（`size`、`quality` 留空时不会出现）：

```bash
curl http://192.168.1.13:4444/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <你的 API Key>" \
  -d '{"model":"gpt-image-2.6","prompt":"一张极简产品海报","n":1}'
```

## 文件保存位置

生成成功的图片按日期保存到：

```text
data/plugin_data/astrbot_plugin_chatgpt2api_image/images/YYYYMMDD/
```

平台发送时会使用这些本地文件。超过保留时长的旧文件会在后续生图时自动清理，不需要手动维护。

## 常见问题

### 404 接口地址不存在

检查 `base_url` 是否多写或少写了路径。插件默认请求
`<base_url>/v1/images/generations`；如果服务端本身挂在 `/v1` 下，填到
`http://host:port/v1` 即可，不要重复写 `/v1`。

### 401 / 403 鉴权失败

检查 `api_key` 是否正确。配置项只填 Key 本身，不需要带 `Bearer ` 前缀；
插件会自动添加。如果 Key 已失效或被禁用，需要到 ChatGPT2API 后台重新生成。

### 请求超时

生图通常需要几十秒。可以调大 `request_timeout`，或者把 `image_count`
调回 1。插件不会自动重试生图请求，避免重复扣费或重复出图。

### 图片发送失败

插件会先把图片保存到本地再交给 AstrBot 发送。如果日志显示文件已保存但
平台发送失败，通常是平台适配器或文件上传限制导致，可以检查 AstrBot 日志。

### 群聊误触发

可以把 `allow_group` 关闭，或调整 AstrBot 的全局唤醒前缀 / @ 设置。

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

## 说明与限制

- 仅支持一个 ChatGPT2API 服务地址和一个固定模型。
- 不包含提示词审核、内容安全审核或账号池管理，相关能力由 ChatGPT2API 服务端负责。
- 生成数量上限为 4；实际可用数量取决于服务端账号权限。
- API Key 保存在 AstrBot 插件配置中，请妥善保管配置文件和日志。

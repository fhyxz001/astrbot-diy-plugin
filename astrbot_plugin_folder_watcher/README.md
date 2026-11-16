# astrbot_plugin_folder_watcher

一个 AstrBot 文件夹变更监视插件：轮询监视多个容器内文件夹，发现**文件新增或删除**后，调用 AstrBot 当前模型结合人格润色成自然的话术，再主动私聊推送到绑定的 UMO。

适合 Docker 部署的 AstrBot：宿主机目录通过 `-v` 挂载进容器，插件只读取容器内路径，不关心宿主机路径长什么样。

## 功能特性

- 支持配置多个监视项，每项独立设置目录、用途说明、接收 UMO、启用状态和轮询间隔。
- 只关心文件增减，不推送文件内容修改。
- 首次扫描只建立基线，不会把目录里已有的文件全部推送一遍。
- 文件变化后用当前聊天模型 + 人格提示词改写提醒，例如“下载目录里多了一部动画”的播报会更自然。
- AI 失败、超时或返回内容缺少关键路径/文件名时，自动回退到原始模板，不丢提醒。
- 后台 asyncio 任务 + `asyncio.to_thread` 扫描，不阻塞 AstrBot 事件循环。
- 目录不存在、权限不足、扫描不完整等异常会记录日志，并保留上一份快照，后台任务不会崩。
- 默认忽略隐藏文件和 `.tmp`、`.part`、`.crdownload` 等下载临时文件，规则可配置。
- 支持 debounce 合并，短时间内多次变化合并为一条消息，减少刷屏；也减少 AI 调用次数。
- 单条消息最多展示 20 个文件（可配置），超出显示“等 N 个未列出”。
- 插件卸载 / 重载时自动取消后台任务，并通知扫描线程尽快退出。

## 为什么用轮询

本插件使用轮询，而不是 inotify / watchdog，原因如下：

1. **Docker bind mount 下 inotify 不可靠。** 宿主机上的文件事件不一定会穿过 bind mount 传播到容器；Docker Desktop（Windows / macOS）的虚拟化文件共享层更容易出现丢失事件。
2. **网络文件系统不支持 inotify。** NFS、SMB/CIFS、部分 NAS 挂载、FUSE 文件系统通常无法提供可靠的 inotify 事件。
3. **行为可预测、依赖为零。** 只使用 Python 标准库，不需要 `watchfiles` 等额外依赖，也不依赖宿主机和容器的 inotify 限制。
4. **只关心最终状态。** 本插件只需要知道两次扫描之间多了哪些文件、少了哪些文件，轮询天然贴合这个模型。

代价是**发现延迟**：最坏情况下约为 `poll_interval + debounce_seconds` 秒；目录很大时单次扫描也会更慢。默认 30 秒适合大多数场景，可以通过配置调整。

## 目录结构

```text
astrbot_plugin_folder_watcher/
├── main.py             # 插件主文件
├── _conf_schema.json   # WebUI 配置 Schema
├── metadata.yaml       # 插件元数据
└── README.md
```

## 安装

要求 AstrBot **>= 4.10.4**：多监视项使用 `template_list` 配置类型，该类型从 4.10.4 开始提供。

AI 润色需要 AstrBot 里至少配置一个可用的聊天模型。没有配置模型也能运行，只是会自动回退到原始模板提醒。

### 方式一：放到 data/plugins

在宿主机上进入 AstrBot 的 `data/plugins` 目录（该目录通常已挂载到容器 `/AstrBot/data`）：

```bash
cd /path/to/astrbot/data/plugins
git clone https://gitee.com/hexbkyoma/astrbot_plugin_folder_watcher.git
```

然后在 AstrBot WebUI 的「插件」页面重载插件，或重启 AstrBot。

如果用 Docker 且容器内安装了 git，也可以：

```bash
docker exec -it astrbot bash
cd /AstrBot/data/plugins
git clone https://gitee.com/hexbkyoma/astrbot_plugin_folder_watcher.git
```

### 方式二：WebUI 安装

在 WebUI「插件」页面选择从仓库链接安装，填入：

```text
https://gitee.com/hexbkyoma/astrbot_plugin_folder_watcher.git
```

如果当前 AstrBot 版本不支持 Gitee 链接安装，请使用方式一。

## Docker 挂载示例

假设宿主机有两个目录：

- `/srv/downloads`：下载目录
- `/srv/comics`：漫画入库目录

`compose.yml`：

```yaml
services:
  astrbot:
    image: soulter/astrbot:latest
    container_name: astrbot
    restart: unless-stopped
    ports:
      - "6185:6185"
    volumes:
      - ./data:/AstrBot/data
      - /srv/downloads:/watched/downloads:ro
      - /srv/comics:/watched/comics:ro
```

对应的 `docker run`：

```bash
docker run -itd \
  --name astrbot \
  -p 6185:6185 \
  -v "$PWD/data:/AstrBot/data" \
  -v /srv/downloads:/watched/downloads:ro \
  -v /srv/comics:/watched/comics:ro \
  soulter/astrbot:latest
```

配置里的 `folder_path` 必须写**冒号右边的容器内路径**：

```text
/watched/downloads
/watched/comics
```

## 配置

在 WebUI「插件 → 文件夹变更监视 → 配置」中添加监视项。每个监视项包含：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `folder_path` | 是 | 容器内绝对路径，例如 `/watched/downloads`。 |
| `description` | 是 | 用途说明，会出现在推送消息里。 |
| `bind_umo` | 是 | 接收私聊的 UMO，格式 `平台ID:消息类型:会话ID`。 |
| `enabled` | 否 | 是否启用，默认 `true`。 |
| `poll_interval` | 否 | 该项的轮询间隔（秒）；`0` 表示使用全局默认值。 |

全局配置：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `default_poll_interval` | `30` | 全局默认轮询间隔（秒）。 |
| `ignore_hidden` | `true` | 忽略任何以 `.` 开头的文件/目录。 |
| `ignore_patterns` | 见下 | 额外忽略规则，fnmatch 通配符。 |
| `debounce_seconds` | `5` | 检测到变化后等待多久再推送，用于合并。 |
| `max_files_per_message` | `20` | 单条消息最多列出的新增 + 删除条目数。 |
| `ai_enabled` | `true` | 是否用模型润色提醒。关闭后直接发原始模板。 |
| `ai_provider_id` | 空 | 固定使用某个模型提供商；留空跟随每个 UMO 当前使用的聊天模型。 |
| `ai_persona_id` | 空 | 固定使用某个人格；留空依次使用当前对话人格、UMO 默认人格。 |
| `ai_timeout_seconds` | `30` | AI 请求超时时间，超时回退模板。 |
| `ai_fallback_to_template` | `true` | AI 失败时是否发送原始模板；关闭则本次不推送。 |

默认忽略规则：

```text
*.tmp
*.temp
*.part
*.partial
*.download
*.crdownload
*.aria2
~$*
*.swp
*.swo
*.swx
.~lock.*
```

规则同时匹配文件名和相对路径，例如 `*.tmp`、`cache/*` 都可以。`ignore_hidden=true` 时，`.git`、`.cache`、`.DS_Store` 等会被直接跳过。

### 多监视项是怎么表达的

AstrBot 的 Schema 没有“可增删的对象数组”这种原生类型：

- `object` 只能表达固定的一套子字段，不能重复多组；
- `list` 只能存简单值，无法承载每项的多个字段；
- `template_list`（AstrBot 4.10.4+）可以在 WebUI 中增删模板项，并逐项填写字段，是最贴近需求的写法。

保存后的配置大致如下（`__template_key` 由 `template_list` 自动写入）：

```json
{
  "watch_items": [
    {
      "__template_key": "folder",
      "folder_path": "/watched/downloads",
      "description": "下载目录",
      "bind_umo": "aiocqhttp:FriendMessage:123456789",
      "enabled": true,
      "poll_interval": 30
    },
    {
      "__template_key": "folder",
      "folder_path": "/watched/comics",
      "description": "漫画入库",
      "bind_umo": "aiocqhttp:FriendMessage:123456789",
      "enabled": true,
      "poll_interval": 0
    }
  ],
  "default_poll_interval": 30,
  "ignore_hidden": true,
  "ignore_patterns": [
    "*.tmp",
    "*.part",
    "*.crdownload"
  ],
  "debounce_seconds": 5,
  "max_files_per_message": 20,
  "ai_enabled": true,
  "ai_provider_id": "",
  "ai_persona_id": "",
  "ai_timeout_seconds": 30,
  "ai_fallback_to_template": true
}
```

修改配置后请在 WebUI 重载插件（或重启 AstrBot），后台任务会按新配置重建。

## AI 人格化提醒

### 调用流程

1. 扫描目录，得到新增 / 删除文件集合。
2. debounce 合并后，取本次要展示的用途、容器路径、数量、文件清单，组装成 JSON 数据。
3. 解析模型和人格，调用 AstrBot 当前聊天模型：

   ```python
   provider_id = await self.context.get_current_chat_provider_id(umo=bind_umo)
   response = await self.context.llm_generate(
       chat_provider_id=provider_id,
       prompt=payload_json,
       system_prompt=system_prompt,
   )
   ```

   这是 AstrBot 推荐的 `llm_generate` 调用方式，不会创建对话、不会写入历史，也不会污染用户正常的聊天上下文。

4. 校验模型输出是否原样保留容器路径和本次展示的文件名。
5. 校验通过就发送 AI 文案；失败、超时、空内容或缺少关键事实时，按 `ai_fallback_to_template` 回退到原始模板。

### 模型怎么选

- `ai_provider_id` 留空：使用 **该监视项 `bind_umo` 当前会话的聊天模型**。不同推送对象可以使用不同模型。
- `ai_provider_id` 选择固定提供商：所有监视项都用这个模型。
- 没有可用模型、提供商被删除、API Key 失效等情况会记录日志并回退模板。

### 人格怎么选

按以下优先级解析：

1. `ai_persona_id` 显式指定的人格；
2. `bind_umo` 当前对话使用的人格（如果该会话已经建立对话）；
3. `bind_umo` 对应配置文件里的默认人格。

插件会把人格的 `system_prompt` 放在任务约束之前，让模型用该人格的口吻播报。没有匹配到人格时使用一个友好的默认播报助手设定。

示例（实际输出会随人格和模型变化）：

```text
哼，下载目录里多了一部《某动画》第 01 话，别只顾着收藏不看啊。
路径：/watched/downloads
新增 1 个，删除 0 个
+ [SubGroup] Some Anime - 01.mkv
```

### 事实校验与回退

模型润色只负责“语气”，不能改变事实。插件会检查 AI 输出是否包含：

- 容器内绝对路径；
- 本次展示的每一个新增 / 删除文件名（原样字符串）。

如果模型翻译、缩写、漏掉或改写了文件名，这次 AI 文案会被判定为不可靠，自动回退到模板。数量、用途等字段由提示词强约束；模型仍可能出错，因此保留了模板兜底。

`ai_fallback_to_template=false` 时，AI 失败或校验不通过则本次**不发送**，只记日志。除非你非常在意“绝不出现模板腔”，否则建议保持默认 `true`。

### 隐私与成本

- 插件不会读取文件内容，只把**用途说明、容器路径、文件名和数量**发给模型提供商。
- 文件名同样可能包含敏感信息；如果使用云端模型，请确认可以接受这些元数据离开本机。
- 每次有效推送会调用一次模型。`debounce_seconds` 和 `max_files_per_message` 同时起到控制调用次数和提示词长度的作用。
- AI 请求默认 30 秒超时，避免提供商卡住时长时间占用推送任务。

## 获取 UMO

1. 用要接收推送的账号，在目标平台给机器人发一条**私聊**消息。
2. 在私聊里发送：

```text
/sid
```

3. 复制返回结果中的 `UMO`，形如：

```text
aiocqhttp:FriendMessage:123456789
```

4. 填入对应监视项的 `bind_umo`。

说明：

- 第一段是**平台适配器实例 ID**，不一定是适配器类型名。例如你给 OneBot 适配器起名 `aiocqhttp-1`，UMO 可能就是 `aiocqhttp-1:FriendMessage:123456789`。以 `/sid` 输出为准。
- 私聊消息类型通常是 `FriendMessage`；群聊是 `GroupMessage`。本插件面向私聊推送，群聊 UMO 虽然技术上能发送，但不在设计目标内。
- 不同监视项可以绑定不同用户，也可以绑定同一个用户。
- 主动消息依赖平台支持。部分平台（如 QQ 官方 API）对主动私聊有限制，可能发送失败；失败会记录日志，但不会影响后续监视。

## 宿主机路径 vs 容器路径（常见坑）

这是最容易配错的地方。

| 宿主机 | 容器内 | 配置应填 |
| --- | --- | --- |
| `/srv/downloads` | `/watched/downloads` | `/watched/downloads` |
| `D:\Downloads`（Docker Desktop） | `/watched/downloads` | `/watched/downloads` |
| 未挂载的 `/srv/comics` | 容器内不存在 | 先加 `-v /srv/comics:/watched/comics:ro` |

典型错误：把宿主机的 `/srv/downloads` 直接填进 `folder_path`。AstrBot 运行在容器里，它看到的是容器文件系统，不是宿主机文件系统；除非容器内恰好也有这个路径，否则会一直报“路径不存在”。

另外注意：

- 挂载路径区分大小写（Linux 容器）。
- 相对路径不可用，`folder_path` 必须是绝对路径。
- Docker Desktop 下宿主机路径要在 Docker 的文件共享范围内。
- 宿主机上的符号链接如果指向挂载目录之外，容器内可能无法访问；插件不会跟随目录符号链接递归。
- 修改 `volumes` 后需要重建容器（`docker compose up -d`），只重载插件不会改变挂载。

## `:ro` 只读挂载有影响吗？

没有影响。插件只需要读取目录项和文件元数据（名称、类型），不需要写入、修改或删除文件。因此：

```yaml
- /srv/downloads:/watched/downloads:ro
```

这种只读挂载完全够用，而且更安全。

注意区分两件事：

- `:ro` 是挂载层只读，不影响插件扫描；
- **文件权限**是另一回事。容器内运行 AstrBot 的用户必须对目录有 `r-x` 权限，否则会报权限不足。

如果日志提示 `Permission denied`，可以在宿主机检查并授予只读遍历权限，例如：

```bash
chmod -R o+rX /srv/downloads
```

更严谨的做法是让宿主机目录的 UID/GID 与容器运行用户匹配，而不是简单放开所有权限。

`/AstrBot/data` 必须保持可写，否则 AstrBot 无法保存插件配置；不要把它也改成 `:ro`。

## 模板提醒示例

以下模板用于 `ai_enabled=false`，或 AI 超时 / 未配置 / 校验失败后的回退：

```text
📁 文件夹变动提醒
用途：下载目录
路径：/watched/downloads
新增 3 个，删除 1 个

+ 2026/11/09/example.mkv
+ 2026/11/09/example.nfo
+ 2026/11/09/poster.jpg
- old/expired.tmp
```

当新增 + 删除总数超过 `max_files_per_message`（默认 20）时：

```text
……等 37 个未列出
```

行为细节：

- 先列新增，再列删除；合计最多列出配置的条数。
- 不读取文件内容；AI 模式下只发送用途、路径、文件名和数量给模型提供商。
- 同一次 debounce 窗口内“新增后又删除”的文件会相互抵消，不产生无意义的推送。
- 如果推送失败（平台离线、UMO 错误、主动消息受限等），只记录日志，下一轮监视继续运行。

## 监视行为与限制

- **递归扫描**：会扫描配置目录下的所有子目录。
- **只比较文件名集合**：同名文件内容被覆盖不会推送；重命名会表现为“删除旧名 + 新增新名”。
- **首次启动不推送**：第一次成功扫描只建立基线。插件重载后也会重新建立基线，因此停机期间的变化不会被补发。
- **扫描是快照式的**：如果文件在两次扫描之间创建又删除，最终集合没有变化，不会推送。
- **扫描不完整时跳过比对**：某个子目录临时无权限 / 读取失败时，本轮不会把该目录下的文件误判为删除，而是保留上一份快照，下轮重试。
- **大目录开销**：每个监视项独立线程扫描；目录特别大或位于慢速网络存储时，请增大 `poll_interval`。
- **线程退出**：插件卸载时会设置停止事件，让正在遍历目录的线程在下一个检查点退出。极端情况下（例如底层网络挂载阻塞），线程可能还会短暂停留，但不会再推送。

## 排障

### 日志里提示“路径不存在”

确认路径是**容器内路径**，并且在容器里可见：

```bash
docker exec -it astrbot ls -ld /watched/downloads
```

### 日志里提示“权限不足 / Permission denied”

在容器内验证：

```bash
docker exec -it astrbot ls -la /watched/downloads
```

检查运行用户、目录权限和父目录的 `x` 权限。`:ro` 不会自动解决权限问题。

### 收不到推送

依次检查：

1. `bind_umo` 是否从目标私聊的 `/sid` 原样复制；
2. UMO 第一段是否是平台实例 ID，而不是想当然的适配器类型；
3. 平台是否支持主动私聊，机器人是否被限制；
4. AstrBot 日志中是否有“未找到 UMO 对应的平台实例”或发送异常；
5. 文件是否被忽略规则过滤（隐藏文件、`.tmp`、`.part`、`.crdownload` 等）；
6. 是否在首次基线建立前就期待推送——首次扫描只建基线；
7. 是否设置了 `ai_fallback_to_template=false`，且 AI 调用失败 / 超时，导致本次被跳过。

### AI 没有生效 / 回退成了模板

先看日志中的关键词：

- `AI 润色失败`：模型提供商不可用、API Key 错误、网络错误等；
- `AI 润色超时`：模型响应超过 `ai_timeout_seconds`；
- `AI 润色结果缺少 ... 项关键路径/文件名`：模型改写了文件清单，触发事实校验；
- `ai_persona_id 配置的人格不存在`：人格选择器里的 ID 已失效。

检查顺序：

1. WebUI「模型提供商」里是否有可用的聊天模型，`ai_provider_id` 留空时对应 UMO 是否有当前模型；
2. 调大 `ai_timeout_seconds`，或换更快的模型；
3. 确认 `ai_persona_id` 留空或选择一个存在的人格；
4. 模型较小时容易漏掉文件名，可减小 `max_files_per_message`，降低单次提示词长度；
5. 仍然失败时保持 `ai_fallback_to_template=true`，至少不会漏掉提醒。

### AI 返回的内容不准确

插件已经校验容器路径和本次展示的文件名，缺失就会回退模板。数量、用途等语义无法完全自动校验，建议：

- 使用更强的模型；
- 减小 `max_files_per_message`，一次少列一些文件；
- 在人格提示词里补充“播报事实优先、不要编造”。

### 消息来得太慢 / 太频繁

- 太慢：减小 `poll_interval` 和 `debounce_seconds`；AI 模式下换更快的模型，或减小 `max_files_per_message`。
- 太频繁：增大 `poll_interval` / `debounce_seconds`，或把同一目录的多个监视项合并；AI 调用次数也会随之减少。
- 目录很大：优先增大 `poll_interval`，避免频繁全量遍历。

### 查看日志

```bash
docker logs -f astrbot
```

也可以在 WebUI 的日志页面查看插件日志。扫描错误会做去重，同一错误不会每个周期都刷屏。

## 卸载

在 WebUI 停用或卸载插件即可。`terminate()` 会取消轮询任务和 debounce 推送任务，并通知扫描线程退出。

如果手动删除插件目录，请同时重启 AstrBot，确保后台任务随旧实例结束。

## 安全说明

- 插件只读取目录结构和文件元数据，不读取、不传输文件内容。
- AI 润色会把用途、容器路径、文件名和数量发送给所配置的模型提供商；请据此选择本地模型或可信的云端模型。
- 推送目标完全由 `bind_umo` 决定，请只绑定可信的私聊会话。
- 文件相对路径会出现在消息里；如果目录名本身包含敏感信息，请注意推送对象和模型提供商。
- 建议对监视目录使用 `:ro` 挂载，遵循最小权限原则。

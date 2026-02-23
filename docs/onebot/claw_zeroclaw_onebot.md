# ZeroClaw OneBot Channel（NapCat 反向 WS）

本文件已替换早期“设计草案”。旧内容里包含 OpenClaw/正向 WS/HTTP 上报等无关方案，已移除并以当前实现为准。

## 目标与现状

**目标**：ZeroClaw 作为 OneBot v11 反向 WebSocket 服务端，NapCat（Docker 中）作为客户端周期性连接：

- 地址：`ws://host.docker.internal:12347/onebot`
- Token：`your_access_token_here`

**当前实现（最小可用）**：
- ZeroClaw 监听 OneBot 反向 WS，并接收 OneBot v11 message 事件
- 支持私聊/群聊开关、群聊需@触发、用户/群白名单
- 出站发送使用 OneBot `send_private_msg` / `send_group_msg`
- 出站内容目前以“纯文本”为主（可选 message=array 或 string）

实现代码：
- OneBot channel：[onebot.rs](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/src/channels/onebot.rs)
- 配置结构：[schema.rs:OneBotConfig](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/src/config/schema.rs#L3406-L3430)

## 配置（config.toml）

在 ZeroClaw 的 `config.toml` 中启用 OneBot：

```toml
default_provider = "bigmodel"
default_model = "GLM-4-Flash"
default_temperature = 0.7

[channels_config]
cli = false

[channels_config.onebot]
listen_host = "0.0.0.0"
listen_port = 12347
ws_path = "/onebot"
access_token = "your_access_token_here"

enable_private = true
enable_group = true
require_mention_in_group = true

allowed_users = ["*"]
allowed_groups = ["*"]

expect_message_array = true
map_images_to_markers = true
```

字段说明（与实现一致）：
- `listen_host/listen_port/ws_path`：ZeroClaw 作为 WS 服务端监听地址
- `access_token`：如果设置，则校验 `Authorization: Bearer <token>`（NapCat 反向 WS 会以该方式携带）
- `enable_private/enable_group`：是否处理私聊/群聊
- `require_mention_in_group`：群聊是否要求 @机器人
- `allowed_users/allowed_groups`：空数组表示全部拒绝；包含 `"*"` 表示放行所有
- `expect_message_array`：出站发送时 `message` 字段用 array（推荐）还是 string
- `map_images_to_markers`：入站 image 段转成 `[IMAGE:...]` 文本标记

## NapCat 侧（反向 WS 客户端）

NapCat 需要配置“反向 WebSocket（OneBot 11）”客户端：
- URL：`ws://host.docker.internal:12347/onebot`
- Token：`your_access_token_here`
- 间隔：按 NapCat 配置保持约 30 秒一次重连/探活即可

## 运行方式（Windows）

只要有 `zeroclaw.exe` + 一个可写的配置目录即可运行：

```powershell
$env:ZEROCLAW_API_KEY="你的 bigmodel key"
.\zeroclaw.exe --config-dir .\your-config-dir daemon
```

启动后日志出现类似内容即表示 OneBot 服务端已启动：
- `OneBot WS server listening on ws://0.0.0.0:12347/onebot`

NapCat 成功连上时会出现：
- `OneBot WS connected (connections=1)`

## 消息映射（当前实现）

### 入站（OneBot v11 → ZeroClaw）

仅处理 `post_type == "message"`。

- 私聊：
  - `sender = "user:<user_id>"`
  - `reply_target = "user:<user_id>"`
- 群聊：
  - `sender = "group:<group_id>:user:<user_id>"`
  - `reply_target = "group:<group_id>"`

内容解析：
- message 为 string：直接作为文本
- message 为 array：解析常见段并拼接为文本
  - `text`：拼接
  - `at`：用于判断是否 @机器人或 @all
  - `image`：在 `map_images_to_markers=true` 时转换为 `[IMAGE:<url|file|id>]`
  - `file`：转换为 `[FILE:name=...,id=...]`
  - `record/video`：转换为 `[AUDIO]` / `[VIDEO]`
  - `reply`：转换为 `(reply to #<id>)`

### 出站（ZeroClaw → OneBot v11）

recipient 约定：
- 私聊：`user:<user_id>`
- 群聊：`group:<group_id>`

发送动作：
- 私聊：`send_private_msg`
- 群聊：`send_group_msg`

当前实现只发送文本内容（不会把 `[IMAGE:...]` 等 marker 反向解析为 OneBot 段）。

## 故障排查

### 401 Unauthorized

说明 token 校验失败或缺失：
- 确认 NapCat 反向 WS Token 与 `access_token` 完全一致
- 当前正确 token 是：`your_access_token_here`

### 能连上但群里不回

常见原因：
- `require_mention_in_group=true` 但消息没有 @机器人/@all
- `allowed_users/allowed_groups` 没放行（注意空数组会全部拒绝）

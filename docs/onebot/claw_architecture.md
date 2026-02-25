# ZeroClaw OneBot（NapCat 反向 WS）架构说明

本文件聚焦 ZeroClaw 内置 OneBot v11（NapCat）通道的当前实现。旧版本中与 OpenClaw 插件系统相关的内容已移除。

## 数据流

```
NapCat (WS Client)  -->  ZeroClaw OneBot WS Server  -->  ChannelMessage  -->  Agent/LLM
NapCat (WS Client)  <--  ZeroClaw OneBot WS Server  <--  send_*_msg payload
```

## 关键约定

- ZeroClaw 作为服务端监听：`ws://<listen_host>:<listen_port><ws_path>`
  - 默认：`ws://0.0.0.0:12347/onebot`
- 若配置 `access_token`，则要求 NapCat 握手时携带：
  - `Authorization: Bearer <token>`
- 群消息触发策略：
  - `require_mention_in_group=true` 时，只有 @机器人（或 @all）才进入 ZeroClaw

## 模块与文件

- OneBot 通道实现：[onebot.rs](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/src/channels/onebot.rs)
  - `listen()`：启动 axum WS 服务端并接入 ZeroClaw channel loop
  - `handle_onebot_ws()`：握手鉴权与升级 WebSocket
  - `parse_onebot_event()`：将 OneBot v11 message 事件转换为 `ChannelMessage`
  - `build_send_payload()`：将 `SendMessage` 转换为 OneBot v11 `send_private_msg` / `send_group_msg` action
- 配置结构：[schema.rs:OneBotConfig](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/src/config/schema.rs#L3406-L3430)
  - `listen_host/listen_port/ws_path/access_token`
  - `enable_private/enable_group/require_mention_in_group`
  - `allowed_users/allowed_groups/expect_message_array/map_images_to_markers`
- 通道注册：`src/channels/mod.rs`（OneBot 作为可配置通道被收集并启动）

## 消息格式策略（实现现状）

- 入站：
  - 兼容 `message` 为 string 或 array
  - 当 `map_images_to_markers=true` 时，image 段变为 `[IMAGE:...]` 文本 marker
- 出站：
  - `expect_message_array=true` 时以 array 形式发送文本段，否则发送 string
  - 当前不会把 `[IMAGE:...]` 等 marker 反解为 OneBot 消息段（因此以纯文本方式投递）

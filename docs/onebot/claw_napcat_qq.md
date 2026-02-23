# NapCat + ZeroClaw（OneBot 反向 WS）对接指南

本文件用于 ZeroClaw 的 OneBot 反向 WS 对接。旧版本中与 OpenClaw 插件、HTTP 上报、正向 WS（NapCat 作为服务端）相关的内容已弃用并移除。

## 架构

```
QQ 用户 <-> NapCat (QQ客户端) <-> OneBot v11 反向 WS 客户端 <-> ZeroClaw OneBot WS 服务端 <-> LLM
```

关键点：
- NapCat 作为 WebSocket 客户端主动连接 ZeroClaw
- ZeroClaw 监听 `0.0.0.0:12347/onebot`
- 可选鉴权：`Authorization: Bearer <token>`

## ZeroClaw 侧准备

在 ZeroClaw 的 `config.toml` 启用 OneBot：
- `listen_port = 12347`
- `ws_path = "/onebot"`
- `access_token = "your_access_token_here"`

启动 daemon 后看到：
- `OneBot WS server listening on ws://0.0.0.0:12347/onebot`

## NapCat（Docker）反向 WS 配置

在 NapCat WebUI 中找到 OneBot 11 的反向 WebSocket/WS Client/Reverse WS（名称可能随版本略有差异），配置：
- 连接地址：`ws://host.docker.internal:12347/onebot`
- Token：`your_access_token_here`
- 重连/探活：保持 NapCat 默认或设置为约 30 秒一次即可

注意：
- Token 必须完全一致
- `ws_path` 末尾是否带 `/` 均可（ZeroClaw 同时支持 `/onebot` 与 `/onebot/`）

## 验证连通

ZeroClaw 日志出现：
- `OneBot WS connected (connections=1)`：说明 NapCat 已连接成功

如果出现 401：
- `... (missing access token)`：NapCat 未携带 token 或 token 配置为空
- `... (invalid access token)`：token 不一致

## 参考

- NapCat 文档：https://napneko.github.io/use/integration
- OneBot v11：https://11.onebot.dev
- ZeroClaw OneBot 配置与行为说明：见同目录 [claw_zeroclaw_onebot.md](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/src/onebot/claw_zeroclaw_onebot.md)

# ZeroClaw + NapCat（OneBot v11）速查

本文件为 ZeroClaw + NapCat（OneBot 反向 WS）运行速查。旧版本中与 OpenClaw/ClawHub 相关的命令已移除。

## 常用命令

### 启动 daemon（推荐）

```powershell
$env:ZEROCLAW_API_KEY="你的 bigmodel key"
.\zeroclaw.exe --config-dir .\your-config-dir daemon
```

启动成功后会输出：
- 网关地址（HTTP/Web Dashboard）
- 当前默认模型与 provider
- OneBot WS 监听：`ws://0.0.0.0:12347/onebot`

### 查看配置

```powershell
.\zeroclaw.exe --config-dir .\your-config-dir config show
```

### 直接测试模型（不经过 OneBot）

```powershell
$env:ZEROCLAW_API_KEY="你的 bigmodel key"
.\zeroclaw.exe --config-dir .\your-config-dir agent -m "ping"
```

## OneBot 配置要点

ZeroClaw 作为 OneBot 反向 WS 服务端，配置示例见：
- [claw_zeroclaw_onebot.md](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/src/onebot/claw_zeroclaw_onebot.md)

关键参数：
- `listen_port = 12347`
- `ws_path = "/onebot"`
- `access_token = "your_access_token_here"`

## NapCat 侧要点（Docker）

反向 WS 客户端连接：
- `ws://host.docker.internal:12347/onebot`
- Token：`your_access_token_here`

## 常见问题

### 401 Unauthorized

- missing access token：NapCat 没带 token 或 token 为空
- invalid access token：NapCat token 与 ZeroClaw 的 `access_token` 不一致

注意 token 需与 NapCat 侧配置完全一致

### 群里不响应

常见原因：
- `require_mention_in_group=true` 但未 @机器人/@all
- `allowed_users/allowed_groups` 未放行（空数组会全部拒绝）

### OneBot 连接状态判断

以 ZeroClaw 日志为准：
- `OneBot WS server listening ...`：服务端已监听
- `OneBot WS connected (connections=1)`：NapCat 已连接成功

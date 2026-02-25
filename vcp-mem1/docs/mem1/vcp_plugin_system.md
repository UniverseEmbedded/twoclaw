# VCP 插件系统

## 六大插件协议

VCP 支持静态、服务器、同步、异步、消息预处理、混合式六大类型插件：

- 合计 300 多个官方插件
- 几乎涉及所有生产应用场景
- 从平台控制到多媒体生成到复杂编辑到程序反编译到物联网
- 落地即生态

## 插件清单 (plugin-manifest.json)

插件清单是插件的"身份证"和"说明书"。

### 核心字段

- `name`：插件内部识别名
- `displayName`：显示名称
- `version`：版本号
- `description`：插件描述
- `pluginType`：插件类型（`static`, `messagePreprocessor`, `synchronous`, `asynchronous`, `service`, `hybridservice`）

### 执行入口

- `entryPoint`：执行脚本的命令（如 `python script.py` 或 `node script.js`）
- `communication`：通信协议（如 `protocol: "stdio"` 表示通过标准输入输出通信）

### 配置蓝图 (configSchema)

声明插件所需的配置项及其类型、默认值、描述。这些配置将通过 `_getPluginConfig` 方法合并全局和插件专属 `.env` 配置后传递给插件。

### 能力声明 (capabilities)

对于 static 插件：
- 定义 `systemPromptPlaceholders`（插件提供的占位符，如 `{{MyWeatherData}}`）

对于 synchronous 或 asynchronous 插件：
- 定义 `invocationCommands`
- 每个命令包含：
  - `command`：内部识别名（例如 "submit", "query"）
  - `description`：给 AI 看的指令描述（支持在管理面板编辑）
  - `example`：可选，提供一个更具体的调用场景示例

### WebSocket 推送配置 (webSocketPush)（可选）

如果你的插件执行成功后，希望将其结果通过 WebSocket 推送给客户端：

```json
{
  "enabled": true,
  "usePluginResultAsMessage": false,
  "messageType": "yourMessageType",
  "targetClientType": "VCPLog"
}
```

## stdio 插件

stdio 插件（常用于 `synchronous`, `asynchronous` 和部分 `static`）：

- 从标准输入 (`stdin`) 读取数据（通常是 JSON 字符串形式的参数）
- 通过标准输出 (`stdout`) 返回结果

### 对于 synchronous 插件，必须遵循以下 JSON 格式：

```json
{
  "status": "success" | "error",
  "result": "成功时返回的字符串内容或JSON对象",
  "error": "失败时返回的错误信息字符串",
  "messageForAI": "可选，给AI的额外提示信息",
  "base64": "可选，返回的Base64编码数据 (如图片、音频)"
}
```

### 对于 asynchronous 插件：

1. **初始响应**：插件脚本在收到任务后，必须立即向标准输出打印一个初始响应：

```json
{
  "status": "success",
  "result": { 
    "requestId": "unique_task_id_123", 
    "message": "任务已提交，正在后台处理中。" 
  },
  "messageForAI": "视频生成任务已提交，ID为 unique_task_id_123。请告知用户耐心等待。"
}
```

2. **后台处理**：插件脚本随后启动其耗时的后台任务

3. **回调服务器**：后台任务完成后，插件脚本通过向 VCP 服务器的 `/plugin-callback/:pluginName/:taskId` 发送 HTTP POST 请求：

```json
{
  "requestId": "unique_task_id_123",
  "status": "Succeed",
  "pluginName": "MyAsyncPlugin",
  "videoUrl": "http://example.com/video.mp4",
  "message": "视频 (ID: unique_task_id_123) 生成成功！"
}
```

## 配置与依赖

- **插件专属配置**：在插件目录下创建 `.env` 文件
- **依赖管理**：
  - Python 插件使用 `requirements.txt`
  - Node.js 插件使用 `package.json`

## 重启 VCP 服务器

`PluginManager` 会在启动时自动发现并加载新插件。

## 更新系统提示词，赋能 AI

利用 `{{VCPMySuperPlugin}}`（由 `PluginManager` 根据 `plugin-manifest.json` 的 `invocationCommands` 自动生成）将新插件的能力告知 AI。

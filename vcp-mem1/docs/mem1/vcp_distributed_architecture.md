# VCP 分布式架构

## 星型网络拓扑

VCP 的分布式架构将原有的单体应用升级为一个由"主服务器"和多个"分布式节点"组成的星型网络。

```
graph TD
    subgraph "用户/客户端"
        U[用户/前端应用]
    end

    subgraph "VCP 主服务器 (VCPToolBox - 核心调度)"
        S[server.js - 核心调度与通信]
        PM[Plugin.js - 插件管理器]
        WSS[WebSocketServer.js - 通信骨架]
        CONF[配置系统]
        VAR[通用变量替换引擎]
        MEM[VCP 记忆系统]
        ADMIN[Web 管理面板]
      
        subgraph "本地插件生态"
            P_LOCAL["本地插件 (静态/预处理/服务等)"]
        end
    end

    subgraph "VCP 分布式节点 1 (e.g., GPU服务器)"
        DS1[VCPDistributedServer.js]
        PM1[NodePMOne]
        subgraph "节点1的插件"
            P_GPU["GPU密集型插件 (e.g., 视频生成)"]
        end
    end

    subgraph "VCP 分布式节点 2 (e.g., 内网文件服务器)"
        DS2[VCPDistributedServer.js]
        PM2[NodePMTwo]
        subgraph "节点2的插件"
            P_FILE["内网文件搜索/读取插件"]
        end
    end
  
    subgraph "外部依赖"
        AI_MODEL[后端 AI 大语言模型 API]
    end

    U -- "HTTP请求" --> S
    S -- "HTTP响应" --> U
    S -- "WebSocket消息" <--> U
    S -- "构造完整请求" --> AI_MODEL
    AI_MODEL -- "AI响应 (含VCP指令)" --> S
    S -- "WebSocket连接" <--> WSS
    DS1 -- "WebSocket连接" --> WSS
    DS2 -- "WebSocket连接" --> WSS
    WSS -- "注册/注销云端插件" --> PM
    PM -- "请求执行云端工具" --> WSS
    WSS -- "转发工具调用指令" --> DS1
    DS1 -- "调用本地插件" --> P_GPU
    P_GPU -- "执行结果" --> DS1
    DS1 -- "通过WebSocket返回结果" --> WSS
    WSS -- "将结果返回给PM" --> PM
    PM -- "将结果注入AI对话" --> S
```

## 核心交互流程

### 启动与注册

1. 主服务器启动，初始化 `PluginManager` 和 `WebSocketServer`
2. 各个分布式节点启动，加载其本地的插件
3. 分布式节点通过 WebSocket 连接到主服务器
4. 发送包含所有本地插件清单的 `register_tools` 消息
5. 主服务器的 `PluginManager` 动态注册这些"云端插件"，显示名称自动添加 `[云端]` 前缀

### AI 调用工具

1. AI 在响应中嵌入 `<<<[TOOL_REQUEST]>>>` 指令
2. 主服务器的 `PluginManager` 接收到调用请求
3. **智能路由**：
   - 如果是**本地插件**，则直接在主服务器上执行
   - 如果是**云端插件**（带有 `isDistributed: true` 标记），调用 `WebSocketServer.js` 的 `executeDistributedTool` 方法

### 远程执行与结果返回

1. `WebSocketServer` 通过 WebSocket 连接，向目标分布式节点发送 `execute_tool` 消息
2. 目标分布式节点收到消息后，其本地的 `PluginManager` 调用并执行相应的插件
3. 插件执行完毕后，分布式节点将结果通过 WebSocket 发回给主服务器
4. 主服务器的 `WebSocketServer` 根据任务 ID 找到并唤醒之前挂起的调用请求
5. 将最终结果返回给 `PluginManager`

### 后续处理

- `PluginManager` 拿到执行结果后，将其注入到 AI 的对话历史中
- 再次调用 AI 模型，完成闭环

### 断开连接与注销

- 如果分布式节点与主服务器的 WebSocket 连接断开
- `WebSocketServer` 会通知 `PluginManager`
- `PluginManager` 会自动注销掉所有属于该断开节点提供的云端插件

## 分布式文件解析系统

这是 VCP 分布式网络架构中的一项革命性功能，它为所有 Agent 提供了无缝、可靠的跨服务器文件访问能力。

### 工作原理

**VCPFileAPI v4.0 超栈追踪版**：

1. **本地优先**：系统首先尝试在主服务器的本地文件系统上直接读取该文件
2. **来源追溯**：如果本地文件不存在，系统会利用内置的 IP 追踪能力，根据发起本次工具调用的 POST 请求来源 IP，精准地识别出这个请求实际上起源于哪个已连接的分布式服务器
3. **实时文件请求**：主服务器的常驻核心服务 `FileFetcherServer` 会通过内部 WebSocket 协议，向已识别的源分布式服务器发送一个 `internal_request_file` 请求
4. **远程执行与返回**：源分布式服务器收到请求后，会读取其本地对应的文件，将其编码为 Base64 字符串，并通过 WebSocket 将数据安全地返回给主服务器
5. **无缝重试**：主服务器的 `PluginManager` 在获取到文件的 Base64 数据后，会自动将原始工具调用中的 `file://` 路径参数替换为一个包含 Base64 数据的 Data URI，然后透明地用这个新参数重新调用同一个插件

### 带来的优势

- **极致的鲁棒性**：彻底摆脱了过去依赖 HTTP 图床或文件镜像作为"补丁"的脆弱方案
- **对 Agent 透明**：整个复杂的远程文件获取和参数替换过程对最终的插件是完全透明的
- **未来的基石**：这个系统是构建更复杂的星型、网状 Agent 网络，实现跨设备协同任务的关键一步

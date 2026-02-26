# ZeroClaw ↔ mem1 Bridge（本地无成本跑通）

## 目标
- ZeroClaw 侧 memory recall 时会额外调用 mem1 `/search` 注入 KB 片段
- ZeroClaw 侧 auto_save 写回时会调用 mem1 `/memories` 记录对话片段
- 可选：启动时对配置的目录调用 mem1 `/mem1/ingest` 自动构建 KB

## 推荐本地配置（避免消耗云端额度）
- mem1 使用 hash embedder（不走网络、无费用）
- pool_router 使用 mock/provider（如果只验证 memory 注入/写回，可以不启动任何 LLM 提供方）

## 1) 启动 mem1（hash embedder）
在仓库根目录执行：

```powershell
.\python\.venv\Scripts\python -m mem1 --host 127.0.0.1 --port 8001 --debug `
  --embedder-provider hash --embedder-model hash-v1
```

然后把 KB 目录 ingest 进去（示例用 docs/mem1_kb_small）：

```powershell
.\python\.venv\Scripts\python -c "import httpx; print(httpx.post('http://127.0.0.1:8001/mem1/ingest', json={'source_type':'file','source_path':'docs/mem1_kb_small','incremental':True}).json())"
```

## 2) 配置 ZeroClaw 使用 mem1 bridge
示例配置见 [dev/onebot-local/config.toml](file:///d:/pama1234/pfp/p-2026-01/zeroclaw/dev/onebot-local/config.toml) 的 `[memory.mem1]`：
- `enabled=true`
- `base_url=http://127.0.0.1:8001`
- `ingest_on_startup=true/false`
- `ingest_paths=[...]`（相对路径按 workspace_dir 拼接；绝对路径会原样使用）

## 3) 验证点
- KB 注入：触发一次需要记忆检索的对话/任务，mem1 会收到 `/search`
- 写回：触发一次 auto_save，mem1 会收到 `/memories`

## 4) 诊断脚本（VertexAI/Gemini 连通性，低成本）
```powershell
.\python\.venv\Scripts\python .\python\scripts\mem1_vertex_embed_diag.py --mode both --calls 1 --embed-timeout 10
```


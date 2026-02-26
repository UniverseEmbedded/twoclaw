# 测试计划

本文档定义mem1记忆系统的自动化测试方案，包括ingest测试、search测试、A/B对比测试和回退测试。

---

## 一、测试目录规范

### 1.1 fixtures目录结构

```
tests/
├── fixtures/
│   └── kb_small/           # 小型测试知识库
│       ├── A.md            # 包含"项目代号=Moonlight"
│       ├── B.md            # 包含"负责人=Alice"
│       ├── C.md            # 包含"接口/v1/chat/completions用于..."
│       └── D.md            # 包含"用户偏好Python"
│
├── test_ingest.py          # ingest测试
├── test_search.py          # search测试
├── test_ab.py              # A/B对比测试
└── test_fallback.py        # 回退测试
```

### 1.2 测试文档内容示例

**A.md**：
```markdown
# 项目信息

项目代号是Moonlight。
项目启动于2025年1月。
```

**B.md**：
```markdown
# 团队信息

负责人是Alice。
技术栈包括Python和Rust。
```

---

## 二、Ingest测试

### 2.1 新增文件测试

**测试步骤**：
1. 启动watcher指向fixtures目录
2. 等待mem1.status显示chunk_count增长
3. 验证chunks/tags/chunk_tag_map表有数据

**验收标准**：
- [ ] chunk_count等于预期值
- [ ] tag_count大于0
- [ ] 每个chunk都有对应的tag映射

### 2.2 修改文件测试

**测试步骤**：
1. 修改fixtures目录中的文件
2. 等待watcher检测变更
3. 验证chunk被更新

**验收标准**：
- [ ] chunk内容更新
- [ ] chunk_hash变化
- [ ] 不产生重复chunk

### 2.3 删除文件测试

**测试步骤**：
1. 删除fixtures目录中的文件
2. 等待watcher检测删除
3. 验证chunk被标记inactive或删除

**验收标准**：
- [ ] chunk不再出现在search结果中
- [ ] （软删除）历史记录保留

### 2.4 幂等测试

**测试步骤**：
1. 重跑ingest（不修改文件）
2. 验证不产生重复数据

**验收标准**：
- [ ] chunk_count不变
- [ ] 无重复chunk_hash
- [ ] 无重复tag

---

## 三、Search测试（Phase A）

### 3.1 基础召回测试

**测试用例**：

| query | 期望结果包含 |
|-------|--------------|
| 项目代号是什么？ | Moonlight |
| 谁是负责人？ | Alice |
| 技术栈有哪些？ | Python, Rust |

**验收标准**：
- [ ] hit@5 > 80%（前5条包含正确答案）
- [ ] 平均延迟 < 500ms

### 3.2 过滤测试

**测试步骤**：
1. 按scope过滤
2. 按source_path过滤
3. 按时间范围过滤

**验收标准**：
- [ ] 过滤结果符合预期
- [ ] 无越权访问其他scope的数据

### 3.3 边界测试

| 场景 | 预期行为 |
|------|----------|
| 空query | 返回空或提示 |
| 超长query | 截断或正常处理 |
| 特殊字符query | 正常处理，无注入风险 |

---

## 四、Search测试（Phase B）

### 4.1 Boost生效测试

**测试步骤**：
1. 准备测试query（较抽象的问法）
2. 运行Phase A和Phase B对比
3. 验证boost后排名提升

**测试用例**：

| query | Phase A排名 | Phase B预期 |
|-------|-------------|-------------|
| 谁lead这个项目？ | 可能不在top5 | top5内包含Alice |
| 用什么语言开发？ | 可能分散 | top5内包含Python/Rust |

**验收标准**：
- [ ] 抽象query的hit@5提升
- [ ] debug_info输出正确（activation、boostFactor等）

### 4.2 A/B对比测试

**测试步骤**：
1. 准备固定测试集（20-50个query）
2. 运行A组（boost off）和B组（boost on）
3. 人工打分 + 计算hit@k

**指标**：

| 指标 | 计算方式 |
|------|----------|
| hit@5 | 前5条包含正确答案的比例 |
| MRR | 正确答案排名倒数的均值 |
| 多样性 | 结果间语义差异度的均值 |

**验收标准**：
- [ ] B组hit@5 >= A组hit@5
- [ ] B组MRR >= A组MRR
- [ ] B组多样性 >= A组多样性 * 0.9

---

## 五、回退测试

### 5.1 tagger不可用

**测试步骤**：
1. 模拟tagger超时或失败
2. 执行search
3. 验证回退到Phase A

**验收标准**：
- [ ] search仍然可用
- [ ] 返回Phase A结果
- [ ] 日志记录回退事件

### 5.2 tag_index不可用

**测试步骤**：
1. 模拟tag_index未就绪
2. 执行search
3. 验证回退到Phase A

**验收标准**：
- [ ] search仍然可用
- [ ] 不尝试boost
- [ ] 日志记录回退事件

### 5.3 mem1不可用

**测试步骤**：
1. 模拟mem1服务不可用
2. ZeroClaw执行对话
3. 验证无记忆模式运行

**验收标准**：
- [ ] ZeroClaw继续运行
- [ ] 不注入记忆
- [ ] 日志记录降级事件

### 5.4 embedding模型不一致

**测试步骤**：
1. 模拟query embedding模型与库内不同
2. 执行search
3. 验证降级行为

**验收标准**：
- [ ] 报警或日志记录
- [ ] 降级为Phase A检索
- [ ] 不执行boost

---

## 六、性能基线测试

### 6.1 Ingest性能

| 指标 | 目标 |
|------|------|
| 单文件ingest延迟 | < 5s（含embedding+tagger） |
| 批量ingest吞吐 | > 100 chunks/min |

### 6.2 Search性能

| 指标 | 目标 |
|------|------|
| Phase A延迟 | < 200ms |
| Phase B延迟 | < 500ms |
| 并发100 QPS | 延迟P99 < 1s |

### 6.3 索引大小

| 指标 | 目标 |
|------|------|
| 1000 chunks索引大小 | < 100MB |
| 10000 chunks索引大小 | < 1GB |

---

## 七、测试执行命令

```bash
# 运行所有测试
pytest tests/

# 运行ingest测试
pytest tests/test_ingest.py -v

# 运行search测试
pytest tests/test_search.py -v

# 运行A/B对比测试
pytest tests/test_ab.py -v --tb=short

# 运行回退测试
pytest tests/test_fallback.py -v

# 生成覆盖率报告
pytest tests/ --cov=mem1 --cov-report=html
```

---

## 八、持续集成

### 8.1 CI流程

```yaml
# .github/workflows/test.yml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -e .[dev]
      - run: pytest tests/ -v --cov=mem1
      - run: pytest tests/test_ab.py -v  # A/B测试
```

### 8.2 测试报告

每次CI运行生成：
- 测试通过率
- 覆盖率报告
- A/B对比结果
- 性能基线对比

---

## 相关文档

- [mem1 API规范](./claw_mem1_api_spec.md)
- [TagMemo算法迁移](./claw_tagmemo_algorithm.md)
- [目录摄取工程方案](./claw_mem1_ingestion.md)
- [分阶段交付计划](./claw_mem1_phases.md)
- [写入策略](./claw_mem1_write_policy.md)

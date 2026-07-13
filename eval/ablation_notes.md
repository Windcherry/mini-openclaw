# 消融实验草稿

> 方法学：固定任务集，每次只改一个因素，对比两组或多组样本轨迹的成功率/Token/步数。
> D4 起用真 Agent 运行轨迹替换构造样本，每组多次运行取均值。

---

## 消融 1：system-prompt 有 vs 无

**日期**：Day 3（样本轨迹）→ Day 4 起用真轨迹复现

**变量**：system-prompt（有 / 无）

**固定**：
- 任务集：read-config、list-dir（共 2 条）
- 模型：deepseek-chat（或 deepseek-v4-flash）
- 样本轨迹：手工构造

**结果**：

| 组别 | 成功率 | 平均 Token | 平均步数 | n |
|------|--------|------------|----------|---|
| 有 system-prompt | 1.00 | 335 | 1.0 | 2 |
| 无 system-prompt | 0.00 | 128 | 1.0 | 2 |

Δ 成功率 = +1.00

**归因**：
- 无 system-prompt 时，agent 不知道 `<tool_call>` 调用约定 → 从不调工具 → 依赖工具完成的任务全部失败。
- 有 system-prompt 时，prompt 中包含了工具列表和使用规范（"需要时用 `<tool_call>` 调工具"），agent 正确发起了工具调用。
- Token 开销差异：有 sys 的 prompt_tokens 显著更高（~330 vs ~120），因为 system 段里塞了工具 schema 说明。这是功能换成本的经典 trade-off。

**局限**：
- 样本量太小（各 2 条），未做统计显著性检验。
- 样本是手工构造的，理想化了"有 sys 必成功、无 sys 必失败"的情形。真实运行中可能有中间地带（无 sys 时偶尔猜对、有 sys 时也可能调错工具）。
- D4 起用真轨迹复现，每组 ≥10 次运行取均值。

---

## 待追加的消融维度

### Day 7：上下文管理
- compaction 有 vs 无（长任务场景）
- observation 截断 有 vs 无

### Day 8：工具扩展
- MCP 工具接入 有 vs 无
- 工具数量对成功率的影响（工具多了，选错的概率是否上升？）

### Day 9：领域知识
- Skill 加载 有 vs 无
- 领域专用 system-prompt vs 通用 system-prompt

### Day 10：安全与鲁棒性
- 权限拦截 有 vs 无（危险命令拒绝率）
- 错误恢复（try/except 回喂）有 vs 无
- FakeBackend vs DeepSeekBackend（模型能力对成功率的影响）

---

## 方法学要点

1. **一次只改一个变量**：如果同时改 system-prompt 和工具集，无法把效果归因到具体因素。
2. **多次运行取均值**：LLM 有随机性（temperature > 0 时），单次结果不可靠。
3. **同一批任务**：两组必须在完全相同的任务集上对比，否则不公平。
4. **记录全部指标**：成功率、平均步数、平均 Token、JSON 合法率——不只盯成功率。
5. **注明局限**：样本量、构造/真实、统计显著性、是否有混杂变量。

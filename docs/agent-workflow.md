# Agent Workflow / agent-workflow

## 流水线

```
START
 ↓ Case Planner      问题 → 审理命题 + 关键概念 + 争议 + 检索词（纯事实查询被拒绝）
 ↓ Research Agent    知乎 query ×3 ∥ 全网 query ×2（变体/立场/反例/数据搜索）
 ↓ Source Ranker     去重 → 域名独立性 → 加权排序 → Top 12
 ↓ Evidence Agent    结构化证据提取（fact/opinion/data/case/prediction/assumption/limitation）
 ↓ [并行] Prosecutor ∥ Defense   基于已有证据构建 Steelman 论证（禁止造证据）
 ↓ Cross Examiner    偷换概念/因果倒置/样本偏差/来源集中/观点当事实/缺反例…（规则+LLM 双轨）
 ↓ Judge Agent       基于证据结构裁决；输出 conditional / insufficient 而非假装确定
 ↓ Verdict Writer    《知识判决书》（不得改变事实含义）
END
```

无 Agent 自由对话、无循环。每个 Agent 一次完成，运行记录写入 `agent_runs`。

## 双轨实现

每个 Agent = LLM 路径 + 启发式路径：

- LLM 路径：LLM Gateway（OpenAI 兼容 /chat/completions，json_mode，鲁棒 JSON 提取：
  剥代码围栏、平衡括号扫描、坏 JSON 自动重试）。Prompt 集中在 `app/prompts/templates.py`。
- 启发式路径：确定性规则（关键词立场判定、证据强度排序、规则化质证检查）。
  LLM 未配置或调用失败时自动降级；`cases.engine_mode` 记录实际模式并在 UI 展示。

## 证据可信度（Evidence Confidence）

```
score = Σ w_i × f_i
权重（app/services/evidence_engine.py::WEIGHTS）:
  relevance .28 | authority .18 | community .14 | recency .12
  cross_validation .13 | directness .10 | source_independence .05
证据层 = 来源分 × 类型系数(fact 1.0 … assumption 0.4)
        ± 独立佐证 +0.08×n / 相反证据 −0.1×n
```

- UI 术语为「证据可信度 / 判决置信度」，不宣称 Truth Score
- 社区信号取对数尺度（唯点赞论防护）；来源集中度参与独立性扣分

## 数据诚实性

- 每条证据必须挂 `source_id`；论点引用 `evidence_ids`
- Judge 事实性结论关联 evidence_ids；无法追溯的判断写入 `unknowns` 并标注「AI 推断，待验证」
- 观点/预测类证据在质证阶段会被标记 `opinion_as_fact`
- 五层 Source→Evidence→Claim→Argument→Verdict 是带 ID 的引用链，不等于经过独立事实核查；无来源、缺口、模式降级或裁判证据冲突时必须保留不确定性。前端的核心链路/全量链路只是可视化取舍，不保证每条路径都有完整引用。

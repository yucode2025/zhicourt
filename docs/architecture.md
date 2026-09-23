# 架构 / Architecture

## 总体

ZhiCourt = Vue 3 SPA + FastAPI 单体（线程池工作流）+ MariaDB + Redis（可选）+ Nginx。
为 4C4G / 5Mbps 轻量实例设计：单 uvicorn worker、无重型中间件、全部外部计算走 API。

```
┌──────────────┐    ┌─────────────────────────────┐
│  Vue 3 SPA   │    │        Nginx (80/443)       │
│  6 页面      │◄───┤  /  → dist 静态 + SPA fallback│
└──────────────┘    │  /api → 127.0.0.1:8000       │
                    └──────────────┬──────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │  FastAPI (systemd, 1 worker) │
                    │  api/  REST + SSE            │
                    │  workflows/  案件流水线线程池  │
                    │  agents/  9 个 Agent          │
                    │  services/ Provider 层        │
                    └───┬──────────┬──────────┬────┘
                        ▼          ▼          ▼
                    MariaDB      Redis      外部 API
                   (25 业务表) (缓存/限流) 知乎/全网/LLM
```

## 关键决策

| 决策 | 理由 |
|---|---|
| 工作流在后台线程 + 事件落库 | 无需任务队列；刷新页面可恢复进度（事件存 `cases.progress_events` JSON） |
| SSE 按事件序号轮询 + 流式双模 | Nginx 关缓冲即可用；断线自动降级轮询；多 worker 兼容 |
| LLM 失败 → 启发式引擎 | 第三方 LLM 波动不阻断演示；输出标注引擎模式 |
| Mock Provider 与 Real Provider 严格分离 | 数据诚实性：演示内容必须可见地标注 |
| 证据评分是确定性加权公式 | 可解释、可测试、可调权重；不做"LLM 自己打分" |
| 匿名 cookie 会话 | 随机 token，仅哈希落库；匿名与注册用户严格区分 |

## 并发控制

- `ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CASES=3)`，超出案件排队（前端显示"排队等待开庭"）。
- 每案件外部调用预算：知乎搜索 ≤3 query、全网 ≤2 query（缓存命中不计数）。
- Source→Evidence→Claim→Argument→Verdict 是五层**引用关系**，不保证所有节点完整、引用无误或证据充分；图谱可筛选/折叠，缺失引用与降级结论须显式标注，不应把链路视为事实证明。

## 失败与降级矩阵

| 故障 | 行为 |
|---|---|
| 知乎搜索失败 | 提示"知乎来源暂时不可用"，继续用其余来源 |
| 全网搜索失败 | 继续用知乎内容 |
| 全部搜索失败 | 案件 failed，可重新开庭 |
| LLM 超时/429/非法 JSON | 该 Agent 降级启发式，workflow 继续 |
| Redis 不可用 | 进程内 TTL 缓存 |
| 单 Agent 异常 | 记录 agent_runs.error，案件 failed 可重试 |

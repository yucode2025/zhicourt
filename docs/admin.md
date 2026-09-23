# 管理后台 / admin

入口 `/admin`（仅 ADMIN 角色；API 层 `/api/admin/**` 全部 `require_admin` 强制校验）。

## 功能

| 页面 | 路径 | 内容 |
|---|---|---|
| 运营概览 | `/admin` | 今日/总用户与案件、进行中/失败/完成、今日质询、7 天趋势图（ECharts）、缓存命中率、最近失败案件 |
| 用户管理 | `/admin/users` | 服务端搜索 + 分页（20/50/100）、禁用/启用（二次确认、不能禁用自己）、角色调整（审计） |
| 案件管理 | `/admin/cases` | 状态筛选/搜索/分页、来源与证据数量、engine mode、失败案件重试（二次确认，仅失败案件可重试） |
| API 用量 | `/admin/api-usage` | 各 Provider 今日调用/限额余量/缓存命中/失败，80% 黄色 90% 红色预警；**统计全部来自本地 api_usage 表，查询本身零官方消耗** |
| Provider 状态 | `/admin/providers` | 每个能力 REAL / MOCK / FALLBACK / DISABLED / NOT_USED 一目了然（严禁 Mock 隐藏）+ Redis/DB 健康 + 数据完整性检查（孤立证据/断链引用/Mock 未标记） |
| 审计日志 | `/admin/audit` | 管理员敏感操作：USER_DISABLED / USER_ENABLED / USER_ROLE_CHANGED / CASE_RETRIED / SETTING_CHANGED / CASE_VIEWED_PRIVATE |
| 系统设置 | `/admin/settings` | 业务准入 `usage_access_policy`（guest / authenticated / zhihu）、Feature Flags（allow_guest_cases / enable_hot_cases / enable_web_search / enable_mock_provider…）及运行参数；Secret 只显示已配置/未配置 |

## 访问策略与 Feature Flags 生效点

- `usage_access_policy=guest` → 普通业务允许游客；`authenticated` → 只允许已登录账号；`zhihu` → 只允许知乎登录或已绑定知乎的账号。
- 登录/注册/OAuth、个人中心绑定路径、ADMIN 后台、公开分享和健康检查为必要豁免，但仍执行登录、ADMIN、资源可见性等各自权限校验。
- `allow_guest_cases=false` → 仅在 `guest` 档额外阻止游客创建案件；它不能放宽 `authenticated`/`zhihu`。
- `enable_hot_cases=false` → `/api/hot` 返回已关闭提示。
- `enable_web_search=false` / `enable_mock_provider` → Provider 状态页显示 DISABLED / 控制演示模式。

收紧策略前先在另一个浏览器会话验证管理员登录和 OAuth；先用 `authenticated` 观察，再切 `zhihu`。误配置时从仍有效的 ADMIN 豁免入口恢复；若 UI 不可用，按部署手册在维护窗口内修正数据库设置并保留审计记录。完整组合矩阵见 [authentication.md](authentication.md)。

## 失败任务恢复

- 案件失败后管理员可「重新开庭」：清理旧产出（sources/evidence/arguments/verdict/agent_runs）后重新入队
- 重试会消耗 API 配额，因此有二次确认；**已完成案件不可重试**（防止额度浪费）
- 完整的"从失败步骤恢复"列为赛后增强（当前实现为安全的整案重试）

### 执行 fencing（2026-09 起）

- 每次启动/重试原子递增 `run_generation` 并发放随机租约；worker 写任何数据前必须以
  `id + generation + lease_token + running` 条件验证执行权，旧线程的迟写入会被拒绝。
- 服务重启时自动恢复：租约过期的 running 与遗留 queued 被条件认领，已完成阶段标为
  reused、Judge 重跑（同代恢复）；租约新鲜的 running 属于其他存活进程，不处理。
- 最终判决在单个事务内原子提交（Verdict + writer run + engine_mode + 状态 + done 事件），
  不再出现"已有 Verdict 但案件 running/failed"或"queued 永久滞留"的中间态。
- `case_run_attempts` 记录每代执行的触发者、恢复阶段与失败原因，供运营排查。
- 阶段级 `from_stage` 重试目前仅用于**内部中断恢复**；向用户开放前仍需 checkpoint
  有效性校验、服务端 retry_options、expected_generation 幂等与 UI 交接说明。

## 数据完整性检查（Integrity Check）

`GET /api/admin/integrity` 检查：
1. Evidence 无有效来源 → FAIL
2. Argument/Verdict 引用不存在的 Evidence → WARNING
3. 非演示来源 URL 异常 → WARNING
4. `demo://` 来源未标记 is_demo → FAIL

结果：PASS / WARNING / FAIL。

## 隐私

- 后台不展示用户密码（只有 hash）；Secret 只显示状态
- 管理员查看私有案件详情 → 写入 `CASE_VIEWED_PRIVATE` 审计
- 审计日志不记录密码 / Key

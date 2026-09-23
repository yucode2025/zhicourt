# 数据库 / database

MariaDB（兼容 MySQL 8），utf8mb4。迁移：`cd backend && python -m alembic upgrade head`。
`scripts/init_db.py` 包含演示种子，仅在明确需要的开发/演示环境执行；生产安装与更新只运行 Alembic upgrade，不自动种子或 downgrade。

## 表结构（27 张业务表，另有 alembic_version）

```
users            用户/游客帐户；新案件默认私有，公开需属主主动发布
anonymous_sessions  游客随机会话 token 哈希（非用户 ID cookie）
zhihu_oauth_states  OAuth 一次性 state 哈希、过期/消费状态及 login/link 发起上下文
zhihu_oauth_accounts  本站用户与知乎 UID 的唯一绑定及基础资料快照；不持久化 OAuth Token
cases            案件：命题/状态/计划/进度事件(JSON)/engine_mode/is_demo
                 执行 fencing：run_generation、lease_token/owner/expiry/heartbeat、
                 resume_from_stage、last_completed_stage、failure_stage/kind/code、retry_count
  ├─ sources     来源：知乎|web，互动信号，rank_score，independence_score，is_demo
  │    └─ evidence  证据：claim/stance/type/summary/quoted_fragment/strength/limitations
  ├─ claims      核心主张（side, evidence_ids）
  ├─ arguments   控辩论证（side, evidence_ids, strength）
  ├─ agent_runs  每个 Agent 的运行记录（mode/status/耗时/token；run_generation/stage/
  │              attempt/reused_from_generation/error_code/error_kind 支持代次审计）
  ├─ cross_examinations  质证发现（issue_type/severity/related_evidence_ids）
  ├─ user_questions      用户质询（target/challenge_type/response）
  └─ verdict     判决书（1:1）：结论/置信度/共识/分歧/最强证据/缺口/未知/判决改变器/推荐学习
case_run_attempts  每代执行的审计记录（trigger、actor、resume/复用阶段、failure、时间戳）
api_usage        Provider 用量（provider, date, calls, cache_hits, failures）
user_sessions    服务端会话（rotation，14 天 TTL）
favorites        用户收藏的案件/判决书
admin_audit_logs 管理员审计日志（动作白名单）
system_settings  运行配置与 Feature Flags（含三档 usage_access_policy、allow_guest_cases；Provider Secret 以 enc:v1: Fernet 密文存储）
claim_evidence / argument_evidence / argument_claim  主张与论证的显式引用边
verdict_strongest_evidence / verdict_counter_evidence  判决的正反证据引用边
cross_exam_evidence / user_question_evidence  质证与用户质询的证据引用边
```

## 关系

```
Case 1─N Sources 1─N Evidence
Case 1─N Arguments N─N Evidence（显式关系表；JSON ID 列保留为兼容快照）
Claims N─N Evidence；Arguments N─N Claims
Case 1─1 Verdict
```

## 约定

- ID：`{prefix}_{uuid20}`（case_/src_/ev_/arg_/run_/cx_/uq_/vd_/usr_）
- 时间：UTC naive datetime
- JSON 列：progress_events / plan / evidence_ids / limitations 等（JSON 类型）
- 索引：cases.user_id / cases.status / cases.created_at / sources.case_id / evidence.case_id / evidence.source_id / api_usage.usage_date

## 执行 fencing 约定

- 每次启动/重试原子递增 `cases.run_generation` 并发放随机 `lease_token`；worker 必须以
  `id + run_generation + lease_token + status='running'` 条件更新验证执行权后才能写任何数据
  （进度、AgentRun、阶段产物、失败标记、最终判决）。旧代次/旧租约的写入会被静默拒绝。
- 最终判决（Verdict + writer run + engine_mode + verdict_ready + finished_at + done 事件 +
  lease 清理 + attempt 终态）在单个事务内原子提交。
- 服务重启时：租约已过期的 running 与遗留 queued 由 `recover_cases` 条件认领并同代恢复
  （已完成阶段标为 reused，judge 必然重跑）；租约新鲜的 running 属于其他存活进程，不处理。
- `case_run_attempts` 记录每代执行的 trigger/actor/resume/失败原因，是整案与阶段恢复的
  规范审计来源；`admin_audit_logs` 只镜像管理员行为。
- 阶段级恢复目前仅用于**内部中断恢复**（重启后同代续跑），不向用户开放 `from_stage` API；
  开放前需要 checkpoint 有效性校验、retry_options 服务端计算、expected_generation 幂等与
  完整 UI 交接说明（保留/清理哪些产物、公开链接失效）。

## 认证数据约束

- `system_settings.usage_access_policy` 只接受 `guest`、`authenticated`、`zhihu`；缺失/类型错误/未知值必须回退到代码中的安全默认值，而不是自动放宽。它与 `allow_guest_cases` 的组合语义见 [authentication.md](authentication.md)。
- `zhihu_oauth_states` 只保存 state 哈希，短 TTL、单次原子消费；login/link 用途和发起账号/会话必须一起校验。可以定期删除过期或已消费记录。
- `zhihu_oauth_accounts.user_id` 与 `zhihu_uid` 都唯一，形成一对一绑定；禁止 upsert 覆盖为另一 UID。昵称/头像仅是资料快照，不是授权凭证。
- 不保存 OAuth access token。若从旧 schema 升级仍存在 `access_token`、`token_expires_at` 兼容列，迁移/上线检查必须确认历史值已清空；备份、导出和日志同样不得含 token。Provider Secret 与 OAuth App Key 属于另一类服务端 Secret，仍按各自外置/加密规则保管。

## 迁移与恢复

生产只使用 `python -m alembic upgrade head`。更新必须在维护窗口停写并先校验备份；不可逆迁移的 downgrade 会显式拒绝，任何结构不兼容的回滚均须从备份**在隔离环境先演练恢复**，见 [deployment.md](deployment.md)。不要在生产直接 DROP DATABASE 或向已有升级后表混入旧 SQL。恢复旧备份后要再次核对 `usage_access_policy`、OAuth 一对一唯一约束、空 token 约束和外置 `SECRET_ENCRYPTION_KEY` 是否与该备份匹配。

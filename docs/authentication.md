# 认证与用户体系 / authentication

## 会话与凭证边界

- **本站认证**：服务端 Session + HttpOnly Cookie（`zhicourt_session`，SameSite=Lax，生产 Secure），不使用 localStorage JWT；`user_sessions` 保存会话哈希/标识并在 14 天后过期。
- **密码**：bcrypt（cost 10）；数据库只保存 `password_hash`，禁止明文。
- **Session Rotation**：登录、OAuth 登录/绑定完成和修改密码都会轮换当前浏览器会话；其他设备既有会话不会自动撤销。
- **失败保护**：密码登录按 IP 限流且统一返回“用户名或密码错误”，不泄露用户名存在性。
- **游客迁移**：匿名身份使用独立 HttpOnly Cookie；注册或首次登录后将该浏览器的游客案件与收藏幂等迁移到正式账号。
- **知乎 OAuth Token 不持久化**：access token 只在后端回调请求内短暂用于读取知乎基础资料，用后丢弃；不得进入数据库、Cookie、浏览器存储、URL、日志或审计详情。若兼容旧版数据库列，发布前必须清空并确认备份不含 token；不得把该列当作可复用授权。

## 业务访问策略 `usage_access_policy`

管理员可在系统设置中选择三档。策略约束普通业务页面/API，不替代资源属主、ADMIN、公开/私有等后端权限校验。

| 值 | 普通业务能力准入 | 典型用途 |
|---|---|---|
| `guest` | 游客、本站账号、知乎账号均可进入 | 开放试用/演示 |
| `authenticated` | 必须登录本站账号或知乎账号 | 需要可追溯用户身份 |
| `zhihu` | 必须是知乎登录账号，或已绑定知乎的本站账号 | 知乎活动或限定运营 |

策略豁免仅用于避免锁死必要入口：

- 登录、注册、OAuth 发起/回调及公开 capabilities 查询；
- 已登录用户的个人中心（`zhihu` 档下，本站账号必须能进入这里完成绑定）；
- 管理后台（仍必须通过后端 ADMIN 校验）；
- `/share/{public_id}` 公开只读分享；
- 健康/就绪等运维端点。

豁免不等于匿名可写：个人中心仍要求登录，管理后台仍要求 ADMIN，私有案件仍只对属主/管理员可见，公开分享仍只返回已发布案件的脱敏只读结构。

### 与 `allow_guest_cases` 的组合

| `usage_access_policy` | `allow_guest_cases=true` | `allow_guest_cases=false` |
|---|---|---|
| `guest` | 游客可进入业务并创建案件 | 游客可进入允许的只读/规划体验，但创建案件返回 403；正式账号可创建 |
| `authenticated` | 未登录者已被访问策略拦截；该 Flag 对其无额外放宽 | 同左；登录账号可创建 |
| `zhihu` | 未满足知乎身份者已被访问策略拦截；该 Flag 对其无额外放宽 | 同左；知乎身份账号可创建 |

因此收紧顺序建议为：先确认 OAuth/本站登录可用，再将策略从 `guest` 改为 `authenticated`，最后才改为 `zhihu`。不要把 `allow_guest_cases=true` 误认为能绕过后两档策略。

## 角色与可见性

| 角色 | 能力 |
|---|---|
| 游客（无账号） | 在策略允许时浏览业务、创建案件（另受 `allow_guest_cases` 控制） |
| USER | 案件归属、收藏、学习档案、设置；只访问自己的私有数据 |
| ADMIN | USER 能力 + `/api/admin/**`；后端 `require_admin` 强制校验，前端 guard 只是体验优化 |

- 新案件默认 `is_public=False`；属主完成审理后可主动发布只读链接 `/share/{public_id}`。
- 私有案件只允许属主与管理员读取；其他身份返回 404，避免泄露存在性。
- 管理员读取他人私有案件会写 `CASE_VIEWED_PRIVATE` 审计；普通写接口不能借管理员身份绕过属主校验。

## 知乎 OAuth：登录与绑定

回调前端路由固定为 `/login/zhihu`。生产登记 URI 必须是规范 HTTPS 完整地址，例如 `https://court.example.com/login/zhihu`，并与 `ZHIHU_OAUTH_REDIRECT_URI` 逐字符一致（协议、主机、端口、路径及是否带尾斜杠都不可漂移）。该 Origin 还必须在 `CORS_ORIGINS` 中。

- **login 模式**：未登录用户从登录页发起。已有知乎 UID 绑定则登录对应用户，否则创建新的知乎身份用户。
- **link 模式**：已登录的本站密码账号在个人中心发起；敏感操作需要重新验证当前密码，state 同时绑定发起模式、用户与浏览器会话。回调只允许完成该账号的首次知乎绑定。
- **禁止换绑/抢绑**：一个知乎 UID 只能绑定一个本站用户；本站用户一旦绑定知乎 UID，不允许通过再次授权改绑其他 UID；发现任一侧已绑定其他主体均返回冲突，不自动合并账号。需要处置错误绑定时应走经审计的人工支持流程，而不是直接改生产库。
- App Key 只用于后端换取 token，不进入授权 URL或前端。前端只提交一次性授权码和 state 给同源后端。

OAuth state 使用高熵随机值：浏览器持有 HttpOnly、SameSite=Lax、生产 Secure Cookie，数据库只保存 state 的 SHA-256 哈希、过期时间、用途/发起主体和消费状态。有效期 10 分钟并原子单次消费；Cookie 不匹配、用途/会话/用户不匹配、过期和重放都拒绝。无论成功或失败，授权码和 state 都不得写日志，回调页读取查询参数后立即从地址栏移除。

## 主要 API

```text
POST /api/auth/register          注册并迁移游客数据
POST /api/auth/login             密码登录（rotation + 限流）
POST /api/auth/logout            登出并清理会话
GET  /api/capabilities           公开返回访问策略及 OAuth 是否可用，不返回凭证
GET  /api/auth/zhihu/authorize  生成 login 授权 URL 和一次性 state（未登录）
POST /api/auth/zhihu/link/authorize 复核本站密码后生成 link 授权 URL/state
POST /api/auth/zhihu/callback    校验 state 上下文，完成登录或首次绑定
GET  /api/auth/me                当前用户（绑定状态由账号类型/资料能力返回）
PATCH /api/auth/me               修改昵称/预设头像
POST /api/auth/me/password       校验原密码并修改（rotation）
GET  /api/auth/me/overview       用户中心统计
GET  /api/auth/me/verdicts       最近判决/收藏
GET  /api/auth/me/learning       学习档案
POST/DELETE /api/cases/{id}/favorite
GET  /api/share/{public_id}      公开只读分享，无私人字段
```

具体 HTTP method 以当前 OpenAPI 和前端调用契约为准；滚动发布 OAuth method/字段变更时，前后端必须作为同一 release 原子切换。

## 管理员创建

禁止硬编码默认管理员。生产首次部署可通过外置环境临时提供：

```bash
ADMIN_USERNAME=admin ADMIN_PASSWORD='强密码' python scripts/create_admin.py
```

命令幂等；创建后从环境中移除 `ADMIN_PASSWORD`，登录后修改密码，并确认 ADMIN 在收紧 `usage_access_policy` 后仍可进入管理后台。

# API 集成 / api-integration

> 依据：官方 skill 包 `zhihu-cli 0.5.3`（references/http-api.md、hackathon-content-api.md），2026-09 官方文档核验。

## 已接入的官方能力

| 能力 | 官方端点 | 方法 | 状态 |
|---|---|---|---|
| 知乎搜索 | `GET /api/v1/content/zhihu_search?Query=&Count=1..10` | GET | ✅ REAL（填 Secret 即启用） |
| 全网搜索 | `GET /api/v1/content/global_search?Query=&Count=1..20&SearchDB=all` | GET | ✅ REAL |
| 知乎热榜 | `GET /api/v1/content/hot_list?Limit=1..30` | GET | ✅ REAL |
| 直答 Agent | `POST /v1/chat/completions`（OpenAI 兼容，model=zhida-fast-1p5 等） | POST | ✅ REAL（Flag `enable_direct_answer` 控制，默认关） |
| 知识库 RAG | `POST /api/v1/knowledge/search` | POST | Provider 方法已实现（需有效 KnowledgeBaseIDs 与启用配置；不能把公开黑客松知识列表当作私有知识库） |
| 官方额度 | `GET /api/v1/quota?APIIDs=...`（查询不消耗额度） | GET | ✅ 管理后台实时展示 |
| 知乎 OAuth | `authorize` → `access_token` → `user` | GET/POST | ✅ 登录、首次绑定与游客案件迁移；access token 仅在回调内存中使用，不落库（填 App ID/App Key 后启用） |
| 黑客松故事/知识 | `GET api.zhihu.com/km-indep-home/hackathon/v2/story|knowledge/list`（**无需鉴权**） | GET | ✅ 首页“黑客松选题库”，后端 `/api/hackathon/content` |

## 鉴权（官方 Bearer 规范）

```
Authorization: Bearer <Access Secret>
X-Request-Timestamp: <秒级 Unix 时间戳>
Content-Type: application/json
```

Access Secret 获取：developer.zhihu.com 个人中心。它用于知乎搜索等官方 API，不等同于 OAuth App Key；OAuth App Key 只在服务端换取一次性授权 token，token 用于读取基础资料后立即丢弃，不进入数据库、Cookie、URL 或日志。

## 响应规范

外层 `{Code, Message, Data}`。Code：0 成功 / 10001 参数 / 20001 鉴权 / 30001 频率 / 90001 内部。
Item 字段（大写）：`Title / ContentType / ContentID / ContentText / Url（带溯源 UTM） / CommentCount / VoteUpCount / AuthorName / EditTime（秒级时间戳） / CommentInfoList[{Content}]（精选评论） / AuthorityLevel("1"-"4") / RankingScore`。

Provider 层映射（`app/services/zhihu/client.py::_map_item`）：
- `EditTime` → `published_at`（UTC 日期）
- `CommentInfoList` 精选评论拼入 `summary`（保留原文，供 Evidence Agent 引用）
- `AuthorityLevel` → `Source.authority_level`，并参与 Evidence Engine 权威度评分；缺失时再使用域名/来源类型启发式。

## 如何启用真实模式

**方式一（推荐）**：管理后台 `/admin/providers` → 「官方 API 接入配置」→ 填 Access Secret → 保存即生效（无需重启）。Base URL 与路径留空即使用官方默认。
**方式二**：`.env` 设 `ZHIHU_ACCESS_SECRET` + `WEB_SEARCH_API_KEY` 后启动（旧变量 `ZHIHU_API_KEY` 仍兼容）。

优先级：后台 DB 配置 > `.env` > 官方默认路径；**真实模式需有效 Secret**。直答默认关闭且仅辅助背景，不充当证据；Knowledge 搜索需要正确知识库 ID，当前不应宣称审理流程已自动检索知识库。后台修改 Base URL/origin 后必须重新填写 Secret；路径依官方规范，不允许以未知路径冒充官方服务。

缺 Key 时只有启用 `enable_mock_provider` 才可使用 Mock；Mock `demo://` 与 `is_demo` 均需标注，禁用 Mock 后不能假装已有真实数据。真实出站 URL 检查拒绝内网/保留地址、不跟随重定向。配置变更写审计日志，Key 仅显示状态/脱敏，DB 中为 `enc:v1:` Fernet 密文；主密钥必须外置并与数据库一并备份。

黑客松故事/知识列表是活动专用匿名接口，与 Access Secret、OAuth 和私有 KnowledgeBase 相互独立。服务端仅访问代码内固定的 `https://api.zhihu.com` 路径，限制响应大小、字段长度和条目数，缓存 30 分钟；前端将其明确标为选题线索，不直接当作庭审证据。固定官方域名兼容透明代理的 Fake-IP，自定义 Provider 仍执行完整 DNS/私网 SSRF 检查。

## 缓存与限额

| Key | TTL | 官方限额（自然日） |
|---|---|---|
| `search:zhihu_search:{hash}` | 6h | 见 `/api/v1/quota` 实时 |
| `search:web_search:{hash}` | 6h | 同上 |
| `zhihu:hot:v2:*` | 25min | 同上 |
| `zhihu:hackathon:v2:{kind}` | 30min | 活动接口未公布额度，主动缓存减压 |

- 管理后台「官方额度」卡片实时展示 TotalQuota/Used/Remaining（quota 查询本身不消耗额度）
- 接近 80% 限额自动跳过实时搜索，仅用缓存（`api_usage.near_limit`）
- 每案件预算：知乎搜索 ≤3 query、全网 ≤2 query

## 失败降级（不变）

知乎搜索失败 → 提示后继续用其余来源；全网失败 → 继续用知乎；全部失败 → 案件 failed 可重试；Code 20001/30001 在后台连通测试中给出明确提示。

## Mock 诚实性（保留）

未配置 Secret 时仍为 Mock 演示模式，界面标注「演示数据」。配置后 Provider 状态页显示 REAL。

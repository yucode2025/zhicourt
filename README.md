# ZhiCourt · 知识法庭

> **让观点接受证据审理。** 不是替你得到答案，而是让你看清答案为什么成立。

ZhiCourt 是知乎黑客松「知识炼金场：学习工具与知识生产」赛道的参赛作品。它把真实争议变成一场结构化的“知识审理”：

```
Source → Evidence → Claim → Argument → Verdict   （每一步可追溯）
```

系统检索知乎真实讨论与全网公开资料，提取结构化证据（明确区分事实 / 观点 / 数据 / 预测 / 未知），
由控辩双方基于证据构建最强论证（Steelman 而非 Strawman），质证方检查逻辑漏洞，
最终 Judge 基于证据结构给出**条件性判断**与《知识判决书》。

## 主要功能

- **创建案件**：AI 将模糊问题重写为可审理的明确命题（纯事实查询会被礼貌拒绝）
- **黑客松选题库**：无需密钥读取官方“知乎知识/知乎故事”活动列表，一键转成待审命题（活动内容不直接冒充证据）
- **自动取证**：并行检索知乎真实讨论与全网公开资料，去重、按可信度排序（不是唯点赞论）
- **证据引擎**：可解释的 Evidence Confidence 加权评分（相关性/权威度/社区/时效/交叉验证/独立性）
- **庭审现场**：控辩审三栏 + Agent 实时进度（SSE）+ 用户质询（自动分类：事实/逻辑/证据/定义/范围/来源）
- **证据图谱**：桌面 Vue Flow 画布 + 移动端链路列表，可视化 Source→Evidence→Claim→Argument→Verdict 显式追溯链
- **知识判决书**：结论、置信度、共识、分歧、最强证据/反例、证据缺口、可改变判决的新证据、推荐学习问题
- **用户体系**：注册/登录（bcrypt + HttpOnly Session + Rotation）与知乎 OAuth、游客数据自动迁移、用户中心（概览/案件/收藏/质询/学习档案/设置）
- **判决书分享**：新案件默认私有；仅属主主动发布后可通过只读 `/share/{public_id}` 分享，也可撤回/删除
- **管理后台** `/admin`：运营概览（7 天趋势）、用户管理、案件管理（失败重试）、API 用量监控（限额预警）、Provider 状态（REAL/MOCK 一目了然）、Feature Flags、审计日志、数据完整性检查
- **历史案件**：匿名会话保存，随时继续

## 技术栈

| 层 | 技术 |
|---|---|
| Frontend | Vue 3 + TypeScript(strict) + Vite + Pinia + Vue Router + Naive UI + Vue Flow |
| Backend | Python + FastAPI + SQLAlchemy 2.0 + Pydantic + httpx |
| Database | MariaDB / MySQL（Alembic 迁移） |
| Cache | Redis（不可用时自动降级内存缓存） |
| Streaming | SSE |
| 生产 | Nginx + systemd（Ubuntu 22.04，4C4G 轻量实例友好） |

## 架构

```
浏览器 (Vue 3 SPA)
   │  /            静态资源 (Nginx, gzip, 长缓存)
   │  /api/*       反代 127.0.0.1:8000 (SSE 不缓冲)
   ▼
FastAPI (uvicorn, systemd)
   ├─ api/          REST + SSE 端点，统一 Error Schema
   ├─ workflows/    案件流水线（线程池，全局并发 3~5，超出排队）
   │    Planner → Research → SourceRanker → Evidence
   │    → (Prosecutor ∥ Defense) → CrossExaminer → Judge → VerdictWriter
   ├─ agents/       各 Agent：LLM 优先，失败降级启发式引擎
   ├─ services/
   │    ├─ zhihu/      知乎官方 API Adapter（Real / Mock 严格分离）
   │    ├─ web_search/ 全网搜索 Provider
   │    ├─ llm/        LLM Gateway（OpenAI 兼容，重试/JSON 修复/用量统计）
   │    └─ cache/      Redis + 内存降级
   ├─ evidence_engine  可解释加权评分（权重集中配置）
   └─ models/          MariaDB 25 张业务表（含匿名/OAuth 会话与显式引用关系）
```

## 目录结构

```
zhicourt/
├─ frontend/          Vue 3 SPA（6 页面：首页/创建/庭审/图谱/判决书/历史）
├─ backend/
│  ├─ app/            FastAPI 应用
│  ├─ migrations/     Alembic
│  ├─ scripts/        init_db.py（仅开发环境按需创建演示种子）
│  └─ tests/          pytest 套件（含安全回归）
├─ deploy/            install.sh / deploy.sh / render.py / nginx.conf / zhicourt.service
├─ docs/              架构 / API 集成 / Agent 工作流 / 数据库 / 部署 / 演示脚本
└─ LICENSE            Apache-2.0 开源许可证
```

## 环境要求

- Python 3.10+ / Node.js 20 / MariaDB 或 MySQL 8 / Redis（可选）/ Nginx（生产）

## 快速启动（开发）

### 1. 数据库

```sql
CREATE DATABASE zhicourt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'zhicourt'@'localhost' IDENTIFIED BY '<unique-local-password>';
CREATE USER 'zhicourt'@'127.0.0.1' IDENTIFIED BY '<unique-local-password>';
GRANT ALL PRIVILEGES ON zhicourt.* TO 'zhicourt'@'localhost';
GRANT ALL PRIVILEGES ON zhicourt.* TO 'zhicourt'@'127.0.0.1';
```

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # Linux
cp ../.env.example .env                            # 设置 DB_PASSWORD 为上面创建用户时所选的独立密码
.venv/Scripts/python -m alembic upgrade head       # 迁移
.venv/Scripts/python scripts/init_db.py            # 建表 + 演示案件种子
# （可选）创建管理员：
ADMIN_USERNAME=admin ADMIN_PASSWORD=强密码 .venv/Scripts/python scripts/create_admin.py
.venv/Scripts/python -m uvicorn app.main:app --port 8000 --reload
```

### 3. Frontend

```bash
cd frontend
npm ci
npm run dev          # http://localhost:5173（/api 已代理到 8000）
```

## .env 配置说明

见 [.env.example](.env.example)。关键项：

- **知乎官方 API**：`ZHIHU_API_BASE_URL` + `ZHIHU_ACCESS_SECRET`（旧变量 `ZHIHU_API_KEY` 仍兼容）；直答走 `/v1/chat/completions`（默认关闭），Knowledge 搜索走 `/api/v1/knowledge/search`（需真实 `KnowledgeBaseIDs`，不能把黑客松公开知识列表混同为私有知识库）。路径只在官方协议确认后覆盖。未配置凭据且允许 Mock 时进入明确标注的演示模式，`demo://` 非真实可点击链接；关闭 Mock 而无凭据时来源不可用，不伪造真实资料。
- **LLM**：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（OpenAI 兼容接口）。
  未配置时 Agent 走**启发式引擎**（界面标注「启发式演示模式」），完整流程仍可运行。
- **全网搜索**：`WEB_SEARCH_BASE_URL` + `WEB_SEARCH_API_KEY`。
- **Secret 存储**：后台 Provider Secret 在 DB 以 `enc:v1:` Fernet 密文保存；必须连同外置 `SECRET_ENCRYPTION_KEY` 主密钥备份。五层 Source→Evidence→Claim→Argument→Verdict 是可追溯**引用结构**，不是独立事实认证；缺失/冲突证据、LLM 降级时结论仍有限制。

### Mock 与真实数据隔离

- `MockZhihuProvider` / `MockWebSearchProvider` 只在未配置 Key 时启用；来源 URL 使用 `demo://` 协议（不可点击），作者一律「演示用户」。
- 真实 Provider（`RealZhihuProvider`）带超时、指数退避重试（≤2 次）、响应大小限制、每日限额保护。
- 界面任何演示内容都渲染黄色「◇ 演示数据」徽标。

## 缓存与 API 限额

| Key | TTL | 说明 |
|---|---|---|
| `zhihu:hot:v2:*` | 25 min | 热榜（按 Provider 配置隔离缓存） |
| `zhihu:hackathon:v2:{kind}` | 30 min | 官方活动故事/知识选题（无需凭据） |
| `search:zhihu_search:{hash}` | 6 h | 知乎搜索 |
| `search:web_search:{hash}` | 6 h | 全网搜索 |

`GET /api/usage` 可查看各 Provider 当日调用 / 缓存命中 / 失败 / 限额余量。

## 生产部署（Ubuntu 22.04）

生产部署强制 HTTPS，域名需先解析到服务器并放行 80/443；Node.js 20 必须预先由可信软件源安装（脚本不运行远程 NodeSource shell）：

```bash
sudo APP_DIR=/opt/zhicourt \
  DOMAIN=court.example.com \
  CERTBOT_EMAIL=admin@example.com \
  bash deploy/install.sh

# 之后更新（APP_DIR 必须与首次安装一致）：
sudo APP_DIR=/opt/zhicourt bash deploy/deploy.sh
```

脚本将 root 拥有的只读版本写入 `APP_DIR/releases/`，原子切换 `current`；配置和 Fernet 主密钥在 `/etc/zhicourt/zhicourt.env`，安装时会从规范 Origin 写入 `CORS_ORIGINS` 与 `TRUSTED_HOSTS`。更新在维护前实际导入候选 release 的后端配置做 preflight；数据库备份与共享资源在 `/var/lib/zhicourt/`。更新须安排停写维护窗口；不自动执行 schema downgrade，兼容性未核对不可切回旧版。仅临时演示可显式传入 `ALLOW_INSECURE_HTTP=true PUBLIC_HOST=服务器IP`，不可承载生产数据。备份恢复与维护流程见 [docs/deployment.md](docs/deployment.md)。

## 开发模式 / 测试 / 构建

```bash
# 后端测试、覆盖率、静态检查（SQLite 内存库，不依赖真实 Provider）
cd backend
.venv/Scripts/pip install -r requirements-dev.lock
.venv/Scripts/python -m pytest --cov=app --cov-branch --cov-fail-under=60
cd .. && backend/.venv/Scripts/python -m ruff check backend/app backend/tests backend/migrations

# 前端单元测试、类型检查、生产构建与桌面/390px 浏览器测试（Node.js >=20.19）
cd frontend
npm ci
npm test
npm run build
npm run test:e2e
npm audit --omit=dev --audit-level=high

# 生产预览（含 /api 代理）
npm run preview
```

## 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| `Access denied for user 'zhicourt'` | 数据库未创建或密码不一致，重看「快速启动第 1 步」 |
| 首页一直空白 | 后端未启动（`/api` 请求失败），先起 uvicorn |
| 界面显示「演示数据」 | 未配置知乎 Access Secret，属预期行为；在管理后台保存或配置 `.env` 后重启 |
| SSE 无进度 | Nginx 未关 `proxy_buffering`，确认使用 `deploy/nginx.conf` |
| Redis 连接失败 | 自动降级内存缓存，日志有 WARNING，不影响运行 |
| `Data too long for column` | 对照 Alembic 版本和备份确认 schema；切勿直接 `DROP DATABASE`，按部署手册先隔离演练恢复 |

## 文档索引

- [docs/architecture.md](docs/architecture.md) — 系统架构
- [docs/api-integration.md](docs/api-integration.md) — 知乎官方 API 集成与 Provider 设计
- [docs/agent-workflow.md](docs/agent-workflow.md) — Agent 流水线与 Evidence Engine
- [docs/database.md](docs/database.md) — 数据模型
- [docs/deployment.md](docs/deployment.md) — 部署手册
- [docs/hackathon-demo.md](docs/hackathon-demo.md) — 3 分钟演示脚本
- [docs/authentication.md](docs/authentication.md) — 用户体系 / 会话 / 权限模型
- [docs/admin.md](docs/admin.md) — 管理后台 / Feature Flags / 审计

## 许可

本项目源码以 [Apache License 2.0](LICENSE) 发布。运行时凭据、数据库备份、
真实用户数据和第三方品牌素材不在此授权范围内，也不应提交到公开仓库。

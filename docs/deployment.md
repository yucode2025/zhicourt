# 部署手册（Ubuntu Server 22.04 LTS）

## 前置条件

- HTTPS 生产：独占的规范域名 A/AAAA 指向本机，公网 80/443 开放，提供 `CERTBOT_EMAIL`；HTTP 仅显式演示，不承载真实用户数据。
- 系统 Python 3.10、MariaDB、Redis、Nginx 由 `install.sh` 通过 Ubuntu apt 安装；**Node.js 20 必须预先从可信、已验证的软件源安装**（或由发布流水线提供预构建产物），脚本不运行 NodeSource 远程安装脚本。
- 从独立的、已审查 staging 源码目录执行脚本；源码目录不得嵌套在 `APP_DIR` 内，反之亦然。`APP_DIR` 放在 `/opt` 等非 `/home`/`/root` 目录（systemd `ProtectHome=true`）；发布脚本不会 `git pull` 或修改源代码仓库。
- 预留足够磁盘用于新旧 release、venv、`npm ci` 和数据库备份。升级迁移前需要明确维护窗口，确认上一版本可否读取新 schema；不得把可用性寄托于 Alembic downgrade。

```bash
sudo APP_DIR=/opt/zhicourt DOMAIN=court.example.com \
  CERTBOT_EMAIL=admin@example.com \
  ADMIN_USERNAME=admin ADMIN_PASSWORD='强管理员密码' \
  bash deploy/install.sh
# 显式 HTTP 演示（不承载生产数据）：
sudo APP_DIR=/opt/zhicourt ALLOW_INSECURE_HTTP=true PUBLIC_HOST=203.0.113.10 bash deploy/install.sh
```

`DB_PASS` 不传时自动生成；只在**首次创建** `/etc/zhicourt/zhicourt.env` 时写入。安装脚本从规范 `PUBLIC_ORIGIN` 同时写入 `CORS_ORIGINS` 和精确主机名 `TRUSTED_HOSTS`，满足后端生产配置的 fail-closed 校验；后续人工修改必须保持 `TRUSTED_HOSTS` 包含 `PUBLIC_ORIGIN` 主机。若 env 已存在，安装脚本拒绝重跑、不会重置 MariaDB 帐号；后续使用 `deploy.sh`。若此前使用旧版 `APP_DIR/backend/.env` 或 `backend/.secret_key`，先停旧服务并核对、迁移其配置/密钥及数据，再移走旧原地代码目录；新脚本不自动迁移或覆盖旧布局。新装失败留下 env/维护标记时，排查原因、校验密钥与备份后从 staging 显式用 `INSTALL_MODE=true CERTBOT_EMAIL=... bash deploy/deploy.sh` 续装。管理员创建只在同时提供 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 时执行；安装/更新**不运行** `scripts/init_db.py`、不默认写入演示种子。`APP_ENV=prod` + HTTPS，或 `APP_ENV=dev` + 显式 `ALLOW_INSECURE_HTTP=true` + HTTP；其他组合拒绝渲染。

## 发布布局与持久化边界

```text
/opt/zhicourt/releases/<timestamp>-<pid>/     root 拥有、只读的代码/venv/dist
/opt/zhicourt/current -> releases/<release>    同目录临时链接 + mv -T 原子切换
/etc/zhicourt/zhicourt.env                     root:zhicourt 0640，进程配置/主密钥
/var/lib/zhicourt/assets/                     运行用户可写的持久资源；Nginx /shared-assets/
/var/lib/zhicourt/backups/                    root 0700，数据库压缩备份 0600
/var/lib/zhicourt/maintenance                 存在时所有站点请求均 503
/var/lib/zhicourt/deploy.lock                 flock 排他锁
```

Provider Secret 以 `enc:v1:` Fernet 密文保存在数据库 `system_settings`，**必须同时保全** `/etc/zhicourt/zhicourt.env` 的 `SECRET_ENCRYPTION_KEY` 与数据库备份；缺失密钥后旧密文无法恢复。生产不依赖代码根目录的 `.secret_key` 自动生成。已有旧版 `.secret_key` 必须先人工转入外置 env（`SECRET_ENCRYPTION_KEY=`），再发布，否则部署会阻止升级。绝不把 env、主密钥、备份或持久资产放在 release 里；不要对外暴露 `/etc/zhicourt`。systemd `ProtectSystem=strict`，仅 `/var/lib/zhicourt` 可写，工作目录和依赖归 root 所有。

发布复制使用脚本内同一组 rsync 规则，明确排除 Git、env/密钥、依赖、构建/测试缓存，以及仓库根目录的 `artifacts/`、`.audit-runtime/`、`.zcode/`、`.codex/`、`.agents/` 等本机会话或工具目录。更新脚本时必须同时通过 `bash deploy/deploy.sh --self-test`；该命令无需 root，只构造哨兵目录并验证正常源码和 `.env.example` 被复制、敏感/本地产物未进入候选 release。不要仅依赖 `.gitignore`，因为 staging 目录可能含未跟踪文件。

## 更新（安排维护窗口）

在独立 staging checkout 的项目根目录运行：

```bash
sudo APP_DIR=/opt/zhicourt bash deploy/deploy.sh
```

脚本先 `flock`，复制源码至新 release、建隔离 venv、`npm ci` + 构建并设只读；随后在进入维护前用候选 release 的 `deploy/render.py --preflight` 读取外置 env 并**实际导入 `app.core.config`**，确认生产 Origin/Trusted Host、数据库字段和固定 Fernet 密钥均被真实后端配置接受。通过后才创建维护标记（Nginx 503）、停旧服务、读取外置数据库凭证做一致性 dump、`gzip -t` 和非空校验、执行 `alembic upgrade head`，统一渲染并 `nginx -t`，最后原子切换 `current`、启动服务并以 `/api/health/ready` DB 检查门禁。成功后才清除维护标记。构建前或 preflight 失败时旧服务不受影响；维护标记后失败不会自动恢复服务或自动 downgrade，必须由运维干预。

`deploy/render.py` 同时用于 install/deploy，生产渲染始终要求严格绝对 Unix `APP_DIR`；显式 Windows 路径兼容仅供非生产本地 renderer 测试。HTTPS HTTP→固定域名重定向、未知 Host 默认 444、生产 `/api/docs`/`/api/redoc`/`/api/openapi.json` 返回 404；安全头（包括 Permissions-Policy）在 server 和每个包含 `add_header` 的 location 完整重复，避免 Nginx 的 `add_header` 继承替换规则漏头。OAuth 精确回调 `location = /login/zhihu` 关闭 access log，返回 `Cache-Control: no-store`、`Referrer-Policy: no-referrer` 并重复 CSP/HSTS 等全部安全头；`frontend/index.html` 还在任何图标/模块请求之前声明 `<meta name="referrer" content="no-referrer">`，双层防止授权码/state 经 Referrer 外送。`NGINX_CSP_MODE=report-only`（默认）可在验证资源策略后改 `enforce` 并重新发布。CSP 需按实际第三方资源核对，禁止盲目强制。certbot HTTP-01 仅规范域名的 `/.well-known/acme-challenge/` 开放。

> 注意：`ALLOW_SCHEMA_ROLLBACK=true` **仅在已人工确认旧代码可读取升级后 schema 时**于本次发布传入。只有切换后失败且该值为 true 时，脚本尝试原子切回旧 release、重启并检查 ready；成功才关闭维护。默认不回滚代码；升级 schema 后即使切旧代码也**不会**执行 Alembic downgrade。

## OAuth 上线与真实 state 验证

1. 在知乎活动/开放平台登记唯一生产回调 `https://<规范域名>/login/zhihu`；将相同字符串写入外置 env 的 `ZHIHU_OAUTH_REDIRECT_URI`，填写 App ID/App Key，并确认该 Origin 与 `PUBLIC_ORIGIN`、`CORS_ORIGINS`、`TRUSTED_HOSTS` 一致。不要将 App Key 与开放 API 的 `ZHIHU_ACCESS_SECRET` 混用。
2. 发布前运行 renderer 和发布源自检（需要 `rsync`，安装脚本和 CI 会提供）；发布后先保持 `usage_access_policy=guest` 或 `authenticated`，不要直接切 `zhihu`。确认 `/api/capabilities` 只返回启用状态/回调路径/策略，不泄露凭证。
3. 用全新无痕窗口完成一次 **login**：在本站发起授权、在知乎确认、回跳固定 URI、成功建立本站会话。再用一个未绑定的本站密码账号完成一次 **link**，重新登录确认 `zhihu_bound` 仍为 true。
4. 真实验证 state，而非只测 Mock：保存发起授权的 state 测试上下文后，确认正常回调成功；重复提交同一 code/state 应失败；另一浏览器提交、篡改 state、超过 10 分钟、把 login 回调当 link、切换发起用户/会话后回调都应失败。测试账号与授权码不得出现在工单、终端历史、截图或日志中。
5. 检查 `curl -sSI https://<域名>/login/zhihu`：应包含 `Cache-Control: no-store`、`Referrer-Policy: no-referrer`、全部安全头；Nginx access log 不应新增该精确路径记录。确认页面读取参数后地址栏不再含 code/state，数据库/备份/应用日志无 OAuth token。
6. 最后分别验证游客、本站账号、知乎账号/已绑定账号、ADMIN 和公开分享在三档策略及 `allow_guest_cases` 组合下的预期，再由管理员逐级收紧策略。

OAuth 故障优先回滚**配置/策略**而非数据库 schema：保留维护证据，先将 `usage_access_policy` 恢复到发布前值，或移除/修正 OAuth 三项 env 后重新发布，使 OAuth 能力 fail closed；不要解绑、换绑、手改 UID，也不要恢复到会重新持久化 token 的旧代码。若本次迁移改变 OAuth/state 表且旧代码不兼容，继续维护模式并按下节从匹配备份和 release 成对恢复。回滚完成后重新验证登录、ADMIN 豁免、公开分享、回调响应头和日志脱敏。

## 故障处置、恢复数据库备份

1. 保持维护标记，停止 API：`sudo touch /var/lib/zhicourt/maintenance && sudo systemctl stop zhicourt`。保留失败 release、日志与当前库以便排查。
2. 检查 `sudo journalctl -u zhicourt -n 100 --no-pager`、`sudo nginx -t`、`sudo systemctl status mariadb redis-server`；确认备份路径（更新输出）并执行 `sudo gzip -t /var/lib/zhicourt/backups/<文件>.sql.gz`。备份只包含 DB，不包含 env/主密钥/共享资产，须单独从安全备份恢复它们。
3. **仅当**上一版在当前 schema 上兼容，手工将 `current` 原子切至保留的旧 release，然后 `sudo systemctl restart zhicourt` 并检查 ready；否则不要直接切回。操作示例：`sudo ln -s /opt/zhicourt/releases/<旧版> /opt/zhicourt/.current.restore && sudo mv -Tf /opt/zhicourt/.current.restore /opt/zhicourt/current`。
4. 若 schema 不兼容，先在隔离环境恢复压缩 SQL 并演练检查；在停写、确认时间点/损失窗口、**备份现库**后，才人工重建受影响的生产库并从旧备份恢复。隔离演练示例（**不得直接导入现存生产库**）：`sudo mariadb -e 'CREATE DATABASE zhicourt_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'`，再 `sudo sh -c 'gzip -cd /var/lib/zhicourt/backups/<文件>.sql.gz | mariadb zhicourt_restore'`。验证成功后单独安排生产库清空/替换、切换配置与旧 release；**不可**向已有升级后表追加导入；需同时恢复匹配的 Fernet 主密钥并校验迁移版本、表数及端到端业务。绝不自动 `alembic downgrade`。
5. `sudo systemctl restart zhicourt && curl -fsS http://127.0.0.1:8000/api/health/ready` 成功且 `sudo nginx -t` 成功，再 `sudo rm /var/lib/zhicourt/maintenance`。从隔离恢复演练到真实生产切换必须有明确运维审批。

## 检查

```bash
sudo systemctl status zhicourt nginx --no-pager
curl -fsS http://127.0.0.1:8000/api/health/ready
curl -fsSI https://court.example.com/
curl -sSI https://court.example.com/api/docs    # 生产应 404
curl -sSI https://court.example.com/login/zhihu # no-store + no-referrer + 全部安全头
bash deploy/deploy.sh --self-test
sudo journalctl -u zhicourt -n 100 --no-pager
```

Redis 可降级但建议监测；MariaDB readiness 失败会阻止切出维护。证书通过 `certbot.timer` 续期与部署钩子 `nginx -t && systemctl reload nginx`。建议定期离机加密备份并实际演练恢复数据库、env 主密钥与共享资产。

#!/usr/bin/env bash
# Bootstrap Ubuntu 22.04; releases are created solely by deploy.sh.
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf '请使用 sudo。\n' >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
APP_DIR="${APP_DIR:-/opt/zhicourt}"
APP_USER="${APP_USER:-zhicourt}"
[[ "$APP_DIR" != / && "$APP_DIR" =~ ^/[a-zA-Z0-9_./\ -]+$ &&
   "$APP_DIR" != */../* && "$APP_DIR" != */./* && "$APP_DIR" != */.. && "$APP_DIR" != */. &&
   "$APP_DIR" != /home/* && "$APP_DIR" != /root/* ]] || { printf 'APP_DIR 无效（必须位于非 Home 目录）。\n' >&2; exit 1; }
[[ "$APP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 1
if [[ -e "$APP_DIR/backend" || -e "$APP_DIR/current" || -L "$APP_DIR/current" ||
      -e /etc/zhicourt/zhicourt.env || -L /etc/zhicourt/zhicourt.env ]]; then
  printf '检测到既有安装；请先按部署手册迁移旧版 env/Fernet 密钥并在独立 staging 使用 deploy.sh。\n' >&2
  exit 1
fi
ALLOW_INSECURE_HTTP="${ALLOW_INSECURE_HTTP:-false}"
case "$ALLOW_INSECURE_HTTP" in
  true)
    host="${PUBLIC_HOST:-}"
    [[ "$host" =~ ^[a-zA-Z0-9.-]+$ ]] || { printf 'HTTP 演示需设置 PUBLIC_HOST。\n' >&2; exit 1; }
    host="${host,,}"
    APP_ENV=dev; origin="http://$host" ;;
  false)
    host="${DOMAIN:-}"
    [[ "$host" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ && "${CERTBOT_EMAIL:-}" == *@*.* ]] || {
      printf '生产需设置 DOMAIN 和 CERTBOT_EMAIL。\n' >&2; exit 1;
    }
    host="${host,,}"
    APP_ENV=prod; origin="https://$host" ;;
  *) printf 'ALLOW_INSECURE_HTTP 只能为 true/false。\n' >&2; exit 1 ;;
esac
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
packages=(ca-certificates curl rsync git python3 python3-venv python3-pip nginx redis-server mariadb-server util-linux)
[[ "$APP_ENV" == prod ]] && packages+=(certbot)
apt-get install -y -qq "${packages[@]}" >/dev/null
# Install Node.js 20 from a preprovisioned, trusted package repository; never pipe a remote shell script.
if ! command -v node >/dev/null || [[ "$(node --version)" != v20.* ]]; then
  printf '请预先从可信软件源安装 Node.js 20（或由 CI 提供预构建包）后重试。\n' >&2
  exit 1
fi
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/zhicourt --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR/releases" /var/lib/zhicourt/backups /var/lib/zhicourt/assets
chmod 0700 /var/lib/zhicourt/backups
chown "$APP_USER:$APP_USER" /var/lib/zhicourt/assets
systemctl enable --now mariadb
redis_conf=/etc/redis/redis.conf
for setting in 'bind 127.0.0.1' 'protected-mode yes' 'maxmemory 128mb' 'maxmemory-policy allkeys-lru'; do
  name="${setting%% *}"
  if grep -Eq "^[#[:space:]]*${name}[[:space:]]+" "$redis_conf"; then
    sed -Ei "s/^[#[:space:]]*${name}[[:space:]]+.*/${setting}/" "$redis_conf"
  else
    printf '\n%s\n' "$setting" >> "$redis_conf"
  fi
done
systemctl enable --now redis-server
systemctl restart redis-server
# Existing external config is authoritative; never rotate the DB account by rerunning install.
ENV_FILE=/etc/zhicourt/zhicourt.env
DB_PASS="${DB_PASS:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
[[ "$DB_PASS" =~ ^[a-zA-Z0-9._~!@#%^+=-]+$ ]] || { printf 'DB_PASS 存在不支持的字符。\n' >&2; exit 1; }
mariadb <<SQL
CREATE DATABASE IF NOT EXISTS zhicourt CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'zhicourt'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER 'zhicourt'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON zhicourt.* TO 'zhicourt'@'localhost';
CREATE USER IF NOT EXISTS 'zhicourt'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
ALTER USER 'zhicourt'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON zhicourt.* TO 'zhicourt'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
install -d -m 0750 -o root -g "$APP_USER" /etc/zhicourt
install -m 0640 -o root -g "$APP_USER" /dev/null "$ENV_FILE"
python3 - "$ENV_FILE" "$APP_ENV" "$ALLOW_INSECURE_HTTP" "$origin" "$DB_PASS" <<'PY'
import secrets
import base64
from pathlib import Path
import sys
from urllib.parse import urlsplit
path, env, allow_http, origin, password = sys.argv[1:]
key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
trusted_host = urlsplit(origin).hostname
if not trusted_host:
    raise SystemExit('PUBLIC_ORIGIN does not contain a host')
Path(path).write_text('\n'.join((
    f'APP_ENV={env}', f'ALLOW_INSECURE_HTTP={allow_http}', f'PUBLIC_ORIGIN={origin}',
    f'CORS_ORIGINS={origin}', f'TRUSTED_HOSTS={trusted_host}',
    'APP_HOST=127.0.0.1', 'APP_PORT=8000',
    'DB_HOST=127.0.0.1', 'DB_PORT=3306', 'DB_USER=zhicourt', f'DB_PASSWORD={password}',
    'DB_NAME=zhicourt', 'REDIS_URL=redis://127.0.0.1:6379/0',
    f'SECRET_ENCRYPTION_KEY={key}', 'NGINX_CSP_MODE=report-only',
)) + '\n', encoding='utf-8')
PY
# No sample seed or init_db.py in production or demo. Explicit opt-in admin creation only.
APP_DIR="$APP_DIR" APP_USER="$APP_USER" INSTALL_MODE=true CERTBOT_EMAIL="${CERTBOT_EMAIL:-}" bash "$SCRIPT_DIR/deploy.sh"
if [[ -n "${ADMIN_USERNAME:-}" && -n "${ADMIN_PASSWORD:-}" ]]; then
  (cd "$APP_DIR/current/backend" && runuser -u "$APP_USER" -- env HOME=/var/lib/zhicourt \
    "$APP_DIR/current/backend/.venv/bin/python" "$APP_DIR/current/deploy/run-with-env.py" "$ENV_FILE" \
    "$APP_DIR/current/backend/.venv/bin/python" scripts/create_admin.py)
fi
if [[ "$APP_ENV" == prod ]]; then
  install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
  printf '#!/bin/sh\nnginx -t && systemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/zhicourt-nginx
  chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/zhicourt-nginx
  systemctl enable --now certbot.timer
fi
printf '安装完成：%s；密钥与配置位于 %s，请立即将其纳入安全备份。\n' "$origin" "$ENV_FILE"

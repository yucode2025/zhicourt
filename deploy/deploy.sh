#!/usr/bin/env bash
# Ubuntu 22.04; SOURCE_DIR is the immutable candidate tree, never a live git pull.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
# One filter list is shared by real releases and --self-test so safety checks cannot drift.
RELEASE_RSYNC_RULES=(
  --exclude=.git --include=.env.example --exclude='.env.*' --exclude=.env
  --exclude=.secret_key --exclude=.venv --exclude=node_modules --exclude=dist
  --exclude=__pycache__ --exclude=.pytest_cache --exclude=.mypy_cache --exclude=.ruff_cache
  --exclude=gui-test-screenshots --exclude=test-results --exclude=playwright-report
  --exclude=htmlcov --exclude=.coverage --exclude=coverage.xml --exclude='*.pyc'
  --exclude=/artifacts/ --exclude=/.audit-runtime/ --exclude=/.zcode/
  --exclude=/.codex/ --exclude=/.agents/
)
copy_release_source() {
  rsync -a "${RELEASE_RSYNC_RULES[@]}" "$1/" "$2/"
}
source_kind=""
source_revision=""
declared_artifact_sha256=""
validate_source_provenance() {
  if git -C "$SOURCE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local repository_root dirty
    repository_root="$(git -C "$SOURCE_DIR" rev-parse --show-toplevel)"
    [[ "$(readlink -f "$repository_root")" == "$SOURCE_DIR" ]] || {
      printf '发布源码必须是 Git checkout 根目录。\n' >&2
      return 1
    }
    dirty="$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=all)"
    [[ -z "$dirty" ]] || {
      printf '发布源码包含未提交或未跟踪文件；请从干净 commit 构建。\n' >&2
      return 1
    }
    source_kind="git"
    source_revision="$(git -C "$SOURCE_DIR" rev-parse --verify HEAD)"
    git -C "$SOURCE_DIR" cat-file -e "${source_revision}^{commit}"
    return
  fi

  # Exported artifacts do not contain .git. Their build system must provide
  # immutable provenance rather than silently producing an untraceable release.
  [[ "${RELEASE_SOURCE_REVISION:-}" =~ ^[0-9a-fA-F]{40,64}$ ]] || {
    printf '非 Git 制品必须设置 RELEASE_SOURCE_REVISION（40-64 位十六进制）。\n' >&2
    return 1
  }
  [[ "${RELEASE_ARTIFACT_SHA256:-}" =~ ^[0-9a-fA-F]{64}$ ]] || {
    printf '非 Git 制品必须设置 RELEASE_ARTIFACT_SHA256。\n' >&2
    return 1
  }
  source_kind="artifact"
  source_revision="${RELEASE_SOURCE_REVISION,,}"
  declared_artifact_sha256="${RELEASE_ARTIFACT_SHA256,,}"
}
release_source_self_test() {
  command -v rsync >/dev/null
  local work
  work="$(mktemp -d)"
  trap 'rm -rf -- "${work:-}"' EXIT
  mkdir -p "$work/source"/{backend,frontend,artifacts,.audit-runtime,.zcode,.codex,.agents}
  printf 'safe\n' > "$work/source/backend/app.py"
  printf 'example\n' > "$work/source/.env.example"
  printf 'secret\n' > "$work/source/.env.prod"
  for directory in artifacts .audit-runtime .zcode .codex .agents; do
    printf 'local-only\n' > "$work/source/$directory/marker"
  done
  mkdir -p "$work/release"
  copy_release_source "$work/source" "$work/release"
  [[ -f "$work/release/backend/app.py" && -f "$work/release/.env.example" ]]
  [[ ! -e "$work/release/.env.prod" ]]
  for directory in artifacts .audit-runtime .zcode .codex .agents; do
    [[ ! -e "$work/release/$directory" ]]
  done
  rm -rf "$work"
  printf 'deployment release-source self-test: ok\n'
}
if [[ ${1:-} == --self-test ]]; then
  [[ $# -eq 1 ]] || { printf '%s\n' '--self-test 不接受其他参数。' >&2; exit 2; }
  release_source_self_test
  exit 0
fi
[[ $EUID -eq 0 ]] || { printf '请使用 sudo。\n' >&2; exit 1; }
APP_DIR="${APP_DIR:-/opt/zhicourt}"
APP_USER="${APP_USER:-zhicourt}"
STATE_DIR=/var/lib/zhicourt
ENV_FILE=/etc/zhicourt/zhicourt.env
BACKUP_DIR="$STATE_DIR/backups"
MAINTENANCE="$STATE_DIR/maintenance"
[[ "$APP_DIR" != / && "$APP_DIR" =~ ^/[a-zA-Z0-9_./\ -]+$ && "$APP_DIR" != */../* && "$APP_DIR" != */./* && "$APP_DIR" != */.. && "$APP_DIR" != */. && "$APP_DIR" != /home/* && "$APP_DIR" != /root/* ]] || { printf 'APP_DIR 无效（必须位于非 Home 目录）。\n' >&2; exit 1; }
[[ "$APP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 1
[[ -f "$ENV_FILE" && -d "$SOURCE_DIR/backend" && -d "$SOURCE_DIR/frontend" ]] || { printf '缺少外置环境文件或发布源码。\n' >&2; exit 1; }
if [[ "$SOURCE_DIR" == "$APP_DIR" || "$SOURCE_DIR" == "$APP_DIR/"* || "$APP_DIR" == "$SOURCE_DIR/"* ]]; then
  printf '发布源码目录与 APP_DIR 不得互相嵌套，请在独立 staging checkout 中执行。\n' >&2
  exit 1
fi
id -u "$APP_USER" >/dev/null
command -v flock >/dev/null
command -v node >/dev/null
command -v git >/dev/null
command -v sha256sum >/dev/null
[[ "$(node --version)" == v20.* ]] || { printf '请预先安装 Node.js 20（不执行远程安装脚本）。\n' >&2; exit 1; }
validate_source_provenance
for dir in "$APP_DIR" "$APP_DIR/releases" "$STATE_DIR" "$BACKUP_DIR" "$STATE_DIR/assets" "$STATE_DIR/acme"; do
  [[ ! -L "$dir" ]] || { printf '拒绝使用符号链接持久目录：%s\n' "$dir" >&2; exit 1; }
done
mkdir -p "$APP_DIR/releases" "$BACKUP_DIR" "$STATE_DIR/assets" "$STATE_DIR/acme/.well-known/acme-challenge"
chown root:root "$APP_DIR" "$APP_DIR/releases" "$STATE_DIR" "$STATE_DIR/acme"
chown -R root:root "$BACKUP_DIR"
find "$BACKUP_DIR" -type d -exec chmod 0700 {} +
find "$BACKUP_DIR" -type f -exec chmod 0600 {} +
chmod 0755 "$APP_DIR" "$APP_DIR/releases" "$STATE_DIR" "$STATE_DIR/assets" "$STATE_DIR/acme" "$STATE_DIR/acme/.well-known" "$STATE_DIR/acme/.well-known/acme-challenge"
chmod 0700 "$BACKUP_DIR"
chown "$APP_USER:$APP_USER" "$STATE_DIR/assets"
[[ ! -L "$STATE_DIR/deploy.lock" ]] || exit 1
exec 9>"$STATE_DIR/deploy.lock"
chown root:root "$STATE_DIR/deploy.lock"
chmod 0600 "$STATE_DIR/deploy.lock"
flock -n 9 || { printf '已有部署在运行。\n' >&2; exit 1; }

# The renderer is loaded from the new candidate; use its own virtualenv after staging.
release="$APP_DIR/releases/$(date -u +%Y%m%dT%H%M%SZ)-${source_revision:0:12}-$$"
previous=""
if [[ -e "$APP_DIR/backend" ]]; then
  printf '旧版原地目录仍存在；先迁移 backend/.env 和 .secret_key，禁止直接覆盖。\n' >&2
  exit 1
fi
if [[ -L "$APP_DIR/current" ]]; then
  previous="$(readlink -f "$APP_DIR/current")"
  [[ "$previous" == "$APP_DIR/releases/"* && -d "$previous" ]] || { printf 'current 链接不在 releases 内。\n' >&2; exit 1; }
elif [[ -e "$APP_DIR/current" ]]; then
  printf 'current 必须是指向 releases 的符号链接；旧版原地目录需人工迁移。\n' >&2
  exit 1
fi
if [[ -n "$previous" && -f "$previous/backend/.secret_key" ]]; then
  printf '检测到旧版 .secret_key；先将该密钥迁至 %s 的 SECRET_ENCRYPTION_KEY，再更新。\n' "$ENV_FILE" >&2
  exit 1
fi
mkdir -p "$release"
# Never copy secrets, caches or local mutable files into a release.
copy_release_source "$SOURCE_DIR" "$release"
release_source_sha256="$(
  cd "$release"
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1
)"
python3 -m venv "$release/backend/.venv"
"$release/backend/.venv/bin/python" -m pip install -q -r "$release/backend/requirements.lock"
npm --prefix "$release/frontend" ci --no-audit --no-fund
npm --prefix "$release/frontend" run build
mapfile -t migration_heads < <(
  cd "$release/backend"
  ./.venv/bin/python -m alembic -c alembic.ini heads | sed -n 's/[[:space:]].*$//p'
)
[[ ${#migration_heads[@]} -eq 1 && "${migration_heads[0]}" =~ ^[a-zA-Z0-9_.-]+$ ]] || {
  printf '发布制品必须只有一个可识别的 Alembic head。\n' >&2
  exit 1
}
{
  printf 'source_kind=%s\n' "$source_kind"
  printf 'source_revision=%s\n' "$source_revision"
  [[ -z "$declared_artifact_sha256" ]] || \
    printf 'declared_artifact_sha256=%s\n' "$declared_artifact_sha256"
  printf 'release_source_sha256=%s\n' "$release_source_sha256"
  printf 'alembic_head=%s\n' "${migration_heads[0]}"
  printf 'built_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$release/RELEASE_METADATA"
# Release code and dependencies are root-owned. Only /var/lib/zhicourt is writable by the service.
chown -R root:root "$release"
chmod -R a-w "$release"

# Fail before maintenance/database changes unless both deployment validation and the
# real backend configuration import accept the exact external environment.
mapfile -t cfg < <(
  "$release/backend/.venv/bin/python" "$release/deploy/render.py" --preflight \
    --env-file "$ENV_FILE" --backend-dir "$release/backend"
)
[[ ${#cfg[@]} -eq 2 ]] || exit 1
http_mode="${cfg[0]}"
db_name="${cfg[1]}"
[[ "$db_name" =~ ^[a-zA-Z0-9_]+$ ]] || exit 1
render() {
  "$release/backend/.venv/bin/python" "$release/deploy/render.py" "$1" \
    --app-dir "$APP_DIR" --app-user "$APP_USER" --env-file "$ENV_FILE" \
    ${2:+--mode "$2"} --output "$3"
}
ready() {
  local response
  for ((attempt=1; attempt<=30; attempt++)); do
    if systemctl is-active --quiet zhicourt && response="$(curl -fsS --max-time 3 http://127.0.0.1:8000/api/health/ready 2>/dev/null)" \
       && python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("status") != "ok")' <<< "$response"; then
      return 0
    fi
    sleep 2
  done
  journalctl -u zhicourt -n 100 --no-pager >&2 || true
  return 1
}
# No automatic downgrade. ALLOW_SCHEMA_ROLLBACK=true is an operator assertion after
# verifying the previous release is compatible with the *current upgraded schema*.
rollback_allowed="${ALLOW_SCHEMA_ROLLBACK:-false}"
[[ "$rollback_allowed" == true || "$rollback_allowed" == false ]] || exit 1
switched=false
config=""
recover() {
  local status=$?
  trap - EXIT
  [[ -z "$config" ]] || rm -f "$config"
  if (( status != 0 )); then
    printf '发布失败；维护窗口保持开启。数据库不会自动降级。\n' >&2
    if [[ "$switched" == true && -n "$previous" && "$rollback_allowed" == true ]]; then
      ln -s "$previous" "$APP_DIR/.current.rollback.$$"
      mv -Tf "$APP_DIR/.current.rollback.$$" "$APP_DIR/current"
      if systemctl restart zhicourt && ready; then
        rm -f "$MAINTENANCE"
        printf '旧版在当前数据库 schema 上就绪；维护窗口已关闭。\n' >&2
      fi
    fi
    printf '若 schema 不兼容，保持维护并从经校验备份恢复，参见 docs/deployment.md。\n' >&2
  fi
  exit "$status"
}
trap recover EXIT

printf '==> 进入维护窗口，停止旧服务\n'
touch "$MAINTENANCE"
chmod 0644 "$MAINTENANCE"
systemctl stop zhicourt 2>/dev/null || true
# Backup only after writers have stopped. Root-only credentials and artifacts.
config="$(mktemp "$STATE_DIR/.db-client.XXXXXX")"
chmod 0600 "$config"
"$release/backend/.venv/bin/python" - "$ENV_FILE" "$config" <<'PY'
from dotenv import dotenv_values
from pathlib import Path
import sys
v = dotenv_values(sys.argv[1])
def escape(value):
    return str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
Path(sys.argv[2]).write_text('[client]\n' + ''.join(f'{k}="{escape(v[key])}"\n' for k, key in
    [('host','DB_HOST'), ('port','DB_PORT'), ('user','DB_USER'), ('password','DB_PASSWORD')]))
PY
backup="$BACKUP_DIR/${db_name}_$(date -u +%Y%m%dT%H%M%SZ)_$$.sql.gz"
printf '==> 备份与校验 %s\n' "$backup"
mariadb-dump --defaults-extra-file="$config" --single-transaction --routines --events "$db_name" | gzip -9 > "$backup"
chmod 0600 "$backup"
gzip -t "$backup"
[[ -s "$backup" && "$(gzip -cd "$backup" | wc -c)" -gt 100 ]] || { printf '数据库备份不完整。\n' >&2; exit 1; }
rm -f "$config"
config=""

printf '==> 升级 schema（禁止自动 downgrade）\n'
(
  cd "$release/backend"
  runuser -u "$APP_USER" -- env HOME="$STATE_DIR" \
    "$release/backend/.venv/bin/python" "$release/deploy/run-with-env.py" "$ENV_FILE" \
    "$release/backend/.venv/bin/python" -m alembic upgrade head
)
# Build and validate configs before switching current; certbot uses an isolated ACME vhost.
render service '' /etc/systemd/system/zhicourt.service
if [[ "$http_mode" == https && ! -f "/etc/letsencrypt/live/$("$release/backend/.venv/bin/python" - "$ENV_FILE" <<'PY'
from dotenv import dotenv_values
from urllib.parse import urlsplit
import sys
print(urlsplit(dotenv_values(sys.argv[1])['PUBLIC_ORIGIN']).hostname)
PY
)/fullchain.pem" ]]; then
  [[ "${INSTALL_MODE:-false}" == true ]] || { printf '缺少证书，请先完成安装。\n' >&2; exit 1; }
  render nginx acme /etc/nginx/sites-available/zhicourt
  ln -sfn /etc/nginx/sites-available/zhicourt /etc/nginx/sites-enabled/zhicourt
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable --now nginx
  host="$("$release/backend/.venv/bin/python" - "$ENV_FILE" <<'PY'
from dotenv import dotenv_values
from urllib.parse import urlsplit
import sys
print(urlsplit(dotenv_values(sys.argv[1])['PUBLIC_ORIGIN']).hostname)
PY
)"
  certbot certonly --webroot --webroot-path "$STATE_DIR/acme" --non-interactive --agree-tos \
    --email "${CERTBOT_EMAIL:?请设置 CERTBOT_EMAIL}" -d "$host"
fi
render nginx "$http_mode" /etc/nginx/sites-available/zhicourt
ln -sfn /etc/nginx/sites-available/zhicourt /etc/nginx/sites-enabled/zhicourt
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
ln -s "$release" "$APP_DIR/.current.next.$$"
mv -Tf "$APP_DIR/.current.next.$$" "$APP_DIR/current"
switched=true
systemctl enable zhicourt
systemctl enable --now nginx
systemctl reload nginx
systemctl restart zhicourt
printf '==> 等待 readiness\n'
ready
systemctl is-active --quiet nginx
rm -f "$MAINTENANCE"
trap - EXIT
printf '发布成功：%s；备份：%s\n' "$release" "$backup"

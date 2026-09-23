#!/usr/bin/env python3
"""Render nginx/systemd from one validated configuration (install and deploy)."""
import argparse
import ast
import base64
import ipaddress
import os
import re
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from dotenv import dotenv_values


_UNIX_APP_DIR = re.compile(r"/[a-zA-Z0-9_./ -]+")


def configuration(env_path: Path):
    values = dotenv_values(env_path)
    origin = (values.get("PUBLIC_ORIGIN") or "").strip()
    parsed = urlsplit(origin)
    host = parsed.hostname or ""
    demo = values.get("APP_ENV") == "dev" and (values.get("ALLOW_INSECURE_HTTP") or "").lower() == "true"
    prod = values.get("APP_ENV") == "prod" and (values.get("ALLOW_INSECURE_HTTP") or "").lower() == "false"
    if not ((demo and parsed.scheme == "http") or (prod and parsed.scheme == "https")):
        raise ValueError("APP_ENV, ALLOW_INSECURE_HTTP and PUBLIC_ORIGIN disagree")
    if (not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or not host or parsed.port or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or
            origin != f"{parsed.scheme}://{host}"):
        raise ValueError("PUBLIC_ORIGIN must contain only the canonical scheme and host")
    if prod:
        try:
            ipaddress.ip_address(host)
            raise ValueError("Production requires a DNS name")
        except ValueError as exc:
            if str(exc) == "Production requires a DNS name":
                raise
        if "." not in host:
            raise ValueError("Production requires a DNS name")
    trusted_hosts = [
        item.strip().lower()
        for item in (values.get("TRUSTED_HOSTS") or "").split(",")
        if item.strip()
    ]
    if prod and host.lower() not in trusted_hosts:
        raise ValueError("Production TRUSTED_HOSTS must include the PUBLIC_ORIGIN host")
    csp = (values.get("NGINX_CSP_MODE") or "report-only").strip()
    if csp not in ("report-only", "enforce"):
        raise ValueError("NGINX_CSP_MODE must be report-only or enforce")
    return values, host, prod, csp


def validate_app_dir(app_dir: str, *, allow_windows_test_path: bool = False) -> None:
    """Keep production paths POSIX-only; permit explicit Windows paths for local tests."""
    unix_valid = bool(
        app_dir != "/"
        and _UNIX_APP_DIR.fullmatch(app_dir)
        and not app_dir.startswith(("/home/", "/root/"))
        and all(part not in (".", "..") for part in app_dir.split("/"))
    )
    if unix_valid:
        return
    if allow_windows_test_path:
        path = PureWindowsPath(app_dir)
        if (path.is_absolute() and path.drive and path.root == "\\"
                and all(part not in (".", "..") and "\x00" not in part for part in path.parts)):
            return
    raise ValueError("Invalid APP_DIR: production rendering requires an absolute Unix path outside /home and /root")


def security_headers(prod, csp, *, referrer_policy="strict-origin-when-cross-origin"):
    # Avoid inline scripts; style-src unsafe-inline is required by Naive UI runtime styles.
    policy = ("default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
              "form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data:; font-src 'self' data:; "
              "connect-src 'self'; upgrade-insecure-requests" if prod else
              "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
              "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
              "font-src 'self' data:; connect-src 'self'")
    header = "Content-Security-Policy" if csp == "enforce" else "Content-Security-Policy-Report-Only"
    lines = [
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        f'add_header Referrer-Policy "{referrer_policy}" always;',
        'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;',
        f'add_header {header} "{policy}" always;',
    ]
    if prod:
        lines.append('add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;')
    return "\n".join("    " + line for line in lines)


def render_nginx(template, app_dir, host, prod, csp, mode):
    if mode == "https" and not prod or mode == "http" and prod or mode == "acme" and not prod:
        raise ValueError("Nginx mode conflicts with PUBLIC_ORIGIN")
    headers = security_headers(prod, csp)
    defaults = "server { listen 80 default_server; listen [::]:80 default_server; return 444; }\n"
    if mode == "https":
        defaults += ("server { listen 443 ssl default_server; listen [::]:443 ssl default_server; "
                     f"ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem; "
                     f"ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem; return 444; }}\n")
    redirect = ""
    if mode in ("https", "acme"):
        # ACME exception exists only on canonical HTTP vhost; never redirect to user-controlled Host.
        redirect = (f"server {{ listen 80; listen [::]:80; server_name {host};\n"
                    f"{headers}\n"
                    "location ^~ /.well-known/acme-challenge/ { root /var/lib/zhicourt/acme; }\n"
                    + (f"location / {{ if (-f /var/lib/zhicourt/maintenance) {{ return 503; }} "
                       f"return 308 https://{host}$request_uri; }}\n" if mode == "https" else
                       "location / { return 503; }\n") + "}\n")
    if mode == "acme":
        return defaults + redirect
    tls = (f"ssl_certificate /etc/letsencrypt/live/{host}/fullchain.pem;\n"
           f"ssl_certificate_key /etc/letsencrypt/live/{host}/privkey.pem;\n"
           "ssl_protocols TLSv1.2 TLSv1.3;\nssl_session_cache shared:SSL:10m;\n"
           "ssl_session_timeout 1d;\nssl_session_tickets off;") if prod else ""
    replacements = {
        "__SERVER_NAME__": host, "__APP_DIR__": app_dir,
        "__LISTEN_DIRECTIVES__": "listen 443 ssl http2;\nlisten [::]:443 ssl http2;" if prod else "listen 80;\nlisten [::]:80;",
        "__TLS_DIRECTIVES__": tls,
        "__SECURITY_HEADERS__": headers,
        "__OAUTH_SECURITY_HEADERS__": security_headers(prod, csp, referrer_policy="no-referrer"),
        "__PROD_DOCS_BLOCK__": ("location = /api/docs { return 404; }\n"
                                "location ^~ /api/docs/ { return 404; }\n"
                                "location = /api/openapi.json { return 404; }\n"
                                "location = /api/redoc { return 404; }") if prod else "",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    if re.search(r"__[A-Z_]+__", template):
        raise ValueError("Unrendered nginx placeholder")
    return defaults + redirect + template


def backend_preflight(env_path: Path, backend_dir: Path) -> tuple[str, str]:
    """Import the real backend config using only the external deployment env."""
    values, host, prod, _ = configuration(env_path)
    key = (values.get("SECRET_ENCRYPTION_KEY") or "").strip()
    try:
        if len(key) != 44 or len(base64.b64decode(key, altchars=b"-_", validate=True)) != 32:
            raise ValueError("invalid Fernet key")
    except (ValueError, UnicodeEncodeError):
        raise ValueError(
            "SECRET_ENCRYPTION_KEY must be a persistent 44-character Fernet key in external env"
        ) from None
    for required in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        if not (values.get(required) or "").strip():
            raise ValueError(f"missing {required}")
    config_path = backend_dir.resolve() / "app" / "core" / "config.py"
    config_tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    application_keys = {
        node.value
        for node in ast.walk(config_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]+", node.value)
    }
    for name in application_keys:
        os.environ.pop(name, None)
    os.environ.update({name: value for name, value in values.items() if value is not None})
    # Prevent a staging .env from filling omitted keys after the external env is loaded.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.path.insert(0, str(backend_dir.resolve()))
    from app.core.config import settings

    if settings.is_prod != prod:
        raise ValueError("backend APP_ENV disagrees with deployment mode")
    if values["PUBLIC_ORIGIN"] not in settings.cors_origins:
        raise ValueError("backend CORS_ORIGINS does not include PUBLIC_ORIGIN")
    if prod and host.lower() not in settings.trusted_hosts:
        raise ValueError("backend production TRUSTED_HOSTS does not include PUBLIC_ORIGIN host")
    if not settings.database_url:
        raise ValueError("backend database URL is empty")
    return "https" if prod else "http", values["DB_NAME"]


def self_test() -> None:
    template_dir = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as directory:
        env_path = Path(directory) / "prod.env"
        env_path.write_text(
            "APP_ENV=prod\nALLOW_INSECURE_HTTP=false\n"
            "PUBLIC_ORIGIN=https://court.example.com\n"
            "CORS_ORIGINS=https://court.example.com\n"
            "TRUSTED_HOSTS=court.example.com\n",
            encoding="utf-8",
        )
        _, host, prod, csp = configuration(env_path)
        rendered = render_nginx(
            (template_dir / "nginx.conf").read_text(encoding="utf-8"),
            "/opt/zhicourt", host, prod, csp, "https",
        )
        required = (
            "listen 80 default_server", "listen 443 ssl default_server", "return 444",
            "return 308 https://court.example.com$request_uri", "Permissions-Policy",
            "Strict-Transport-Security", "Content-Security-Policy-Report-Only",
            "location = /api/docs { return 404; }", "location = /api/openapi.json { return 404; }",
            'proxy_set_header Host court.example.com;', 'root "/opt/zhicourt/current/frontend/dist";',
        )
        assert all(item in rendered for item in required)
        assert "https://$host" not in rendered and not re.search(r"__[A-Z_]+__", rendered)
        for is_prod, policy_mode, nginx_mode in (
            (True, "report-only", "https"), (True, "enforce", "https"),
            (False, "report-only", "http"),
        ):
            site = render_nginx(
                (template_dir / "nginx.conf").read_text(encoding="utf-8"),
                "/opt/zhicourt", host, is_prod, policy_mode, nginx_mode,
            )
            callback = site.split("location = /login/zhihu {", 1)[1].split("\n    }", 1)[0]
            assert "access_log off;" in callback
            assert 'add_header Cache-Control "no-store" always;' in callback
            assert "rewrite ^ /index.html? break;" in callback
            assert "try_files $uri =404;" in callback
            assert security_headers(is_prod, policy_mode, referrer_policy="no-referrer").strip() in callback
            assert 'Referrer-Policy "strict-origin-when-cross-origin"' not in callback
            assert not re.search(r"__[A-Z_]+__", site)
        env_path.write_text(
            "APP_ENV=prod\nALLOW_INSECURE_HTTP=false\n"
            "PUBLIC_ORIGIN=https://court.example.com\nTRUSTED_HOSTS=other.example.com\n",
            encoding="utf-8",
        )
        try:
            configuration(env_path)
        except ValueError as exc:
            assert "TRUSTED_HOSTS" in str(exc)
        else:
            raise AssertionError("production accepted a missing canonical trusted host")
    validate_app_dir("/opt/zhicourt")
    validate_app_dir(r"C:\local tests\zhicourt", allow_windows_test_path=True)
    for invalid in ("relative/path", "/home/user/zhicourt", "/opt/../root", r"C:\prod\zhicourt"):
        try:
            validate_app_dir(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"strict APP_DIR validation accepted {invalid!r}")
    print("deployment renderer self-test: ok")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", nargs="?", choices=("nginx", "service"))
    parser.add_argument("--mode", choices=("http", "https", "acme"))
    parser.add_argument("--app-dir")
    parser.add_argument("--app-user")
    parser.add_argument("--env-file")
    parser.add_argument("--output")
    parser.add_argument("--backend-dir")
    command = parser.add_mutually_exclusive_group()
    command.add_argument("--self-test", action="store_true")
    command.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        if args.kind or any((args.mode, args.app_dir, args.app_user, args.env_file, args.output, args.backend_dir)):
            parser.error("--self-test cannot be combined with rendering arguments")
        self_test()
        return
    if args.preflight:
        if args.kind or not args.env_file or not args.backend_dir or any((args.mode, args.app_dir, args.app_user, args.output)):
            parser.error("--preflight requires only --env-file and --backend-dir")
        mode, database = backend_preflight(Path(args.env_file), Path(args.backend_dir))
        print(mode)
        print(database)
        return
    if not args.kind or not all((args.app_dir, args.app_user, args.env_file, args.output)):
        parser.error("kind, --app-dir, --app-user, --env-file and --output are required")
    values, host, prod, csp = configuration(Path(args.env_file))
    validate_app_dir(args.app_dir, allow_windows_test_path=not prod)
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.app_user):
        raise ValueError("Invalid APP_USER")
    template_dir = Path(__file__).resolve().parent
    if args.kind == "nginx":
        if not args.mode:
            parser.error("--mode required for nginx")
        content = render_nginx((template_dir / "nginx.conf").read_text(encoding="utf-8"), args.app_dir, host, prod, csp, args.mode)
    else:
        content = (template_dir / "zhicourt.service").read_text(encoding="utf-8").replace(
            "__APP_DIR__", args.app_dir).replace("__APP_USER__", args.app_user).replace(
            "__ENV_FILE__", args.env_file)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)


if __name__ == "__main__":
    main()

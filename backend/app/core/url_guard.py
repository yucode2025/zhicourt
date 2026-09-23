"""Provider Base URL 规范化与 SSRF 防护。"""
from __future__ import annotations

import ipaddress
import os
import posixpath
import socket
from urllib.parse import quote, unquote, urlsplit, urlunsplit


class UnsafeURLError(ValueError):
    """目标 URL 不允许访问。"""


def public_source_url(url: str) -> str:
    """只向客户端暴露无凭据的绝对 http(s) 或演示链接；历史脏数据返回空串。"""
    if not isinstance(url, str) or not url or url != url.strip() or any(ord(ch) < 33 or ch == "\\" for ch in url):
        return ""
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https", "demo") or not parts.netloc or not parts.hostname:
            return ""
        if parts.username is not None or parts.password is not None:
            return ""
        _ = parts.port
        return url
    except ValueError:
        return ""


def _private_urls_allowed() -> bool:
    """仅在明确的开发环境中保留本地 Provider 兼容；生产始终关闭。"""
    is_dev = os.getenv("APP_ENV", "dev").strip().lower() == "dev"
    opted_in = os.getenv("PROVIDER_ALLOW_PRIVATE_URLS", "").strip().lower() in ("1", "true", "yes", "on")
    return is_dev and opted_in


def _normalized_parts(url: str):
    value = (url or "").strip()
    if not value:
        raise UnsafeURLError("URL 不能为空")
    if any(ord(ch) < 32 for ch in value) or "\\" in value:
        raise UnsafeURLError("地址包含不允许的字符")

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError("仅支持 http/https 地址")
    if not parsed.hostname:
        raise UnsafeURLError("地址缺少主机名")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("不允许携带用户信息的地址")
    if parsed.query or parsed.fragment:
        raise UnsafeURLError("Base URL 不允许携带查询参数或片段")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("端口不合法") from exc
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeURLError("端口不合法")

    raw_host = parsed.hostname.rstrip(".")
    try:
        ip = ipaddress.ip_address(raw_host)
        host = ip.compressed.lower()
        host_for_netloc = f"[{host}]" if ip.version == 6 else host
    except ValueError:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise UnsafeURLError("主机名不合法") from exc
        if not host or any(label == "" for label in host.split(".")):
            raise UnsafeURLError("主机名不合法")
        host_for_netloc = host

    default_port = 443 if scheme == "https" else 80
    netloc = host_for_netloc if port in (None, default_port) else f"{host_for_netloc}:{port}"

    # 对路径做稳定的百分号/点段归一化，根路径统一省略末尾斜杠。
    try:
        decoded_path = unquote(parsed.path, errors="strict")
    except UnicodeError as exc:
        raise UnsafeURLError("路径编码不合法") from exc
    normalized_path = posixpath.normpath(decoded_path or "/")
    if decoded_path.endswith("/") and normalized_path != "/":
        normalized_path += "/"
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    path = quote(normalized_path, safe="/%:@!$&'()*+,;=-._~")
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")

    normalized = urlunsplit((scheme, netloc, path, "", ""))
    return normalized, scheme, host, port or default_port


def normalize_provider_url(url: str) -> str:
    """规范化 Provider Base URL，不进行 DNS 查询。"""
    return _normalized_parts(url)[0]


def provider_origin(url: str) -> str:
    """返回规范化 origin（scheme + host + 非默认端口）。"""
    normalized, scheme, host, port = _normalized_parts(url)
    parsed = urlsplit(normalized)
    default_port = 443 if scheme == "https" else 80
    host_part = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host_part}" + (f":{port}" if port != default_port else "")


def same_provider_origin(left: str, right: str) -> bool:
    """按规范化后的 origin 比较两个 Base URL。"""
    return provider_origin(left) == provider_origin(right)


def ensure_provider_url_basic(url: str) -> str:
    """保存配置用校验：规范化 URL，但不触发 DNS。"""
    return normalize_provider_url(url)


def ensure_safe_provider_url(url: str) -> str:
    """在每次出站前校验 scheme、主机和全部 DNS 解析结果。"""
    normalized, _scheme, host, port = _normalized_parts(url)
    if _private_urls_allowed():
        return normalized

    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        raise UnsafeURLError("不允许访问内网地址")

    try:
        literal_ip = ipaddress.ip_address(host)
        addresses = [literal_ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeURLError("主机名解析失败") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError as exc:
                raise UnsafeURLError("主机名解析结果不合法") from exc

    if not addresses or any(not ip.is_global for ip in addresses):
        raise UnsafeURLError("不允许访问内网或保留地址")
    return normalized

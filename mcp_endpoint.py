"""Read and safely validate a user-provided MCP endpoint.

The endpoint may contain an access token, so it is handled only in memory and
is never written to a configuration file or log. Production connections use
WSS by default; plain WS is limited to loopback unless explicitly enabled.
校验用户提供的 MCP 地址；远程明文连接需要显式启用。
"""

import ipaddress
import os
from urllib.parse import urlsplit


INSECURE_WS_ENV = "XIAOZHI_MCP_ALLOW_INSECURE_WS"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def insecure_ws_enabled() -> bool:
    """Return whether the operator explicitly allowed remote plain-text WS."""
    return os.environ.get(INSECURE_WS_ENV, "").strip().lower() in _TRUE_VALUES


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_mcp_endpoint(endpoint: str, allow_insecure_ws: bool = False) -> str:
    """Validate and return an MCP WebSocket endpoint.

    Any hostname or IP is accepted over ``wss://``; no vendor domain or path
    is hard-coded. Plain ``ws://`` is allowed on loopback for local testing.
    Remote plain WS requires an explicit opt-in to avoid exposing the token.
    """
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise ValueError("MCP 接入点不能为空。")
    if any(character.isspace() for character in endpoint):
        raise ValueError("MCP 接入点不能包含空白字符。")

    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"wss", "ws"}:
        raise ValueError("MCP 接入点必须使用 wss:// 或 ws://。")
    if not parsed.hostname:
        raise ValueError("MCP 接入点缺少服务器地址。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("请勿在 MCP 地址中使用用户名或密码。")
    if parsed.fragment:
        raise ValueError("MCP 接入点不能包含 URL 片段。")
    if parsed.scheme == "ws" and not _is_loopback(parsed.hostname) and not allow_insecure_ws:
        raise ValueError(
            "远程 MCP 接入点必须使用 wss://；如确需局域网明文 WS，"
            f"请显式设置 {INSECURE_WS_ENV}=1。"
        )
    return endpoint

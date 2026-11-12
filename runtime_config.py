"""Resolve bridge settings for the current user's computer.

Every setting can be overridden with an environment variable. Defaults are
limited to a safe loopback service and never contain a developer-specific
path, LAN address, or XiaoZhi MCP endpoint.
从当前用户环境读取配置；默认任务接口监听本机回环地址。
"""

import ipaddress
import os
from pathlib import Path


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bridge_host() -> str:
    host = os.environ.get("XIAOZHI_BRIDGE_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("XIAOZHI_BRIDGE_HOST 不能为空。")
    allow_remote = os.environ.get("XIAOZHI_ALLOW_REMOTE_BRIDGE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not _is_loopback(host) and not allow_remote:
        raise ValueError(
            "本机任务接口默认只能监听回环地址；若明确需要远程访问，"
            "请先配置认证和安全隧道，再设置 XIAOZHI_ALLOW_REMOTE_BRIDGE=1。"
        )
    return host


def bridge_port() -> int:
    raw = os.environ.get("XIAOZHI_BRIDGE_PORT", "8765").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("XIAOZHI_BRIDGE_PORT 必须是端口号。") from exc
    if not 1 <= port <= 65535:
        raise ValueError("XIAOZHI_BRIDGE_PORT 必须在 1 到 65535 之间。")
    return port


def bridge_base_url() -> str:
    host = bridge_host()
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{url_host}:{bridge_port()}"


def workspace() -> Path:
    configured = os.environ.get("XIAOZHI_WORKSPACE")
    selected = Path(configured).expanduser() if configured else Path.home()
    selected = selected.resolve()
    if not selected.is_dir():
        raise ValueError(f"任务工作目录不存在：{selected}")
    return selected


def data_dir(app_root: Path) -> Path:
    configured = os.environ.get("XIAOZHI_BRIDGE_DATA_DIR")
    selected = Path(configured).expanduser() if configured else app_root / "data"
    selected = selected.resolve()
    selected.mkdir(parents=True, exist_ok=True)
    return selected


def default_agent(available_agents: set[str] | None = None) -> str:
    configured = os.environ.get("XIAOZHI_DEFAULT_AGENT", "").strip().lower()
    if configured and configured not in {"workbuddy", "codex", "deepseek"}:
        raise ValueError("XIAOZHI_DEFAULT_AGENT 只能是 workbuddy、codex 或 deepseek。")
    if available_agents:
        if configured:
            if configured not in available_agents:
                raise ValueError(f"指定的默认执行器未安装：{configured}")
            return configured
        return next(name for name in ("workbuddy", "codex", "deepseek") if name in available_agents)
    return configured or "workbuddy"

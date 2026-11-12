"""Locate the local Codex CLI without relying on one shell session's PATH.

从显式配置、PATH 和常见安装位置查找 Codex 命令。
"""

import os
import shutil
from pathlib import Path


def discover_codex() -> str | None:
    """Return an available ``codex.exe`` path, or ``None`` when absent."""
    configured = os.environ.get("CODEX_CLI")
    if configured and Path(configured).is_file():
        return str(Path(configured))

    on_path = shutil.which("codex.exe") or shutil.which("codex")
    if on_path:
        return on_path

    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        install_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        if install_root.is_dir():
            candidates.extend(install_root.glob("*/codex.exe"))

    candidates.extend(
        [
            Path.home() / ".codex" / ".sandbox-bin" / "codex.exe",
            Path.home() / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe",
        ]
    )
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        return None
    # Desktop-app hash directories can contain multiple versions; use the
    # executable with the newest modification time.
    return str(max(existing, key=lambda candidate: candidate.stat().st_mtime))

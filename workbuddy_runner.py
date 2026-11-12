"""Discover and invoke the headless CLI bundled with WorkBuddy.

Task text is passed through standard input so it does not appear in the
process command line. The CLI uses WorkBuddy's own login state; this bridge
does not read, copy, or persist WorkBuddy account tokens.
发现 WorkBuddy 与 Node，使用其已有登录状态执行任务并解析结果。
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def _version_key(path: Path) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", path.name)
    return tuple(int(value) for value in numbers) if numbers else (0,)


def _discover_node() -> Path | None:
    configured = os.environ.get("WORKBUDDY_NODE")
    if configured and Path(configured).is_file():
        return Path(configured)

    versions = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    candidates = list(versions.glob("*/node.exe")) if versions.is_dir() else []
    if candidates:
        return max(candidates, key=lambda item: _version_key(item.parent))

    system_node = shutil.which("node.exe") or shutil.which("node")
    return Path(system_node) if system_node else None


def _registry_install_roots() -> list[Path]:
    """Read install roots from uninstall metadata and fail quietly."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    roots: list[Path] = []
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as child:
                            name = str(winreg.QueryValueEx(child, "DisplayName")[0])
                            if not name.lower().startswith("workbuddy"):
                                continue
                            try:
                                install = str(winreg.QueryValueEx(child, "InstallLocation")[0]).strip()
                            except OSError:
                                install = ""
                            if install:
                                roots.append(Path(install.strip('"')))
                                continue
                            icon = str(winreg.QueryValueEx(child, "DisplayIcon")[0])
                            icon_path = icon.split(",", 1)[0].strip().strip('"')
                            if icon_path:
                                roots.append(Path(icon_path).parent)
                    except OSError:
                        continue
        except OSError:
            continue
    return roots


def discover_workbuddy_command() -> list[str] | None:
    """Return ``[node.exe, codebuddy]``, or ``None`` when unavailable."""
    configured = os.environ.get("WORKBUDDY_CLI")
    cli_candidates: list[Path] = []
    if configured:
        cli_candidates.append(Path(configured))
    cli_candidates.extend(
        root / "resources" / "app.asar.unpacked" / "cli" / "bin" / "codebuddy"
        for root in _registry_install_roots()
    )
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        cli_candidates.append(
            Path(local_app_data)
            / "Programs"
            / "WorkBuddy"
            / "resources"
            / "app.asar.unpacked"
            / "cli"
            / "bin"
            / "codebuddy"
        )

    cli = next((candidate for candidate in cli_candidates if candidate.is_file()), None)
    node = _discover_node()
    if cli is None or node is None:
        return None
    return [str(node), str(cli)]


def run_workbuddy(
    command: list[str],
    request: str,
    workspace: Path,
    permission_mode: str = "acceptEdits",
    timeout: int = 600,
) -> str:
    """Run one WorkBuddy task synchronously and return its final text."""
    if permission_mode not in {"default", "acceptEdits", "plan"}:
        raise ValueError("不支持的 WorkBuddy 权限模式。")
    max_turns = os.environ.get("WORKBUDDY_MAX_TURNS", "80")
    if not max_turns.isdigit() or not 1 <= int(max_turns) <= 500:
        raise ValueError("WORKBUDDY_MAX_TURNS 必须是 1 到 500。")

    args = command + [
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        permission_mode,
        "--max-turns",
        max_turns,
    ]
    completed = subprocess.run(
        args,
        input=request,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        shell=False,
        cwd=workspace,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "WorkBuddy CLI 返回非零退出码"
        raise RuntimeError(detail[-3000:])

    try:
        messages = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WorkBuddy 返回了无法解析的结果。") from exc
    if not isinstance(messages, list):
        raise RuntimeError("WorkBuddy 返回格式不符合预期。")

    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("type") != "result":
            continue
        if message.get("is_error") or message.get("subtype") != "success":
            raise RuntimeError(str(message.get("result") or "WorkBuddy 任务失败。")[-3000:])
        denials = message.get("permission_denials")
        if isinstance(denials, list) and denials:
            raise RuntimeError("WorkBuddy 需要人工授权，任务未确认完成。")
        result = message.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()

    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, list):
            texts = [part.get("text", "") for part in content if isinstance(part, dict)]
            result = "\n".join(text for text in texts if text).strip()
            if result:
                return result
    raise RuntimeError("WorkBuddy 没有返回最终文本结果。")

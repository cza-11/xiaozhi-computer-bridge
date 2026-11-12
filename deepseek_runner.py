"""Discover and run the official DeepSeek Harness headless CLI.

发现 DeepSeek Harness 命令，并通过标准输入交付任务。
"""

import os
import shutil
import subprocess
from pathlib import Path


def discover_deepseek() -> str | None:
    """Find an existing dsh launcher without downloading or installing it."""
    configured = os.environ.get("DEEPSEEK_HARNESS_CLI", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    found = shutil.which("dsh.cmd") or shutil.which("dsh.exe") or shutil.which("dsh")
    if found:
        return found
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if appdata:
        candidates.append(Path(appdata) / "npm" / "dsh.cmd")
    if local:
        programs = Path(local) / "Programs"
        if programs.is_dir():
            candidates.extend(programs.glob("*DeepSeek*/resources/runtime/cli/bin/dsh.cmd"))
    return next((str(path) for path in candidates if path.is_file()), None)


def run_deepseek(command: str, request: str, workspace: Path, timeout: int = 600) -> str:
    """Run one noninteractive task with no task text in the command line."""
    args = [command, "headless", "--permission-mode", "workspace-write", "--approval-policy", "never", "-"]
    completed = subprocess.run(
        args,
        input=request,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=workspace,
        timeout=timeout,
        shell=False,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr.strip() or "DeepSeek Harness 执行失败。")[-3000:])
    result = completed.stdout.strip()
    if not result:
        raise RuntimeError("DeepSeek Harness 未返回最终结果。")
    return result

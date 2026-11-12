"""Describe supported agents and route tasks by their strongest capabilities.

定义 Agent 能力画像，并依据关键词、用户指定和默认值选择执行器。
"""

from __future__ import annotations

import re
from collections.abc import Iterable


AGENT_PROFILES = {
    "workbuddy": {
        "display_name": "WorkBuddy",
        "strengths": [
            "office documents and spreadsheets",
            "email, calendar, and collaboration workflows",
            "browser and general desktop operations",
        ],
        "strengths_zh": "办公文档、表格、邮件、日历、浏览器和桌面操作",
        "keywords": (
            "word", "excel", "powerpoint", "ppt", "pdf", "office",
            "document", "spreadsheet", "presentation", "email", "mail",
            "calendar", "meeting", "browser", "form", "report", "crm",
            "文档", "表格", "幻灯片", "演示文稿", "邮件", "日历", "会议",
            "浏览器", "网页", "表单", "报告", "办公", "客户资料", "发消息",
        ),
    },
    "codex": {
        "display_name": "Codex",
        "strengths": [
            "software development and repository changes",
            "debugging, testing, and code review",
            "terminal automation, APIs, databases, and deployment",
        ],
        "strengths_zh": "编程、代码修改、调试测试、终端、接口、数据库和部署",
        "keywords": (
            "code", "coding", "program", "repository", "repo", "bug",
            "debug", "test", "api", "database", "sql", "git", "compile",
            "build", "deploy", "docker", "terminal", "shell", "powershell",
            "python", "javascript", "typescript", "java", "c++", "rust",
            "代码", "编程", "程序", "项目", "仓库", "报错", "错误", "调试",
            "测试", "接口", "数据库", "编译", "构建", "部署", "终端", "脚本",
            "重构", "代码审查", "单元测试", "源码",
        ),
    },
    "deepseek": {
        "display_name": "DeepSeek Harness",
        "strengths": [
            "repository exploration and analysis",
            "complex reasoning, planning, and research",
            "software changes and command line work",
        ],
        "strengths_zh": "项目分析、复杂推理、方案规划、研究和编程",
        "keywords": (
            "deepseek", "harness", "research", "reasoning", "analyze",
            "analysis", "investigate", "explore", "architecture", "plan",
            "方案", "规划", "分析", "调研", "研究", "推理", "探索",
            "架构", "比较", "梳理", "调查",
        ),
    },
}


def supported_agents() -> set[str]:
    """Return all agents for which this bridge has an execution adapter."""
    return set(AGENT_PROFILES)


def capability_catalog(available_agents: Iterable[str]) -> list[dict]:
    """Return public capability metadata for locally available agents."""
    catalog = []
    for name in sorted(set(available_agents)):
        profile = AGENT_PROFILES.get(name)
        if profile:
            catalog.append(
                {
                    "name": name,
                    "display_name": profile["display_name"],
                    "strengths": list(profile["strengths"]),
                }
            )
    return catalog


def summarize_task(request: str, limit: int = 80) -> str:
    """Create a short, deterministic summary suitable for spoken responses."""
    text = re.sub(r"\s+", " ", request or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip(" ,，。.;；:：") + "…"


def route_task(
    request: str,
    available_agents: Iterable[str],
    preferred_agent: str | None = None,
    fallback_agent: str = "workbuddy",
) -> tuple[str, str]:
    """Select an available agent and return the selection reason.

    An explicit agent always wins when it is available. Automatic routing then
    scores capability keywords. Ties use the configured fallback so general
    tasks retain a predictable customer experience.
    """
    available = set(available_agents) & supported_agents()
    if not available:
        raise ValueError("no_available_agent")
    if preferred_agent:
        if preferred_agent not in supported_agents():
            raise ValueError("invalid_agent")
        if preferred_agent not in available:
            raise ValueError("agent_unavailable")
        return preferred_agent, "explicit"

    # Keyword scores are heuristics, not a measurement of actual capability.
    # 关键词评分是预设规则，并不代表对 Agent 实际能力的动态评测。
    normalized = (request or "").casefold()
    scores = {
        agent: sum(1 for keyword in AGENT_PROFILES[agent]["keywords"] if keyword in normalized)
        for agent in available
    }
    highest = max(scores.values())
    winners = sorted(agent for agent, score in scores.items() if score == highest)
    if highest > 0 and len(winners) == 1:
        return winners[0], "capability_match"
    if fallback_agent in available:
        return fallback_agent, "fallback"
    return sorted(available)[0], "fallback"

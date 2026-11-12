import unittest

from agent_router import capability_catalog, route_task, summarize_task


class AgentRouterTest(unittest.TestCase):
    def test_routes_software_work_to_codex(self):
        agent, reason = route_task(
            "请修复 Python 项目的单元测试和 API 错误",
            {"workbuddy", "codex"},
        )
        self.assertEqual((agent, reason), ("codex", "capability_match"))

    def test_routes_office_work_to_workbuddy(self):
        agent, reason = route_task(
            "请整理 Excel 表格并通过邮件发送会议报告",
            {"workbuddy", "codex"},
        )
        self.assertEqual((agent, reason), ("workbuddy", "capability_match"))

    def test_routes_research_work_to_deepseek(self):
        self.assertEqual(
            route_task("请调研和分析这个技术方案", {"workbuddy", "codex", "deepseek"}),
            ("deepseek", "capability_match"),
        )

    def test_respects_explicit_available_agent(self):
        self.assertEqual(
            route_task("修复代码", {"workbuddy", "codex"}, "workbuddy"),
            ("workbuddy", "explicit"),
        )

    def test_falls_back_when_only_one_agent_is_installed(self):
        self.assertEqual(
            route_task("整理文件", {"codex"}, fallback_agent="workbuddy"),
            ("codex", "fallback"),
        )

    def test_rejects_an_unavailable_explicit_agent(self):
        with self.assertRaisesRegex(ValueError, "agent_unavailable"):
            route_task("整理文件", {"codex"}, "workbuddy")

    def test_summary_is_compact_and_catalog_is_filtered(self):
        self.assertTrue(summarize_task("一" * 100).endswith("…"))
        catalog = capability_catalog({"codex", "not-supported"})
        self.assertEqual([item["name"] for item in catalog], ["codex"])


if __name__ == "__main__":
    unittest.main()

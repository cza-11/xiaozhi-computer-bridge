import os
import unittest
from unittest.mock import patch

from mcp_endpoint import insecure_ws_enabled, validate_mcp_endpoint


class McpEndpointTest(unittest.TestCase):
    def test_accepts_custom_secure_server_and_path(self):
        endpoint = 'wss://mcp.customer.example/custom/endpoint?token=secret'
        self.assertEqual(validate_mcp_endpoint(endpoint), endpoint)

    def test_accepts_loopback_ws_for_local_testing(self):
        self.assertEqual(
            validate_mcp_endpoint('ws://127.0.0.1:9000/mcp'),
            'ws://127.0.0.1:9000/mcp',
        )

    def test_rejects_remote_plaintext_ws_by_default(self):
        with self.assertRaisesRegex(ValueError, 'wss://'):
            validate_mcp_endpoint('ws://192.168.1.20:9000/mcp')

    def test_accepts_remote_plaintext_ws_when_explicitly_enabled(self):
        endpoint = 'ws://192.168.1.20:9000/mcp'
        self.assertEqual(validate_mcp_endpoint(endpoint, allow_insecure_ws=True), endpoint)

    def test_rejects_non_websocket_scheme(self):
        with self.assertRaisesRegex(ValueError, 'wss:// 或 ws://'):
            validate_mcp_endpoint('https://mcp.customer.example/mcp')

    def test_rejects_credentials_and_fragments(self):
        with self.assertRaisesRegex(ValueError, '用户名或密码'):
            validate_mcp_endpoint('wss://user:pass@mcp.customer.example/mcp')
        with self.assertRaisesRegex(ValueError, 'URL 片段'):
            validate_mcp_endpoint('wss://mcp.customer.example/mcp#token')

    def test_reads_insecure_ws_switch_from_environment(self):
        with patch.dict(os.environ, {'XIAOZHI_MCP_ALLOW_INSECURE_WS': 'true'}, clear=False):
            self.assertTrue(insecure_ws_enabled())


if __name__ == '__main__':
    unittest.main()

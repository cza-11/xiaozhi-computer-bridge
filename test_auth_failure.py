import asyncio
import queue
import unittest

import bridge
import xiaozhi_mcp


class AuthenticationFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_401_stops_retrying_and_sets_health_state(self):
        async def reject(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(
                b'HTTP/1.1 401 Unauthorized\r\n'
                b'Content-Length: 0\r\n'
                b'Connection: close\r\n\r\n'
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(reject, '127.0.0.1', 0)
        try:
            port = server.sockets[0].getsockname()[1]
            await asyncio.wait_for(
                xiaozhi_mcp.connect(
                    f'ws://127.0.0.1:{port}/mcp/?token=test', queue.Queue()
                ),
                timeout=3,
            )
            state = bridge.get_runtime_state()
            self.assertEqual(state['mcp_status'], 'auth_failed')
            self.assertEqual(state['mcp_http_status'], 401)
        finally:
            server.close()
            await server.wait_closed()


if __name__ == '__main__':
    unittest.main()

"""Configurable MCP client sharing one serial queue across local agents.

主动连接用户配置的小智 MCP 地址，注册工具并转发任务和状态通知。
"""
import argparse
import asyncio
import getpass
import json
import os
import queue
import threading
import uuid
from http.server import ThreadingHTTPServer

import websockets
from websockets.exceptions import InvalidStatus
import bridge
from agent_router import AGENT_PROFILES
from codex_runner import discover_codex
from deepseek_runner import discover_deepseek
from mcp_endpoint import insecure_ws_enabled, validate_mcp_endpoint
from runtime_config import bridge_host, bridge_port, default_agent
from workbuddy_runner import discover_workbuddy_command
import xiaozhi_adapter

def build_tools(available_agents, selected_default):
    """Advertise only the agents that are usable on this computer."""
    agent_values = sorted(available_agents)
    capability_text = '；'.join(
        f"{AGENT_PROFILES[name]['display_name']}擅长{AGENT_PROFILES[name]['strengths_zh']}"
        for name in agent_values
        if name in AGENT_PROFILES
    )
    tools = [
        {'name': 'submit_computer_task', 'description': f'用户要求电脑处理任务时优先调用。当前电脑检测到：{capability_text}。用户未明确指定执行器时必须省略 agent，由系统根据任务内容自动选择最擅长的执行器；能力匹配不明确时才使用默认执行器 {selected_default}。每次只允许一个任务，必须逐字播报 speech 字段。', 'inputSchema': {'type': 'object', 'properties': {'request': {'type': 'string', 'description': '完整任务和必要上下文'}, 'agent': {'type': 'string', 'enum': agent_values, 'description': '仅当用户明确说出执行器名称时填写；否则省略，由程序自动路由'}}, 'required': ['request'], 'additionalProperties': False}},
        {'name': 'get_computer_task', 'description': '按短编号查询任务状态和结果。必须逐字播报 speech 字段，不得要求或朗读 UUID。', 'inputSchema': {'type': 'object', 'properties': {'task_number': {'type': 'integer', 'minimum': 1, 'description': '用户听到的任务数字，例如任务1填写1'}}, 'required': ['task_number'], 'additionalProperties': False}},
        {'name': 'get_current_computer_task', 'description': '当用户询问“当前任务是什么”“现在在做什么”或类似问题时调用。返回当前任务或最近任务的状态、概要和执行器，必须逐字播报 speech 字段。', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    ]
    if 'workbuddy' in available_agents:
        tools.append({'name': 'submit_workbuddy_task', 'description': '仅当用户明确指定 WorkBuddy 时调用。任务与其他执行器共用同一个单任务锁，必须逐字播报 speech 字段。', 'inputSchema': {'type': 'object', 'properties': {'request': {'type': 'string'}}, 'required': ['request'], 'additionalProperties': False}})
    if 'codex' in available_agents:
        tools.append({'name': 'submit_codex_task', 'description': '仅当用户明确指定 Codex 时调用；未指定执行器时应调用 submit_computer_task。任务与其他执行器共用同一个单任务锁，必须逐字播报 speech 字段。', 'inputSchema': {'type': 'object', 'properties': {'request': {'type': 'string'}}, 'required': ['request'], 'additionalProperties': False}})
        tools.append({'name': 'get_codex_task', 'description': '兼容旧版的任务查询工具；新请求优先使用 get_computer_task。', 'inputSchema': {'type': 'object', 'properties': {'task_number': {'type': 'integer', 'minimum': 1}}, 'required': ['task_number'], 'additionalProperties': False}})
    if 'deepseek' in available_agents:
        tools.append({'name': 'submit_deepseek_task', 'description': '仅当用户明确指定 DeepSeek Harness 时调用；其他情况使用 submit_computer_task 自动路由。与其他执行器共用单任务锁，必须逐字播报 speech 字段。', 'inputSchema': {'type': 'object', 'properties': {'request': {'type': 'string'}}, 'required': ['request'], 'additionalProperties': False}})
    return tools


TOOLS = build_tools({'workbuddy', 'codex', 'deepseek'}, 'workbuddy')

def handle(message):
    """Handle MCP JSON-RPC requests without waiting for task completion."""
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        return None
    if 'id' not in message:
        return None  # JSON-RPC notifications do not receive a response.
    ident, method = message['id'], message.get('method')
    params = message.get('params') or {}
    if not isinstance(params, dict):
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Invalid params'}}
    if method == 'initialize':
        version = params.get('protocolVersion', '2024-11-05')
        result = {'protocolVersion': version, 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'xiaozhi-computer-task-bridge', 'version': '0.3.0'}}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        name, args = params.get('name'), params.get('arguments', {})
        if not isinstance(args, dict):
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Invalid arguments'}}
        request_id = uuid.uuid4().hex
        if name == 'submit_computer_task':
            # Omit the agent unless the user explicitly selected one. The
            # local router owns capability matching and availability checks.
            value = xiaozhi_adapter.submit_computer_task(
                args.get('request'), request_id, agent=args.get('agent')
            )
        elif name == 'submit_workbuddy_task':
            value = xiaozhi_adapter.submit_workbuddy_task(args.get('request'), request_id)
        elif name == 'submit_codex_task':
            value = xiaozhi_adapter.submit_codex_task(args.get('request'), request_id)
        elif name == 'submit_deepseek_task':
            value = xiaozhi_adapter.submit_deepseek_task(args.get('request'), request_id)
        elif name in {'get_computer_task', 'get_codex_task'}:
            value = xiaozhi_adapter.get_computer_task(args.get('task_number'))
        elif name == 'get_current_computer_task':
            value = xiaozhi_adapter.get_current_computer_task()
        else:
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Unknown tool'}}
        # Put the sentence to be spoken first.  A completed task may append its
        # result for the model to summarize, without exposing the UUID.
        text = value.get('speech', '任务处理失败。')
        if name != 'get_current_computer_task' and value.get('status') == 'succeeded' and value.get('result'):
            text += '\n任务结果：' + str(value['result'])
        expected_refusal = value.get('code') == 'task_in_progress'
        result = {'content': [{'type': 'text', 'text': text}], 'isError': not value.get('ok', False) and not expected_refusal}
    else:
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}

async def send_task_notifications(ws, events):
    """Forward terminal task events over MCP without blocking tool calls."""
    # 完成事件通过 MCP 通知尽力发送；协议没有送达确认。
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.25)
            continue

        number = event.get('task_number')
        status = event.get('status')
        if status == 'succeeded':
            speech = f'当前任务{number}已完成。'
            level = 'info'
        elif status == 'unknown':
            speech = f'当前任务{number}的结果暂时无法确认。'
            level = 'warning'
        else:
            speech = f'当前任务{number}未能完成。'
            level = 'error'
        message = {
            'jsonrpc': '2.0',
            'method': 'notifications/message',
            'params': {
                'level': level,
                'logger': 'xiaozhi-codex-task-bridge',
                'data': {
                    'event': 'computer_task_status',
                    'task_number': number,
                    'agent': event.get('agent'),
                    'status': status,
                    'speech': speech,
                },
            },
        }
        try:
            await ws.send(json.dumps(message, ensure_ascii=False))
            print(speech + ' 已发送到小智 MCP 通知通道。', flush=True)
        except Exception:
            # Keep the event for the next WebSocket connection.  Delivery is
            # best-effort because the protocol has no acknowledgement.
            events.put(event)
            raise
        finally:
            events.task_done()


async def connect(endpoint, events):
    """Maintain the outbound MCP WebSocket with bounded exponential backoff."""
    delay = 1
    bridge.set_mcp_status('connecting')
    while True:
        try:
            # websockets 15 inherits the Windows system proxy by default.
            # Connect to the MCP endpoint directly so an HTTP proxy cannot
            # rewrite the WebSocket Upgrade and cause a non-101 response.
            # Codex can still use CODEX_BRIDGE_PROXY independently.
            async with websockets.connect(
                endpoint,
                open_timeout=15,
                max_size=65536,
                ping_interval=20,
                proxy=None,
            ) as ws:
                bridge.set_mcp_status('connected')
                print('已连接小智 MCP 接入点，等待工具请求。', flush=True)
                delay = 1
                sender = asyncio.create_task(send_task_notifications(ws, events))
                try:
                    async for raw in ws:
                        try:
                            request = json.loads(raw)
                            # Run bounded loopback HTTP calls in a worker thread
                            # so they never block the WebSocket event loop.
                            response = await asyncio.to_thread(handle, request)
                            if response is not None:
                                await ws.send(json.dumps(response, ensure_ascii=False))
                        except (ValueError, TypeError):
                            print('忽略无效请求。', flush=True)
                finally:
                    sender.cancel()
                    try:
                        await sender
                    except asyncio.CancelledError:
                        pass
        except InvalidStatus as exc:
            # Log only the status code. Never log the response object, URL, or
            # query string because they may expose the endpoint token.
            status = exc.response.status_code
            if status in {401, 403}:
                hint = '，服务器拒绝了接入令牌，请重新生成 MCP 接入点'
                bridge.set_mcp_status('auth_failed', status)
            elif status == 404:
                hint = '，服务器未找到该 MCP 路径'
                bridge.set_mcp_status('retrying', status)
            elif status == 429:
                hint = '，连接请求过于频繁'
                bridge.set_mcp_status('retrying', status)
            elif 500 <= status <= 599:
                hint = '，服务器或网关暂时异常'
                bridge.set_mcp_status('retrying', status)
            else:
                hint = ''
                bridge.set_mcp_status('retrying', status)
            if status in {401, 403}:
                print(f'WebSocket 握手失败：HTTP {status}{hint}。程序已停止，请更换接入点后重新启动。', flush=True)
                return
            print(f'WebSocket 握手失败：HTTP {status}{hint}；{delay} 秒后重试。', flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        except Exception as exc:
            # Exception details may contain the tokenized URL; do not print them.
            bridge.set_mcp_status('retrying')
            print(f'连接中断或失败（{type(exc).__name__}），{delay} 秒后重试。', flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

def main():
    """Discover agents, start the local bridge, then connect to XiaoZhi."""
    # 启动时发现受支持的 Agent，之后启动工作线程和本机 HTTP 服务。
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['mock', 'codex', 'live'], default='mock', help='codex 是兼容旧启动脚本的 live 别名')
    parser.add_argument('--sandbox', choices=['read-only', 'workspace-write'], default='read-only')
    parser.add_argument('--workbuddy-permission', choices=['default', 'acceptEdits', 'plan'], default='acceptEdits')
    parser.add_argument(
        '--allow-insecure-ws',
        action='store_true',
        help='显式允许连接非本机的 ws:// MCP 地址（生产环境不建议）',
    )
    args = parser.parse_args()
    endpoint = os.environ.get('XIAOZHI_MCP_ENDPOINT') or getpass.getpass(
        '粘贴 MCP 接入点地址（不显示）：'
    )
    try:
        endpoint = validate_mcp_endpoint(
            endpoint,
            allow_insecure_ws=args.allow_insecure_ws or insecure_ws_enabled(),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    codex = discover_codex()
    workbuddy_command = discover_workbuddy_command()
    deepseek_command = discover_deepseek()
    available_agents = set()
    if workbuddy_command:
        available_agents.add('workbuddy')
    if codex:
        available_agents.add('codex')
    if deepseek_command:
        available_agents.add('deepseek')
    if args.mode in {'codex', 'live'} and not available_agents:
        raise SystemExit('未找到可用执行器；请至少安装并配置 WorkBuddy、Codex 或 DeepSeek Harness。')
    selected_default = default_agent(available_agents or {'workbuddy', 'codex', 'deepseek'})
    os.environ['XIAOZHI_DEFAULT_AGENT'] = selected_default
    bridge.DEFAULT_AGENT = selected_default
    bridge.set_runtime_agents(available_agents, selected_default)
    global TOOLS
    TOOLS = build_tools(available_agents or {'workbuddy', 'codex', 'deepseek'}, selected_default)
    bridge.init()
    events = queue.Queue()
    threading.Thread(
        target=bridge.worker,
        args=(args.mode, codex, args.sandbox, events.put, workbuddy_command, args.workbuddy_permission, deepseek_command),
        daemon=True,
    ).start()
    try:
        host, port = bridge_host(), bridge_port()
        server = ThreadingHTTPServer((host, port), bridge.Handler)
    except OSError:
        raise SystemExit(f'本机 {bridge_host()}:{bridge_port()} 已被占用，请先关闭旧任务桥。')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'本机任务桥已启动，执行模式：{args.mode}，可用执行器：{", ".join(sorted(available_agents))}，默认：{selected_default}。按 Ctrl+C 停止。', flush=True)
    try:
        asyncio.run(connect(endpoint, events))
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()

if __name__ == '__main__':
    main()

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import queue

import websockets
import xiaozhi_adapter
import xiaozhi_mcp

async def run():
    events = queue.Queue()

    async def fake_cloud(ws):
        for request in [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'submit_computer_task','arguments':{'request':'测试后台任务'}}},
        ]:
            await ws.send(json.dumps(request))
            response = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert response['id'] == request['id'] and 'result' in response, response
            if request['id'] == 2:
                assert len(response['result']['tools']) == 7
                generic = next(tool for tool in response['result']['tools'] if tool['name'] == 'submit_computer_task')
                assert 'agent' not in generic['inputSchema']['required']
                assert any(tool['name'] == 'get_current_computer_task' for tool in response['result']['tools'])
                assert any(tool['name'] == 'submit_deepseek_task' for tool in response['result']['tools'])
            if request['id'] == 3:
                text=response['result']['content'][0]['text']
                assert text == '当前任务1已开始。', text
                await ws.send(json.dumps({'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'get_current_computer_task','arguments':{}}}))
                current = json.loads(await asyncio.wait_for(ws.recv(), 2))
                current_text = current['result']['content'][0]['text']
                assert current['id'] == 4 and '任务概要：测试后台任务' in current_text, current
                events.put({'event':'task_completed', 'task_number':1, 'status':'succeeded'})
                notification = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert notification['method'] == 'notifications/message', notification
                assert notification['params']['data']['speech'] == '当前任务1已完成。', notification
                return 1

    loop = asyncio.get_running_loop()
    done = loop.create_future()
    async def handler(ws):
        try: done.set_result(await fake_cloud(ws))
        except Exception as e: done.set_exception(e)
    async with websockets.serve(handler, '127.0.0.1', 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = asyncio.create_task(xiaozhi_mcp.connect(f'ws://127.0.0.1:{port}', events))
        start=time.monotonic()
        task_number=await asyncio.wait_for(done, 5)
        assert time.monotonic()-start < 2
        client.cancel()
        try: await client
        except asyncio.CancelledError: pass
    for _ in range(40):
        result=await asyncio.to_thread(xiaozhi_adapter.get_codex_task,task_number)
        if result.get('status') == 'succeeded': break
        await asyncio.sleep(.2)
    assert result['status']=='succeeded',result
    assert result['agent']=='codex',result
    assert result['routing_reason']=='capability_match',result
    print('PASS MCP short number, completion notification, and asynchronous result')

with tempfile.TemporaryDirectory() as tempdir:
    env=dict(os.environ, XIAOZHI_BRIDGE_DB=os.path.join(tempdir,'test.sqlite3'))
    p=subprocess.Popen([sys._base_executable,'bridge.py','--mode','mock','--port','18766'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert p.stdout.readline()
        xiaozhi_adapter.BASE_URL='http://127.0.0.1:18766'
        asyncio.run(run())
    finally:
        p.terminate()
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill(); p.wait()

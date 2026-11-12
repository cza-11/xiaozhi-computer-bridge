import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

BASE='http://127.0.0.1:18765'
tempdir = tempfile.TemporaryDirectory()
env = dict(os.environ, XIAOZHI_BRIDGE_DB=os.path.join(tempdir.name, 'test.sqlite3'))
server=subprocess.Popen([sys._base_executable,'bridge.py','--mode','mock','--port','18765'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
try:
    assert server.stdout.readline().strip()
    def call(path,data=None):
        req=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=3) as response: return json.load(response)
    key=uuid.uuid4().hex
    start=time.monotonic()
    first=call('/tasks',{'request':'模拟语音任务','idempotency_key':key})
    assert first['agent'] == 'workbuddy', first
    duration=time.monotonic()-start
    assert duration<1, duration
    second=call('/tasks',{'request':'模拟语音任务','idempotency_key':key})
    assert first['task_number']==second['task_number']==1
    try:
        call('/tasks',{'request':'不应并发执行','idempotency_key':uuid.uuid4().hex})
        raise AssertionError('second active task should be refused')
    except urllib.error.HTTPError as error:
        assert error.code == 409
        refusal = json.load(error)
        assert refusal['error'] == 'task_in_progress'
    state=call('/tasks/by-number/'+str(first['task_number']))
    assert state['status'] in ('queued','running')
    current=call('/tasks/current')
    assert current['is_active'] and current['task_number'] == 1, current
    assert current['summary'] == '模拟语音任务', current
    assert current['routing_reason'] == 'fallback', current
    health = call('/health')
    assert health['status']=='ok'
    assert {profile['name'] for profile in health['agent_profiles']} == {'workbuddy', 'codex', 'deepseek'}, health
    until=time.monotonic()+10
    while time.monotonic()<until:
        state=call('/tasks/by-number/'+str(first['task_number']))
        if state['status']=='succeeded': break
        time.sleep(.2)
    assert state['status']=='succeeded' and '模拟语音任务' in state['result'], state
    # Race several submissions: the database invariant must accept exactly one.
    def racing_submit(index):
        try:
            return ('accepted', call('/tasks', {'request':f'并发任务{index}', 'idempotency_key':uuid.uuid4().hex}))
        except urllib.error.HTTPError as error:
            return ('refused', error.code)
    with ThreadPoolExecutor(max_workers=5) as pool:
        race = list(pool.map(racing_submit, range(5)))
    accepted = [value for kind, value in race if kind == 'accepted']
    refused = [value for kind, value in race if kind == 'refused']
    assert len(accepted) == 1 and accepted[0]['task_number'] == 2, race
    assert refused == [409] * 4, race

    import xiaozhi_adapter
    xiaozhi_adapter.BASE_URL = BASE
    spoken_current = xiaozhi_adapter.get_current_computer_task()
    assert spoken_current['ok'] and '任务概要：并发任务' in spoken_current['speech'], spoken_current
    busy = xiaozhi_adapter.submit_codex_task('第三个任务', uuid.uuid4().hex)
    assert not busy['ok'] and busy['code'] == 'task_in_progress', busy
    assert busy['speech'] == '抱歉，前一个任务还在进行中，请稍后再布置任务。'
    assert xiaozhi_adapter.get_computer_task(2)['ok']
    until=time.monotonic()+10
    while time.monotonic()<until:
        state=call('/tasks/by-number/2')
        if state['status']=='succeeded': break
        time.sleep(.2)
    explicit = xiaozhi_adapter.submit_codex_task('明确交给 Codex', uuid.uuid4().hex)
    assert explicit['ok'] and explicit['agent'] == 'codex', explicit
    until=time.monotonic()+10
    while time.monotonic()<until:
        state=call('/tasks/by-number/'+str(explicit['task_number']))
        if state['status']=='succeeded': break
        time.sleep(.2)
    assert state['status'] == 'succeeded' and state['agent'] == 'codex', state
    deepseek = xiaozhi_adapter.submit_computer_task('请调研并分析技术方案', uuid.uuid4().hex)
    assert deepseek['ok'] and deepseek['agent'] == 'deepseek', deepseek
    until=time.monotonic()+10
    while time.monotonic()<until:
        state=call('/tasks/by-number/'+str(deepseek['task_number']))
        if state['status']=='succeeded': break
        time.sleep(.2)
    assert state['status'] == 'succeeded' and state['agent'] == 'deepseek', state
    print('PASS: automatic routing, explicit Codex, current-task summary, atomic single-task refusal, idempotency, and async result; seconds=',round(duration,3))
finally:
    server.terminate()
    try: server.wait(timeout=5)
    except subprocess.TimeoutExpired: server.kill()
    tempdir.cleanup()

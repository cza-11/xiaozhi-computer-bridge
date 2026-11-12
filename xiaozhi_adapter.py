"""Import these functions from a self-hosted XiaoZhi backend tool adapter.

The server must run on the same host or use a separately secured tunnel. A
successful submission only means that the job was queued, not completed.
适配层调用本机 HTTP 接口，并生成小智可以直接播报的中文文本。
"""
import json
import urllib.error
import urllib.request
import uuid

from runtime_config import bridge_base_url

BASE_URL = bridge_base_url()

def submit_computer_task(request: str, request_id: str = '', agent: str | None = None) -> dict:
    """Submit a task and let the bridge route it unless an agent is explicit."""
    if not isinstance(request, str) or not request.strip():
        return {'ok': False, 'error': '任务内容不能为空'}
    if agent is not None and agent not in {'workbuddy', 'codex', 'deepseek'}:
        return {'ok': False, 'code': 'invalid_agent', 'speech': '任务执行器无效。'}
    payload = {'request': request, 'idempotency_key': request_id or uuid.uuid4().hex}
    if agent is not None:
        payload['agent'] = agent
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(BASE_URL + '/tasks', data=data, headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            result = json.load(response)
        number = int(result['task_number'])
        return {
            'ok': True,
            'task_number': number,
            'status': result['status'],
            'agent': result.get('agent'),
            'summary': result.get('summary'),
            'routing_reason': result.get('routing_reason'),
            'speech': f'当前任务{number}已开始。',
        }
    except urllib.error.HTTPError as e:
        # A busy bridge is an expected refusal, not a system failure.  Return a
        # natural-language sentence so XiaoZhi can speak it verbatim.
        if e.code == 409:
            try:
                result = json.load(e)
            except (ValueError, OSError):
                result = {}
            if result.get('error') == 'task_in_progress':
                return {
                    'ok': False,
                    'code': 'task_in_progress',
                    'task_number': result.get('task_number'),
                    'status': result.get('status'),
                    'speech': '抱歉，前一个任务还在进行中，请稍后再布置任务。',
                }
        if e.code == 503:
            return {'ok': False, 'code': 'agent_unavailable', 'speech': '当前电脑没有可用于此任务的执行器。'}
        return {'ok': False, 'code': 'http_error', 'speech': '任务提交失败，请稍后再试。'}
    except (OSError, ValueError, KeyError) as e:
        return {'ok': False, 'code': 'bridge_unavailable', 'speech': '任务桥暂时不可用，请稍后再试。', 'detail': str(e)}


def submit_workbuddy_task(request: str, request_id: str = '') -> dict:
    return submit_computer_task(request, request_id, agent='workbuddy')


def submit_codex_task(request: str, request_id: str = '') -> dict:
    """Keep the legacy Codex tool entry point compatible after upgrades."""
    return submit_computer_task(request, request_id, agent='codex')


def submit_deepseek_task(request: str, request_id: str = '') -> dict:
    """Submit an explicitly requested DeepSeek Harness task."""
    return submit_computer_task(request, request_id, agent='deepseek')

def get_computer_task(task_number: int) -> dict:
    """Query by the short number spoken to the user; UUIDs stay internal."""
    if isinstance(task_number, bool) or not isinstance(task_number, int) or task_number < 1:
        return {'ok': False, 'code': 'invalid_task_number', 'speech': '任务编号无效。'}
    try:
        with urllib.request.urlopen(BASE_URL + '/tasks/by-number/' + str(task_number), timeout=2) as response:
            result = json.load(response)
        status = result.get('status')
        if status == 'succeeded':
            speech = f'当前任务{task_number}已完成。'
        elif status in {'queued', 'running'}:
            speech = f'当前任务{task_number}还在进行中。'
        elif status == 'unknown':
            speech = f'当前任务{task_number}的结果暂时无法确认。'
        else:
            speech = f'当前任务{task_number}未能完成。'
        return {'ok': True, **result, 'speech': speech}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'ok': False, 'code': 'not_found', 'speech': f'没有找到任务{task_number}。'}
        return {'ok': False, 'code': 'http_error', 'speech': '任务查询失败，请稍后再试。'}
    except (OSError, ValueError) as e:
        return {'ok': False, 'code': 'bridge_unavailable', 'speech': '任务查询失败，请稍后再试。', 'detail': str(e)}


def get_codex_task(task_number: int) -> dict:
    """Support the legacy query name for tasks run by either agent."""
    return get_computer_task(task_number)


def get_current_computer_task() -> dict:
    """Return a spoken summary of the active task, or the most recent task."""
    # 返回活动任务概要；没有活动任务时描述最近任务。
    try:
        with urllib.request.urlopen(BASE_URL + '/tasks/current', timeout=2) as response:
            result = json.load(response)
        number = int(result['task_number'])
        summary = result.get('summary') or '没有任务概要'
        agent = {'workbuddy': 'WorkBuddy', 'codex': 'Codex', 'deepseek': 'DeepSeek Harness'}.get(
            result.get('agent'), result.get('agent') or '未知执行器'
        )
        status = result.get('status')
        if result.get('is_active'):
            if status == 'queued':
                speech = f'当前任务是任务{number}，正在等待处理。任务概要：{summary}。将由{agent}处理。'
            elif status == 'unknown':
                speech = f'当前任务是任务{number}，执行结果需要确认。任务概要：{summary}。执行器是{agent}。'
            else:
                speech = f'当前任务是任务{number}，正在进行中。任务概要：{summary}。由{agent}处理。'
        else:
            state = '已经完成' if status == 'succeeded' else '未能完成'
            speech = f'当前没有正在进行的任务。最近的是任务{number}，任务概要：{summary}，{state}。'
        return {'ok': True, **result, 'speech': speech}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'ok': True, 'status': 'empty', 'speech': '当前没有正在进行的任务，也没有历史任务。'}
        return {'ok': False, 'code': 'http_error', 'speech': '当前任务查询失败，请稍后再试。'}
    except (OSError, ValueError, KeyError) as e:
        return {'ok': False, 'code': 'bridge_unavailable', 'speech': '当前任务查询失败，请稍后再试。', 'detail': str(e)}

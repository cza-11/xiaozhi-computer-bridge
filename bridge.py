"""Local asynchronous bridge between XiaoZhi and one computer task at a time.

本机任务桥：负责持久化、单任务互斥、Agent 分发和结果查询。
"""
import argparse
import json
import os
import queue
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent_router import capability_catalog, route_task, summarize_task, supported_agents
from codex_runner import discover_codex
from deepseek_runner import discover_deepseek, run_deepseek
from runtime_config import bridge_host, bridge_port, data_dir, default_agent, workspace
from workbuddy_runner import discover_workbuddy_command, run_workbuddy

ROOT = Path(__file__).resolve().parent
WORKSPACE = workspace()
DATA_DIR = data_dir(ROOT)
DB = Path(os.environ.get('XIAOZHI_BRIDGE_DB', str(DATA_DIR / 'tasks.sqlite3')))
DEFAULT_AGENT = default_agent()
# Only one task may be queued/running/unknown at a time.  The queue remains
# bounded as a second line of defence, while the database index below is the
# authoritative cross-thread single-task guard.
# SQLite 唯一索引负责跨请求线程互斥；有界队列提供第二层保护。
jobs = queue.Queue(maxsize=1)
runtime_state_lock = threading.Lock()
runtime_state = {
    'mcp_status': 'disconnected',
    'mcp_http_status': None,
    'available_agents': [],
    'agent_profiles': [],
    'default_agent': DEFAULT_AGENT,
}


def set_mcp_status(status, http_status=None):
    """Expose credential-free cloud status through the local health check."""
    with runtime_state_lock:
        runtime_state['mcp_status'] = status
        runtime_state['mcp_http_status'] = http_status


def set_runtime_agents(available_agents, selected_default):
    """Publish detected agents and the selected default to health checks."""
    with runtime_state_lock:
        runtime_state['available_agents'] = sorted(available_agents)
        runtime_state['agent_profiles'] = capability_catalog(available_agents)
        runtime_state['default_agent'] = selected_default


def get_runtime_state():
    """Return an atomic snapshot of non-secret runtime state."""
    with runtime_state_lock:
        return dict(runtime_state)

@contextmanager
def db():
    """Open a short-lived transactional SQLite connection."""
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def init():
    """Create or migrate the durable task store and recover queued work."""
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, request TEXT NOT NULL, summary TEXT, agent TEXT NOT NULL DEFAULT "workbuddy", routing_reason TEXT, status TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL, idem TEXT UNIQUE, display_no INTEGER)')
        # Upgrade databases created by the previous UUID-only implementation.
        columns = {row['name'] for row in c.execute('PRAGMA table_info(tasks)')}
        if 'display_no' not in columns:
            c.execute('ALTER TABLE tasks ADD COLUMN display_no INTEGER')
        if 'agent' not in columns:
            # Historical rows in the old schema were all executed by Codex.
            c.execute("ALTER TABLE tasks ADD COLUMN agent TEXT NOT NULL DEFAULT 'codex'")
        if 'summary' not in columns:
            c.execute('ALTER TABLE tasks ADD COLUMN summary TEXT')
        if 'routing_reason' not in columns:
            c.execute('ALTER TABLE tasks ADD COLUMN routing_reason TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS tasks_display_no ON tasks(display_no) WHERE display_no IS NOT NULL')
        # A partial unique index makes the one-active-task rule atomic even
        # when several HTTP request threads submit at exactly the same time.
        # 即使多个请求同时到达，数据库也只允许一个活动任务。
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS tasks_one_active ON tasks((1)) WHERE status IN ('queued','running','unknown')")
        c.execute("UPDATE tasks SET status='unknown', result='服务重启时任务正在运行，请核查执行结果', updated=? WHERE status='running'", (time.time(),))
        for row in c.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created"):
            jobs.put_nowait(row['id'])

def update(task_id, status, result=None):
    """Persist a task state transition and its bounded result text."""
    with db() as c:
        c.execute('UPDATE tasks SET status=?, result=?, updated=? WHERE id=?', (status, result, time.time(), task_id))

def publish_event(callback, event):
    """Best-effort event delivery must never change a task's final status."""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        pass


def worker(mode, codex, sandbox, on_event=None, workbuddy_command=None, workbuddy_permission='acceptEdits', deepseek_command=None):
    """Consume tasks serially and dispatch each one to its selected agent."""
    # 后台线程串行执行任务；执行结束后保存状态并尽力发送通知。
    # Only this background thread waits for an agent. MCP calls return as soon
    # as a task has been accepted into the durable queue.
    while True:
        task_id = jobs.get()
        row = None
        try:
            with db() as c:
                row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if row is None or row['status'] != 'queued':
                continue
            update(task_id, 'running')
            if mode == 'mock':
                time.sleep(3)
                update(task_id, 'succeeded', f"模拟 {row['agent']} 任务已完成：{row['request']}")
                publish_event(on_event, {'event': 'task_completed', 'task_number': row['display_no'], 'status': 'succeeded', 'agent': row['agent']})
                continue
            if row['agent'] == 'workbuddy':
                if not workbuddy_command:
                    raise RuntimeError('未找到 WorkBuddy CLI，请确认 WorkBuddy 已安装并登录。')
                result = run_workbuddy(
                    workbuddy_command,
                    row['request'],
                    WORKSPACE,
                    permission_mode=workbuddy_permission,
                )
            elif row['agent'] == 'deepseek':
                if not deepseek_command:
                    raise RuntimeError('未找到 DeepSeek Harness CLI，请确认已安装并配置模型。')
                result = run_deepseek(deepseek_command, row['request'], WORKSPACE)
            elif row['agent'] == 'codex':
                if not codex:
                    raise RuntimeError('未找到 codex.exe。')
                output = DATA_DIR / ('result-' + task_id + '.txt')
                args = [codex, 'exec', '--skip-git-repo-check', '--sandbox', sandbox, '--ephemeral', '-C', str(WORKSPACE), '-o', str(output), '-']
                # Apply the optional proxy only to the Codex child. Never
                # modify system networking or proxy the loopback task API.
                child_env = os.environ.copy()
                proxy = child_env.pop('CODEX_BRIDGE_PROXY', '')
                if proxy:
                    child_env['HTTPS_PROXY'] = proxy
                    child_env['HTTP_PROXY'] = proxy
                    child_env['NO_PROXY'] = 'localhost,127.0.0.1'
                proc = subprocess.run(args, input=row['request'], text=True, encoding='utf-8', errors='replace', stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600, shell=False, env=child_env)
                result = output.read_text(encoding='utf-8', errors='replace') if output.exists() else ''
                if proc.returncode != 0 or not result.strip():
                    raise RuntimeError('Codex 执行失败：' + proc.stderr[-3000:])
            else:
                raise RuntimeError('未知任务执行器：' + str(row['agent']))

            update(task_id, 'succeeded', result[:16000])
            publish_event(on_event, {'event': 'task_completed', 'task_number': row['display_no'], 'status': 'succeeded', 'agent': row['agent']})
        except subprocess.TimeoutExpired:
            update(task_id, 'unknown', '执行超时，结果待核查；请勿自动重试外部操作')
            publish_event(on_event, {'event': 'task_completed', 'task_number': row['display_no'], 'status': 'unknown', 'agent': row['agent']})
        except Exception as e:
            update(task_id, 'failed', '执行异常：' + str(e)[:500])
            task_number = row['display_no'] if row is not None else None
            agent = row['agent'] if row is not None else None
            publish_event(on_event, {'event': 'task_completed', 'task_number': task_number, 'status': 'failed', 'agent': agent})
        finally:
            jobs.task_done()

class Handler(BaseHTTPRequestHandler):
    """Expose the loopback-only submission, query, and health API."""
    def reply(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        # Idempotency protects callers from duplicate task creation after a
        # retry, while the partial SQLite index enforces global serialization.
        # 幂等键避免同一请求重复入库；不同请求共享单任务锁。
        if urlparse(self.path).path != '/tasks':
            return self.reply(404, {'error':'not_found'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if n < 1 or n > 16384: return self.reply(400, {'error':'invalid_size'})
            payload = json.loads(self.rfile.read(n).decode('utf-8'))
            request, idem = payload.get('request'), payload.get('idempotency_key')
            requested_agent = payload.get('agent')
            if not isinstance(request,str) or not request.strip() or len(request)>4000: return self.reply(400, {'error':'invalid_request'})
            if not isinstance(idem,str) or not 1<=len(idem)<=128: return self.reply(400, {'error':'invalid_idempotency_key'})
            if requested_agent is not None and requested_agent not in supported_agents(): return self.reply(400, {'error':'invalid_agent'})
            state = get_runtime_state()
            available_agents = set(state['available_agents']) or supported_agents()
            try:
                agent, routing_reason = route_task(
                    request,
                    available_agents,
                    preferred_agent=requested_agent,
                    fallback_agent=state['default_agent'] or DEFAULT_AGENT,
                )
            except ValueError as exc:
                code = str(exc)
                status = 503 if code in {'no_available_agent', 'agent_unavailable'} else 400
                return self.reply(status, {'error': code})
            summary = summarize_task(request)
            now = time.time(); task_id = uuid.uuid4().hex
            with db() as c:
                existing = c.execute('SELECT id, request, summary, agent, routing_reason, status FROM tasks WHERE idem=?', (idem,)).fetchone()
                if existing:
                    if existing['request'] != request.strip() or existing['agent'] != agent: return self.reply(409, {'error':'idempotency_conflict'})
                    number = c.execute('SELECT display_no FROM tasks WHERE id=?', (existing['id'],)).fetchone()['display_no']
                    return self.reply(200, {'task_number':number, 'status':existing['status'], 'agent':existing['agent'], 'summary':existing['summary'] or summary, 'routing_reason':existing['routing_reason'] or routing_reason})
                active = c.execute("SELECT display_no, status FROM tasks WHERE status IN ('queued','running','unknown') ORDER BY created LIMIT 1").fetchone()
                if active:
                    return self.reply(409, {'error':'task_in_progress', 'task_number':active['display_no'], 'status':active['status']})
                number = c.execute('SELECT COALESCE(MAX(display_no), 0) + 1 AS next_number FROM tasks').fetchone()['next_number']
                try:
                    c.execute('INSERT INTO tasks (id,request,summary,agent,routing_reason,status,result,created,updated,idem,display_no) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                              (task_id,request.strip(),summary,agent,routing_reason,'queued',None,now,now,idem,number))
                except sqlite3.IntegrityError:
                    # Another request won the race for the one active slot.
                    active = c.execute("SELECT display_no, status FROM tasks WHERE status IN ('queued','running','unknown') ORDER BY created LIMIT 1").fetchone()
                    if active:
                        return self.reply(409, {'error':'task_in_progress', 'task_number':active['display_no'], 'status':active['status']})
                    raise
            try: jobs.put_nowait(task_id)
            except queue.Full:
                update(task_id,'failed','队列已满')
                return self.reply(503, {'error':'queue_full','task_number':number})
            return self.reply(202, {'task_number':number,'status':'queued','agent':agent,'summary':summary,'routing_reason':routing_reason})
        except (ValueError, UnicodeError, AttributeError): return self.reply(400, {'error':'invalid_json'})
        except sqlite3.IntegrityError: return self.reply(409, {'error':'retry_same_key'})

    def do_GET(self):
        # XiaoZhi queries by the spoken number; UUID lookup remains available
        # only for local diagnostics and backward compatibility.
        path = urlparse(self.path).path
        if path == '/health': return self.reply(200, {'status':'ok', **get_runtime_state()})
        if path == '/tasks/current':
            # Prefer the active task; otherwise report the latest task.
            # 优先返回活动任务；没有活动任务时返回最近一条。
            with db() as c:
                row = c.execute("SELECT display_no,request,summary,agent,routing_reason,status,result,created,updated FROM tasks WHERE status IN ('queued','running','unknown') ORDER BY created LIMIT 1").fetchone()
                is_active = row is not None
                if row is None:
                    row = c.execute('SELECT display_no,request,summary,agent,routing_reason,status,result,created,updated FROM tasks ORDER BY created DESC LIMIT 1').fetchone()
            if row is None: return self.reply(404, {'error':'no_tasks'})
            return self.reply(200, {'task_number':row['display_no'],'summary':row['summary'] or summarize_task(row['request']),'agent':row['agent'],'routing_reason':row['routing_reason'],'status':row['status'],'result':row['result'],'created':row['created'],'updated':row['updated'],'is_active':is_active})
        if path.startswith('/tasks/by-number/'):
            try:
                task_number = int(path.removeprefix('/tasks/by-number/'))
            except ValueError:
                return self.reply(400, {'error':'invalid_task_number'})
            with db() as c:
                row = c.execute('SELECT display_no,request,summary,agent,routing_reason,status,result,created,updated FROM tasks WHERE display_no=?', (task_number,)).fetchone()
            if row is None: return self.reply(404, {'error':'not_found'})
            return self.reply(200, {'task_number':row['display_no'],'summary':row['summary'] or summarize_task(row['request']),'agent':row['agent'],'routing_reason':row['routing_reason'],'status':row['status'],'result':row['result'],'created':row['created'],'updated':row['updated']})
        if not path.startswith('/tasks/'): return self.reply(404, {'error':'not_found'})
        with db() as c:
            row = c.execute('SELECT id,display_no,agent,status,result,created,updated FROM tasks WHERE id=?', (path[7:],)).fetchone()
        if row is None: return self.reply(404, {'error':'not_found'})
        # The UUID endpoint is retained for local diagnostics only.  MCP users
        # receive display_no and never need to hear or repeat the internal ID.
        return self.reply(200, {'task_id':row['id'],'task_number':row['display_no'],'agent':row['agent'],'status':row['status'],'result':row['result'],'created':row['created'],'updated':row['updated']})

def main():
    """Run the standalone local bridge without the cloud MCP client."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['mock','codex','live'],default='mock', help='codex 是兼容旧脚本的 live 别名')
    parser.add_argument('--port',type=int,default=bridge_port())
    parser.add_argument('--sandbox',choices=['read-only','workspace-write'],default='read-only')
    args=parser.parse_args()
    codex=discover_codex()
    workbuddy_command=discover_workbuddy_command()
    deepseek_command=discover_deepseek()
    available_agents = {
        name
        for name, available in (
            ('workbuddy', bool(workbuddy_command)),
            ('codex', bool(codex)),
            ('deepseek', bool(deepseek_command)),
        )
        if available
    }
    if args.mode == 'mock':
        # Mock mode exercises every supported route without requiring either
        # desktop agent to be installed on the test computer.
        available_agents = supported_agents()
    selected_default = default_agent(available_agents) if available_agents else DEFAULT_AGENT
    set_runtime_agents(available_agents, selected_default)
    if args.mode in {'codex','live'} and not available_agents:
        parser.error('未找到可用执行器；请至少安装并配置 WorkBuddy、Codex 或 DeepSeek Harness。')
    init(); threading.Thread(target=worker,args=(args.mode,codex,args.sandbox,None,workbuddy_command,'acceptEdits',deepseek_command),daemon=True).start()
    host = bridge_host()
    server=ThreadingHTTPServer((host,args.port),Handler)
    print(f'任务桥 http://{host}:{args.port} 模式={args.mode}',flush=True)
    server.serve_forever()

if __name__ == '__main__': main()

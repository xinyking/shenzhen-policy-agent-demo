"""Single-process asynchronous task lifecycle; browser connections do not own tasks."""
import asyncio
import hashlib
import json
import sqlite3
from uuid import uuid4
from app.agent.main_agent import PolicyGraph
from app.agent.llm import ModelError
from app.memory.personal_memory import PersonalMemory
from app.api.run_store import RunStore, TERMINAL
from app.tools.screenshot_tools import ScreenshotStore, ScreenshotError


class RunConflict(Exception):
    pass


class RunLimit(Exception):
    pass


class RunService:
    def __init__(self, service):
        self.service = service
        self.store = RunStore(service.settings.store.parent / 'private/runs.sqlite3')
        self.tasks, self.sessions = {}, {}
        self.images = ScreenshotStore()

    def submit(self, body):
        request_id = body['client_request_id']
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = self.store.by_request(request_id)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise RunConflict('同一请求标识不能用于不同问题')
            return self.store.get(previous['id'])
        if body['session_id'] and body['session_id'] in self.sessions:
            raise RunConflict('当前会话已有任务运行，请等待或取消')
        if len(self.tasks) >= 4:
            raise RunLimit('当前任务较多，请稍后重试')
        # Snapshot model configuration now; subsequent settings edits cannot change this run.
        try:
            model = self.service.model_factory() if body['mode'] != 'offline' else None
        except ModelError:
            model = None
        screenshot = None
        if body.get('screenshot_id'):
            if not body.get('image_consent'):
                raise ScreenshotError('请确认已遮挡无关个人信息，并同意将截图发送给所配置的模型')
            if model is None or body['mode'] == 'offline':
                raise ScreenshotError('截图识别需要配置支持图片的模型，不能使用仅检索模式')
            screenshot = self.images.take(body['screenshot_id'])
        run_id = uuid4().hex
        self.store.create(run_id, request_id, fingerprint, body['session_id'])
        self.store.emit(run_id, {'type': 'run_queued', 'node': None, 'data': {}})
        if body['session_id']:
            self.sessions[body['session_id']] = run_id
        self.tasks[run_id] = asyncio.create_task(self.execute(run_id, body, model, screenshot))
        self.tasks[run_id].add_done_callback(lambda task: self.cleanup(run_id, body['session_id'], task))
        return self.store.get(run_id)

    def cleanup(self, run_id, session_id, task):
        # Cancellation before the coroutine's first instruction never enters its finally block.
        self.tasks.pop(run_id, None)
        if self.sessions.get(session_id) == run_id:
            self.sessions.pop(session_id, None)
        if task.cancelled() and self.store.get(run_id)['status'] not in TERMINAL:
            self.store.finish(run_id, 'cancelled', error='任务已取消，未保存本轮回答或日程。')

    async def execute(self, run_id, body, model, screenshot=None):
        graph = None
        try:
            self.store.emit(run_id, {'type': 'run_started', 'node': None, 'data': {}}, 'running')
            graph = PolicyGraph(self.service, body, run_id, model, screenshot=screenshot)
            screenshot = None
            async with asyncio.timeout(graph.seconds + 10):
                result = await graph.run(lambda event: self.store.emit(run_id, event))
            # Yield once before the small synchronous commit; cancellation never writes memory afterwards.
            await asyncio.sleep(0)
            status = ('needs_input' if result['runtime']['termination'] == 'needs_user_input' or result['mode_used'] == 'no_evidence' else
                      'degraded' if body['mode'] != 'offline' and result['mode_used'] == 'extractive' else 'completed')
            self.store.finish(run_id, status, result)
            if body['use_memory'] and not body.get('screenshot_id'):
                try:
                    PersonalMemory(self.service.settings.store.parent / 'private/personal-memory.sqlite3').remember(
                        body['session_id'], body['question'], result['answer'])
                except (OSError, ValueError, sqlite3.Error):
                    # Task result already committed. Memory failure must not turn success into a model error.
                    self.store.emit(run_id, {'type': 'memory_save_failed', 'node': None, 'data': {}})
        except asyncio.CancelledError:
            self.store.finish(run_id, 'cancelled', error='任务已取消，未保存本轮回答或日程。')
            raise
        except Exception:
            # Never expose provider bodies, input context, credentials or unvalidated generated claims.
            self.store.finish(run_id, 'failed', error='任务未完成，请检查数据或模型连接后重试。')
        finally:
            if graph is not None:
                graph.screenshot = None
            self.tasks.pop(run_id, None)
            if self.sessions.get(body['session_id']) == run_id:
                self.sessions.pop(body['session_id'], None)

    def cancel(self, run_id):
        item = self.store.get(run_id)
        if item is None or item['status'] in TERMINAL:
            return item
        task = self.tasks.get(run_id)
        if task:
            self.store.emit(run_id, {'type': 'cancel_requested', 'node': None, 'data': {}}, 'cancelling')
            task.cancel()
        return self.store.get(run_id)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.images.items.clear()

"""Task lifecycle/real graph behavior using deterministic models, not answer-quality claims."""
import asyncio
import json
import threading
import time
import unittest
from fastapi.testclient import TestClient
from app.api.server import create_app
from app.services.policy_service import Settings
from app.api.run_store import RunStore, TERMINAL
from test_ingestion import DataFixture

CLASSIFY = {'domains': ['employment'], 'intent': 'answer'}
ANSWER = {'action': 'answer', 'arguments': {}, 'gaps': []}
CLAIMS = {'claims': [{'title': '适用对象', 'text': '测试政策涉及硕士深圳条件。', 'evidence_ids': ['E1']}], 'missing_information': []}
REVIEW = {'verdicts': [{'claim_index': 0, 'supported': True, 'reason': '虚构测试原文支持'}]}


class ScriptedModel:
    def __init__(self, values):
        self.values = list(values)
        self.inputs = []
        self.blocked = threading.Event()

    async def generate(self, instruction, data, timeout=45):
        self.inputs.append(data)
        value = self.values.pop(0)
        if value == 'block':
            self.blocked.set()
            await asyncio.sleep(30)
        await asyncio.sleep(.01)
        return json.loads(json.dumps(value, ensure_ascii=False))


class RunTests(DataFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.snapshot()
        self.model = ScriptedModel([CLASSIFY, ANSWER, CLAIMS, REVIEW])
        self.client = self.enterContext(TestClient(create_app(Settings(self.store), lambda: self.model)))
        self.headers = {'X-Policy-Client': 'workspace'}

    def submit(self, **extra):
        body = {'question': '硕士深圳政策的对象流程和材料', 'mode': 'multi_agent',
                'session_id': 'test-session', 'client_request_id': 'request-one', **extra}
        response = self.client.post('/v1/runs', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['run_id'], body

    def wait(self, run_id):
        for _ in range(200):
            status = self.client.get('/v1/runs/' + run_id).json()
            if status['status'] in TERMINAL:
                return status
            time.sleep(.02)
        self.fail('Task did not reach terminal state')

    def test_real_graph_completes_with_sanitized_replay_and_idempotence(self):
        run_id, body = self.submit(context='PRIVATE-CONTEXT-DO-NOT-EMIT')
        status = self.wait(run_id)
        self.assertEqual(status['status'], 'completed', status)
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['runtime']['engine'], 'langgraph')
        self.assertEqual(result['budget']['model_calls'], 4)
        self.assertEqual([s['name'] for s in result['skills_used']], ['policy_research'])
        self.assertEqual(result['schedule_drafts'], [])
        self.assertEqual(result['verification']['status'], 'supported')
        events_text = self.client.get(f'/v1/runs/{run_id}/events').text
        events = [json.loads(line[6:]) for line in events_text.splitlines() if line.startswith('data: ')]
        self.assertNotIn('PRIVATE-CONTEXT', events_text)
        self.assertNotIn('测试政策涉及', events_text)  # No unvalidated draft text on progress channel.
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        starts = [e['node'] for e in events if e['type'] == 'node_started']
        self.assertEqual(set(starts[:2]), {'check_store', 'load_memory'})
        self.assertEqual(starts[-1], 'finish')
        replay = self.client.get(f'/v1/runs/{run_id}/events', headers={'Last-Event-ID': str(events[-2]['seq'])}).text
        self.assertEqual(replay.count('data: '), 1)
        duplicate = self.client.post('/v1/runs', json=body, headers=self.headers)
        self.assertEqual(duplicate.json()['run_id'], run_id)
        self.assertEqual(len(self.model.inputs), 4)
        self.assertEqual(self.client.get('/v1/memory').json()['applications'], [])

    def test_explicit_offline_mode_completes_without_model_or_false_failure(self):
        run_id, _ = self.submit(mode='offline')
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertEqual(result['budget']['model_calls'], 0)
        self.assertEqual(self.model.inputs, [])

    def test_cancel_during_model_keeps_events_and_never_writes_memory(self):
        self.model.values = [CLASSIFY, ANSWER, 'block']
        run_id, _ = self.submit(use_memory=True)
        self.assertTrue(self.model.blocked.wait(4))
        # HTTP remains responsive while model awaits; progress already exists before any answer.
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get(f'/v1/runs/{run_id}/result').status_code, 409)
        prior = self.client.app.state.runs.store.events(run_id)
        self.assertTrue(any(e['type'] == 'node_started' and e['node'] == 'generate' for e in prior))
        self.client.post(f'/v1/runs/{run_id}/cancel', headers=self.headers)
        self.assertEqual(self.wait(run_id)['status'], 'cancelled')
        self.assertEqual(self.client.post(f'/v1/runs/{run_id}/cancel', headers=self.headers).json()['status'], 'cancelled')
        self.assertEqual(self.client.get('/v1/memory').json()['message_count'], 0)

    def test_request_origin_busy_session_and_conflicting_id(self):
        self.model.values = ['block']
        run_id, body = self.submit()
        self.assertEqual(self.client.post('/v1/runs', json=body).status_code, 403)
        self.assertEqual(self.client.get(f'/v1/runs/{run_id}/events', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/v1/runs', json={**body, 'question': '不同问题'}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/v1/runs', json={**body, 'client_request_id': 'request-two'}, headers=self.headers).status_code, 409)
        self.client.post(f'/v1/runs/{run_id}/cancel', headers=self.headers)
        self.wait(run_id)
        self.assertEqual(self.client.get('/v1/runs/not-found').status_code, 404)

    def test_invalid_citation_repairs_once_then_extracts(self):
        invalid = {'claims': [{'text': '无效引用', 'evidence_ids': ['E99']}], 'missing_information': []}
        self.model.values = [CLASSIFY, ANSWER, invalid, invalid]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'degraded')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertNotIn('无效引用', result['answer'])
        self.assertEqual(result['budget']['model_calls'], 4)
        self.assertEqual(sum(e['type'] == 'repair_requested' for e in self.client.app.state.runs.store.events(run_id)), 1)

    def test_invalid_decision_has_one_bounded_repair(self):
        self.model.values = [CLASSIFY, {'answer': '这不是决策协议'}, ANSWER, CLAIMS, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['budget']['model_calls'], 5)
        repairs = [e for e in self.client.app.state.runs.store.events(run_id) if e['type'] == 'repair_requested']
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]['node'], 'decide')

    def test_explicit_schedule_is_only_a_draft_and_memory_is_opt_in(self):
        self.model.values = [CLASSIFY, ANSWER, CLAIMS, REVIEW,
                             {'items': [{'title': '测试事项', 'evidence_ids': ['E1'], 'materials': [], 'method': ''}]}]
        run_id, _ = self.submit(question='硕士深圳政策，请帮我加入日程', use_memory=True)
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertTrue(result['schedule_drafts'][0]['requires_confirmation'])
        self.assertIsNone(result['schedule_drafts'][0]['date'])
        memory = self.client.get('/v1/memory').json()
        self.assertEqual(memory['applications'], [])
        self.assertEqual(memory['message_count'], 1)

    def test_identity_clarification_redirect_and_tool_loop(self):
        self.model.values = [CLASSIFY,
            {'action': 'read_source', 'arguments': {'source_id': 'test-source', 'section': '硕士'}, 'gaps': ['材料']},
            {'action': 'clarify', 'arguments': {'question': '请提供您的学历和个人信息'}, 'gaps': []}, CLAIMS, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['budget']['correction_rounds'], 1)
        self.assertEqual(result['missing_information'], [])
        self.assertEqual(result['budget']['model_calls'], 5)

    def test_unrequested_plan_classification_cannot_authorize_schedule(self):
        self.model.values = [{**CLASSIFY, 'intent': 'plan'}, ANSWER, CLAIMS, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['routing']['intent'], 'answer')
        self.assertEqual(result['schedule_drafts'], [])

    def test_stale_task_is_marked_interrupted_without_rerunning(self):
        journal = RunStore(self.root / 'other-private/runs.sqlite3')
        journal.create('stale', 'request-stale', 'fingerprint', 'old-session')
        journal.emit('stale', {'type': 'run_started', 'node': None, 'data': {}}, 'running')
        reloaded = RunStore(journal.path)
        self.assertEqual(reloaded.get('stale')['status'], 'interrupted')
        self.assertIsNone(reloaded.result('stale'))

    def test_two_sessions_can_await_models_concurrently(self):
        self.model.values = ['block', 'block']
        first, _ = self.submit()
        second, _ = self.submit(client_request_id='request-two', session_id='another-session')
        for _ in range(100):
            if len(self.model.inputs) >= 2:
                break
            time.sleep(.02)
        self.assertEqual(len(self.model.inputs), 2)
        for run_id in (first, second):
            self.client.post(f'/v1/runs/{run_id}/cancel', headers=self.headers)
            self.assertEqual(self.wait(run_id)['status'], 'cancelled')

    def test_tool_corrections_stop_at_two_and_do_not_leak_claims(self):
        tool = {'action': 'read_source', 'arguments': {'source_id': 'unknown-source', 'section': '材料'}, 'gaps': ['尚缺资料']}
        self.model.values = [CLASSIFY, tool, tool, CLAIMS, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['budget']['correction_rounds'], 2)
        self.assertEqual(result['mode_used'], 'agent')
        self.assertEqual(result['verification']['status'], 'supported')
        self.assertEqual(result['budget']['model_calls'], 5)
        self.assertEqual(len([e for e in result['agent_trace'] if e['event'] == 'tool_error']), 2)

    def test_two_tools_and_repairs_still_leave_review_as_seventh_call(self):
        tool = {'action': 'read_source', 'arguments': {'source_id': 'test-source', 'section': '硕士'}, 'gaps': ['流程']}
        other = {**tool, 'arguments': {'source_id': 'test-source', 'section': '深圳'}}
        invalid = {'claims': [{'text': '错误引用', 'evidence_ids': ['E999']}], 'missing_information': []}
        self.model.values = [CLASSIFY, {'answer': '无效决策'}, tool, other, invalid, CLAIMS, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['budget']['model_calls'], 7)
        self.assertEqual(result['budget']['research_model_calls'], 6)
        self.assertEqual(result['budget']['correction_rounds'], 2)
        self.assertEqual(result['verification']['status'], 'supported')
        self.assertNotIn('failure', result)

    def test_bad_research_decisions_can_still_generate_from_valid_evidence(self):
        self.model.values = [CLASSIFY, {'bad': True}, {'bad': True}, CLAIMS, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['verification']['status'], 'supported')

    def test_invalid_review_format_repairs_before_answer_is_published(self):
        self.model.values = [CLASSIFY, ANSWER, CLAIMS, {'verdicts': []}, REVIEW]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'completed')
        events = self.client.app.state.runs.store.events(run_id)
        self.assertTrue(any(e['node'] == 'review' and e['type'] == 'repair_requested' for e in events))

    def test_unavailable_review_reports_its_stage_without_publishing_draft(self):
        self.model.values = [CLASSIFY, ANSWER, CLAIMS, {'verdicts': []}, {'verdicts': []}]
        run_id, _ = self.submit()
        self.assertEqual(self.wait(run_id)['status'], 'degraded')
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['failure']['stage'], 'review')
        self.assertEqual(result['verification']['status'], 'not_completed')
        self.assertNotIn('测试政策涉及', result['answer'])

    def test_review_cannot_keep_unsupported_claims(self):
        self.model.values = [CLASSIFY, ANSWER, CLAIMS,
                             {'verdicts': [{'claim_index': 0, 'supported': False, 'reason': '证据不足'}]}]
        run_id, _ = self.submit()
        self.wait(run_id)
        result = self.client.get(f'/v1/runs/{run_id}/result').json()
        self.assertEqual(result['claims'], [])
        self.assertNotIn('测试政策涉及', result['answer'])
        self.assertEqual(result['verification']['removed'], 1)


class EarlyCancelTests(DataFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.snapshot()

    async def test_cancel_before_coroutine_starts_releases_session(self):
        from app.api.task_service import RunService
        from app.services.policy_service import PolicyService
        manager = RunService(PolicyService(Settings(self.store)))
        body = {'question': '硕士深圳', 'context': '', 'mode': 'offline', 'top_k': 5,
                'use_memory': False, 'session_id': 's', 'client_request_id': 'one'}
        run_id = manager.submit(body)['run_id']
        manager.cancel(run_id)
        await manager.close()
        self.assertEqual(manager.store.get(run_id)['status'], 'cancelled')
        self.assertEqual(manager.sessions, {})

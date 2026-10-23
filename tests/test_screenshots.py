import asyncio
import io
import json
import time
import unittest
from PIL import Image
from fastapi.testclient import TestClient
from app.api.server import create_app
from app.services.policy_service import Settings
from app.tools.screenshot_tools import ScreenshotStore, ScreenshotError, validate_image, validate_recognition, validate_comparison
from app.agent.llm import ModelError
from test_ingestion import DataFixture
from test_runs import ScriptedModel, CLAIMS, REVIEW, TERMINAL


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (80, 100), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


RECOGNITION = {'title': '硕士深圳材料申请', 'is_application_page': True,
               'visible_text': '申请表必需\n亲属关系证明按情形提供\n申请截止2027年10月20日',
               'materials': [{'name': '申请表', 'requirement': 'required', 'condition': '', 'quote': '申请表必需', 'location': '材料列表第1项'},
                             {'name': '亲属关系证明', 'requirement': 'conditional', 'condition': '按情形提供', 'quote': '亲属关系证明按情形提供', 'location': '材料列表第2项'}],
               'procedure': '', 'dates': [{'date': '2027-10-20', 'label': '申请截止', 'quote': '申请截止2027年10月20日', 'location': '底部'}], 'gaps': ['未展示模板']}
COMPARISON = {'checks': [{'id': key, 'verdict': 'not_found', 'policy_evidence_ids': [], 'policy_quote': '', 'explanation': '测试政策未列出该材料。'} for key in ('S1', 'S2')]}


class ScreenshotContracts(unittest.TestCase):
    def test_decode_size_mime_and_memory_expiry(self):
        raw = png()
        info = validate_image(raw, 'image/png')
        self.assertEqual(info['width'], 80)
        for content, mime in ((b'not-image', 'image/png'), (raw, 'image/jpeg'), (b'x' * (4 * 1024 * 1024 + 1), 'image/png')):
            with self.assertRaises(ScreenshotError):
                validate_image(content, mime)
        store = ScreenshotStore()
        key = store.put(raw, info)['screenshot_id']
        self.assertEqual(store.take(key)['raw'], raw)
        with self.assertRaises(ScreenshotError): store.take(key)
        key = store.put(raw, info)['screenshot_id']
        store.items[key]['created'] -= 601
        with self.assertRaises(ScreenshotError): store.take(key)

    def test_quotes_dates_and_unverified_recognition(self):
        output = validate_recognition(json.loads(json.dumps(RECOGNITION)))
        self.assertTrue(output['recognition_requires_confirmation'])
        self.assertEqual(output['materials'][1]['requirement'], 'conditional')
        self.assertEqual(output['dates'][0]['date'], '2027-10-20')
        for field in ('quote', 'date'):
            bad = json.loads(json.dumps(RECOGNITION))
            if field == 'quote': bad['materials'][0]['quote'] = '图片没有的文字'
            else: bad['dates'][0]['date'] = '2028-10-20'
            with self.assertRaises(ModelError): validate_recognition(bad)

    def test_policy_quote_cannot_be_fabricated_or_screenshot_presented_as_policy(self):
        materials = validate_recognition(RECOGNITION)['materials']
        self.assertEqual(len(validate_comparison(COMPARISON, materials, [])), 2)
        bad = json.loads(json.dumps(COMPARISON))
        bad['checks'][0].update(verdict='supported', policy_evidence_ids=['E1'], policy_quote='伪造原文')
        with self.assertRaises(ModelError): validate_comparison(bad, materials, [{'evidence_id': 'E1', 'text': '真实依据'}])


class VisionModel(ScriptedModel):
    async def generate_image(self, instruction, data, raw, mime, model_name='', timeout=45):
        self.image_calls = getattr(self, 'image_calls', 0) + 1
        self.asserted_image = raw.startswith(b'\x89PNG') and mime == 'image/png'
        return await super().generate(instruction, data, timeout)


class ScreenshotWorkflowTests(DataFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.snapshot()
        self.model = VisionModel([RECOGNITION, CLAIMS, REVIEW, COMPARISON])
        self.client = self.enterContext(TestClient(create_app(Settings(self.store), lambda: self.model)))
        self.headers = {'X-Policy-Client': 'workspace'}

    def upload(self):
        response = self.client.post('/v1/screenshots', content=png(), headers={**self.headers, 'Content-Type': 'image/png'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['screenshot_id']

    def submit(self, key, **extra):
        return self.client.post('/v1/runs', headers=self.headers, json={'question': '硕士深圳申请材料是什么', 'mode': 'quick',
            'session_id': 'screen-session', 'client_request_id': 'screen-request', 'screenshot_id': key, 'image_consent': True, **extra})

    def wait(self, key):
        for _ in range(300):
            status = self.client.get('/v1/runs/' + key).json()
            if status['status'] in TERMINAL: return status
            time.sleep(.02)
        self.fail('Task not finished')

    def test_no_consent_no_transmission_and_upload_is_private(self):
        self.assertEqual(self.client.post('/v1/screenshots', content=png(), headers={'Content-Type': 'image/png'}).status_code, 403)
        key = self.upload()
        self.assertEqual(self.submit(key, image_consent=False).status_code, 400)
        self.assertEqual(self.model.inputs, [])
        self.assertIn(key, self.client.app.state.runs.images.items)

    def test_full_workflow_is_reviewable_and_never_auto_saves_schedule_or_image(self):
        key = self.upload()
        response = self.submit(key, use_memory=True)
        self.assertEqual(response.status_code, 202, response.text)
        run = response.json()['run_id']
        self.assertEqual(self.wait(run)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run}/result').json()
        self.assertEqual(result['budget']['model_calls'], 4)
        self.assertEqual(result['execution_profile'], 'quick')
        self.assertEqual(result['screenshot_analysis']['materials'][0]['id'], 'S1')
        self.assertEqual(result['screenshot_analysis']['checks']['S1']['verdict'], 'not_found')
        self.assertEqual(result['screenshot_analysis']['dates'][0]['date'], '2027-10-20')
        self.assertTrue(self.model.asserted_image)
        self.assertEqual(self.model.inputs[1]['user_screenshot_context']['materials'][0]['name'], '申请表')
        self.assertNotIn(key, self.client.app.state.runs.images.items)
        self.assertEqual(self.client.get('/v1/memory').json()['applications'], [])
        self.assertEqual(self.client.get('/v1/memory').json()['message_count'], 0)
        events = self.client.get(f'/v1/runs/{run}/events').text
        self.assertNotIn('亲属关系证明', events)
        self.assertNotIn('image_url', events)
        self.assertNotIn('iVBOR', json.dumps(result))
        # Explicit user-confirmed screenshot material has a distinct source kind and survives storage.
        application = {'id': 'screen-plan', 'title': '材料准备', 'date': '2027-10-18', 'created': 'test',
            'materialsChecklist': [{'text': '申请表', 'evidence_id': 'S1', 'quote': '申请表必需', 'source_kind': 'screenshot',
                                    'source_title': '截图', 'location': '材料列表第1项', 'requirement': 'required', 'condition': ''}]}
        self.assertEqual(self.client.post('/v1/memory/applications', json=application, headers=self.headers).status_code, 200)
        saved = self.client.get('/v1/memory').json()['applications'][0]
        self.assertEqual(saved['materialsChecklist'][0]['source_kind'], 'screenshot')
        application['materialsChecklist'][0]['source_kind'] = 'policy'
        self.assertEqual(self.client.post('/v1/memory/applications', json=application, headers=self.headers).status_code, 422)

    def test_non_application_page_does_not_invent_materials_or_call_policy_model(self):
        self.model.values = [{**RECOGNITION, 'is_application_page': False, 'materials': [], 'dates': []}]
        response = self.submit(self.upload())
        run = response.json()['run_id']
        self.assertEqual(self.wait(run)['status'], 'needs_input')
        result = self.client.get(f'/v1/runs/{run}/result').json()
        self.assertEqual(result['claims'], [])
        self.assertEqual(len(self.model.inputs), 1)

    def test_comparison_failure_keeps_valid_policy_answer_with_unverified_materials(self):
        self.model.values = [RECOGNITION, CLAIMS, REVIEW, {'checks': []}]
        response = self.submit(self.upload())
        run = response.json()['run_id']
        self.assertEqual(self.wait(run)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run}/result').json()
        self.assertEqual(result['screenshot_analysis']['comparison_status'], 'unavailable')
        self.assertEqual(result['screenshot_analysis']['checks']['S1']['verdict'], 'not_found')
        self.assertEqual(result['verification']['status'], 'supported')

    def test_unsupported_vision_does_not_claim_recognition_succeeded(self):
        self.client.app.state.service.model_factory = lambda: ScriptedModel([])
        response = self.submit(self.upload())
        run = response.json()['run_id']
        self.assertEqual(self.wait(run)['status'], 'needs_input')
        result = self.client.get(f'/v1/runs/{run}/result').json()
        self.assertEqual(result['screenshot_analysis']['status'], 'unavailable')
        self.assertEqual(result['claims'], [])

    def test_quick_question_keeps_semantic_review_with_two_calls(self):
        self.model.values = [CLAIMS, REVIEW]
        response = self.client.post('/v1/runs', headers=self.headers, json={'question': '硕士深圳条件', 'mode': 'quick', 'client_request_id': 'quick-one'})
        run = response.json()['run_id']
        self.assertEqual(self.wait(run)['status'], 'completed')
        result = self.client.get(f'/v1/runs/{run}/result').json()
        self.assertEqual(result['budget']['model_calls'], 2)
        self.assertEqual(result['verification']['status'], 'supported')
        events = self.client.app.state.runs.store.events(run)
        self.assertFalse(any(e['type'] == 'node_started' and e['node'] == 'decide' for e in events))

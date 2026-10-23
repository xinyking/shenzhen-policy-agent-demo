import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.server import create_app
from app.knowledge.ingestion import read_json, write_json
from app.agent.llm import JsonModel, ModelError, NoRedirect
from app.agent.answer import answer_question, validate_answer
from app.services.policy_service import Settings
from test_ingestion import DataFixture


class QATests(DataFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.snapshot()
        self.factory = Mock()
        self.model = self.factory.return_value
        self.model.generate.return_value = {'claims': [{'text': '测试文件要求硕士，并在深圳工作。',
            'evidence_ids': ['E1']}], 'missing_information': []}
        self.client = self.enterContext(TestClient(create_app(Settings(self.store), self.factory)))

    def ask(self, **changes):
        return self.client.post('/v1/ask', json={'question': '硕士工作城市深圳', **changes})

    def test_offline_excerpts_have_quotes_sources_and_no_storage(self):
        result = self.ask().json()
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertEqual(result['status'], 'evidence_only')
        self.assertTrue(result['sources'])
        self.assertIn('source_url', result['sources'][0])
        self.assertIn('[E1]', result['answer'])
        self.factory.assert_not_called()
        self.assertEqual(list(self.root.rglob('*.sqlite3')), [])

    def test_valid_model_answer(self):
        result = self.ask(mode='llm', context='我是硕士').json()
        self.assertEqual(result['mode_used'], 'llm')
        self.assertEqual(result['status'], 'answered')
        payload = self.model.generate.call_args.args[1]
        self.assertEqual(payload['context'], '我是硕士')
        self.assertTrue(payload['evidence'])
        self.assertIn('published_at', payload['evidence'][0])

    def test_fabricated_citation_falls_back(self):
        self.model.generate.return_value['claims'][0]['evidence_ids'] = ['FAKE']
        result = self.ask(mode='llm').json()
        self.assertEqual(result['mode_used'], 'extractive')
        self.assertTrue(any('回退' in warning for warning in result['warnings']))
        self.assertNotIn('FAKE', result['answer'])

    def test_invalid_citation_is_retried_once_without_guessing(self):
        self.model.generate.side_effect = [
            {'claims': [{'text': '不能展示', 'evidence_ids': []}], 'missing_information': []},
            {'claims': [{'text': '修复后的测试回答', 'evidence_ids': ['E1']}], 'missing_information': []}]
        result = self.ask(mode='llm').json()
        self.assertEqual(result['mode_used'], 'llm')
        self.assertIn('修复后的', result['answer'])
        self.assertNotIn('不能展示', result['answer'])
        self.assertEqual(self.model.generate.call_count, 2)

    def test_summary_can_cite_more_than_three_supplied_sources(self):
        ids = ['E1', 'E2', 'E3', 'E4']
        value = {'claims': [{'text': '综合说明', 'evidence_ids': ids}], 'missing_information': []}
        self.assertEqual(validate_answer(value, [{'evidence_id': e} for e in ids]), value)

    def test_section_suggestion_exports_its_actual_evidence(self):
        self.model.generate.return_value = {'claims': [
            {'title': '下一步先核对条件', 'kind': 'suggestion',
             'text': '建议先核对硕士和在深圳工作的条件。', 'evidence_ids': ['E1']}],
            'missing_information': []}
        result = self.ask(mode='llm').json()
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['claims'][0]['kind'], 'suggestion')
        report = result['report_markdown']
        self.assertIn('下一步先核对条件', report)
        self.assertIn('[^1]', report)
        self.assertIn('## 引用片段', report)
        for line in result['sources'][0]['text'].splitlines():
            self.assertIn('> ' + line, report)

    def test_summary_supported_and_reference_deduplicated(self):
        self.model.generate.return_value = {'claims': [
            {'title': '先说结论', 'kind': 'summary', 'text': '可以先核对硕士条件。', 'evidence_ids': ['E1']},
            {'title': '我的建议', 'kind': 'suggestion', 'text': '建议核对深圳工作条件。', 'evidence_ids': ['E1']}],
            'missing_information': []}
        result = self.ask(mode='llm').json()
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['report_markdown'].count('[^1]:'), 1)
        self.assertEqual(result['report_markdown'].count('## 引用片段'), 1)
        self.assertLess(result['report_markdown'].index('我的建议'), result['report_markdown'].index('## 引用片段'))

    def test_invalid_section_metadata_falls_back(self):
        for metadata in ({'title': ''}, {'title': 'x' * 81}, {'kind': 'official_approval'}, {'kind': []}):
            with self.subTest(metadata=metadata):
                self.model.generate.return_value = {'claims': [
                    {'text': '测试', 'evidence_ids': ['E1'], **metadata}], 'missing_information': []}
                self.assertEqual(self.ask(mode='llm').json()['mode_used'], 'extractive')

    def test_model_error_falls_back(self):
        self.model.generate.side_effect = ModelError('模型不可用')
        self.assertEqual(self.ask(mode='llm').json()['mode_used'], 'extractive')

    def test_no_evidence_does_not_call_model(self):
        result = self.ask(question='火星探测', mode='llm').json()
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['sources'], [])
        self.factory.assert_not_called()

    def test_named_document_material_heading_keeps_list_context(self):
        from test_ingestion import SOURCE
        from app.knowledge.ingestion import parse_html, save_snapshot
        raw = '''<html><head><meta name="ArticleTitle" content="虚构创业补贴"></head>
        <body><div class="news_cont_d_wrap"><p>一、受理条件</p><p>虚构创业补贴仅测试使用。</p>
        <p>二、申请材料</p><p>测试申请需要材料甲。</p><p>材料乙作为额外测试证明。</p>
        <p>三、办理流程</p><p>提交虚构创业补贴申请并上传相应材料。</p></div></body></html>'''.encode()
        save_snapshot(self.store, SOURCE, raw, parse_html(raw, SOURCE), 'local_import')
        result = self.ask(question='虚构创业补贴申请需要哪些材料').json()
        self.assertIn('材料甲', result['answer'])
        self.assertIn('材料乙', result['answer'])
        self.assertTrue(result['sources'][0]['span_ids'])

    def test_missing_model_configuration_falls_back(self):
        with patch.dict(os.environ, {}, clear=True):
            result = answer_question(self.store, '硕士', mode='llm')
        self.assertEqual(result['mode_used'], 'extractive')

    def test_failed_refresh_not_used_to_answer(self):
        manifest = read_json(self.store / 'manifest.json')
        manifest['documents']['test-source']['last_check_status'] = 'failed'
        write_json(self.store / 'manifest.json', manifest)
        result = self.ask(mode='llm').json()
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.factory.assert_not_called()

    def test_model_can_report_insufficient_evidence(self):
        self.model.generate.return_value = {'claims': [], 'missing_information': ['请提供政策名称。']}
        result = self.ask(mode='llm').json()
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['missing_information'], ['请提供政策名称。'])

    def test_invalid_structured_answers(self):
        valid = {'claims': [{'text': '说明', 'evidence_ids': ['E1']}], 'missing_information': []}
        variants = [None, {}, {'claims': [], 'missing_information': []},
            {**valid, 'claims': [{'text': '说明', 'evidence_ids': []}]},
            {**valid, 'claims': [{'text': '说明', 'evidence_ids': ['E1', 'E1']}]},
            {**valid, 'claims': [{'text': '伪造 [E99]', 'evidence_ids': ['E1']}]},
            {**valid, 'claims': [{'text': '访问 https://evil.com', 'evidence_ids': ['E1']}]},
            {**valid, 'missing_information': 'bad'}]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ModelError):
                validate_answer(value, [{'evidence_id': 'E1'}])

    def test_invalid_inputs_do_not_echo_personal_text(self):
        for changes in ({'question': ' '}, {'top_k': 9}, {'top_k': '5'}, {'mode': 'unknown'},
                        {'profile': 'PRIVATE-SECRET'}, {'context': 'x' * 1001}):
            with self.subTest(changes=changes):
                response = self.ask(**changes)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('PRIVATE-SECRET', response.text)

    def test_removed_routes_are_absent(self):
        schema = self.client.get('/openapi.json').json()
        self.assertEqual(set(schema['paths']), {'/health', '/v1/ask', '/v1/search', '/v1/sources', '/v1/sources/{source_id}', '/v1/model', '/v1/model/test', '/v1/memory', '/v1/memory/applications', '/v1/memory/applications/{application_id}', '/v1/runs', '/v1/runs/{run_id}', '/v1/runs/{run_id}/events', '/v1/runs/{run_id}/result', '/v1/runs/{run_id}/cancel', '/v1/screenshots'})
        self.assertEqual(self.client.post('/v1/sessions', json={}).status_code, 404)
        self.assertEqual(self.client.post('/v1/recommendations', json={}).status_code, 404)

    def test_large_body_rejected(self):
        self.assertEqual(self.client.post('/v1/ask', content=b'x' * 65537).status_code, 413)

    def test_tampered_source_returns_safe_error(self):
        next(self.store.glob('documents/*/*/raw.html')).write_bytes(b'bad')
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.root), response.text)

    def test_missing_store_health_still_available(self):
        self.client.app.state.service.settings.store = self.root / 'missing'
        self.assertEqual(self.ask().status_code, 503)
        self.assertEqual(self.client.get('/health').status_code, 200)


class ModelTests(unittest.TestCase):
    def test_model_redirect_blocked(self):
        with self.assertRaises(ModelError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')

    def test_malformed_provider_response_as_model_error(self):
        config = {'POLICY_MODEL_BASE_URL': 'https://example.com/v1', 'POLICY_MODEL_NAME': 'test', 'POLICY_MODEL_API_KEY': 'not-real'}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"choices": null}'
        with patch.dict(os.environ, config, clear=True), patch('app.agent.llm.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ModelError):
                JsonModel().generate('test', {})

    def test_no_implicit_credentials(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ModelError):
            JsonModel()

    def test_remote_http_rejected(self):
        config = {'POLICY_MODEL_BASE_URL': 'http://example.com/v1', 'POLICY_MODEL_NAME': 'test', 'POLICY_MODEL_API_KEY': 'not-real'}
        with patch.dict(os.environ, config, clear=True), self.assertRaises(ModelError):
            JsonModel()

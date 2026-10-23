import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.api.server import create_app
from app.agent.llm import ModelError
from app.services.policy_service import Settings
from test_ingestion import DataFixture


class ConfigurationTests(DataFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.snapshot()
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.client = self.enterContext(TestClient(create_app(Settings(self.store))))
        self.headers = {'X-Policy-Client': 'workspace'}
        self.body = {'base_url': 'https://example.com/v1', 'model_name': 'test', 'api_key': 'PRIVATE-KEY'}

    def configure(self, body=None):
        return self.client.put('/v1/model', json=body or self.body, headers=self.headers)

    def test_configuration_never_returns_key(self):
        response = self.configure()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['configured'])
        self.assertNotIn('PRIVATE-KEY', response.text)
        response = self.client.get('/v1/model')
        self.assertNotIn('PRIVATE-KEY', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('POLICY_MODEL_API_KEY', os.environ)

    def test_optional_vision_model_persists_without_changing_text_model_or_key(self):
        self.configure({**self.body, 'vision_model_name': 'test-vision'})
        from app.agent.model_config import ModelConfiguration
        restored = ModelConfiguration(self.client.app.state.model_config.path)
        self.assertEqual(restored.status()['model_name'], 'test')
        self.assertEqual(restored.status()['vision_model_name'], 'test-vision')
        self.assertEqual(restored.factory().key, 'PRIVATE-KEY')
        self.assertEqual(restored.factory().vision_name, 'test-vision')
        self.configure({**self.body, 'api_key': ''})
        self.assertEqual(self.client.get('/v1/model').json()['vision_model_name'], 'test-vision')

    def test_runtime_configuration_used_by_question_answering(self):
        self.configure()
        with patch('app.agent.llm.JsonModel.generate', return_value={'claims': [{'text': '模拟回答', 'evidence_ids': ['E1']}], 'missing_information': []}) as generate:
            result = self.client.post('/v1/ask', json={'question': '硕士深圳', 'mode': 'llm'}).json()
        self.assertEqual(result['mode_used'], 'llm')
        generate.assert_called_once()
        model = self.client.app.state.model_config.factory()
        self.assertEqual((model.base, model.name, model.key), ('https://example.com/v1', 'test', 'PRIVATE-KEY'))

    def test_blank_key_preserves_existing_only_for_same_endpoint(self):
        self.configure()
        body = {**self.body, 'api_key': '', 'model_name': 'other'}
        self.assertEqual(self.configure(body).status_code, 200)
        self.assertEqual(self.configure({**body, 'base_url': 'https://other.example/v1'}).status_code, 400)

    def test_invalid_configuration_and_large_body_do_not_echo_key(self):
        for base in ('http://example.com', 'https://user:pass@example.com', 'https://example.com?key=PRIVATE-KEY'):
            response = self.configure({**self.body, 'base_url': base})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('PRIVATE-KEY', response.text)
        response = self.configure({**self.body, 'extra': 'PRIVATE-KEY'})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('PRIVATE-KEY', response.text)
        self.assertEqual(self.client.put('/v1/model', content=b'x'*65537, headers=self.headers).status_code, 413)

    def test_configuration_cross_origin_writes_rejected(self):
        self.assertEqual(self.client.put('/v1/model', json=self.body).status_code, 403)
        response = self.client.put('/v1/model', json=self.body, headers={**self.headers, 'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)

    def test_nonlocal_host_rejected(self):
        response = self.client.get('/v1/model', headers={'Host': 'untrusted.example'})
        self.assertEqual(response.status_code, 400)

    def test_connection_success_and_safe_failure(self):
        self.configure()
        with patch('app.agent.llm.JsonModel.generate', return_value={'ok': True}):
            self.assertTrue(self.client.post('/v1/model/test', headers=self.headers).json()['ok'])
        with patch('app.agent.llm.JsonModel.generate', side_effect=ModelError('模型 HTTP 错误 401；未记录响应正文')):
            response = self.client.post('/v1/model/test', headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('PRIVATE-KEY', response.text)

    def test_clear_does_not_restore_environment_credentials(self):
        self.configure()
        response = self.client.delete('/v1/model', headers=self.headers)
        self.assertFalse(response.json()['configured'])
        with self.assertRaises(ModelError):
            self.client.app.state.model_config.factory()

    def test_detail_is_readonly_and_source_ids_not_paths(self):
        response = self.client.get('/v1/sources/test-source')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['text'])
        self.assertEqual(self.client.get('/v1/sources/unknown').status_code, 404)

    def test_saved_configuration_survives_app_restart_and_clear_removes_file(self):
        self.configure()
        path = self.store.parent / 'private/model-config.json'
        self.assertTrue(path.is_file())
        from app.agent.model_config import ModelConfiguration
        restored = ModelConfiguration(path)
        self.assertTrue(restored.status()['configured'])
        self.assertEqual(restored.factory().key, 'PRIVATE-KEY')
        self.assertNotIn('PRIVATE-KEY', str(restored.status()))
        restored.clear()
        self.assertFalse(path.exists())
        self.assertFalse(ModelConfiguration(path).status()['configured'])

    def test_corrupt_file_reports_safe_status(self):
        from app.agent.model_config import ModelConfiguration
        path = self.root / 'bad.json'
        path.write_text('{bad PRIVATE-KEY', encoding='utf-8')
        status = ModelConfiguration(path).status()
        self.assertFalse(status['configured'])
        self.assertTrue(status['load_error'])
        self.assertNotIn('PRIVATE-KEY', str(status))

    def test_environment_credentials_override_saved_file(self):
        self.configure()
        from app.agent.model_config import ModelConfiguration
        with patch.dict(os.environ, {'POLICY_MODEL_BASE_URL': 'https://env.example/v1',
                'POLICY_MODEL_NAME': 'env-model', 'POLICY_MODEL_API_KEY': 'ENV-PRIVATE'}):
            restored = ModelConfiguration(self.client.app.state.model_config.path)
            self.assertEqual(restored.status()['origin'], 'environment')
            self.assertEqual(restored.factory().key, 'ENV-PRIVATE')

    def test_persist_failure_preserves_previous_configuration(self):
        self.configure()
        config = self.client.app.state.model_config
        with patch('app.agent.model_config.os.replace', side_effect=OSError('PRIVATE-KEY')):
            response = self.configure({**self.body, 'model_name': 'changed'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('PRIVATE-KEY', response.text)
        self.assertEqual(config.status()['model_name'], 'test')
        self.assertEqual(list(config.path.parent.glob('.model-*.tmp')), [])

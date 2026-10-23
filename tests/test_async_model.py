import asyncio
import json
import unittest
from unittest.mock import patch
import httpx
from app.agent.async_llm import AsyncJsonModel
from app.agent.llm import JsonModel, ModelError


class AsyncModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_payload_uses_visual_override_and_nonstreaming(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'vision-test')
            self.assertFalse(payload['stream'])
            parts = payload['messages'][-1]['content']
            self.assertTrue(any(part.get('type') == 'image_url' and part['image_url']['url'].startswith('data:image/png;base64,') for part in parts))
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
        model = AsyncJsonModel(JsonModel('https://example.test/v1', 'text-test', 'test-only-key'))
        with patch('app.agent.async_llm.httpx.AsyncClient', self.client_factory(handler)):
            self.assertEqual(await model.generate_image('识图', {}, b'test-bytes', 'image/png', model_name='vision-test'), {'ok': True})

    def client_factory(self, handler):
        client = httpx.AsyncClient
        return lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)

    async def test_non_streaming_request_json_and_safe_transport(self):
        def handler(request):
            self.assertEqual(request.url.path, '/v1/chat/completions')
            self.assertFalse(json.loads(request.content)['stream'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
        model = AsyncJsonModel(JsonModel('https://example.test/v1', 'test-model', 'test-only-key'))
        with patch('app.agent.async_llm.httpx.AsyncClient', self.client_factory(handler)):
            self.assertEqual(await model.generate('测试', {}), {'ok': True})

    async def test_redirect_and_error_body_are_not_exposed(self):
        for status in (302, 401):
            def handler(request):
                return httpx.Response(status, text='SECRET-PROVIDER-BODY', headers={'Location': 'https://other.test'})
            model = AsyncJsonModel(JsonModel('https://example.test', 'test-model', 'test-only-key'))
            with patch('app.agent.async_llm.httpx.AsyncClient', self.client_factory(handler)):
                with self.assertRaises(ModelError) as error:
                    await model.generate('测试', {})
                self.assertNotIn('SECRET', str(error.exception))
                self.assertIn(str(status), str(error.exception))

    async def test_timeout_and_cancellation_propagate(self):
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.sleep(5)
            return httpx.Response(200, json={})
        model = AsyncJsonModel(JsonModel('https://example.test', 'test-model', 'test-only-key'))
        with patch('app.agent.async_llm.httpx.AsyncClient', self.client_factory(handler)):
            with self.assertRaises(ModelError):
                await model.generate('测试', {}, timeout=.01)
            task = asyncio.create_task(model.generate('测试', {}))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

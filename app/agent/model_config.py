"""Local private credential file; never return keys or mutate environment variables."""
import os
import json
import uuid
from pathlib import Path
from threading import RLock
from app.agent.llm import JsonModel, ModelError


class ModelConfiguration:
    def __init__(self, path=None):
        self.lock = RLock()
        self.path = Path(path) if path else None
        self.vision_name = os.environ.get('POLICY_VISION_MODEL_NAME', '')
        self.values = tuple(os.environ.get(name, '') for name in
            ('POLICY_MODEL_BASE_URL', 'POLICY_MODEL_NAME', 'POLICY_MODEL_API_KEY'))
        self.origin = 'environment' if any(self.values) else 'none'
        self.load_error = False
        if self.path and self.path.is_file() and not all(self.values):
            try:
                with self.path.open('rb') as stream:
                    raw = stream.read(16385)
                if len(raw) > 16384:
                    raise ValueError('configuration too large')
                data = json.loads(raw)
                values = tuple(data[name] for name in ('base_url', 'model_name', 'api_key'))
                if not all(isinstance(v, str) for v in values):
                    raise ValueError('invalid configuration')
                model = JsonModel(*values)
                self.values = (model.base, model.name, model.key)
                vision = os.environ.get('POLICY_VISION_MODEL_NAME') or data.get('vision_model_name', '')
                if not isinstance(vision, str) or len(vision) > 200:
                    raise ValueError('invalid vision model')
                self.vision_name = vision
                self.origin = 'local_file'
            except (OSError, ValueError, KeyError, TypeError, ModelError):
                self.values, self.origin, self.load_error = ('', '', ''), 'none', True

    def status(self):
        with self.lock:
            base, name, key = self.values
            return {'base_url': base, 'model_name': name, 'vision_model_name': self.vision_name, 'key_present': bool(key),
                    'configured': bool(base and name and key), 'origin': self.origin,
                    'persistence': 'local_private_file' if self.path else 'server_process_memory',
                    'load_error': self.load_error}

    def persist(self, values, vision_name=''):
        if not self.path:
            return
        temporary = self.path.with_name('.model-' + uuid.uuid4().hex + '.tmp')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({**dict(zip(('base_url', 'model_name', 'api_key'), values)), 'vision_model_name': vision_name}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise ModelError('本地模型配置保存失败，请检查私有目录写入权限') from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def save(self, base, name, key, vision_name=None):
        with self.lock:
            if not key:
                if base.rstrip('/') != self.values[0].rstrip('/'):
                    raise ModelError('更换接口地址时需要重新输入 API Key')
                key = self.values[2]
            model = JsonModel(base.strip(), name.strip(), key)
            vision_name = self.vision_name if vision_name is None and model.base == self.values[0] else vision_name or ''
            if not isinstance(vision_name, str) or len(vision_name) > 200:
                raise ModelError('视觉模型名称格式无效')
            vision_name = vision_name.strip()
            self.persist((model.base, model.name, model.key), vision_name)
            self.values = (model.base, model.name, model.key)
            self.vision_name = vision_name
            self.origin = 'local_file' if self.path else 'runtime'
            self.load_error = False
            return self.status()

    def clear(self):
        with self.lock:
            if self.path:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    raise ModelError('本地模型配置删除失败，请检查私有目录权限') from None
            self.values, self.origin = ('', '', ''), 'none'
            self.vision_name = ''
            self.load_error = False
            return self.status()

    def factory(self):
        with self.lock:
            model = JsonModel(*self.values)
            model.vision_name = self.vision_name
            return model

    def test(self):
        result = self.factory().generate('连接测试：仅返回 {"ok": true}。', {}, timeout=15)
        if result.get('ok') is not True:
            raise ModelError('接口已响应，但未返回约定的 JSON 对象')
        return {'ok': True, 'message': '连接成功，JSON 输出可用；尚未验证政策回答质量'}

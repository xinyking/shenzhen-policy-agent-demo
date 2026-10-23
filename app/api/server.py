"""Local policy workspace, evidence endpoints and process-local model configuration."""
from app.utils.path_utils import PROJECT_ROOT
import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.llm import JsonModel, ModelError
from app.agent.model_config import ModelConfiguration
from app.services.policy_service import PolicyService, ServiceUnavailable, Settings
from app.memory.personal_memory import PersonalMemory
from app.api.task_service import RunService
from app.api.routes.runs import install_run_routes
from datetime import date
import sqlite3


class BodyLimit:
    def __init__(self, app, limit=65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT'}:
            return await self.app(scope, receive, send)
        messages, size = [], 0
        limit = 4 * 1024 * 1024 if scope['path'] == '/v1/screenshots' else self.limit
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > limit:
                return await JSONResponse(status_code=413, content={'error': {'code': 'body_too_large',
                    'message': '截图超过4MiB限制' if scope['path'] == '/v1/screenshots' else '请求正文超过64KiB限制'}})(scope, receive, send)
            messages.append(message)
            if not message.get('more_body', False):
                break
        iterator = iter(messages)
        async def replay():
            return next(iterator, None) or await receive()
        await self.app(scope, replay, send)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=50)

    @field_validator('query')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('问题不能为空')
        return value.strip()


class AskInput(Input):
    question: str = Field(min_length=1, max_length=500)
    context: str = Field(default='', max_length=1000)
    top_k: int = Field(default=5, ge=1, le=8)
    mode: Literal['offline', 'llm', 'agent', 'multi_agent'] = 'offline'
    use_memory: bool = False
    session_id: str | None = Field(default=None, max_length=64, pattern=r'^[a-zA-Z0-9-]+$')

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        return SearchInput.nonblank(value)


def default_settings():
    root = Path(os.environ.get('POLICY_PROJECT_ROOT', PROJECT_ROOT))
    return Settings(store=root / 'data/store')


class ModelInput(Input):
    base_url: str = Field(min_length=1, max_length=2000)
    model_name: str = Field(min_length=1, max_length=200)
    api_key: str = Field(default='', max_length=4000, repr=False)
    vision_model_name: str | None = Field(default=None, max_length=200)

class MemoryInput(Input):
    profile: str = Field(max_length=1000)

class MaterialRecord(Input):
    text: str = Field(min_length=1,max_length=200)
    evidence_id: str = Field(pattern=r'^[ES]\d+$',max_length=16)
    quote: str = Field(min_length=1,max_length=500)
    done: bool = False
    source_kind: Literal['policy','screenshot'] = 'policy'
    location: str = Field(default='',max_length=100)
    source_title: str = Field(default='',max_length=150)
    requirement: Literal['required','conditional','optional','unclear'] = 'unclear'
    condition: str = Field(default='',max_length=300)

    @model_validator(mode='after')
    def distinguish_source(self):
        if not self.evidence_id.startswith('S' if self.source_kind == 'screenshot' else 'E'):
            raise ValueError('截图与政策引用类型不匹配')
        return self

class PolicyReference(Input):
    evidence_id: str = Field(pattern=r'^E\d+$',max_length=16)
    title: str = Field(min_length=1,max_length=500)
    source_url: str = Field(max_length=2000)
    version: str = Field(min_length=1,max_length=128)
    locator: str = Field(min_length=1,max_length=300)

    @field_validator('source_url')
    @classmethod
    def official_reference(cls,value):
        from urllib.parse import urlparse
        url=urlparse(value)
        if url.scheme!='https' or not url.hostname or not (url.hostname=='sz.gov.cn' or url.hostname.endswith('.sz.gov.cn')) or url.username or url.password:
            raise ValueError('引用必须为深圳官方网址')
        return value

class ApplicationInput(Input):
    id: str = Field(min_length=1,max_length=64,pattern=r'^[a-zA-Z0-9-]+$')
    title: str = Field(min_length=1,max_length=150)
    date: str
    notes: str = Field(default='',max_length=1000)
    materials: str = Field(default='',max_length=2000)
    applicationMethod: str = Field(default='',max_length=2000)
    applicationStatus: Literal['planned','preparing','submitted','approved','rejected'] = 'planned'
    done: bool = False
    consultationId: str | None = Field(default=None,max_length=64)
    created: str = Field(max_length=64)
    materialsChecklist: list[MaterialRecord] = Field(default_factory=list,max_length=12)
    policySources: list[PolicyReference] = Field(default_factory=list,max_length=8)

    @field_validator('title')
    @classmethod
    def nonblank_title(cls,value):
        if not value.strip(): raise ValueError('标题不能为空')
        return value.strip()

    @field_validator('date')
    @classmethod
    def valid_date(cls, value):
        if date.fromisoformat(value).isoformat()!=value: raise ValueError('日期无效')
        return value


def create_app(settings=None, model_factory=JsonModel):
    settings = settings or default_settings()
    memory = PersonalMemory(settings.store.parent / 'private/personal-memory.sqlite3')
    @asynccontextmanager
    async def lifespan(app):
        app.state.model_config = ModelConfiguration(settings.store.parent / 'private/model-config.json')
        app.state.service = PolicyService(settings, app.state.model_config.factory if model_factory is JsonModel else model_factory)
        app.state.runs = RunService(app.state.service)
        try:
            yield
        finally:
            await app.state.runs.close()
    app = FastAPI(title='深圳政务智能问答', version='0.17.1', lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.middleware('http')
    async def protect_configuration(request, call_next):
        if request.url.hostname not in {'127.0.0.1', 'localhost', 'testserver'}:
            return JSONResponse(status_code=400, content={'error': {'code': 'invalid_host', 'message': '仅支持本机访问'}})
        if request.url.path.startswith(('/v1/runs','/v1/screenshots')):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse(status_code=403, content={'error': {'message': '任务仅允许同源工作台访问'}})
        if request.url.path.startswith(('/v1/model','/v1/memory','/v1/runs','/v1/screenshots')) and request.method != 'GET':
            origin = request.headers.get('origin')
            if request.headers.get('x-policy-client') != 'workspace' or (origin and origin != str(request.base_url).rstrip('/')):
                return JSONResponse(status_code=403, content={'error': {'code': 'forbidden', 'message': '本地配置与记忆仅允许同源工作台请求'}})
        response = await call_next(request)
        if request.url.path.startswith(('/v1/model','/v1/memory','/v1/runs','/v1/screenshots')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ModelError)
    async def model_error(request, exc):
        return JSONResponse(status_code=400, content={'error': {'code': 'model_error', 'message': str(exc)}})

    @app.get('/v1/model')
    def model_status(request: Request):
        return request.app.state.model_config.status()

    @app.put('/v1/model')
    def model_save(body: ModelInput, request: Request):
        return request.app.state.model_config.save(body.base_url, body.model_name, body.api_key, body.vision_model_name)

    @app.delete('/v1/model')
    def model_clear(request: Request):
        return request.app.state.model_config.clear()

    @app.post('/v1/model/test')
    def model_test(request: Request):
        return request.app.state.model_config.test()

    @app.get('/v1/memory')
    def memory_get(): return memory.snapshot()

    @app.put('/v1/memory')
    def memory_save(body: MemoryInput):
        memory.save_profile(body.profile)
        return memory.snapshot()

    @app.delete('/v1/memory')
    def memory_clear():
        memory.clear()
        return memory.snapshot()

    @app.post('/v1/memory/applications')
    def application_save(body: ApplicationInput):
        memory.save_application(body.model_dump())
        return {'ok':True}

    @app.delete('/v1/memory/applications/{application_id}')
    def application_delete(application_id: str):
        memory.delete_application(application_id)
        return {'ok':True}

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse(status_code=422, content={'error': {'code': 'invalid_input',
            'message': '输入格式或字段值不合法'}})

    async def unavailable(request, exc):
        return JSONResponse(status_code=503, content={'error': {'code': 'data_unavailable',
            'message': '本地政策数据不可用，请检查采集状态及快照完整性'}})
    for error in (ServiceUnavailable, ValueError, OSError, KeyError, TypeError, sqlite3.Error):
        app.add_exception_handler(error, unavailable)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'version': '0.17.1', 'snapshot_store_present':
                (settings.store / 'manifest.json').is_file(), 'default_mode': 'offline', 'persistence': 'none'}

    @app.get('/v1/sources')
    def sources(request: Request):
        return {'sources': request.app.state.service.sources()}

    @app.get('/v1/sources/{source_id}')
    def source_detail(source_id: str, request: Request):
        doc = next((doc for doc in request.app.state.service.documents() if doc['source_id'] == source_id), None)
        if doc is None:
            return JSONResponse(status_code=404, content={'error': {'code': 'not_found', 'message': '未收录该来源'}})
        return {key: doc.get(key) for key in ('source_id', 'title', 'source_url', 'published_at', 'publisher', 'version', 'last_checked_at', 'last_check_status', 'text', 'spans')}

    @app.post('/v1/search')
    def search(body: SearchInput, request: Request):
        return request.app.state.service.search(body.query, body.top_k)

    @app.post('/v1/ask')
    def ask(body: AskInput, request: Request):
        return request.app.state.service.ask(body.question, body.context, body.top_k, body.mode, body.use_memory, body.session_id)
    install_run_routes(app, AskInput)
    dist = PROJECT_ROOT / 'frontend/dist'
    if (dist / 'assets').is_dir() and (dist / 'index.html').is_file():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')
        @app.get('/', include_in_schema=False)
        def workspace():
            return FileResponse(dist / 'index.html')
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description='启动本地政务问答后端')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port范围为1–65535')
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()

"""HTTP task control and replayable SSE; only sanitized business events leave the graph."""
import asyncio
import json
import time
from typing import Literal
from fastapi import Request, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import Field
from app.api.task_service import RunConflict, RunLimit
from app.api.run_store import TERMINAL
from app.tools.screenshot_tools import validate_image, ScreenshotError


def install_run_routes(app, ask_input):
    # Share strict request constraints with /v1/ask without creating an api import cycle.
    class RunInput(ask_input):
        client_request_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9-]+$')
        mode: Literal['offline', 'llm', 'agent', 'multi_agent', 'quick'] = 'offline'
        screenshot_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
        image_consent: bool = False
        vision_model: str = Field(default='', max_length=200)

    def get_manager(request):
        return request.app.state.runs

    def require_run(manager, run_id):
        item = manager.store.get(run_id)
        if item is None:
            raise HTTPException(404, '任务不存在或已过保留期限')
        return item

    @app.post('/v1/screenshots')
    async def upload(request: Request):
        raw = await request.body()
        mime = request.headers.get('content-type', '').split(';')[0]
        try:
            info = await asyncio.to_thread(validate_image, raw, mime)
            return get_manager(request).images.put(raw, info)
        except ScreenshotError as exc:
            return JSONResponse(status_code=400, content={'error': {'message': str(exc)}})

    @app.post('/v1/runs', status_code=202)
    async def submit(body: RunInput, request: Request):
        try:
            return get_manager(request).submit(body.model_dump())
        except RunConflict as exc:
            return JSONResponse(status_code=409, content={'error': {'message': str(exc)}})
        except RunLimit as exc:
            return JSONResponse(status_code=429, content={'error': {'message': str(exc)}})
        except ScreenshotError as exc:
            return JSONResponse(status_code=400, content={'error': {'message': str(exc)}})

    @app.get('/v1/runs/{run_id}')
    async def status(run_id: str, request: Request):
        return require_run(get_manager(request), run_id)

    @app.get('/v1/runs/{run_id}/result')
    async def result(run_id: str, request: Request):
        manager = get_manager(request)
        item = require_run(manager, run_id)
        return manager.store.result(run_id) if item['result_available'] else JSONResponse(status_code=409, content={
            'error': {'message': item['error'] or '任务尚未产生结果'}, 'status': item['status']})

    @app.post('/v1/runs/{run_id}/cancel')
    async def cancel(run_id: str, request: Request):
        manager = get_manager(request)
        require_run(manager, run_id)
        return manager.cancel(run_id)

    @app.get('/v1/runs/{run_id}/events')
    async def events(run_id: str, request: Request, after: int = Query(default=0, ge=0, le=1_000_000)):
        manager = get_manager(request)
        require_run(manager, run_id)
        header = request.headers.get('last-event-id', '')
        if len(header) <= 16 and header.isdecimal():
            after = max(after, int(header))

        async def stream():
            cursor, last_ping = after, time.monotonic()
            while True:
                if await request.is_disconnected():
                    break
                batch = manager.store.events(run_id, cursor)
                for event in batch:
                    cursor = event['seq']
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                item = manager.store.get(run_id)
                if item is None or item['status'] in TERMINAL:
                    break
                if time.monotonic() - last_ping > 10:
                    yield ': heartbeat\n\n'
                    last_ping = time.monotonic()
                await asyncio.sleep(.25)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

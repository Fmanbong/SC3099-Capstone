"""SAIV application factory and deployment entry point."""
import asyncio
import importlib
import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from app.config import Settings
from app.database import Base, build_database
from app.errors import error_response, install_error_handlers, log_failure, unexpected_response
from app.security import hash_password
from app.services.rate_limit import RateLimiter

logger = logging.getLogger('saiv')


class RequestContextMiddleware:
    """Bound JSON bodies even without Content-Length and attach request correlation."""
    def __init__(self, app, settings, registry_metrics):
        self.app, self.settings, self.metrics = app, settings, registry_metrics

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        supplied = headers.get(b'x-request-id', b'').decode('ascii', 'ignore')
        request_id = supplied if re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', supplied) else str(uuid.uuid4())
        scope.setdefault('state', {})['request_id'] = request_id
        started, status = time.monotonic(), 500
        response_started = False

        async def response_send(message):
            nonlocal status, response_started
            if message['type'] == 'http.response.start':
                response_started = True
                status = message['status']
                response_headers = MutableHeaders(scope=message)
                response_headers['X-Request-ID'] = request_id
                response_headers['Cache-Control'] = 'no-store'
                response_headers['X-Content-Type-Options'] = 'nosniff'
            await send(message)

        request = Request(scope)
        data = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            data.extend(message.get('body', b''))
            if len(data) > self.settings.max_request_bytes:
                response = error_response(request, 413, 'Request body is too large', 'REQUEST_TOO_LARGE')
                return await response(scope, receive, response_send)
            if not message.get('more_body', False):
                break
        delivered = False

        async def body_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(data), 'more_body': False}
            return await receive()

        try:
            await self.app(scope, body_receive, response_send)
        except Exception as exc:
            # Handle before Starlette's outer error middleware re-raises to
            # Uvicorn, which would otherwise log arbitrary exception values.
            if response_started:
                log_failure(request, exc)
                raise RuntimeError(f'Response failed; request_id={request_id}') from None
            response = unexpected_response(request, exc)
            await response(scope, receive, response_send)
        finally:
            route = scope.get('route')
            path = getattr(route, 'path', 'unmatched')
            self.metrics[0].labels(scope['method'], path, str(status)).inc()
            self.metrics[1].labels(scope['method'], path).observe(time.monotonic() - started)


def create_app(settings=None, *, face_client=None):
    settings = settings or Settings()
    engine, factory = build_database(settings.database_url)
    registry = CollectorRegistry()
    count = Counter('saiv_http_requests_total', 'HTTP requests', ['method', 'path', 'status'], registry=registry)
    duration = Histogram('saiv_http_request_duration_seconds', 'HTTP duration', ['method', 'path'], registry=registry)

    @asynccontextmanager
    async def lifespan(app):
        settings.validate_startup()
        if settings.auto_create_schema:
            Base.metadata.create_all(engine)
            from app.services.audit_guards import install_audit_guards
            with engine.begin() as connection:
                install_audit_guards(connection)
        app.state.limiter = RateLimiter(settings)
        app.state.dummy_password_hash = await run_in_threadpool(hash_password, secrets.token_urlsafe(24), settings.bcrypt_rounds)
        if app.state.face_client is None:
            from app.services.face import FaceServiceClient
            app.state.face_client = FaceServiceClient(settings.face_service_url, settings.face_timeout_seconds,
                                                      max_image_bytes=settings.max_image_bytes)

        async def retention_loop():
            while True:
                await asyncio.sleep(settings.retention_interval_seconds)
                try:
                    from app.services.retention import cleanup_expired_records
                    def cleanup():
                        with factory() as db:
                            db.info['settings'] = settings
                            cleanup_expired_records(db)
                    await run_in_threadpool(cleanup)
                except Exception as exc:
                    logger.error('Retention job failed exception=%s', type(exc).__name__)

        task = asyncio.create_task(retention_loop()) if settings.retention_enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if hasattr(app.state.face_client, 'close'):
                app.state.face_client.close()
            app.state.limiter.close()
            engine.dispose()

    app = FastAPI(title='SAIV Backend API', version='1.0.0', lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.face_client = face_client
    app.add_middleware(RequestContextMiddleware, settings=settings, registry_metrics=(count, duration))
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
                       allow_headers=['Authorization', 'Content-Type', 'X-Request-ID'],
                       expose_headers=['X-Request-ID', 'Retry-After', 'Content-Disposition'])
    install_error_handlers(app)

    for name in ('auth', 'users', 'admin', 'courses', 'sessions', 'enrollments', 'devices', 'checkins', 'stats', 'audit', 'exports'):
        module_name = f'app.routers.{name}'
        app.include_router(importlib.import_module(module_name).router, prefix='/api/v1')

    @app.get('/health', tags=['Operations'])
    def health(request: Request):
        components = {'api': 'healthy', 'database': 'healthy', 'redis': 'disabled'}
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT id FROM users LIMIT 1'))
        except Exception:
            components['database'] = 'unavailable'
        if settings.rate_limit_enabled:
            try:
                app.state.limiter.redis.ping()
                components['redis'] = 'healthy'
            except Exception:
                components['redis'] = 'unavailable'
        healthy = 'unavailable' not in components.values()
        from fastapi.responses import JSONResponse
        return JSONResponse({'status': 'healthy' if healthy else 'unhealthy', **components}, status_code=200 if healthy else 503)

    @app.get('/metrics', include_in_schema=False)
    def metrics():
        return Response(generate_latest(registry), headers={'Content-Type': CONTENT_TYPE_LATEST})

    return app


app = create_app()

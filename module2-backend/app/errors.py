"""Public errors deliberately omit request input and upstream internals."""
import logging
import traceback

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException

logger = logging.getLogger('saiv.errors')


class ApiError(HTTPException):
    def __init__(self, status_code, detail, code):
        super().__init__(status_code, detail)
        self.code = code


def error_response(request, status, detail, code, headers=None):
    safe_headers = {'X-Request-ID': getattr(request.state, 'request_id', ''),
                    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', **(headers or {})}
    return JSONResponse(status_code=status, content={
        'detail': detail, 'code': code,
        'request_id': getattr(request.state, 'request_id', ''),
    }, headers=safe_headers)


def log_failure(request, exc):
    frames = '\n'.join(f'  {f.filename}:{f.lineno} in {f.name}' for f in traceback.extract_tb(exc.__traceback__))
    logger.error('Failure request_id=%s exception=%s\n%s',
                 getattr(request.state, 'request_id', ''), type(exc).__name__, frames)


def unexpected_response(request, exc):
    log_failure(request, exc)
    return error_response(request, 500, 'An internal server error occurred', 'INTERNAL_ERROR')


def install_error_handlers(app):
    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        defaults = {400: 'BAD_REQUEST', 401: 'UNAUTHORIZED', 403: 'FORBIDDEN', 404: 'NOT_FOUND', 405: 'METHOD_NOT_ALLOWED', 429: 'RATE_LIMITED', 503: 'SERVICE_UNAVAILABLE'}
        return error_response(request, exc.status_code, exc.detail,
                              getattr(exc, 'code', defaults.get(exc.status_code, 'HTTP_ERROR')), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]
        return error_response(request, 422, details, 'VALIDATION_ERROR')

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return error_response(request, 400, 'Request conflicts with an existing record or relationship', 'CONFLICT')

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # Driver exceptions can contain bound values: log class + code location, never str(exc).
        log_failure(request, exc)
        return error_response(request, 500, 'Database error occurred', 'DATABASE_ERROR')

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return unexpected_response(request, exc)

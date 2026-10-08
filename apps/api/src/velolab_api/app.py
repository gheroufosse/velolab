import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from velolab_api.auth import login
from velolab_api.auth import router as auth_router
from velolab_api.integrations import get_connection, save_connection, test_connection
from velolab_api.integrations import router as integration_router

# INFO transport logs include athlete URLs. No body or exception-local capture
# is configured; do not enable it for credential enrollment (ADR-025).
for logger_name in ("httpx", "httpcore"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

app = FastAPI(title="velolab-api")
app.include_router(auth_router)
app.include_router(integration_router)
_CONNECTION_ENDPOINTS = (get_connection, test_connection, save_connection)


@app.middleware("http")
async def connection_no_store(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    route = request.scope.get("route")
    if isinstance(route, APIRoute) and route.endpoint in _CONNECTION_ENDPOINTS:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    route = request.scope.get("route")
    if isinstance(route, APIRoute) and route.endpoint in (test_connection, save_connection):
        # Even malformed JSON/body locs can contain input. Keep this static and
        # route-aware so mounted/proxy prefixes cannot bypass redaction.
        return JSONResponse(status_code=422, content={"detail": "invalid_input"})
    if isinstance(route, APIRoute) and route.endpoint is login:
        # FastAPI's default includes raw inputs, which can echo the password.
        details = [
            {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})
    return await request_validation_exception_handler(request, error)


@app.get(path="/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

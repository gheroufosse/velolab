from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from velolab_api.auth import login
from velolab_api.auth import router as auth_router

app = FastAPI(title="velolab-api")
app.include_router(auth_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    route = request.scope.get("route")
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

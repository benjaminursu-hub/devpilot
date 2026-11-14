from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.errors import ApiError
from app.middleware import CatchUnhandledErrorsMiddleware

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DevPilot API",
        version="0.2.0",
        description=(
            "Backend for DevPilot: inspect and analyze failed GitHub Actions runs.\n\n"
            "**No user authentication yet** — for local use only. "
            "See README before deploying anywhere public."
        ),
    )

    # Order matters: the middleware added LAST is the OUTERMOST. The error catcher must sit
    # inside CORS so even 500 responses carry CORS headers (see app/middleware.py).
    app.add_middleware(CatchUnhandledErrorsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default is a list of error objects; the contract requires a string.
        return JSONResponse(status_code=422, content={"detail": _describe(exc)})

    app.include_router(router, prefix=API_PREFIX)
    return app


def _describe(exc: RequestValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        messages.append(f"{location}: {error.get('msg', 'invalid value')}")
    return "Invalid request. " + "; ".join(messages)


app = create_app()

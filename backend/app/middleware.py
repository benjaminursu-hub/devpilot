"""Turn unhandled exceptions into a contract-shaped 500 *inside* the CORS middleware.

Why this exists: Starlette builds the response for an unhandled exception in its outermost
middleware, outside CORSMiddleware. That 500 therefore has no Access-Control-Allow-Origin
header, the browser hides it from the page, and `fetch` rejects as if the server were
unreachable. The frontend then shows "Could not reach the DevPilot API" for what is really a
server bug. Catching the exception here (inside CORS) keeps the CORS headers, returns
{"detail": "..."} like every other error, and logs the traceback.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_setup import logger


class CatchUnhandledErrorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:  # too late to send a clean error response
                raise
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error. Details were logged on the server."},
            )
            await response(scope, receive, send)

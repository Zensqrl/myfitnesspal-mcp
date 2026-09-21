"""LAN application: private gateway-authenticated setup API and archive MCP."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
import hmac
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route

from . import auth
from .onboarding import Onboarding


class GatewayOnly:
    """Reject direct backend access, including requests originating in Chromium."""
    def __init__(self, app, secret: str):
        self.app, self.secret = app, secret

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz":
            headers = dict(scope["headers"])
            if not hmac.compare_digest(headers.get(b"x-mfp-gateway", b""), self.secret.encode()):
                await Response(status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(manager=None):
    manager = manager or Onboarding()
    origin = os.environ["MFP_PUBLIC_ORIGIN"].rstrip("/")
    secret = Path(os.environ["MFP_GATEWAY_TOKEN_FILE"]).read_text().strip()
    if len(secret) < 32 or not origin.startswith("https://"):
        raise ValueError("HTTPS origin and a strong gateway secret are required")
    mcp = FastMCP(
        "MyFitnessPal archive", stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["app:8484"], allowed_origins=[origin],
        ),
    )

    @mcp.tool()
    async def fitness_connection_status() -> dict:
        """Report connection and background synchronization status; never returns credentials."""
        return manager.status()

    @mcp.tool()
    async def fitness_get_day(day: str | None = None) -> dict:
        """Read archived nutrition, meals and notes immediately. Dates are YYYY-MM-DD.

        Stale/missing data queues background refresh; retrieved_at identifies data age.
        A queued refresh is not a completed refresh. Call again later for updated data.
        """
        selected = date.fromisoformat(day) if day else date.today()
        if selected > date.today() or selected < date.today() - timedelta(days=36500):
            raise ValueError("Date must be in the past 100 years through today")
        result = await asyncio.to_thread(manager.archived_day, selected)
        result["refresh_queued"] = manager.queue_day(selected) if result.get("stale") else False
        return result

    @mcp.tool()
    async def fitness_sync_today() -> dict:
        """Queue today's nutrition refresh and return immediately; this does not wait for upstream."""
        return {"refresh_queued": manager.queue_day(date.today()), **manager.status()}

    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await manager.stop()

    async def health(request):
        return JSONResponse({"ok": True})

    async def status(request):
        result = manager.status()
        result["today"] = await asyncio.to_thread(manager.archived_day, date.today())
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    def historical_day(value):
        if not isinstance(value, str):
            raise ValueError()
        parsed = date.fromisoformat(value)
        if parsed > date.today() or parsed < date.today() - timedelta(days=36500):
            raise ValueError()
        return parsed

    async def archive_day(request):
        try:
            selected = historical_day(request.query_params.get("day"))
        except (ValueError, TypeError):
            return JSONResponse({"error": "Choose a valid historical date."}, status_code=400)
        result = await asyncio.to_thread(manager.archive_view, selected)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def authorize_browser(request):
        # Gateway already authenticated the administrator. Also require an active
        # lease and reject cross-origin WebSocket handshakes.
        if request.headers.get("origin", origin) != origin or not manager.status()["browser_open"]:
            return Response(status_code=403)
        return Response(status_code=204)

    async def action(request: Request):
        if (request.headers.get("origin") != origin
                or request.headers.get("x-mfp-request") != "1"
                or request.headers.get("content-type", "").split(";")[0] != "application/json"):
            return Response(status_code=403)
        # Bound request bodies before parsing; cookies must never appear in URLs.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32768:
                return Response(status_code=413)
        import json
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
            op = request.path_params["action"]
            if op == "start":
                fn, args, name = manager.begin, (), "open browser"
            elif op == "finish":
                username = data.get("username") or None
                if username is not None and (not isinstance(username, str) or len(username) > 128):
                    raise ValueError()
                fn, args, name = manager.finish, (username,), "connect"
            elif op == "paste":
                cookie = data.get("cookie", "")
                username = data.get("username") or None
                if not isinstance(cookie, str) or (username is not None and not isinstance(username, str)):
                    raise ValueError()
                fn, args, name = manager.finish, (username, auth.parse_cookie_input(cookie)), "connect"
            elif op == "cancel":
                fn, args, name = manager.cancel, (), "cancel browser"
            elif op == "disconnect":
                if data.get("confirm") is not True:
                    raise ValueError()
                fn, args, name = manager.disconnect, (), "disconnect"
            elif op == "sync":
                queued = manager.queue_day(date.today(), force=True)
                return JSONResponse({"queued": queued, "error": None if queued else manager.message},
                                    status_code=202 if queued else 409)
            elif op == "backfill":
                start = historical_day(data.get("start"))
                force = data.get("force", False)
                if not isinstance(force, bool):
                    raise ValueError()
                queued = manager.queue_historical(
                    start, date.today(), force=force, kind="backfill"
                )
                return JSONResponse(
                    {"queued": queued, "error": None if queued else manager.message},
                    status_code=202 if queued else 409,
                )
            elif op == "sync-range":
                start, end = historical_day(data.get("start")), historical_day(data.get("end"))
                force = data.get("force", False)
                if end < start or not isinstance(force, bool):
                    raise ValueError()
                queued = manager.queue_historical(start, end, force=force, kind="range")
                return JSONResponse(
                    {"queued": queued, "error": None if queued else manager.message},
                    status_code=202 if queued else 409,
                )
            elif op == "cancel-utility":
                cancelled = manager.cancel_historical()
                return JSONResponse(
                    {"cancel_requested": cancelled, "error": None if cancelled else manager.message},
                    status_code=202 if cancelled else 409,
                )
            else:
                return Response(status_code=404)
            if manager.pending:
                return JSONResponse({"error": "Wait for the current operation to finish."}, status_code=409)
            manager.enqueue(name, fn, *args)
            return JSONResponse({"queued": True}, status_code=202)
        except (ValueError, TypeError):
            return JSONResponse({"error": "Invalid request"}, status_code=400)

    async def asset(request):
        name = request.path_params.get("name", "index.html")
        if name not in {"index.html", "app.js", "style.css"}:
            return Response(status_code=404)
        return FileResponse(Path(__file__).parent / "static" / name, headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; frame-src 'self'; object-src 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
        })

    app = Starlette(routes=[
        Route("/healthz", health), Route("/", asset), Route("/assets/{name}", asset),
        Route("/api/status", status), Route("/api/archive/day", archive_day),
        Route("/api/browser/authorize", authorize_browser),
        Route("/api/{action}", action, methods=["POST"]),
        Mount("/", app=mcp.streamable_http_app()),
    ], middleware=[Middleware(GatewayOnly, secret=secret)], lifespan=lifespan)
    app.state.manager = manager
    return app

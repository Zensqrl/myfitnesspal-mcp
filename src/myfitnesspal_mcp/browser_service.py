"""Single-threaded Playwright owner with staged, expiring interactive profiles."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import secrets
import shutil
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import SESSION_COOKIE
from .browser_client import token


class BrowserOwner:
    def __init__(self, root: Path, ttl: int = 900):
        self.root = root
        self.ttl = ttl
        self.context = None
        self.playwright = None
        self.session_id = None
        self.deadline = 0.0
        self.login_url = "https://www.myfitnesspal.com/account/login"

    def _close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            self.context = None
            if self.playwright:
                self.playwright.stop()
            self.playwright = None

    def _launch(self, path: Path, *, headless: bool):
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        self.context = self.playwright.chromium.launch_persistent_context(
            str(path), headless=headless, chromium_sandbox=True,
            viewport={"width": 480, "height": 900},
            is_mobile=True, has_touch=True, device_scale_factor=1,
            user_agent=self.playwright.devices["Pixel 7"]["user_agent"],
            args=["--kiosk", "--window-position=0,0", "--window-size=480,900"],
            timeout=30000,
        )
        self.context.pages[0].goto(
            self.login_url, wait_until="domcontentloaded", timeout=30000
        )

    def expire(self):
        if self.session_id and time.monotonic() >= self.deadline:
            self.cancel()

    def cancel(self):
        self._close()
        self.session_id = None
        self.deadline = 0
        candidate = self.root / "candidate"
        if candidate.exists():
            shutil.rmtree(candidate)

    def recover(self):
        self.cancel()
        active, previous = self.root / "active", self.root / "previous"
        if previous.exists():
            if active.exists():
                shutil.rmtree(previous)
            else:
                previous.rename(active)

    def start(self):
        self.expire()
        if self.session_id:
            raise ValueError("A browser session is already open.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cancel()
        # A fresh interactive profile prevents a rejected account from modifying
        # the previously accepted profile. Reconnect always requires a real login.
        try:
            self._launch(self.root / "candidate", headless=False)
            self.session_id = secrets.token_urlsafe(32)
            self.deadline = time.monotonic() + self.ttl
            return {"session_id": self.session_id}
        except Exception:
            self.cancel()
            raise

    def check(self, session_id):
        self.expire()
        if not self.session_id or not hmac.compare_digest(self.session_id, session_id or ""):
            raise ValueError("The browser session has expired.")

    def cookies(self):
        cookies = {c["name"]: c["value"] for c in self.context.cookies(
            self.login_url
        )}
        if not cookies.get(SESSION_COOKIE):
            raise ValueError("Complete the MyFitnessPal login first.")
        return cookies

    def harvest(self, session_id):
        self.check(session_id)
        return {"cookies": self.cookies()}

    def commit(self, session_id):
        self.check(session_id)
        self._close()
        active, previous = self.root / "active", self.root / "previous"
        if previous.exists():
            shutil.rmtree(previous)
        if active.exists():
            active.rename(previous)
        try:
            (self.root / "candidate").rename(active)
        except Exception:
            if previous.exists():
                previous.rename(active)
            raise
        self.session_id = None
        if previous.exists():
            shutil.rmtree(previous)
        return {"ok": True}

    def refresh(self):
        self.expire()
        if self.session_id:
            raise ValueError("Interactive login is in progress.")
        if not (self.root / "active").exists():
            raise ValueError("Reconnect required.")
        try:
            self._launch(self.root / "active", headless=True)
            return {"cookies": self.cookies()}
        finally:
            self._close()

    def forget(self):
        self.cancel()
        for name in ("active", "previous"):
            path = self.root / name
            if path.exists():
                shutil.rmtree(path)
        return {"ok": True}


def create_app(owner=None):
    owner = owner or BrowserOwner(Path(os.environ.get("MFP_BROWSER_PROFILE", "/browser")))
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")

    async def run(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)

    @asynccontextmanager
    async def lifespan(app):
        await run(owner.recover)
        async def reap():
            while True:
                await asyncio.sleep(5)
                await run(owner.expire)
        task = asyncio.create_task(reap())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await run(owner.cancel)
            executor.shutdown(wait=True)

    async def command(request: Request):
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token()):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            data = await request.json()
            operation = request.path_params["operation"]
            if operation in {"start", "refresh", "forget"}:
                result = await run(getattr(owner, operation))
            elif operation in {"harvest", "commit", "check", "cancel"}:
                await run(owner.check, data.get("session_id"))
                result = await run(owner.cancel) if operation == "cancel" else await run(
                    getattr(owner, operation), data.get("session_id")
                )
            else:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(result or {"ok": True}, headers={"Cache-Control": "no-store"})
        except Exception:
            # Playwright errors can include browser URLs and authentication data.
            return JSONResponse({"error": "Browser operation failed"}, status_code=409)

    async def health(request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/healthz", health),
                            Route("/{operation}", command, methods=["POST"])], lifespan=lifespan)

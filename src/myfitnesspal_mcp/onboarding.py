"""Serialized credential/sync jobs and fast archive reads for the LAN deployment."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from threading import Lock
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import os
import hashlib
import time

from . import auth, browser_client, config, mfp_client
from .freshness import FreshnessPolicy
from .runtime import create_service
from .store import Store


class Onboarding:
    def __init__(self):
        if config.cookie_env() or config.username_env():
            raise ValueError("Unset MFP_COOKIE and MFP_USERNAME for browser onboarding.")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mfp-upstream")
        self.queue = asyncio.Queue(maxsize=64)
        self.pending: set[str] = set()
        self.browser_session = None
        self.browser_deadline = 0.0
        self.busy = None
        self.message = ""
        self.reconnect = False
        self.tasks = []
        self.activity = deque(maxlen=100)
        self.activity_lock = Lock()

    def report(self, message):
        # Only application-authored messages belong here, never upstream errors.
        with self.activity_lock:
            self.activity.append({"time": datetime.now(timezone.utc).isoformat(), "message": message})

    def status(self):
        username = auth.saved_username()
        with self.activity_lock:
            activity = list(self.activity)
        return {
            "connected": bool(auth.load_cookies()), "username": username,
            "reconnect_required": self.reconnect, "busy": self.busy,
            "queued": len(self.pending), "message": self.message,
            "browser_open": bool(self.browser_session and time.monotonic() < self.browser_deadline),
            "activity": activity,
        }

    async def start(self):
        self.tasks = [asyncio.create_task(self.worker()), asyncio.create_task(self.scheduler())]

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        # Shutdown is allowed to finish an in-flight credential transaction.
        await asyncio.to_thread(self.executor.shutdown, wait=True)

    async def run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)

    def enqueue(self, name, fn, *args):
        if name in self.pending:
            self.report("Already queued or running: " + name)
            return True
        if self.queue.full():
            self.message = "Queue full. Try again after the current work finishes."
            self.report(self.message)
            return False
        self.pending.add(name)
        self.queue.put_nowait((name, fn, args))
        self.report("Queued: " + name)
        return True

    async def worker(self):
        while True:
            name, fn, args = await self.queue.get()
            self.busy = name
            self.report("Started: " + name)
            try:
                await self.run(fn, *args)
                self.message = "Completed: " + name
                self.report(self.message)
                if name == "connect":
                    self.queue_day(date.today())
            except Exception as exc:
                # Never expose exception text: upstream/browser errors may contain secrets.
                if mfp_client.is_auth_error(exc):
                    self.reconnect = True
                    self.message = "Reconnect to MyFitnessPal to resume synchronization."
                elif isinstance(exc, AccountMismatch):
                    self.message = "Different account detected. Disconnect before changing accounts."
                elif isinstance(exc, IncompleteSync):
                    self.message = "Sync incomplete: nutrition totals or targets are missing, or the fetch failed. Archived data is retained."
                else:
                    self.message = "Operation failed. Check connection status and try again; archived data is retained."
                self.report(self.message)
            finally:
                self.pending.discard(name)
                self.busy = None
                self.queue.task_done()

    async def scheduler(self):
        interval = max(60, int(os.environ.get("MFP_BACKGROUND_SYNC_SECONDS", "900")))
        while True:
            if auth.load_cookies() and not self.reconnect and not self.browser_session:
                self.queue_day(date.today())
                self.queue_day(date.today() - timedelta(days=1))
            if self.browser_session and time.monotonic() >= self.browser_deadline:
                self.browser_session = None
            await asyncio.sleep(interval)

    def begin(self):
        result = browser_client.call("start")
        self.browser_session = result["session_id"]
        self.browser_deadline = time.monotonic() + 900

    def cancel(self):
        if self.browser_session:
            try:
                browser_client.call("cancel", session_id=self.browser_session)
            finally:
                self.browser_session = None

    def validate(self, cookies, username=None):
        if not cookies.get(auth.SESSION_COOKIE):
            raise mfp_client.AuthenticationError("No session cookie")
        client = mfp_client.build_client(cookies, username=username)
        try:
            actual = client.effective_username
            principal = str(client.user_id or "")
            if not principal:
                raise auth.CredentialError("MyFitnessPal did not identify the account")
            fingerprint = hashlib.sha256(principal.encode()).hexdigest()
            identity = config.account_data_dir(actual) / "principal.sha256"
            if identity.exists() and identity.read_text().strip() != fingerprint:
                raise AccountMismatch()
            if getattr(client, "profile_lookup_fallback", False) and not identity.exists():
                # A user-supplied username is not proof that an old archive belongs
                # to the authenticated upstream principal.
                if config.database_path(actual).exists() or not username:
                    raise AccountMismatch()
            previous = auth.saved_username()
            if previous and auth.load_cookies() and config.normalize_username(previous) != config.normalize_username(actual):
                raise AccountMismatch()
            # Check archive binding before replacing any credentials.
            with closing(Store(config.database_path(actual), account_id=config.account_key(actual))):
                pass
            auth._atomic_write(identity, fingerprint + "\n")
            return actual
        finally:
            client.session.close()

    def finish(self, username=None, cookies=None):
        self.report("Validating MyFitnessPal session and account identity.")
        interactive = cookies is None
        if interactive:
            cookies = browser_client.call("harvest", session_id=self.browser_session)["cookies"]
        actual = self.validate(cookies, username)
        # Persist credentials first. A failed profile promotion cannot invalidate
        # the already validated session; the next reconnect can repair refresh.
        auth.save_cookies(cookies, username=actual)
        self.report("Validated session saved. Preparing synchronization.")
        mfp_client.reset()
        self.reconnect = False
        if interactive:
            try:
                browser_client.call("commit", session_id=self.browser_session)
            finally:
                self.browser_session = None
        elif self.browser_session:
            try:
                self.cancel()
            except Exception:
                self.report("Saved session is valid; old browser cleanup failed, but its local lease is closed.")

    def disconnect(self):
        # Local credentials are removed even if the browser is currently offline.
        saved = auth._read_saved()
        if saved:
            # Keep the archive identity for offline reads, but no usable cookies.
            saved.pop("cookies", None)
            import json
            auth._atomic_write(config.cookies_path(), json.dumps(saved) + "\n")
        mfp_client.reset()
        self.reconnect = False
        self.browser_session = None
        browser_client.call("forget")

    def sync_day(self, day, force=False):
        if not auth.load_cookies():
            raise mfp_client.NotConnectedError("Reconnect required")
        # Validate before synchronization; NutritionService can deliberately
        # suppress auth errors when it has stale data to return.
        try:
            self.report("Checking saved MyFitnessPal session.")
            mfp_client.get_client()
            self._sync(day, force=force)
        except Exception as exc:
            if not mfp_client.is_auth_error(exc):
                raise
            try:
                self.report("Authentication failed. Trying saved-browser session refresh.")
                cookies = browser_client.call("refresh")["cookies"]
            except Exception as refresh_error:
                raise mfp_client.AuthenticationError("Reconnect required") from refresh_error
            actual = self.validate(cookies)
            auth.save_cookies(cookies, username=actual)
            mfp_client.reset()
            self._sync(day, force=force)

    def _sync(self, day, force=False):
        service = create_service(progress=self.report)
        service.propagate_auth_errors = True
        try:
            results = service.sync_range(day, day, force=force)
            nutrition = service.store.nutrition(day.isoformat()) or {}
            totals, goals = nutrition.get("nutrients", {}), nutrition.get("goals", {})
            required = ("calories", "protein", "carbohydrates", "fat")
            if any(result.source == "stale_cache" for result in results) or any(
                values.get(key, values.get("carbs") if key == "carbohydrates" else None) is None
                for values in (totals, goals) for key in required
            ):
                raise IncompleteSync()
            self.report("Nutrition totals and targets are present in the archive.")
            if any(result.warnings for result in results):
                self.report("Nutrition is available; optional data could not all be refreshed.")
        finally:
            service.store.close()

    def queue_day(self, day, force=False):
        reason = None
        if not auth.load_cookies():
            reason = "Sync not queued: no saved session. Connect first."
        elif self.reconnect:
            reason = "Sync not queued: reconnect required. The last authentication attempt failed."
        elif self.status()["browser_open"]:
            reason = "Sync not queued: finish or cancel the active browser login first."
        if reason:
            self.message = reason
            self.report(reason)
            return False
        return self.enqueue("sync " + day.isoformat(), self.sync_day, day, force)

    def archived_day(self, day: date):
        username = auth.saved_username()
        if not username:
            return {"status": "not_connected", "day": day.isoformat()}
        with closing(Store(config.database_path(username), account_id=config.account_key(username))) as store:
            stamp = store.retrieved_at(day.isoformat())
            stale = FreshnessPolicy.from_config().requires_refresh(
                day, stamp, today=date.today(), now=datetime.now(timezone.utc)
            )
            return {
                "status": "cached" if stamp else "not_cached",
                "data": store.day_record(day.isoformat()),
                "retrieved_at": stamp.isoformat() if stamp else None,
                "stale": stale,
            }


class AccountMismatch(RuntimeError):
    pass


class IncompleteSync(RuntimeError):
    pass

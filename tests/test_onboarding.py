import asyncio
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from myfitnesspal_mcp import auth, browser_client, config, mfp_client
from myfitnesspal_mcp.onboarding import AccountMismatch, Onboarding
from myfitnesspal_mcp.store import Store


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    monkeypatch.delenv("MFP_USERNAME", raising=False)
    monkeypatch.delenv("MFP_DATABASE_PATH", raising=False)
    monkeypatch.setenv("MFP_MCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "cookies_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(config, "_restrict_owned_directory", lambda p: None)
    monkeypatch.setattr(auth, "_restrict_windows", lambda p: None)
    result = Onboarding()
    yield result
    result.executor.shutdown(wait=True)
    mfp_client.reset()


def fake_validation(monkeypatch, username="tester"):
    client = SimpleNamespace(effective_username=username, user_id="id-" + username,
                             session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(mfp_client, "build_client", lambda *a, **kw: client)


def test_reject_environment_cookie(monkeypatch):
    monkeypatch.setenv("MFP_COOKIE", "synthetic")
    with pytest.raises(ValueError, match="Unset"):
        Onboarding()


def test_activity_is_bounded_and_rejection_explained(manager):
    assert not manager.queue_day(date.today())
    assert "no saved session" in manager.status()["activity"][-1]["message"]
    for _ in range(110):
        manager.report("Synthetic progress")
    assert len(manager.status()["activity"]) == 100


def test_manual_refresh_forwards_force(manager):
    auth.save_cookies({auth.SESSION_COOKIE: "synthetic"}, "tester")
    assert manager.queue_day(date.today(), force=True)
    name, fn, args = manager.queue.get_nowait()
    assert args == (date.today(), True)


def test_paste_closes_old_browser(manager, monkeypatch):
    fake_validation(monkeypatch)
    calls = []
    monkeypatch.setattr(browser_client, "call", lambda operation, **kw: calls.append(operation))
    manager.browser_session = "synthetic-lease"
    manager.finish(cookies={auth.SESSION_COOKIE: "synthetic"})
    assert manager.browser_session is None
    assert calls == ["cancel"]


def test_missing_nutrition_is_not_success(manager, monkeypatch):
    from myfitnesspal_mcp import onboarding
    observed = {}
    def sync(*args, **kwargs):
        observed.update(kwargs)
        return [SimpleNamespace(source="upstream", warnings=[])]
    service = SimpleNamespace(sync_range=sync, store=SimpleNamespace(
        nutrition=lambda day: {"nutrients": {}, "goals": {}}, close=lambda: None))
    monkeypatch.setattr(onboarding, "create_service", lambda **kw: service)
    with pytest.raises(onboarding.IncompleteSync):
        manager._sync(date.today(), force=True)
    assert observed == {"force": True}


def test_bad_login_preserves_credentials(manager, monkeypatch):
    auth.save_cookies({auth.SESSION_COOKIE: "old"}, "tester")
    monkeypatch.setattr(mfp_client, "build_client", lambda *a, **kw: (_ for _ in ()).throw(
        mfp_client.AuthenticationError("synthetic failure")))
    with pytest.raises(mfp_client.AuthenticationError):
        manager.finish(cookies={auth.SESSION_COOKIE: "new"})
    assert auth.load_cookies()[auth.SESSION_COOKIE] == "old"


def test_wrong_account_preserves_credentials(manager, monkeypatch):
    auth.save_cookies({auth.SESSION_COOKIE: "old"}, "tester")
    fake_validation(monkeypatch, "other")
    with pytest.raises(AccountMismatch):
        manager.finish(cookies={auth.SESSION_COOKIE: "new"})
    assert auth.saved_username() == "tester"
    assert auth.load_cookies()[auth.SESSION_COOKIE] == "old"


def test_finish_validates_then_promotes(manager, monkeypatch):
    fake_validation(monkeypatch)
    calls = []
    def browser(operation, **kwargs):
        calls.append(operation)
        if operation == "harvest":
            return {"cookies": {auth.SESSION_COOKIE: "synthetic"}}
        assert auth.load_cookies()[auth.SESSION_COOKIE] == "synthetic"
        return {"ok": True}
    monkeypatch.setattr(browser_client, "call", browser)
    manager.browser_session = "lease"
    manager.finish()
    assert calls == ["harvest", "commit"]
    assert manager.browser_session is None


def test_disconnect_retains_archive_and_allows_new_account(manager, monkeypatch):
    auth.save_cookies({auth.SESSION_COOKIE: "old"}, "tester")
    with StoreContext("tester") as store:
        store.upsert_nutrition(date.today().isoformat(), calories=100)
    monkeypatch.setattr(browser_client, "call", lambda *a, **kw: {})
    manager.disconnect()
    assert not auth.load_cookies()
    assert manager.archived_day(date.today())["data"]["nutrition"]["calories"] == 100
    fake_validation(monkeypatch, "other")
    manager.finish(cookies={auth.SESSION_COOKIE: "new"})
    assert auth.saved_username() == "other"
    with StoreContext("tester") as store:
        assert store.nutrition(date.today().isoformat())["calories"] == 100


class StoreContext:
    def __init__(self, username):
        self.store = Store(config.database_path(username), account_id=config.account_key(username))
    def __enter__(self):
        return self.store
    def __exit__(self, *args):
        self.store.close()


def test_cached_read_does_not_contact_upstream(manager, monkeypatch):
    auth.save_cookies({auth.SESSION_COOKIE: "old"}, "tester")
    monkeypatch.setattr(mfp_client, "get_client", lambda: pytest.fail("upstream accessed"))
    with StoreContext("tester") as store:
        store.upsert_nutrition(date.today().isoformat(), calories=100)
        store.mark_component(date.today().isoformat(), "nutrition_diary", complete=True,
                             fetched_at=datetime.now(timezone.utc))
    result = manager.archived_day(date.today())
    assert result["status"] == "cached"
    assert result["retrieved_at"]


def test_queue_deduplicates_and_serializes(manager):
    async def check():
        observed = []
        assert manager.enqueue("one", observed.append, 1)
        assert manager.enqueue("one", observed.append, 2)
        assert manager.enqueue("two", observed.append, 3)
        worker = asyncio.create_task(manager.worker())
        await manager.queue.join()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert observed == [1, 3]
    asyncio.run(check())


def test_worker_redacts_exception_text(manager):
    def fail():
        raise RuntimeError("secret-cookie-do-not-disclose")
    async def check():
        manager.enqueue("test", fail)
        worker = asyncio.create_task(manager.worker())
        await manager.queue.join()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert "secret-cookie" not in str(manager.status())
    asyncio.run(check())


def test_sync_is_not_queued_during_interactive_login(manager):
    auth.save_cookies({auth.SESSION_COOKIE: "old"}, "tester")
    manager.browser_session = "lease"
    manager.browser_deadline = time.monotonic() + 60
    assert manager.queue_day(date.today()) is False
    assert not manager.pending


def test_same_username_different_principal_is_rejected(manager, monkeypatch):
    fake_validation(monkeypatch)
    manager.finish(cookies={auth.SESSION_COOKIE: "old"})
    other = SimpleNamespace(effective_username="tester", user_id="different-principal",
                            session=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(mfp_client, "build_client", lambda *a, **kw: other)
    with pytest.raises(AccountMismatch):
        manager.finish(cookies={auth.SESSION_COOKIE: "new"})
    assert auth.load_cookies()[auth.SESSION_COOKIE] == "old"


def test_failed_refresh_marks_reconnect_required(manager, monkeypatch):
    auth.save_cookies({auth.SESSION_COOKIE: "old"}, "tester")
    def fail_client():
        raise mfp_client.AuthenticationError("expired")
    def fail_browser(*args, **kwargs):
        raise RuntimeError("browser unavailable")
    monkeypatch.setattr(mfp_client, "get_client", fail_client)
    monkeypatch.setattr(browser_client, "call", fail_browser)
    async def check():
        manager.queue_day(date.today())
        worker = asyncio.create_task(manager.worker())
        await manager.queue.join()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert manager.reconnect
        assert not manager.queue_day(date.today())
    asyncio.run(check())

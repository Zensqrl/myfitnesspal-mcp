import time
from datetime import date
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from myfitnesspal_mcp.web import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    token = tmp_path / "gateway"
    token.write_text("a" * 64)
    monkeypatch.setenv("MFP_GATEWAY_TOKEN_FILE", str(token))
    monkeypatch.setenv("MFP_PUBLIC_ORIGIN", "https://mfp.test:8443")
    manager = Mock()
    async def noop():
        pass
    manager.start = manager.stop = noop
    manager.status.return_value = {"browser_open": False}
    manager.archived_day.return_value = {"status": "not_cached", "stale": True}
    manager.archive_view.return_value = {"found": False, "day": "2026-01-01", "data": None, "sync": None}
    manager.queue_historical.return_value = True
    manager.cancel_historical.return_value = True
    manager.pending = set()
    with TestClient(create_app(manager)) as result:
        result.manager = manager
        yield result


AUTH = {"X-MFP-Gateway": "a" * 64}
POST = {**AUTH, "Origin": "https://mfp.test:8443", "X-MFP-Request": "1"}


def test_gateway_required(client):
    assert client.get("/api/status").status_code == 403
    assert client.get("/api/status", headers=AUTH).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_csrf_and_body_limits(client):
    assert client.post("/api/start", headers=AUTH, json={}).status_code == 403
    assert client.post("/api/start", headers={**POST, "Origin": "https://evil.test"}, json={}).status_code == 403
    assert client.post("/api/start", headers=POST, json={"x": "x" * 40000}).status_code == 413
    assert client.post("/api/start", headers=POST, json={}).status_code == 202


def test_browser_authorization_requires_lease_and_same_origin(client):
    assert client.get("/api/browser/authorize", headers=AUTH).status_code == 403
    client.manager.status.return_value = {"browser_open": True}
    assert client.get("/api/browser/authorize", headers=AUTH).status_code == 204
    assert client.get("/api/browser/authorize", headers={**AUTH, "Origin": "https://evil.test"}).status_code == 403


def test_disconnect_needs_confirmation(client):
    assert client.post("/api/disconnect", headers=POST, json={}).status_code == 400
    assert client.post("/api/disconnect", headers=POST, json={"confirm": True}).status_code == 202


def test_mcp_lists_only_archive_tools_without_login(client):
    headers = {**AUTH, "Host": "app:8484", "Accept": "application/json, text/event-stream"}
    response = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
    })
    assert response.status_code == 200
    assert "fitness_get_day" in response.text
    assert "fitness_log_food" not in response.text


def test_mcp_cached_read_queues_refresh_promptly(client):
    headers = {**AUTH, "Host": "app:8484", "Accept": "application/json, text/event-stream"}
    client.manager.queue_day.return_value = True
    started = time.monotonic()
    response = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "fitness_get_day", "arguments": {"day": "2025-01-01"}}
    })
    assert response.status_code == 200
    assert 'refresh_queued' in response.text
    assert time.monotonic() - started < 2


def test_historical_utility_validation_and_inclusive_arguments(client):
    response = client.post("/api/sync-range", headers=POST, json={
        "start": "2026-01-02", "end": "2026-01-01", "force": False
    })
    assert response.status_code == 400
    response = client.post("/api/sync-range", headers=POST, json={
        "start": "2026-01-01", "end": "2026-01-02", "force": True
    })
    assert response.status_code == 202
    args, kwargs = client.manager.queue_historical.call_args
    assert args == (date(2026, 1, 1), date(2026, 1, 2))
    assert kwargs == {"force": True, "kind": "range"}


def test_backfill_ends_today_and_cancel_is_explicit(client):
    response = client.post("/api/backfill", headers=POST, json={
        "start": "2026-01-01", "force": False
    })
    assert response.status_code == 202
    args, kwargs = client.manager.queue_historical.call_args
    assert args[0] == date(2026, 1, 1)
    assert args[1] == date.today()
    assert kwargs["kind"] == "backfill"
    assert client.post("/api/cancel-utility", headers=POST, json={}).status_code == 202


def test_archive_day_is_read_only_and_validated(client):
    assert client.get("/api/archive/day?day=not-a-date", headers=AUTH).status_code == 400
    response = client.get("/api/archive/day?day=2026-01-01", headers=AUTH)
    assert response.status_code == 200
    client.manager.archive_view.assert_called_once_with(date(2026, 1, 1))

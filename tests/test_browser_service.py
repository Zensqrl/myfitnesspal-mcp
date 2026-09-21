import time

import pytest
from starlette.testclient import TestClient

from myfitnesspal_mcp import auth
from myfitnesspal_mcp.browser_service import BrowserOwner, create_app


class Context:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True
    def cookies(self, url):
        return [{"name": auth.SESSION_COOKIE, "value": "synthetic"}]


@pytest.fixture
def owner(tmp_path, monkeypatch):
    result = BrowserOwner(tmp_path)
    def launch(path, *, headless):
        path.mkdir(exist_ok=True)
        (path / "test-profile").write_text("synthetic profile")
        result.context = Context()
    monkeypatch.setattr(result, "_launch", launch)
    return result


def test_cancel_retains_active_profile(owner):
    (owner.root / "active").mkdir()
    (owner.root / "active" / "old").write_text("old")
    owner.start()
    owner.cancel()
    assert (owner.root / "active" / "old").exists()
    assert not (owner.root / "candidate").exists()


def test_expired_session_is_closed(owner):
    lease = owner.start()["session_id"]
    context = owner.context
    owner.deadline = time.monotonic() - 1
    with pytest.raises(ValueError):
        owner.harvest(lease)
    assert context.closed
    assert not owner.session_id


def test_wrong_session_cannot_harvest(owner):
    owner.start()
    with pytest.raises(ValueError):
        owner.harvest("wrong")


def test_refresh_cannot_open_interactive_profile(owner):
    owner.start()
    with pytest.raises(ValueError, match="Interactive"):
        owner.refresh()


def test_commit_promotes_only_accepted_candidate(owner):
    lease = owner.start()["session_id"]
    assert owner.harvest(lease)["cookies"][auth.SESSION_COOKIE] == "synthetic"
    owner.commit(lease)
    assert (owner.root / "active" / "test-profile").exists()
    assert not owner.session_id


def test_browser_requires_internal_secret(owner, monkeypatch):
    monkeypatch.setattr("myfitnesspal_mcp.browser_service.token", lambda: "synthetic-secret")
    with TestClient(create_app(owner)) as client:
        assert client.post("/start", json={}).status_code == 401
        response = client.post("/start", json={}, headers={"Authorization": "Bearer synthetic-secret"})
        assert response.status_code == 200
        assert "session_id" in response.json()

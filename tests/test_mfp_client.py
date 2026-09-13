from myfitnesspal_mcp import mfp_client


def test_cookies_to_jar_scopes_to_myfitnesspal():
    jar = mfp_client.cookies_to_jar({"a": "1", "b": "2"})
    cookies = list(jar)
    assert {c.name for c in cookies} == {"a", "b"}
    assert all(c.domain == ".myfitnesspal.com" and c.secure for c in cookies)


def test_username_override_used_when_profile_fails(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, jar, username=None, impersonate=None):
            self._username_override = username
            captured["impersonate"] = impersonate

        def _get_user_metadata(self):
            return mfp_client.CurlCffiClient._get_user_metadata(self)

        def _get_auth_data(self):
            return {}

    def boom(self):
        raise RuntimeError("status 500")

    monkeypatch.setattr(mfp_client.myfitnesspal.Client, "_get_user_metadata", boom)

    fake = FakeClient(None, username="injected-name", impersonate="chrome124")
    assert fake._get_user_metadata() == {"username": "injected-name"}
    assert captured["impersonate"] == "chrome124"


def test_is_auth_error_uses_typed_or_structured_signals():
    assert mfp_client.is_auth_error(mfp_client.AuthenticationError("expired"))
    exc = RuntimeError("request failed")
    exc.status_code = 401
    assert mfp_client.is_auth_error(exc)
    assert not mfp_client.is_auth_error(RuntimeError("401 Unauthorized"))
    assert not mfp_client.is_auth_error(RuntimeError("no food found"))


def test_reset_closes_cached_session(monkeypatch):
    closed = []
    fake = type("Client", (), {"session": type("Session", (), {"close": lambda self: closed.append(True)})()})()
    monkeypatch.setattr(mfp_client, "_client", fake)
    mfp_client.reset()
    assert closed == [True]
    assert mfp_client._client is None

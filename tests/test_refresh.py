import threading
import time

import pytest

from myfitnesspal_mcp import auth, mfp_client, refresh


def _profile_paths(tmp_path, monkeypatch):
    account = tmp_path / "account"
    monkeypatch.setattr(refresh.auth, "saved_username", lambda: "tester")
    monkeypatch.setattr(refresh.config, "account_data_dir", lambda username: account)
    monkeypatch.setattr(
        refresh.config,
        "owned_directory",
        lambda path: (path.mkdir(parents=True, exist_ok=True) or path),
    )
    return account / "browser-profile"


def test_profile_seeded_does_not_create_profile(tmp_path, monkeypatch):
    profile = _profile_paths(tmp_path, monkeypatch)
    assert not refresh.profile_seeded()
    assert not profile.exists()


def test_profile_paths_are_separate_for_different_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("MFP_MCP_DATA_DIR", str(tmp_path))
    first = refresh.profile_dir("account-a", create=True)
    second = refresh.profile_dir("account-b", create=True)
    assert first != second
    assert first.is_dir() and second.is_dir()
    assert first.parent != second.parent


def test_seed_profile_persists_harvested_cookies_and_marker(tmp_path, monkeypatch):
    profile = _profile_paths(tmp_path, monkeypatch)
    harvested = {auth.SESSION_COOKIE: "rotated", "other": "cookie"}
    saved = []
    resets = []
    monkeypatch.setattr(refresh, "_visit_and_harvest", lambda cookies, username: harvested)
    monkeypatch.setattr(refresh.auth, "save_cookies", lambda cookies, username=None: saved.append((cookies, username)))
    monkeypatch.setattr(refresh.mfp_client, "reset", lambda: resets.append(True))

    refresh.seed_profile({auth.SESSION_COOKIE: "old"}, username="tester")

    assert saved == [(harvested, "tester")]
    assert resets == [True]
    assert (profile / ".seeded").is_file()


def test_failed_seed_does_not_persist_or_mark_profile(tmp_path, monkeypatch):
    profile = _profile_paths(tmp_path, monkeypatch)
    saved = []
    monkeypatch.setattr(refresh, "_visit_and_harvest", lambda cookies, username: {})
    monkeypatch.setattr(refresh.auth, "save_cookies", lambda *args, **kwargs: saved.append(True))

    with pytest.raises(mfp_client.AuthenticationError):
        refresh.seed_profile({auth.SESSION_COOKIE: "old"}, username="tester")

    assert saved == []
    assert not (profile / ".seeded").exists()


def test_seed_profile_calls_are_serialized(tmp_path, monkeypatch):
    _profile_paths(tmp_path, monkeypatch)
    active = 0
    peak = 0
    guard = threading.Lock()

    def visit(cookies, username):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with guard:
            active -= 1
        return {auth.SESSION_COOKIE: "rotated"}

    monkeypatch.setattr(refresh, "_visit_and_harvest", visit)
    monkeypatch.setattr(refresh.auth, "save_cookies", lambda *args, **kwargs: None)
    monkeypatch.setattr(refresh.mfp_client, "reset", lambda: None)
    threads = [
        threading.Thread(
            target=refresh.seed_profile,
            args=({auth.SESSION_COOKIE: "old"}, "tester"),
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1


def test_seed_cookies_pin_host_prefixed_cookies_to_the_host():
    params = refresh.seed_cookie_params(
        {"__Secure-next-auth.session-token": "tok", "__Host-next-auth.csrf-token": "c"}
    )
    assert params[0]["domain"] == ".myfitnesspal.com"
    assert params[1] == {
        "name": "__Host-next-auth.csrf-token",
        "value": "c",
        "url": refresh.MFP_URL,
    }


def test_host_cookie_seeding_uses_selected_account_profile(tmp_path, monkeypatch):
    import playwright.sync_api

    profiles = []
    seeded = []

    class FakePage:
        def goto(self, *args, **kwargs):
            pass

        def wait_for_timeout(self, _milliseconds):
            pass

    class FakeContext:
        pages = [FakePage()]

        def add_cookies(self, params):
            seeded.extend(params)

        def cookies(self, _url):
            return [{"name": auth.SESSION_COOKIE, "value": "rotated"}]

        def close(self):
            pass

    class FakeBrowser:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def launch_persistent_context(self, path, *, headless):
            profiles.append((path, headless))
            return FakeContext()

    class FakePlaywright:
        chromium = FakeBrowser()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(
        refresh, "profile_dir", lambda username, *, create: tmp_path / username
    )

    harvested = refresh._visit_and_harvest(
        {auth.SESSION_COOKIE: "token", "__Host-next-auth.csrf-token": "csrf"},
        username="account-a",
    )

    assert profiles == [(str(tmp_path / "account-a"), True)]
    assert seeded[0]["domain"] == ".myfitnesspal.com"
    assert seeded[1] == {
        "name": "__Host-next-auth.csrf-token",
        "value": "csrf",
        "url": refresh.MFP_URL,
    }
    assert harvested == {auth.SESSION_COOKIE: "rotated"}

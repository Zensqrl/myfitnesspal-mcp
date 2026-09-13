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

import pytest

from myfitnesspal_mcp import config


def test_account_key_is_normalized_and_does_not_expose_username():
    key = config.account_key("  ExampleUser ")
    assert key == config.account_key("exampleuser")
    assert "example" not in key
    assert len(key) == 64


@pytest.mark.parametrize("value", ["0", "-1", "3651", "nope"])
def test_sync_days_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("MFP_SYNC_DAYS", value)
    with pytest.raises(ValueError, match="MFP_SYNC_DAYS"):
        config.sync_days()


@pytest.mark.parametrize("value", ["0", "-1", "301", "nope"])
def test_request_timeout_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("MFP_REQUEST_TIMEOUT", value)
    with pytest.raises(ValueError, match="MFP_REQUEST_TIMEOUT"):
        config.request_timeout()


def test_database_paths_are_account_specific(tmp_path, monkeypatch):
    monkeypatch.setenv("MFP_MCP_DATA_DIR", str(tmp_path))
    alice = config.database_path("Alice")
    bob = config.database_path("Bob")
    assert alice != bob
    assert alice.name == bob.name == "data.db"
    assert config.legacy_database_path() == tmp_path / "data.db"


def test_explicit_database_path_overrides_account_directory(tmp_path, monkeypatch):
    path = tmp_path / "archive" / "mfp.sqlite3"
    monkeypatch.setenv("MFP_DATABASE_PATH", str(path))
    assert config.database_path("Alice") == path
    assert path.parent.is_dir()


@pytest.mark.parametrize("name,value", [
    ("MFP_RATE_LIMIT_REQUESTS_PER_MINUTE", "-1"),
    ("MFP_RATE_LIMIT_JITTER_SECONDS", "-1"),
    ("MFP_MUTABLE_HISTORY_DAYS", "0"),
])
def test_new_configuration_rejects_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    functions = {
        "MFP_RATE_LIMIT_REQUESTS_PER_MINUTE": config.rate_limit_requests_per_minute,
        "MFP_RATE_LIMIT_JITTER_SECONDS": config.rate_limit_jitter_seconds,
        "MFP_MUTABLE_HISTORY_DAYS": config.mutable_history_days,
    }
    with pytest.raises(ValueError, match=name):
        functions[name]()

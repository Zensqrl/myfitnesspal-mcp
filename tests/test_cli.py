from pathlib import Path

from myfitnesspal_mcp import cli, config
from myfitnesspal_mcp.store import Store


def _paths(monkeypatch, source: Path, destination: Path) -> None:
    monkeypatch.setattr(config, "legacy_database_path", lambda: source)
    monkeypatch.setattr(config, "database_path", lambda username: destination)


def test_migrate_cache_copies_binds_and_preserves_source(tmp_path, monkeypatch):
    source = tmp_path / "legacy # cache.db"
    destination = tmp_path / "account" / "data.db"
    destination.parent.mkdir()
    legacy = Store(source)
    legacy.set_feel("2026-09-09", "fine", 4)
    legacy.close()
    original = source.read_bytes()
    _paths(monkeypatch, source, destination)

    assert cli.migrate_cache(" ExampleUser ") == 0

    assert source.read_bytes() == original
    migrated = Store(destination)
    assert migrated.feel("2026-09-09")["rating"] == 4
    assert migrated.account_id() == config.account_key("exampleuser")
    migrated.close()


def test_migrate_cache_refuses_existing_destination(tmp_path, monkeypatch):
    source = tmp_path / "legacy.db"
    source.write_bytes(b"source")
    destination = tmp_path / "account.db"
    destination.write_bytes(b"do-not-replace")
    _paths(monkeypatch, source, destination)

    assert cli.migrate_cache("somebody") == 1
    assert destination.read_bytes() == b"do-not-replace"


def test_migrate_cache_race_does_not_remove_other_destination(tmp_path, monkeypatch):
    source = tmp_path / "legacy.db"
    source.write_bytes(b"source")
    destination = tmp_path / "account.db"
    destination.write_bytes(b"won-by-other-process")
    _paths(monkeypatch, source, destination)
    real_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: False if path == destination else real_exists(path),
    )

    assert cli.migrate_cache("somebody") == 1
    assert destination.read_bytes() == b"won-by-other-process"


def test_migrate_cache_failure_removes_only_created_destination(
    tmp_path, monkeypatch
):
    source = tmp_path / "legacy.db"
    legacy = Store(source)
    legacy.close()
    destination = tmp_path / "account.db"
    _paths(monkeypatch, source, destination)

    class BrokenStore:
        def __init__(self, *args, **kwargs):
            raise ValueError("binding failed")

    monkeypatch.setattr("myfitnesspal_mcp.store.Store", BrokenStore)
    assert cli.migrate_cache("somebody") == 1
    assert source.exists()
    assert not destination.exists()


def test_sync_and_backfill_commands_parse():
    parser = cli._build_parser()
    sync = parser.parse_args(["sync", "range", "2024-06-01", "2024-06-30", "--force"])
    assert sync.command == "sync"
    assert sync.sync_command == "range"
    assert sync.force is True
    backfill = parser.parse_args(["backfill", "2020-01-01", "2024-12-31"])
    assert backfill.command == "backfill"
    assert backfill.start.isoformat() == "2020-01-01"

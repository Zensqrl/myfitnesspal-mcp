import argparse
from contextlib import closing
import logging
import sqlite3
import sys


def migrate_cache(username: str) -> int:
    from . import config
    from .store import Store

    source = config.legacy_database_path()
    destination = config.database_path(username)
    if not source.is_file():
        print("No legacy cache was found; nothing was changed.", file=sys.stderr)
        return 1
    if destination.exists():
        print("The account cache already exists; nothing was changed.", file=sys.stderr)
        return 1
    created = False
    try:
        # Reserve the destination atomically, then use SQLite's backup API so
        # a concurrently active legacy database is copied consistently.
        with destination.open("xb"):
            pass
        created = True
        source_uri = source.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as old:
            with closing(sqlite3.connect(destination)) as new:
                old.backup(new)
                new.commit()
        with closing(
            Store(
                destination,
                account_id=config.account_key(username),
                migrate_legacy=True,
            )
        ):
            pass
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        print("Could not migrate the legacy cache; the original was left unchanged.", file=sys.stderr)
        return 1
    print("Legacy cache copied to the selected account. The original was left unchanged.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="myfitnesspal-mcp",
        description="MCP server for MyFitnessPal (stdio by default).",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "auth", "migrate-cache"],
        default="serve",
        help="serve (default) or auth to connect your MyFitnessPal account",
    )
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8484, help="HTTP port (default 8484)")
    parser.add_argument("--username", help="MyFitnessPal username for migrate-cache")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    if args.command == "auth":
        from .auth import run_auth_flow

        raise SystemExit(run_auth_flow())

    if args.command == "migrate-cache":
        if not args.username or not args.username.strip():
            parser.error("migrate-cache requires --username NAME")
        raise SystemExit(migrate_cache(args.username))

    from .server import mcp

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()

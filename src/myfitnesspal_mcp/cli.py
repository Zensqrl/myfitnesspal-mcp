import argparse
from contextlib import closing
from datetime import date
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
        with destination.open("xb"):
            pass
        created = True
        source_uri = source.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as old:
            with closing(sqlite3.connect(destination)) as new:
                old.backup(new)
                new.commit()
        with closing(Store(destination, account_id=config.account_key(username), migrate_legacy=True)):
            pass
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        print("Could not migrate the legacy cache; the original was left unchanged.", file=sys.stderr)
        return 1
    print("Legacy cache copied to the selected account. The original was left unchanged.")
    return 0


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _add_force(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", help="bypass local freshness rules")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myfitnesspal-mcp",
        description="MyFitnessPal MCP server and local nutrition archive tools.",
    )
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8484, help="HTTP port (default 8484)")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the MCP server")
    commands.add_parser("auth", help="connect a MyFitnessPal account")
    migrate = commands.add_parser("migrate-cache", help="copy a legacy cache into an account archive")
    migrate.add_argument("--username", required=True, help="MyFitnessPal username")

    sync = commands.add_parser("sync", help="synchronize the local archive")
    sync_commands = sync.add_subparsers(dest="sync_command", required=True)
    today = sync_commands.add_parser("today", help="synchronize today")
    _add_force(today)
    recent = sync_commands.add_parser("recent", help="synchronize a recent range")
    recent.add_argument("--days", type=int, help="number of calendar days")
    _add_force(recent)
    one_day = sync_commands.add_parser("date", help="synchronize one date")
    one_day.add_argument("date", type=_date)
    _add_force(one_day)
    range_parser = sync_commands.add_parser("range", help="synchronize an inclusive range")
    range_parser.add_argument("start", type=_date)
    range_parser.add_argument("end", type=_date)
    _add_force(range_parser)

    backfill = commands.add_parser("backfill", help="resumable historical synchronization")
    backfill.add_argument("start", type=_date)
    backfill.add_argument("end", type=_date)
    _add_force(backfill)
    return parser


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _run_sync(args) -> int:
    from .runtime import create_service

    service = create_service(progress=_print_progress)
    try:
        if args.sync_command == "today":
            results = service.sync_range(date.today(), date.today(), force=args.force)
        elif args.sync_command == "recent":
            results = service.sync_recent(args.days, force=args.force)
        elif args.sync_command == "date":
            results = service.sync_range(args.date, args.date, force=args.force)
        else:
            results = service.sync_range(args.start, args.end, force=args.force)
        warnings = [warning for result in results for warning in result.warnings]
        _print_warnings(warnings)
        print(f"sync complete: {sum(result.refreshed for result in results)} refreshed, "
              f"{sum(not result.refreshed for result in results)} cached")
        return 1 if warnings else 0
    except Exception as exc:
        logging.getLogger(__name__).error("sync failed: %s", type(exc).__name__)
        return 1
    finally:
        service.store.close()


def _run_backfill(args) -> int:
    from .runtime import create_service

    service = create_service(progress=_print_progress)
    try:
        result = service.backfill(args.start, args.end, force=args.force)
        _print_warnings(result.warnings)
        print(f"backfill complete: {result.refreshed} refreshed, {result.skipped} skipped, "
              f"{result.failed} failed")
        return 1 if result.failed else 0
    finally:
        service.store.close()


def main() -> None:
    from . import config

    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, config.log_level()), stream=sys.stderr)

    if args.command == "auth":
        from .auth import run_auth_flow
        raise SystemExit(run_auth_flow())
    if args.command == "migrate-cache":
        raise SystemExit(migrate_cache(args.username))
    if args.command == "sync":
        raise SystemExit(_run_sync(args))
    if args.command == "backfill":
        raise SystemExit(_run_backfill(args))

    from .server import mcp
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()

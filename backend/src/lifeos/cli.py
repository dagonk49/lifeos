"""Server-side administration (bootstrap, recovery, data checks). Requires a shell on the
server."""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from lifeos import integrity
from lifeos.config import get_settings
from lifeos.core.errors import ProblemError
from lifeos.db.session import Database
from lifeos.modules.identity import service as identity
from lifeos.modules.identity.service import IssuedToken


def _print_token(origin: str, issued: IssuedToken) -> None:
    # The token travels in the URL fragment: never sent to servers or logged by proxies.
    print(f"Enrollment link (valid {issued.expires_in_minutes} min, single use):")
    print(f"  {origin.rstrip('/')}/enroll#token={issued.token}")


async def _create_owner(email: str, name: str, timezone: str | None) -> int:
    settings = get_settings()
    db = Database(settings.database_url, pool_size=1)
    try:
        async with db.sessionmaker() as session, session.begin():
            _, issued = await identity.create_owner(
                session, settings, email=email, display_name=name, timezone=timezone
            )
        print("Owner account created with spaces 'Personnel' and 'Professionnel'.")
        _print_token(settings.public_origin, issued)
        return 0
    finally:
        await db.dispose()


async def _enrollment_token(email: str) -> int:
    settings = get_settings()
    db = Database(settings.database_url, pool_size=1)
    try:
        async with db.sessionmaker() as session, session.begin():
            user = await identity.get_user_by_email(session, email)
            if user is None:
                print("No user with this e-mail.", file=sys.stderr)
                return 1
            issued = await identity.issue_enrollment_token(
                session, settings, user=user, purpose="recovery"
            )
        _print_token(settings.public_origin, issued)
        return 0
    finally:
        await db.dispose()


async def _revoke_sessions(email: str) -> int:
    settings = get_settings()
    db = Database(settings.database_url, pool_size=1)
    try:
        async with db.sessionmaker() as session, session.begin():
            user = await identity.get_user_by_email(session, email)
            if user is None:
                print("No user with this e-mail.", file=sys.stderr)
                return 1
            count = await identity.revoke_all_sessions(session, user_id=user.id, reason="cli")
        print(f"{count} session(s) revoked.")
        return 0
    finally:
        await db.dispose()


async def _verify_data(
    *,
    documents_dir: Path | None,
    archived: set[str] | None,
    files_as_of: datetime | None,
    max_decrypt: int | None,
    alembic_ini: Path,
    require_head: bool,
    as_json: bool,
) -> int:
    settings = get_settings()
    db = Database(settings.database_url, pool_size=1)
    try:
        async with db.sessionmaker() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            report = await integrity.verify(
                session,
                settings,
                documents_dir=documents_dir,
                archived=archived,
                files_as_of=files_as_of,
                max_decrypt=max_decrypt,
                alembic_ini=alembic_ini,
                require_head=require_head,
            )
    except (OSError, DBAPIError) as exc:
        print(f"Error: database unreachable ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        await db.dispose()
    print(integrity.report_json(report) if as_json else report.to_text())
    return report.exit_code


async def _reconcile_originals(documents_dir: Path | None, apply: bool, empty_ok: bool) -> int:
    settings = get_settings()
    root = documents_dir or Path(settings.documents_dir)
    db = Database(settings.database_url, pool_size=1)
    try:
        async with db.sessionmaker() as session, session.begin():
            missing = await integrity.missing_originals(session, root)
            # Not one file: a wrong directory or an unmounted volume, far more likely than
            # a loss.
            empty = not await asyncio.to_thread(integrity.stored_files, root)
            if missing and empty and not empty_ok:
                print(
                    f"Error: no original at all under {root}; nothing changed "
                    "(--even-if-empty if the whole volume is really lost).",
                    file=sys.stderr,
                )
                return 1
            for document in missing:
                print(f"missing original: document {document.id}")
            if not missing:
                print("No missing original.")
                return 0
            if not apply:
                print(
                    f"{len(missing)} missing original(s). Check older backups first "
                    "(docs/restauration.md), then run again with --yes to record them as lost."
                )
                return 2
            count = await integrity.reconcile_originals(session, missing)
        print(f"{count} original(s) recorded as lost (audit: document.original_missing).")
        return 0
    finally:
        await db.dispose()


async def _sensitivity_check(paths: list[Path], expect: Path | None) -> int:
    """Classifies and masks local files as uploads would be; prints no value, no text."""
    from lifeos.modules.documents import corpus
    from lifeos.modules.documents.extraction import build_extractor

    settings = get_settings()
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"Not found: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        manifest = corpus.load_manifest(expect) if expect is not None else {}
    except (OSError, ValueError) as exc:
        print(f"Invalid manifest: {exc}", file=sys.stderr)
        return 1
    extractor = build_extractor(settings)
    checks = []
    for path, name in corpus.files_in(paths):
        check = await corpus.check_file(
            path, name, extractor, max_pages=settings.documents_max_pages
        )
        check.expected = manifest.get(name) if expect is not None else None
        checks.append(check)
        print(corpus.report_line(check))
    read = [c for c in checks if not c.skipped]
    sensitive = [c for c in read if c.categories]
    print(
        f"{len(read)} file(s) read, {len(sensitive)} sensitive, "
        f"{len(checks) - len(read)} skipped. No value or text is shown."
    )
    if expect is None:
        return 0
    unlisted = [c.name for c in read if c.expected is None]
    missed = [c.name for c in read if c.missed]
    extra = [c.name for c in read if c.extra]
    absent = sorted(set(manifest) - {c.name for c in checks})
    if unlisted:
        print(f"Not in the manifest: {', '.join(unlisted)}")
    if absent:
        print(f"In the manifest but not found: {', '.join(absent)}")
    print(f"False negatives: {len(missed)}; false positives: {len(extra)}.")
    return 1 if missed or extra or absent else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifeos-admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-owner", help="create the owner account and an enrollment link")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--timezone", default=None, help="IANA time zone, e.g. Europe/Paris")

    p = sub.add_parser("enrollment-token", help="issue a recovery enrollment link")
    p.add_argument("--email", required=True)

    p = sub.add_parser("revoke-sessions", help="revoke every session of a user")
    p.add_argument("--email", required=True)

    p = sub.add_parser(
        "verify-data",
        help="check the database and the encrypted originals (after a restore); exit status "
        "0 = OK, 2 = missing originals, 1 = unusable",
    )
    files = p.add_mutually_exclusive_group()
    files.add_argument(
        "--documents-dir", type=Path, help="documents directory (default: LIFEOS_DOCUMENTS_DIR)"
    )
    files.add_argument(
        "--files-from",
        metavar="LISTING",
        help="listing of a documents archive (tar -t), '-' for standard input: presence only",
    )
    p.add_argument(
        "--max-decrypt",
        type=int,
        default=None,
        metavar="N",
        help="decrypt at most N originals (default: all; 0: presence only)",
    )
    p.add_argument(
        "--files-as-of",
        type=integrity.parse_instant,
        default=None,
        metavar="TIME",
        help="when the files were archived (Unix time or ISO 8601): documents created later "
        "are reported apart",
    )
    p.add_argument("--alembic-ini", type=Path, default=Path("alembic.ini"), help=argparse.SUPPRESS)
    p.add_argument("--require-head", action="store_true", help="pending migrations are an error")
    p.add_argument("--json", action="store_true", help="one JSON object (for scripts)")

    p = sub.add_parser(
        "reconcile-originals",
        help="record as lost the originals missing from the documents volume (after checks)",
    )
    p.add_argument("--documents-dir", type=Path, default=None)
    p.add_argument("--yes", action="store_true", help="apply (otherwise only list)")
    p.add_argument("--even-if-empty", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser(
        "sensitivity-check",
        help="measure the local sensitivity filter on files (prints no value, no text)",
    )
    p.add_argument("paths", nargs="+", type=Path, help="files or folders, read recursively")
    p.add_argument(
        "--expect",
        type=Path,
        default=None,
        help="JSON manifest (file name -> expected categories): lists the differences",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "create-owner":
            return asyncio.run(_create_owner(args.email, args.name, args.timezone))
        if args.command == "enrollment-token":
            return asyncio.run(_enrollment_token(args.email))
        if args.command == "verify-data":
            archived = None
            if args.files_from is not None:
                listing = (
                    sys.stdin.read()
                    if args.files_from == "-"
                    else Path(args.files_from).read_text(encoding="utf-8")
                )
                archived = integrity.archived_files(listing.splitlines())
            return asyncio.run(
                _verify_data(
                    documents_dir=args.documents_dir,
                    archived=archived,
                    files_as_of=args.files_as_of,
                    max_decrypt=args.max_decrypt,
                    alembic_ini=args.alembic_ini,
                    require_head=args.require_head,
                    as_json=args.json,
                )
            )
        if args.command == "reconcile-originals":
            return asyncio.run(
                _reconcile_originals(args.documents_dir, args.yes, args.even_if_empty)
            )
        if args.command == "sensitivity-check":
            return asyncio.run(_sensitivity_check(args.paths, args.expect))
        return asyncio.run(_revoke_sessions(args.email))
    except ProblemError as exc:
        print(f"Error: {exc.title}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

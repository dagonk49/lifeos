"""Server-side administration (bootstrap and recovery). Requires a shell on the server."""

import argparse
import asyncio
import sys

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

    args = parser.parse_args(argv)
    try:
        if args.command == "create-owner":
            return asyncio.run(_create_owner(args.email, args.name, args.timezone))
        if args.command == "enrollment-token":
            return asyncio.run(_enrollment_token(args.email))
        return asyncio.run(_revoke_sessions(args.email))
    except ProblemError as exc:
        print(f"Error: {exc.title}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

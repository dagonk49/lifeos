"""Complete export of the owner's data (docs/politique-de-donnees.md §2, format described in
docs/conventions-api.md « Export et réversibilité »).

Every table that holds the owner's data is exported, or listed in EXPORT_EXCLUDED with
the reason: tests/test_export.py walks the metadata, so a new table fails the tests until
it is placed in one list or the other. Secrets never leave the server: OMITTED_COLUMNS
names the sealed tokens and the key material, and the encoder refuses raw bytes.

Each table is selected by its owner, found from the schema itself: its `user_id`, or the
owner of the row it belongs to (a memory's revisions, a goal's milestones).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from sqlalchemy import ColumnElement, Table, select

from lifeos import __version__
from lifeos.core.security import utcnow
from lifeos.db import models  # noqa: F401  (every table on Base.metadata)
from lifeos.db.base import Base
from lifeos.modules.guardian.context import Ctx

FORMAT = "lifeos-export"
# 2: one key per table (named after it), exact decimals as strings, every table.
VERSION = 2

# Exported tables, in the order of the document. `users` is the "user" object.
EXPORTED: tuple[str, ...] = (
    "users",
    "spaces",
    "tasks",
    "task_dependencies",
    "journal_entries",
    "facts",
    "fact_pair_dismissals",
    "memories",
    "memory_revisions",
    "memory_facts",
    "conversations",
    "messages",
    "documents",
    "finance_categories",
    "finance_category_rules",
    "transactions",
    "finance_imports",
    "finance_recurring",
    "goals",
    "goal_milestones",
    "goal_checkins",
    "reviews",
    "notifications",
    "notification_preferences",
    "dashboard_preferences",
    "approvals",
    "agent_permissions",
    "connector_accounts",
    "connector_grants",
    "connector_sources",
    "calendar_events",
    "mail_messages",
    "llm_calls",
    "passkey_credentials",
    "user_sessions",
    "audit_events",
)

# Tables of the owner left out on purpose, with the reason (written into the export).
EXPORT_EXCLUDED: dict[str, str] = {
    "embeddings": "dérivées : vecteurs recalculés à partir des textes exportés",
    "idempotency_keys": "transitoires (24 h) : réponses rejouables d'éléments déjà exportés",
    "auth_challenges": "transitoires (minutes) : défis WebAuthn à usage unique",
    "enrollment_tokens": "secrets : empreintes des jetons d'enrôlement à usage unique",
    "oauth_states": "transitoires (minutes) : consentements en cours, vérificateur PKCE scellé",
    "finance_import_rows": (
        "transitoires (24 h) : lignes d'aperçu d'un import de relevé ; les opérations "
        "importées sont exportées"
    ),
    "external_operations": (
        "internes : réservations des écritures chez un fournisseur (identifiants seulement), "
        "purgées avec leur approbation"
    ),
}

# Columns never exported (`*`: in every table), with the reason.
OMITTED_COLUMNS: dict[str, dict[str, str]] = {
    "*": {
        "user_id": "l'export ne contient que les données de son propriétaire",
        "search_vector": "index plein texte dérivé",
    },
    "connector_accounts": {
        "refresh_token_enc": "jeton OAuth scellé",
        "access_token_enc": "jeton OAuth scellé",
    },
    "connector_sources": {
        "page_token": "curseur de synchronisation du fournisseur",
        "lease_until": "verrou de synchronisation",
    },
    "documents": {"storage_key": "emplacement du fichier chiffré sur le serveur"},
    "mail_messages": {"seen_generation": "suivi de la synchronisation"},
    "passkey_credentials": {
        "credential_id": "matériel de clé de l'authentificateur",
        "public_key": "matériel de clé de l'authentificateur",
        "sign_count": "compteur anti-clonage",
    },
    "user_sessions": {"token_hash": "empreinte du cookie de session"},
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def omitted(name: str) -> set[str]:
    return set(OMITTED_COLUMNS["*"]) | set(OMITTED_COLUMNS.get(name, {}))


def owner_filter(target: Table, user_id: uuid.UUID) -> ColumnElement[bool]:
    """Rows of `target` that belong to `user_id`: its own `user_id`, or that of the row
    it belongs to (first foreign key, by column name, towards a table with a `user_id`)."""
    if target.name == "users":
        return target.c.id == user_id
    if "user_id" in target.c:
        return target.c.user_id == user_id
    for fk in sorted(target.foreign_keys, key=lambda f: f.parent.name):
        parent = fk.column.table
        if "user_id" in parent.c:
            return fk.parent.in_(select(fk.column).where(parent.c.user_id == user_id))
    raise ValueError(f"{target.name}: no owner can be found")


async def _rows(ctx: Ctx, name: str) -> list[dict[str, Any]]:
    target = table(name)
    skip = omitted(name)
    columns: list[Any] = [c for c in target.c if c.name not in skip]
    if name == "documents":
        # Whether the encrypted original is still kept (where it lies is not data).
        columns.append(target.c.storage_key.is_not(None).label("original_available"))
    order = [target.c[n] for n in ("created_at", "occurred_at") if n in target.c]
    stmt = (
        select(*columns)
        .where(owner_filter(target, ctx.principal.user_id))
        .order_by(*order, *target.primary_key.columns)
    )
    return [dict(row._mapping) for row in await ctx.db.execute(stmt)]


@dataclass
class Export:
    """The owner's rows, with their Python values (Decimal, datetime...)."""

    exported_at: datetime
    user: dict[str, Any]
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def count(self) -> int:
        return 1 + sum(len(rows) for rows in self.tables.values())

    def document(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": VERSION,
            "app_version": __version__,
            "exported_at": self.exported_at,
            "excluded": {
                "tables": EXPORT_EXCLUDED,
                "columns": OMITTED_COLUMNS,
            },
            "user": self.user,
            **self.tables,
        }

    def to_json(self) -> bytes:
        return json.dumps(plain(self.document()), ensure_ascii=False, indent=2).encode()


async def collect(ctx: Ctx) -> Export:
    """Everything the owner has, read in the request's transaction (one snapshot)."""
    [user] = await _rows(ctx, "users")
    data = Export(exported_at=utcnow(), user=user)
    for name in EXPORTED:
        if name != "users":
            data.tables[name] = await _rows(ctx, name)
    return data


def plain(value: Any) -> Any:
    """JSON values: exact decimals as strings ("12.50"), ISO 8601 dates, UUIDs and
    addresses as strings. Raw bytes are refused: they would be key material."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, uuid.UUID | IPv4Address | IPv6Address):
        return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [plain(v) for v in value]
    raise TypeError(f"{type(value).__name__} values are never exported")

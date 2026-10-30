from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.db.session import Database
from lifeos.modules.guardian.principal import Principal
from lifeos.modules.identity.models import User
from lifeos.modules.llm.base import LLMProvider

if TYPE_CHECKING:
    from lifeos.modules.connectors import Connectors
    from lifeos.modules.retrieval.embedders import Embedder


@dataclass
class Ctx:
    """Everything a service needs to act on behalf of a principal."""

    db: AsyncSession
    database: Database  # for detached writes (audit of refusals, LLM metering)
    settings: Settings
    principal: Principal
    user: User
    llm: LLMProvider
    # Local embedder (None: full-text retrieval only).
    embedder: "Embedder | None" = None
    # Connected-account providers by name (none when connectors are disabled).
    connectors: "Connectors" = field(default_factory=dict)

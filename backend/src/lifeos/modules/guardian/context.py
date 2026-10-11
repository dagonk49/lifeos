from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.db.session import Database
from lifeos.modules.guardian.principal import Principal
from lifeos.modules.identity.models import User
from lifeos.modules.llm.base import LLMProvider


@dataclass
class Ctx:
    """Everything a service needs to act on behalf of a principal."""

    db: AsyncSession
    database: Database  # for detached writes (audit of refusals, LLM metering)
    settings: Settings
    principal: Principal
    user: User
    llm: LLMProvider

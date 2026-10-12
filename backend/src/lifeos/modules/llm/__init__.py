from lifeos.config import Settings
from lifeos.modules.llm.base import LLMProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "gemini":
        from lifeos.modules.llm.gemini import GeminiProvider

        return GeminiProvider(settings)
    if settings.llm_provider == "fake":
        from lifeos.modules.llm.fake import FakeProvider

        return FakeProvider()
    from lifeos.modules.llm.fake import DisabledProvider

    return DisabledProvider()

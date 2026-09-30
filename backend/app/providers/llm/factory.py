from ...config import get_settings
from .base import LLMProvider
from .mock import MockLLMProvider
from .ollama import OllamaLLMProvider, ollama_available


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider.lower() == "ollama" and ollama_available():
        return OllamaLLMProvider()
    return MockLLMProvider()


def active_provider_name() -> str:
    settings = get_settings()
    if settings.llm_provider.lower() == "ollama" and ollama_available():
        return "ollama"
    return "mock"

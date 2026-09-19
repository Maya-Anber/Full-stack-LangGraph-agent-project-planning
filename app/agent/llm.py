"""Provider-neutral chat model factory for the LangGraph nodes."""
from typing import Optional

from flask import current_app

from app.config import ENV_PATH

#: The API key and model variable each supported provider needs.
PROVIDER_KEYS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL"),
}


def is_configured_key(value) -> bool:
    """True when `value` looks like a real key rather than an empty value or the
    `replace-with-your-...` placeholder shipped in .env.example."""
    value = (value or "").strip()
    return bool(value) and not value.startswith("replace-with-your-")


def missing_credentials_message(provider: str, config) -> Optional[str]:
    """Explain why `provider` cannot answer yet, or return None when it is ready.

    Used twice: at startup (a log warning) and by `get_llm()` (the 503 the chat
    widget displays). The message has to stand on its own, because the usual
    cause is a provider/key mismatch in .env rather than a missing file.
    """
    provider = (provider or "").lower()
    if provider not in PROVIDER_KEYS:
        return f"Unsupported LLM_PROVIDER {provider!r}. Choose 'gemini' or 'groq'."

    key_name = PROVIDER_KEYS[provider][0]
    if is_configured_key(config.get(key_name)):
        return None

    hint = ""
    for other_provider, (other_key, _) in PROVIDER_KEYS.items():
        if other_provider != provider and is_configured_key(config.get(other_key)):
            hint = f" {other_key} is set, so LLM_PROVIDER={other_provider} would work."
            break

    return (
        f"{key_name} is not set, but LLM_PROVIDER={provider}. Add {key_name} to {ENV_PATH}"
        f" (see .env.example) and restart the app: configuration is read once, at startup.{hint}"
    )


def get_llm(temperature: float = 0.3):
    """Build the chat model for the configured provider.

    Raises RuntimeError with an actionable message when the selected provider has
    no usable API key; `app/routes/chat.py` turns that into a 503 the widget can
    show. The provider SDK is imported lazily so a missing key never fails with a
    confusing ImportError.
    """
    provider = (current_app.config.get("LLM_PROVIDER") or "").lower()
    problem = missing_credentials_message(provider, current_app.config)
    if problem:
        raise RuntimeError(problem)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=current_app.config["GEMINI_MODEL"],
            google_api_key=current_app.config["GEMINI_API_KEY"],
            temperature=temperature,
            max_output_tokens=1024,
        )

    from langchain_groq import ChatGroq

    return ChatGroq(
        model=current_app.config["GROQ_MODEL"],
        api_key=current_app.config["GROQ_API_KEY"],
        temperature=temperature,
        max_tokens=1024,
    )

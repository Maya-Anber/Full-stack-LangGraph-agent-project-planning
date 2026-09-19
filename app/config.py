import os

from dotenv import dotenv_values, load_dotenv

# Project root, so every path below is absolute no matter where the app is started.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The .env file is pinned to the project root on purpose. `load_dotenv()` without
# a path resolves `.env` against the *current working directory* whenever the
# caller is interactive (`python -c`, IPython/Jupyter, the VS Code Interactive
# Window, `flask shell`). Started from any other directory it silently loads
# nothing, which then looks exactly like "GROQ_API_KEY is not set".
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Real environment variables win over .env (dotenv's default), but the file fills
# in everything that is missing -- including variables that are exported as blank
# strings, which would otherwise shadow the value in .env entirely.
load_dotenv(ENV_PATH, override=False)
_FILE_VALUES = {key: value for key, value in dotenv_values(ENV_PATH).items() if value and value.strip()}


def env(name: str, default: str = "") -> str:
    """Read a configuration value as a stripped string.

    Precedence: non-blank environment variable -> non-blank .env entry -> default.
    Blank values are treated as unset, so a stray `GROQ_API_KEY=` in a shell
    profile or launcher environment cannot hide the key stored in .env.
    """
    for candidate in (os.environ.get(name), _FILE_VALUES.get(name), default):
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


class Config:
    """Central configuration, read from the environment and the project .env."""

    SECRET_KEY = env("FLASK_SECRET_KEY", "dev-secret-key")

    SQLALCHEMY_DATABASE_URI = env(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'novatech.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    LLM_PROVIDER = env("LLM_PROVIDER", "gemini").lower()
    GEMINI_API_KEY = env("GEMINI_API_KEY")
    GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.6-flash")
    GROQ_API_KEY = env("GROQ_API_KEY")
    GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")

    # Kept for backwards compatibility with older .env files.
    ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
    ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

    FB_PAGE_ACCESS_TOKEN = env("FB_PAGE_ACCESS_TOKEN")
    FB_VERIFY_TOKEN = env("FB_VERIFY_TOKEN")

    # How many turns of prior conversation to feed back into the agent
    MAX_HISTORY_MESSAGES = int(env("MAX_HISTORY_MESSAGES", "12"))

    # How many knowledge base chunks to retrieve per query
    RAG_TOP_K = int(env("RAG_TOP_K", "4"))
    RAG_MIN_SCORE = float(env("RAG_MIN_SCORE", "0.05"))
    RAG_EMBEDDING_BACKEND = env("RAG_EMBEDDING_BACKEND", "minilm").lower()
    RAG_EMBEDDING_MODEL = env("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

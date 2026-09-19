"""
Shared pytest fixtures.

Every test runs against a throwaway SQLite file that is seeded exactly like a
fresh install, and no test touches the network or needs an API key:

    app      -> Flask app + app context, on a temp database
    client   -> Flask test client for that app
    scripted -> installs a ScriptedChatModel in place of the real LLM
"""
import pytest

from app import create_app
from app.config import Config
from tests.support import ScriptedChatModel


def build_test_config(db_path):
    """Production config, minus the real database and the real API key."""

    class TestConfig(Config):
        TESTING = True
        SECRET_KEY = "test-secret"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.as_posix()}"

        # A dummy key keeps llm.get_llm() from raising if a test forgets to
        # script the model; every graph test patches the model out entirely.
        ANTHROPIC_API_KEY = "test-key-not-used"
        ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
        LLM_PROVIDER = "gemini"
        GEMINI_API_KEY = "test-key-not-used"
        GEMINI_MODEL = "gemini-2.5-flash"
        RAG_EMBEDDING_BACKEND = "tfidf"

        # Messenger stays off unless a test sets it, so no outbound HTTP happens.
        FB_VERIFY_TOKEN = "test-verify-token"
        FB_PAGE_ACCESS_TOKEN = ""

        RAG_TOP_K = 4
        RAG_MIN_SCORE = 0.05
        MAX_HISTORY_MESSAGES = 12

    return TestConfig


@pytest.fixture()
def app(tmp_path):
    """A Flask app backed by a temp SQLite file, with the seed data loaded."""
    application = create_app(build_test_config(tmp_path / "novatech-test.db"))
    with application.app_context():
        yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def scripted(monkeypatch):
    """
    Install a scripted chat model, e.g.

        model = scripted(classifier=["sales"],
                         sales=[tool_call("add_to_cart", {...}), text_reply("done")])

    Returns the model so the test can assert on what the graph asked it for.
    """

    def _install(**scripts):
        model = ScriptedChatModel(**scripts)
        monkeypatch.setattr("app.agent.nodes.get_llm", lambda temperature=0.3: model)
        return model

    return _install
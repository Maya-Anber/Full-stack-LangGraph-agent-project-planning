"""
Configuration loading: which file is read, and what counts as "set".

Regression tests for the "GROQ_API_KEY is not set. Add it to .env" error that
showed up even though .env contained a key. Two things caused it:

* `load_dotenv()` without a path resolves `.env` against the *current working
  directory* whenever the caller is interactive (`python -c`, IPython, the VS
  Code Interactive Window, `flask shell`), so importing the app from any other
  directory found no file at all;
* python-dotenv never overwrites a variable that already exists in the
  environment -- including one that was exported as a blank string, which then
  shadows the value in .env.

Both are covered here, together with the message the user is shown.
"""
import logging
import os
import subprocess
import sys

import pytest
from dotenv import dotenv_values

from app import create_app
from app.agent.llm import get_llm, is_configured_key, missing_credentials_message
from app.config import BASE_DIR, ENV_PATH, Config, env

# A name that is never in .env, so the precedence rules are tested in isolation
# from whatever the developer has configured locally.
SCRATCH = "NOVATECH_TEST_SETTING"


# ---------------------------------------------------------------------------
# Which file is read
# ---------------------------------------------------------------------------
def test_the_env_file_is_pinned_to_the_project_root():
    assert ENV_PATH == os.path.join(BASE_DIR, ".env")
    assert os.path.isabs(ENV_PATH)


def test_the_env_file_is_found_from_another_working_directory(tmp_path):
    """A subprocess started outside the project must still see .env -- even with
    a blank GROQ_API_KEY in its environment, which used to win over the file."""
    if not os.path.isfile(ENV_PATH):
        pytest.skip(".env is git-ignored; this check needs one on disk")

    expected = (dotenv_values(ENV_PATH).get("GROQ_API_KEY") or "").strip()
    result = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(repr(c.Config.GROQ_API_KEY))"],
        cwd=tmp_path,  # deliberately not the project root
        env={**os.environ, "PYTHONPATH": BASE_DIR, "GROQ_API_KEY": ""},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == repr(expected)


# ---------------------------------------------------------------------------
# What counts as "set"
# ---------------------------------------------------------------------------
def test_a_real_environment_variable_wins_over_the_env_file(monkeypatch):
    monkeypatch.setenv(SCRATCH, "from-shell")
    monkeypatch.setattr("app.config._FILE_VALUES", {SCRATCH: "from-file"})

    assert env(SCRATCH) == "from-shell"


def test_a_blank_environment_variable_does_not_hide_the_env_file(monkeypatch):
    monkeypatch.setenv(SCRATCH, "")
    monkeypatch.setattr("app.config._FILE_VALUES", {SCRATCH: "from-file"})

    assert env(SCRATCH) == "from-file"


def test_missing_values_fall_back_to_the_default(monkeypatch):
    monkeypatch.delenv(SCRATCH, raising=False)
    monkeypatch.setattr("app.config._FILE_VALUES", {})

    assert env(SCRATCH, "fallback") == "fallback"
    assert env(SCRATCH) == ""


def test_values_are_stripped(monkeypatch):
    monkeypatch.setenv(SCRATCH, "  padded  ")

    assert env(SCRATCH) == "padded"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("gsk_something-real", True),
        ("  gsk_something-real  ", True),
        ("", False),
        ("   ", False),
        (None, False),
        ("replace-with-your-groq-key", False),
    ],
)
def test_placeholder_keys_count_as_unconfigured(value, expected):
    assert is_configured_key(value) is expected


# ---------------------------------------------------------------------------
# The message the user sees
# ---------------------------------------------------------------------------
def test_a_missing_provider_key_says_where_to_put_it(app, monkeypatch):
    monkeypatch.setitem(app.config, "LLM_PROVIDER", "groq")
    monkeypatch.setitem(app.config, "GROQ_API_KEY", "")
    monkeypatch.setitem(app.config, "GEMINI_API_KEY", "test-key-not-used")

    with app.app_context(), pytest.raises(RuntimeError) as excinfo:
        get_llm()

    message = str(excinfo.value)
    assert "GROQ_API_KEY is not set" in message
    assert ENV_PATH in message  # the exact file that was read
    assert "LLM_PROVIDER=groq" in message
    assert "GEMINI_API_KEY is set" in message  # the provider/key mismatch hint
    assert "restart" in message


def test_a_configured_provider_reports_no_problem(app):
    assert missing_credentials_message("gemini", app.config) is None


def test_an_unknown_provider_is_reported(app):
    assert "Unsupported LLM_PROVIDER" in missing_credentials_message("openai", app.config)


def test_create_app_warns_at_boot_when_the_provider_has_no_key(tmp_path, caplog):
    class NoKeyConfig(Config):
        TESTING = True
        SECRET_KEY = "test-secret"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(tmp_path / 'no-key.db').as_posix()}"
        LLM_PROVIDER = "groq"
        GROQ_API_KEY = ""
        RAG_EMBEDDING_BACKEND = "tfidf"
        FB_PAGE_ACCESS_TOKEN = ""

    with caplog.at_level(logging.WARNING):
        create_app(NoKeyConfig)

    assert "GROQ_API_KEY is not set" in caplog.text

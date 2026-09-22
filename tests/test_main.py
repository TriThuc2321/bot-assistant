"""`main()` wiring: CLI flags, exit codes, and the limit-aware removal guard."""

from __future__ import annotations

import pytest

import main as main_mod
from bot import config
from bot.sync import RunStats, plan
from bot.zendesk import ScrapeError
from tests.test_sync_plan import article, indexed

ENV_VARS = ("OPENAI_API_KEY", "API_KEY", "MAX_ARTICLES", "DRY_RUN", "VECTOR_STORE_ID")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_dotenv", lambda: None)  # never pick up the repo's real .env
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pipeline(monkeypatch):
    """Stub the network edges; record what reaches fetch/plan/apply."""
    seen = {}

    def fetch(base_url, locale, limit):
        seen["limit"] = limit
        return [article(1), article(2)]

    def fake_plan(docs, files, *, scrape_complete):
        seen["scrape_complete"] = scrape_complete
        return plan(docs, files, scrape_complete=scrape_complete)

    def fake_apply(p, store, dry_run, **kwargs):
        seen["dry_run"] = dry_run
        return RunStats()

    class Store:
        def __init__(self, *args, **kwargs):
            pass

        def list_files(self):
            return [indexed(1), indexed(2), indexed(3)]

    monkeypatch.setattr(main_mod, "fetch_articles", fetch)
    monkeypatch.setattr(main_mod, "build_client", lambda key: object())
    monkeypatch.setattr(main_mod, "resolve_vector_store", lambda *a: "vs_test")
    monkeypatch.setattr(main_mod, "VectorStore", Store)
    monkeypatch.setattr(main_mod, "plan", fake_plan)
    monkeypatch.setattr(main_mod, "apply", fake_apply)
    return seen


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "sk-test")


def test_missing_api_key_exits_1(pipeline):
    assert main_mod.main([]) == 1
    assert "limit" not in pipeline  # failed before scraping


def test_scrape_error_exits_2(monkeypatch, api_key):
    def boom(*args):
        raise ScrapeError("cannot list articles")

    monkeypatch.setattr(main_mod, "fetch_articles", boom)
    assert main_mod.main([]) == 2


def test_default_run_exits_0_and_allows_removals(pipeline, api_key):
    assert main_mod.main([]) == 0
    assert pipeline == {"limit": 0, "scrape_complete": True, "dry_run": False}


def test_dry_run_flag_reaches_apply(pipeline, api_key):
    assert main_mod.main(["--dry-run"]) == 0
    assert pipeline["dry_run"] is True


def test_limit_flag_overrides_env_and_blocks_removals(pipeline, api_key, monkeypatch):
    monkeypatch.setenv("MAX_ARTICLES", "50")
    assert main_mod.main(["--limit", "2"]) == 0
    assert pipeline["limit"] == 2
    assert pipeline["scrape_complete"] is False


def test_negative_limit_is_rejected():
    with pytest.raises(SystemExit):
        main_mod.main(["--limit", "-1"])

"""Store-backed local services refresh profiles and discover their first release."""
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from kge.services.onboard import jobs


@pytest.mark.parametrize("store_url", ["", "   ", "file:///unused-store"])
def test_current_profile_pulls_with_store_url_without_cloud(monkeypatch, store_url):
    monkeypatch.setenv("STORE_URL", store_url)
    monkeypatch.setattr(jobs, "_cloud", lambda: False)
    app = SimpleNamespace(PROFILE=object())
    refreshed, store = object(), object()
    calls = Mock()
    calls.pull_config.return_value = refreshed
    calls.store.return_value = store
    monkeypatch.setattr(jobs, "the_store", calls.store)
    monkeypatch.setattr(jobs, "pull_config", calls.pull_config)
    monkeypatch.setattr(jobs, "pull_work", calls.pull_work)
    result = jobs.current_profile(app)
    if store_url.strip():
        assert result is refreshed
        assert calls.mock_calls == [call.store(), call.pull_config(store, jobs.ROOT),
                                    call.store(), call.pull_work(store, jobs.ROOT, refreshed)]
    else:
        assert result is app.PROFILE
        assert calls.mock_calls == []


@pytest.mark.parametrize("store_url", ["", "   ", "file:///unused-store"])
def test_first_release_follows_with_store_url_without_cloud(monkeypatch, store_url):
    monkeypatch.setenv("STORE_URL", store_url)
    monkeypatch.setattr(jobs, "_cloud", lambda: False)
    released = []
    prof = SimpleNamespace(model_hash=lambda: released[-1] if released else None)
    calls = Mock()
    store = object()
    calls.store.return_value = store
    calls.fetch.side_effect = lambda *args: released.append("ab" * 32)
    app = SimpleNamespace(PROFILE=prof, switch=calls.switch)
    monkeypatch.setattr(jobs, "the_store", calls.store)
    monkeypatch.setattr(jobs, "fetch", calls.fetch)
    jobs.follow_first_release(app)
    if store_url.strip():
        assert calls.mock_calls == [call.store(), call.fetch(store, jobs.ROOT, prof), call.switch()]
    else:
        assert calls.mock_calls == []


def test_first_release_already_present_locally_switches_without_store(monkeypatch):
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.setattr(jobs, "_cloud", lambda: False)
    fetch = Mock()
    monkeypatch.setattr(jobs, "fetch", fetch)
    app = SimpleNamespace(PROFILE=SimpleNamespace(model_hash=lambda: "ab" * 32), switch=Mock())
    jobs.follow_first_release(app)
    fetch.assert_not_called()
    app.switch.assert_called_once_with()

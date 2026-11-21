"""ASK_ORDER and ASK_AI_FALLBACK (#464): their defaults, the fallback off where Ask has no model, and an unknown value
stops start-up naming the setting."""
import pytest

from kge.pipeline.settings import ask_ai_fallback, ask_ai_fallback_setting, ask_order, check_service_auth_settings


@pytest.fixture(autouse=True)
def _unset(monkeypatch):
    for name in ("ASK_ORDER", "ASK_AI_FALLBACK", "K_SERVICE", "PORTAL_SYNC_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_unset_or_blank_gives_the_defaults(monkeypatch):
    assert (ask_order(), ask_ai_fallback_setting(), ask_ai_fallback(True)) == ("rules_first", "on", "on")
    monkeypatch.setenv("ASK_ORDER", "  ")
    monkeypatch.setenv("ASK_AI_FALLBACK", "")
    assert (ask_order(), ask_ai_fallback_setting(), ask_ai_fallback(True)) == ("rules_first", "on", "on")


@pytest.mark.parametrize("value", ["rules_first", "model_first", " Model_First "])
def test_ask_order_takes_its_two_values(monkeypatch, value):
    monkeypatch.setenv("ASK_ORDER", value)
    assert ask_order() == value.strip().lower()


@pytest.mark.parametrize("value", ["on", "off", " OFF "])
def test_ask_ai_fallback_takes_on_or_off_where_ask_has_a_model(monkeypatch, value):
    monkeypatch.setenv("ASK_AI_FALLBACK", value)
    assert ask_ai_fallback_setting() == ask_ai_fallback(True) == value.strip().lower()


@pytest.mark.parametrize("value", [None, "on", "off"])
def test_ask_ai_fallback_is_off_where_ask_has_no_model(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("ASK_AI_FALLBACK", value)
    assert ask_ai_fallback(False) == "off"


@pytest.mark.parametrize("name, value, read", [("ASK_ORDER", "rules-first", ask_order),
                                               ("ASK_ORDER", "model", ask_order),
                                               ("ASK_AI_FALLBACK", "strict", ask_ai_fallback_setting),
                                               ("ASK_AI_FALLBACK", "open", ask_ai_fallback_setting),
                                               ("ASK_AI_FALLBACK", "true", ask_ai_fallback_setting)])
def test_an_unknown_value_is_refused_naming_the_setting_and_stops_start_up(monkeypatch, name, value, read):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=rf"^{name} must be one of .*not {value!r}"):
        read()
    with pytest.raises(ValueError, match=rf"^{name} "):
        check_service_auth_settings()


def test_an_unknown_fallback_is_refused_even_where_ask_has_no_model(monkeypatch):
    monkeypatch.setenv("ASK_AI_FALLBACK", "strict")
    with pytest.raises(ValueError, match=r"^ASK_AI_FALLBACK must be one of on, off, not 'strict'$"):
        ask_ai_fallback(False)

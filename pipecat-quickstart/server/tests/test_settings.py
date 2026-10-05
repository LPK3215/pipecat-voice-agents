"""Config-resolution unit tests: provider switching, disable-thinking payloads, engine parsing."""

import pytest

import settings


# ---------------------------------------------------------------- providers
def test_default_provider_is_modelscope(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MODELSCOPE_MODEL", raising=False)
    cfg = settings.llm_config()
    assert cfg["provider"] == "modelscope"
    assert cfg["base_url"] == "https://api-inference.modelscope.cn/v1"
    assert cfg["model"] == "nex-agi/Nex-N2.5-mini"
    assert cfg["api_key_env"] == "MODELSCOPE_API_KEY"


def test_switch_to_sensenova(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "sensenova")
    monkeypatch.setenv("SENSENOVA_API_KEY", "sk-test")
    monkeypatch.delenv("SENSENOVA_MODEL", raising=False)
    cfg = settings.llm_config()
    assert cfg["provider"] == "sensenova"
    assert cfg["base_url"] == "https://token.sensenova.cn/v1"
    assert cfg["model"] == "sensenova-6.8-flash-lite"
    assert cfg["api_key"] == "sk-test"
    # SenseNova's disable-thinking payload (the only form measured to work)
    assert cfg["thinking_body"] == {"thinking": {"type": "disabled"}}


def test_unknown_provider_falls_back(monkeypatch):
    """A typo in the provider name should not stop the service -- fall back to the default silently."""
    monkeypatch.setenv("LLM_PROVIDER", "no-such-provider")
    assert settings.llm_provider() == "modelscope"


# ---------------------------------------------------------------- disable-thinking payloads
def test_build_llm_extra_none_is_empty(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    # With nothing to inject it must return {}, not an empty extra_body
    assert settings.build_llm_extra(None) == {}


def test_build_llm_extra_passes_thinking_body(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    assert settings.build_llm_extra({"enable_thinking": False}) == {
        "extra_body": {"enable_thinking": False}
    }
    assert settings.build_llm_extra({"thinking": {"type": "disabled"}}) == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }


def test_build_llm_extra_temperature(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0")
    assert settings.build_llm_extra(None) == {"extra_body": {"temperature": 0.0}}


# ---------------------------------------------------------------- engine parsing
@pytest.mark.parametrize(
    "value,expected",
    [("sensevoice", "sensevoice"), ("whisper", "whisper"), ("bogus", "sensevoice"), ("", "sensevoice")],
)
def test_stt_engine_validation(value, expected):
    assert settings.stt_engine(value) == expected


@pytest.mark.parametrize("value,expected", [("piper", "piper"), ("kokoro", "kokoro"), ("x", "piper")])
def test_tts_engine_validation(value, expected):
    assert settings.tts_engine(value) == expected


def test_desc_helpers():
    assert "SenseVoice" in settings.stt_desc("sensevoice")
    assert "Whisper" in settings.stt_desc("whisper")
    assert "Piper" in settings.tts_desc("piper")
    assert "Kokoro" in settings.tts_desc("kokoro")


def test_toggle_sentinels(monkeypatch):
    for off in ("0", "false", "False"):
        assert settings.thinking_disabled(off) is False
        assert settings.tools_enabled(off) is False
    assert settings.thinking_disabled("1") is True
    assert settings.tools_enabled("1") is True

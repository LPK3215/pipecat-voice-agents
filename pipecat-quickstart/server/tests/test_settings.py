"""配置解析层单测：服务商切换、关思考参数、引擎解析。"""

import pytest

import settings


# ---------------------------------------------------------------- 服务商
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
    # 商汤的关思考写法（实测唯一有效）
    assert cfg["thinking_body"] == {"thinking": {"type": "disabled"}}


def test_unknown_provider_falls_back(monkeypatch):
    """服务商拼错不该让服务起不来 —— 静默退回默认。"""
    monkeypatch.setenv("LLM_PROVIDER", "no-such-provider")
    assert settings.llm_provider() == "modelscope"


# ---------------------------------------------------------------- 关思考参数
def test_build_llm_extra_none_is_empty(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    # 不注入任何东西时必须返回 {}，而不是空 extra_body
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


# ---------------------------------------------------------------- 引擎解析
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

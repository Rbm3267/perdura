"""perdura_providers.py -- config-driven worker wiring ("quick connect").

Protocol factories that would otherwise need a real SDK client/key
(anthropic, google-genai, the openai-protocol's QwenWorker, lmstudio-native)
are exercised by monkeypatching perdura's Worker classes with fakes that
record their constructor args -- _build()'s `from perdura import
ClaudeWorker, ...` is a deferred, function-body import, so it re-reads
whatever perdura.ClaudeWorker etc. currently is at call time, the same
monkeypatch.setitem(perdura.WORKER_FACTORIES, ...) pattern already used by
tests/test_escalation_ab.py. Nothing here touches the network or needs an
API key.
"""

import json

import pytest

import perdura
import perdura_providers as providers


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_no_default_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert providers.load_config() == {}


def test_load_config_explicit_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        providers.load_config(str(tmp_path / "nope.json"))


def test_load_config_top_level_workers_must_be_object(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"workers": "not-a-dict"}))
    with pytest.raises(ValueError, match="must be an object"):
        providers.load_config(str(path))


def test_load_config_unknown_protocol_raises(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"workers": {"w": {"protocol": "carrier-pigeon"}}}))
    with pytest.raises(ValueError, match="protocol"):
        providers.load_config(str(path))


def test_load_config_openai_protocol_requires_base_url(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"workers": {"w": {"protocol": "openai"}}}))
    with pytest.raises(ValueError, match="base_url"):
        providers.load_config(str(path))


def test_load_config_valid_file_returns_workers_dict(tmp_path):
    path = tmp_path / "providers.json"
    cfg = {"workers": {"w": {"protocol": "mock"}}}
    path.write_text(json.dumps(cfg))
    assert providers.load_config(str(path)) == cfg["workers"]


def test_load_config_no_workers_key_returns_empty(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({}))
    assert providers.load_config(str(path)) == {}


# ---------------------------------------------------------------------------
# _build / worker_factories -- per-protocol construction
# ---------------------------------------------------------------------------

class _FakeWorker:
    """Records constructor args; stands in for a real SDK-backed Worker."""
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


def test_build_anthropic_protocol_passes_model_to_claude_worker(monkeypatch):
    monkeypatch.setattr(perdura, "ClaudeWorker", _FakeWorker)
    cfg = {"w": {"protocol": "anthropic", "model": "claude-opus-x"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.args == ("claude-opus-x",)
    assert worker.name == "w"


def test_build_anthropic_protocol_defaults_model(monkeypatch):
    monkeypatch.setattr(perdura, "ClaudeWorker", _FakeWorker)
    cfg = {"w": {"protocol": "anthropic"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.args == (perdura.DEFAULT_CLAUDE_MODEL,)


def test_build_google_genai_protocol_passes_model_to_gemini_worker(monkeypatch):
    monkeypatch.setattr(perdura, "GeminiWorker", _FakeWorker)
    cfg = {"w": {"protocol": "google-genai", "model": "gemini-x"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.args == ("gemini-x",)


def test_build_openai_protocol_passes_model_base_url_and_api_key(monkeypatch):
    monkeypatch.setattr(perdura, "QwenWorker", _FakeWorker)
    cfg = {"w": {"protocol": "openai", "model": "deepseek/deepseek-chat",
                 "base_url": "https://openrouter.ai/api/v1",
                 "api_key_env": "OR_KEY"}}
    monkeypatch.setenv("OR_KEY", "secret-tok")
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.args == ("deepseek/deepseek-chat",
                           "https://openrouter.ai/api/v1")
    assert worker.kwargs == {"api_key": "secret-tok"}


def test_build_openai_protocol_defaults_api_key_to_local_placeholder(monkeypatch):
    monkeypatch.setattr(perdura, "QwenWorker", _FakeWorker)
    cfg = {"w": {"protocol": "openai", "base_url": "http://host/v1"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.kwargs == {"api_key": "local"}


def test_build_openai_protocol_missing_env_var_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(perdura, "QwenWorker", _FakeWorker)
    monkeypatch.delenv("UNSET_KEY", raising=False)
    cfg = {"w": {"protocol": "openai", "base_url": "http://host/v1",
                 "api_key_env": "UNSET_KEY"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.kwargs == {"api_key": "local"}


def test_build_lmstudio_native_protocol_passes_model_url_and_key(monkeypatch):
    monkeypatch.setattr(perdura, "LMStudioWorker", _FakeWorker)
    cfg = {"w": {"protocol": "lmstudio-native", "model": "m",
                 "base_url": "http://example:1234", "api_key_env": "LMS_KEY"}}
    monkeypatch.setenv("LMS_KEY", "tok")
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.args == ("m", "http://example:1234")
    assert worker.kwargs == {"api_key": "tok"}


def test_build_lmstudio_native_protocol_defaults_base_url(monkeypatch):
    monkeypatch.setattr(perdura, "LMStudioWorker", _FakeWorker)
    cfg = {"w": {"protocol": "lmstudio-native"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert worker.args == (perdura.DEFAULT_QWEN_MODEL,
                           perdura.DEFAULT_LMSTUDIO_URL)


def test_build_mock_protocol_builds_real_mock_worker():
    cfg = {"w": {"protocol": "mock"}}
    worker = providers.worker_factories(cfg)["w"](None)
    assert isinstance(worker, perdura.MockWorker)


def test_build_sets_config_name_overriding_class_default():
    cfg = {"my-custom-name": {"protocol": "mock"}}
    worker = providers.worker_factories(cfg)["my-custom-name"](None)
    assert worker.name == "my-custom-name"
    assert perdura.MockWorker.name == "mock"   # class default left untouched


def test_worker_factories_does_not_instantiate_until_called(monkeypatch):
    calls = []

    class _CountingFake(_FakeWorker):
        def __init__(self, *a, **kw):
            calls.append((a, kw))
            super().__init__(*a, **kw)

    monkeypatch.setattr(perdura, "ClaudeWorker", _CountingFake)
    cfg = {"w": {"protocol": "anthropic"}}
    factories = providers.worker_factories(cfg)
    assert calls == []          # building the factory dict built nothing
    factories["w"](None)
    assert len(calls) == 1      # only the actual call constructs a worker


def test_worker_factories_keys_match_config_names():
    cfg = {"a": {"protocol": "mock"}, "b": {"protocol": "mock"}}
    assert set(providers.worker_factories(cfg)) == {"a", "b"}


# ---------------------------------------------------------------------------
# cost_tier_overrides
# ---------------------------------------------------------------------------

def test_cost_tier_overrides_extracts_only_set_fields():
    cfg = {"a": {"protocol": "mock", "cost": 0.5, "tier": "frontier"},
          "b": {"protocol": "mock"}}
    costs, tiers = providers.cost_tier_overrides(cfg)
    assert costs == {"a": 0.5}
    assert tiers == {"a": "frontier"}


def test_cost_tier_overrides_empty_when_nothing_set():
    cfg = {"a": {"protocol": "mock"}}
    assert providers.cost_tier_overrides(cfg) == ({}, {})


def test_cost_tier_overrides_coerces_cost_to_float():
    cfg = {"a": {"protocol": "mock", "cost": "2"}}
    costs, _ = providers.cost_tier_overrides(cfg)
    assert costs == {"a": 2.0}

"""
perdura_providers.py — pluggable LLM provider configuration ("quick
connect"): wiring up a new model/provider is a config-file edit, not a
perdura.py code change.

WORKER_FACTORIES (perdura.py) and DEFAULT_COSTS/DEFAULT_TIERS
(perdura_router.py) already shipped as plain, by-name dicts — the
extension point this module formalizes was implicit from day one (the
escalation A/B harness monkeypatches both directly, see
tests/test_escalation_ab.py). This module lets an operator populate them
from a file instead of from Python.

Each config entry names a `protocol` — a wire format perdura already
speaks — plus whatever that protocol needs (model, base_url,
api_key_env, ...). Any OpenAI-compatible vendor (OpenRouter, Together,
Groq, Fireworks, a self-hosted vLLM/Ollama server, ...) is therefore
always just a config entry; only a genuinely new wire protocol needs a
code change.

No config file present (the common case) changes nothing: load_config()
returns {} and perdura.py's built-in worker set is exactly what it
always was.

Example perdura_providers.json:
    {
      "workers": {
        "openrouter-deepseek": {
          "protocol": "openai",
          "model": "deepseek/deepseek-chat",
          "base_url": "https://openrouter.ai/api/v1",
          "api_key_env": "OPENROUTER_API_KEY",
          "cost": 0.5,
          "tier": "frontier"
        }
      }
    }

    python perdura.py run --workers openrouter-deepseek,qwen
    # (auto-discovers perdura_providers.json in the cwd; or pass
    # --provider-config some/other/path.json)
"""

import json
import os

DEFAULT_CONFIG_PATH = "perdura_providers.json"

# The wire formats perdura already knows how to speak. Adding a vendor that
# speaks one of these is a config change; a genuinely new wire format is a
# code change here (one factory branch) plus an entry in this tuple.
PROTOCOLS = ("anthropic", "google-genai", "openai", "lmstudio-native", "mock")

# "openai" has no sensible universal default base_url (unlike the built-in
# "qwen" entry, which defaults to localhost LM Studio) -- every other
# protocol either needs no base_url or already has one.
_REQUIRED_FIELDS = {
    "anthropic": (), "google-genai": (), "openai": ("base_url",),
    "lmstudio-native": (), "mock": (),
}


def load_config(path=None) -> dict:
    """Load `path` (or DEFAULT_CONFIG_PATH if omitted) into {name: cfg}.

    A missing *default* path means "no extra providers configured" — the
    same as today's code-only behavior, not an error. An explicitly named
    path that's missing or malformed IS an error: a typo in
    --provider-config should fail loudly, not silently fall back to the
    built-in workers.
    """
    explicit = path is not None
    path = str(path) if explicit else DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        if explicit:
            raise FileNotFoundError(f"provider config not found: {path}")
        return {}
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            raise ImportError(
                f"{path} is a YAML file; install pyyaml to use YAML "
                f"provider configs (pip install pyyaml), or write JSON instead")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    workers = data.get("workers", {}) if isinstance(data, dict) else None
    if not isinstance(workers, dict):
        raise ValueError(f'{path}: top-level "workers" must be an object')
    for name, cfg in workers.items():
        _validate(path, name, cfg)
    return workers


def _validate(path, name, cfg):
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: worker {name!r} must be an object")
    protocol = cfg.get("protocol")
    if protocol not in PROTOCOLS:
        raise ValueError(
            f"{path}: worker {name!r} needs \"protocol\" to be one of "
            f"{PROTOCOLS}, got {protocol!r}")
    missing = [f for f in _REQUIRED_FIELDS[protocol] if not cfg.get(f)]
    if missing:
        raise ValueError(
            f"{path}: worker {name!r} (protocol {protocol!r}) is missing "
            f"required field(s): {', '.join(missing)}")


def _resolve_api_key(cfg: dict):
    env = cfg.get("api_key_env")
    return os.environ.get(env) if env else None


def _build(name: str, cfg: dict):
    """Construct one Worker instance from a config entry. Perdura's own
    worker classes are imported here, not at module level: perdura.py
    imports this module, so this module importing perdura.py back at
    *module* load time would be a cycle. By the time --workers actually
    selects a config-defined name, perdura is fully loaded, so a deferred
    import here is safe."""
    from perdura import (ClaudeWorker, GeminiWorker, QwenWorker,
                         LMStudioWorker, MockWorker, DEFAULT_CLAUDE_MODEL,
                         DEFAULT_GEMINI_MODEL, DEFAULT_QWEN_MODEL,
                         DEFAULT_LMSTUDIO_URL)
    protocol = cfg["protocol"]
    model = cfg.get("model")
    if protocol == "anthropic":
        worker = ClaudeWorker(model or DEFAULT_CLAUDE_MODEL)
    elif protocol == "google-genai":
        worker = GeminiWorker(model or DEFAULT_GEMINI_MODEL)
    elif protocol == "openai":
        worker = QwenWorker(model or DEFAULT_QWEN_MODEL, cfg["base_url"],
                            api_key=_resolve_api_key(cfg) or "local")
    elif protocol == "lmstudio-native":
        worker = LMStudioWorker(model or DEFAULT_QWEN_MODEL,
                                cfg.get("base_url", DEFAULT_LMSTUDIO_URL),
                                api_key=_resolve_api_key(cfg))
    else:  # "mock" -- the only protocol left after _validate's check
        worker = MockWorker()
    # Config-named, not the protocol class's default .name -- lets two
    # entries share a protocol (e.g. two different "openai" vendors) and
    # still be distinguishable in logs, track records, and the router ledger.
    worker.name = name
    return worker


def worker_factories(config: dict) -> dict:
    """{name: callable(args)->Worker}, same shape as perdura.WORKER_FACTORIES
    -- merge the two (config entries win on a name collision) to extend the
    --workers namespace. Lazy like the built-ins: nothing is instantiated,
    no SDK client opened, no API key required, until a name is actually
    selected via --workers."""
    return {name: (lambda a, n=name, c=cfg: _build(n, c))
           for name, cfg in config.items()}


def cost_tier_overrides(config: dict):
    """({name: cost}, {name: tier}) for entries that set them -- feed into
    perdura_router.registry_from_workers(workers, costs, tiers) so a
    config-defined frontier vendor escalates like one, instead of falling
    into that function's free/local default for any name it doesn't
    recognize."""
    costs, tiers = {}, {}
    for name, cfg in config.items():
        if "cost" in cfg:
            costs[name] = float(cfg["cost"])
        if "tier" in cfg:
            tiers[name] = cfg["tier"]
    return costs, tiers

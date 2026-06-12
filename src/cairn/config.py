# SPDX-License-Identifier: Apache-2.0
"""Shared configuration helpers. Knobs resolved with the precedence
explicit-arg → environment → config file (~/.agentcairn/config.toml) → default.
Home for the KNOBS registry and cairn_env() merge seam."""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_bool(value: str) -> bool:
    """Parse a boolean env/CLI string. Raises ValueError on unrecognized input."""
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"not a boolean: {value!r}")


# ---------------------------------------------------------------------------
# User config file (~/.agentcairn/config.toml): a lower-precedence layer under
# env vars. Keys map MECHANICALLY to env-var names (judge_model ->
# CAIRN_JUDGE_MODEL) so the file schema can never drift from the env surface.
# Precedence everywhere: explicit arg > env var > config file > default.
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path.home() / ".agentcairn" / "config.toml"
_PASSTHROUGH = {"anthropic_api_key": "ANTHROPIC_API_KEY", "ollama_host": "OLLAMA_HOST"}


@dataclass(frozen=True)
class Knob:
    key: str  # config-file key
    env: str  # env-var name
    default: str  # human-readable default (for docs/template)
    description: str
    secret: bool = False


KNOBS: tuple[Knob, ...] = (
    Knob("vault", "CAIRN_VAULT", "~/agentcairn", "Vault directory (the source of truth)."),
    Knob(
        "index",
        "CAIRN_INDEX",
        "~/.cache/agentcairn/index.duckdb",
        "DuckDB index path (rebuildable cache).",
    ),
    Knob(
        "embedder", "CAIRN_EMBEDDER", "fastembed", "Embedding provider: fastembed | ollama | fake."
    ),
    Knob(
        "embed_model",
        "CAIRN_EMBED_MODEL",
        "nomic-ai/nomic-embed-text-v1.5",
        "Embedding model name.",
    ),
    Knob(
        "rerank",
        "CAIRN_RERANK",
        "true",
        "Cross-encoder reranker on recall (biggest quality lever).",
    ),
    Knob("usage", "CAIRN_USAGE", "1", "Token-savings ledger (local, no telemetry)."),
    Knob(
        "usage_path", "CAIRN_USAGE_PATH", "~/.cache/agentcairn/usage.jsonl", "Savings ledger path."
    ),
    Knob(
        "judge",
        "CAIRN_JUDGE",
        "embedding",
        "Memory durability judge: anthropic | embedding | none.",
    ),
    Knob("judge_model", "CAIRN_JUDGE_MODEL", "claude-haiku-4-5", "Model for the LLM judge tier."),
    Knob("judge_timeout", "CAIRN_JUDGE_TIMEOUT", "10", "LLM judge timeout (seconds)."),
    Knob(
        "ollama_host", "OLLAMA_HOST", "http://localhost:11434", "Ollama server (ollama embedder)."
    ),
    Knob(
        "anthropic_api_key", "ANTHROPIC_API_KEY", "", "API key for the LLM judge tier.", secret=True
    ),
)
_KNOWN_KEYS = {k.key for k in KNOBS}

_file_cache: dict[str, str] | None = None
_warned_keys: set[str] = set()


def _reset() -> None:
    """Clear the config-file cache (tests; also after `cairn config --init`)."""
    global _file_cache
    _file_cache = None
    _warned_keys.clear()


def _config_path() -> Path:
    return Path(os.environ.get("CAIRN_CONFIG") or _DEFAULT_CONFIG_PATH).expanduser()


def _translate(key: str) -> str:
    return _PASSTHROUGH.get(key, f"CAIRN_{key.upper()}")


def _coerce(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def config_file_values() -> dict[str, str]:
    """The config file's values, translated to env-var names. Cached per process.
    Missing/malformed/unreadable file -> {} (config must never break a run)."""
    global _file_cache
    if _file_cache is not None:
        return dict(_file_cache)  # copy: callers must not mutate the live cache
    path = _config_path()
    values: dict[str, str] = {}
    try:
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            for key, raw in data.items():
                if key not in _KNOWN_KEYS and key not in _warned_keys:
                    _warned_keys.add(key)
                    print(f"agentcairn: unknown config key {key!r} in {path}", file=sys.stderr)
                values[_translate(key)] = _coerce(raw)
    except Exception as e:  # malformed TOML, unreadable file, ...
        # "\x00file" can't collide with a real config key (unlike plain "file").
        if "\x00file" not in _warned_keys:
            _warned_keys.add("\x00file")
            print(f"agentcairn: ignoring config file {path}: {e}", file=sys.stderr)
        values = {}
    _file_cache = values
    return dict(values)


def cairn_env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    """The unified settings mapping: config-file values overlaid by the (real or
    given) environment. THE seam for every knob read: arg > env > file > default."""
    base = dict(config_file_values())
    base.update(os.environ if env is None else env)
    return base


_DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_DEFAULT_FASTEMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"


def fastembed_model(env: Mapping[str, str] | None = None) -> str:
    """Resolve the FastEmbed model name: CAIRN_EMBED_MODEL or 'nomic-ai/nomic-embed-text-v1.5'.
    (nomic is the default — it wins the LoCoMo embedding sweep; see benchmarks/README.md.
    CAIRN_EMBED_MODEL is shared with the Ollama tier; each provider has its own default.)"""
    if env is None:
        env = cairn_env()
    return env.get("CAIRN_EMBED_MODEL") or _DEFAULT_FASTEMBED_MODEL


def ollama_config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Resolve (model, host) for the Ollama embedder from env, with defaults.
    model ← CAIRN_EMBED_MODEL or 'nomic-embed-text'; host ← OLLAMA_HOST or localhost."""
    if env is None:
        env = cairn_env()
    model = env.get("CAIRN_EMBED_MODEL") or _DEFAULT_OLLAMA_MODEL
    host = env.get("OLLAMA_HOST") or _DEFAULT_OLLAMA_HOST
    return model, host


def resolve_rerank(explicit: bool | None = None, env: Mapping[str, str] | None = None) -> bool:
    """Resolve the reranker on/off setting: explicit arg → CAIRN_RERANK env → True.
    An unparseable CAIRN_RERANK falls back to the default (True) rather than raising,
    so a typo never breaks a query."""
    if explicit is not None:
        return explicit
    if env is None:
        env = cairn_env()
    raw = env.get("CAIRN_RERANK")
    if raw is None:
        return True
    try:
        return parse_bool(raw)
    except ValueError:
        return True


_DEFAULT_JUDGE_MODEL = "claude-haiku-4-5"
_DEFAULT_JUDGE_TIMEOUT = 10.0


def judge_config(env: Mapping[str, str] | None = None) -> tuple[str, str, float]:
    """Resolve (mode, model, timeout) for the Layer-B judge.
    mode ← CAIRN_JUDGE: 'anthropic' | 'embedding' | 'none' (default 'embedding').
    model ← CAIRN_JUDGE_MODEL; timeout ← CAIRN_JUDGE_TIMEOUT seconds."""
    if env is None:
        env = cairn_env()
    mode = (env.get("CAIRN_JUDGE") or "embedding").strip().lower()
    model = env.get("CAIRN_JUDGE_MODEL") or _DEFAULT_JUDGE_MODEL
    try:
        timeout = float(env.get("CAIRN_JUDGE_TIMEOUT") or _DEFAULT_JUDGE_TIMEOUT)
    except ValueError:
        timeout = _DEFAULT_JUDGE_TIMEOUT
    return mode, model, timeout

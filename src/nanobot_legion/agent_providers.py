#!/usr/bin/env python3
"""Provider registry helpers — shared between setup page and agent config.

Extracted from agent_config.py to avoid duplication with setup.py's provider logic.
"""
from __future__ import annotations

import json

# ── provider registry (from official nanobot) ───────────────────────
try:
    from nanobot.providers.registry import PROVIDERS as _NANOBOT_PROVIDERS, find_by_name  # noqa: F401
except ImportError:  # pragma: no cover
    _NANOBOT_PROVIDERS = ()
    def find_by_name(name: str):  # noqa: E302
        return None

# ── UX augmentation ──────────────────────────────────────────────────
_PROVIDER_MODELS: dict[str, list[str]] = {
    "deepseek":    ["deepseek-chat", "deepseek-reasoner"],
    "openai":      ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"],
    "siliconflow": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen3-235B-A22B"],
    "zhipu":       ["glm-4-plus", "glm-4-flash", "glm-4-air"],
    "dashscope":   ["qwen3-235b-a22b", "qwen-max", "qwen-plus"],
    "moonshot":    ["kimi-k2.5", "kimi-k2.6"],
    "gemini":      ["gemini-2.5-flash", "gemini-2.5-pro"],
    "mistral":     ["mistral-large-latest", "mistral-small-latest"],
    "anthropic":   ["claude-sonnet-4-20250514", "claude-haiku-3.5"],
    "volcengine":  ["deepseek-v3-250324", "deepseek-r1-250528"],
    "stepfun":     ["step-3"],
    "minimax":     ["minimax-m1"],
    "qianfan":     ["ernie-4.5-8k", "ernie-speed-8k"],
    "novita":      ["deepseek-r1", "deepseek-v3"],
    "openrouter":  ["openai/gpt-4o-mini"],
    "aihubmix":    ["deepseek-chat"],
    "groq":        ["llama-3.3-70b-versatile"],
    "huggingface": ["Qwen/Qwen3-235B-A22B"],
}

_SKIP_PROVIDERS = frozenset({"bedrock", "azure_openai", "ovms", "nvidia",
                                "openai_codex", "github_copilot",
                                "minimax_anthropic",
                                "volcengine_coding_plan", "byteplus_coding_plan"})


def _get_setup_providers() -> list:
    """Return nanobot ProviderSpec list filtered for the agent config form."""
    result = []
    for spec in _NANOBOT_PROVIDERS:
        if spec.is_oauth or spec.is_local:
            continue
        if spec.name in _SKIP_PROVIDERS:
            continue
        result.append(spec)
    return result


def _build_provider_js_data() -> tuple[str, str]:
    """Generate provider <option> HTML and JS presets object.

    Returns (provider_options_html, presets_js).
    """
    select_lines = ['            <option value="">— 选择服务商 —</option>']
    p_entries = []

    for spec in _get_setup_providers():
        select_lines.append(f'            <option value="{spec.name}">{spec.label}</option>')

        models = _PROVIDER_MODELS.get(spec.name, [])
        base = spec.default_api_base or ""
        p_entries.append(
            f'    {spec.name}:{{base:"{base}",ml:{json.dumps(models)}}}'
        )

    # custom provider
    select_lines.append('            <option value="custom">自定义 (OpenAI 兼容)</option>')
    p_entries.append('    custom:{base:"",ml:[]}')

    options_html = "\n".join(select_lines)
    presets_js = "var __PP = {\n" + ",\n".join(p_entries) + "\n};"

    return options_html, presets_js

#!/usr/bin/env python3
"""patch_message_hardening.py — DeepSeek message format hardening for _sanitize_messages.

Applied during Docker build: injects _hardening_deepseek_messages into
OpenAICompatProvider, plus the call site inside _sanitize_messages.

Fixes (Pitfall → Fix):
  1. "(empty)" placeholder → " "         (model interprets it as literal text)
  2. assistant+tool_calls content absent → enforced pop(content) (some endpoints reject null)
  3. tool/user messages with None content → " " fallback      (DeepSeek requires string)
"""

import re

# ── Target file (at Docker build time this is the installed package) ──
TARGET = "/app/nanobot/providers/openai_compat_provider.py"


def _load_source() -> str:
    """Read the provider source, returning its full text."""
    with open(TARGET, encoding="utf-8") as fh:
        return fh.read()


def _save_source(text: str) -> None:
    """Overwrite the provider source."""
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(text)


# ─────────────────────────────────────────────────────────────
# 1. Inject the _hardening_deepseek_messages static method
# ─────────────────────────────────────────────────────────────

_HARDENING_METHOD = '''
    @staticmethod
    def _hardening_deepseek_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """DeepSeek message format hardening.

        Applies a final pass on sanitized messages to fix two provider-specific
        pitfalls that cause 400 errors or model confusion:

        1. "(empty)" placeholder → " " (single space)
           *_sanitize_empty_content* injects "(empty)" for empty user/tool/system
           content; DeepSeek treats this as literal input, not a marker.

        2. tool / user / system messages with None / "" content → " "
           DeepSeek requires a non-empty string for these roles.

        Assistant messages with tool_calls are left as-is — content=None is
        valid OpenAI API format and we do not drop user-visible text.
        """
        hardened: list[dict[str, Any]] = []
        for msg in messages:
            msg = dict(msg)  # shallow copy

            role = msg.get("role")

            # ── tool messages: ensure non-empty string ──
            if role == "tool":
                content = msg.get("content")
                if content is None or content == "" or content == "(empty)":
                    msg["content"] = " "

            # ── user / system messages ──
            elif role in ("user", "system"):
                content = msg.get("content")
                if content is None or content == "(empty)":
                    msg["content"] = " "
                elif content == "":
                    msg["content"] = " "

            # assistant messages (with or without tool_calls) pass through
            # untouched — upstream _sanitize_messages already sets content=None
            # for assistant+tool_calls, which is accepted by DeepSeek API.
            hardened.append(msg)

        return hardened
'''


def _inject_hardening_method(source: str) -> str:
    """Insert _hardening_deepseek_messages after _sanitize_messages, before '# Build kwargs'."""
    marker = "\n    # ------------------------------------------------------------------\n    # Build kwargs"
    if marker not in source:
        raise RuntimeError("Cannot find '# Build kwargs' marker in openai_compat_provider.py")
    return source.replace(marker, _HARDENING_METHOD + marker)


# ─────────────────────────────────────────────────────────────
# 2. Add the call site inside _sanitize_messages
# ─────────────────────────────────────────────────────────────

# The current return statement in _sanitize_messages:
#         return self._enforce_role_alternation(sanitized)
# We wrap it so the hardening pass runs before role alternation:

_OLD_RETURN = "        return self._enforce_role_alternation(sanitized)\n"
_NEW_RETURN = (
    "        # DeepSeek hardening: fix (empty) placeholders & content format\n"
    "        if force_string_content:\n"
    "            sanitized = self._hardening_deepseek_messages(sanitized)\n"
    "        return self._enforce_role_alternation(sanitized)\n"
)


def _inject_hardening_call(source: str) -> str:
    """Replace the return in _sanitize_messages with the hardened version."""
    if _OLD_RETURN not in source:
        raise RuntimeError(
            "Cannot find the sanitize_messages return statement to patch. "
            "The upstream source may have changed."
        )
    return source.replace(_OLD_RETURN, _NEW_RETURN, 1)


# ─────────────────────────────────────────────────────────────
# Main entry point (called from Dockerfile)
# ─────────────────────────────────────────────────────────────

def apply() -> None:
    src = _load_source()

    # Idempotency guard — if already patched, skip
    if "_hardening_deepseek_messages" in src:
        print("[patch_message_hardening] Already applied — skipping.")
        return

    src = _inject_hardening_method(src)
    src = _inject_hardening_call(src)
    _save_source(src)
    print("[patch_message_hardening] DeepSeek message hardening injected.")


if __name__ == "__main__":
    apply()

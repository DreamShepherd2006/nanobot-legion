#!/usr/bin/env python3
"""
Cross-space / intra-space relay bridge — decoupled from agent internals.

Examples
    python3 /app/squad_bridge_cross.py neo neo dreamshepherd2006-nanobot-staging.hf.space "hi"
    python3 /app/squad_bridge_cross.py sentinel trinity stone2006-nanobot-multi-agent-nightly.ms.show "check pr"

Token 通过 squad_config_loader 统一查询（支持 SQUAD_RELAY_TOKEN env 面板兜底）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error

from nanobot_legion.squad_config_loader import get_relay_peers, get_relay_token, get_relay_token_for


# ── space detection ───────────────────────────────────────────

def _detect_current_space():
    """Return the current space's host domain, or empty str."""
    sid = (os.environ.get("SPACE_ID") or "").lower()
    # HuggingFace
    if sid.endswith("nanobot-staging"):
        return "dreamshepherd2006-nanobot-staging.hf.space"
    if "multi-agent-nightly" in sid:
        return "dreamshepherd2006-nanobot-multi-agent-nightly.hf.space"
    # ModelScope
    if os.environ.get("MODELSCOPE_ENVIRONMENT") == "studio":
        return "stone2006-nanobot-multi-agent-nightly.ms.show"
    return ""


# ── relay ─────────────────────────────────────────────────────

def _relay(sender: str, target: str, domain: str, msg: str):
    token = get_relay_token_for(domain)
    if not token:
        # Fallback: local token for same-space relay
        cur = _detect_current_space()
        if cur and cur == domain:
            token = get_relay_token()
        if not token:
            print(json.dumps({"status": "error", "error": f"no token configured for '{domain}'"}))
            sys.exit(1)

    url = f"https://{domain}/api/squad/relay"
    payload = json.dumps({"sender": sender, "target": target, "message": msg}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "X-Squad-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
        print(json.dumps(body, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(json.dumps({"status": "error", "http": e.code, "body": body[:500]},
                         ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


# ── main ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--list":
        peers = get_relay_peers()
        print(json.dumps(peers, indent=2, ensure_ascii=False))
        return

    if len(sys.argv) < 5:
        print("Usage: python3 squad_bridge_cross.py <sender> <target> <domain> <message>")
        print("       python3 squad_bridge_cross.py --list")
        sys.exit(2)

    sender = sys.argv[1]
    target = sys.argv[2]
    domain = sys.argv[3]
    msg = sys.argv[4]
    _relay(sender, target, domain, msg)


if __name__ == "__main__":
    main()

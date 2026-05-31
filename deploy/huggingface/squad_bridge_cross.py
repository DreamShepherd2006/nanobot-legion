#!/usr/bin/env python3
"""
Squad Bridge Cross-Space v1.0 — 跨空间指挥链中继
=================================================
用 HTTP relay API (POST /api/squad/relay) 在部署空间之间发送消息。
同空间通信请用 squad_bridge.py（WebSocket 直连，更快）。

Usage:
  python3 /app/squad_bridge_cross.py <sender> <target> <space> <message>

Examples:
  # Neo 从 MS Staging → Nightly Neo
  python3 /app/squad_bridge_cross.py neo neo nightly "status check"

  # Assistant 从 Staging → Nightly Trinity
  python3 /app/squad_bridge_cross.py assistant trinity nightly "task done?"

  # 列出可用空间
  python3 /app/squad_bridge_cross.py --list

安全模型:
  • 发送方: token 自动从环境变量注入，agent 不可见 token 值
  • 接收方: 目标空间 gatekeeper 验证 sender 权限（白名单 + USER_AGENT_MAP）
  • 链路: correlation_id 全链路追踪
"""

import sys
import json
import os
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

# ── Space Configuration ──────────────────────────────────────
SPACE_CONFIG = {
    "nightly": {
        "url": "https://dreamshepherd2006-nanobot-multi-agent-nightly.hf.space/api/squad/relay",
        "token_env": "SQUAD_RELAY_TOKEN_HF_NanobotNightly",
        "desc": "HF Nightly (生产)",
    },
    "staging": {
        "url": "https://dreamshepherd2006-nanobot-staging.hf.space/api/squad/relay",
        "token_env": "SQUAD_RELAY_TOKEN_HF_NanobotStaging",
        "desc": "HF Staging (验证)",
    },
    "ms": {
        "url": "https://stone2006-nanobot-multi-agent-nightly.ms.show/api/squad/relay",
        "token_env": "SQUAD_RELAY_TOKEN_MS_NanobotNightly",
        "desc": "ModelScope Staging",
    },
}

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 4]  # seconds
RELAY_TIMEOUT = 120          # seconds (DeepSeek thinking can be slow)

# Permanent errors — not worth retrying
PERMANENT_HTTP_CODES = (400, 401, 403, 404)


# ── Helpers ───────────────────────────────────────────────────

def _make_correlation_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:4]
    return f"xs-{ts}-{short}"


def _detect_current_space() -> str:
    """Try to guess which space this bridge is running in."""
    space_id = os.environ.get("SPACE_ID", "")
    deploy_platform = os.environ.get("DEPLOY_PLATFORM", "")
    ms_env = os.environ.get("MODELSCOPE_ENVIRONMENT", "")

    if ms_env == "studio":
        return "ms"
    if "staging" in space_id.lower():
        return "staging"
    if "nightly" in space_id.lower():
        return "nightly"
    if deploy_platform == "hf-staging":
        return "staging"
    if deploy_platform == "hf-direct":
        return "nightly"
    if deploy_platform == "modelscope":
        return "ms"
    return "unknown"


def _list_spaces():
    current = _detect_current_space()
    print("Available spaces for cross-space relay:\n")
    for name, cfg in SPACE_CONFIG.items():
        marker = " ← 当前" if name == current else ""
        token_status = "✅" if os.environ.get(cfg["token_env"]) else "❌ 无 token"
        print(f"  {name:10s} {cfg['desc']:20s} {token_status}{marker}")
    print(f"\n检测到当前空间: {current}")


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        _list_spaces()
        sys.exit(0)

    if len(sys.argv) < 5:
        print("═" * 56)
        print("  Squad Bridge Cross-Space — 跨空间指挥链")
        print("═" * 56)
        print(f"  Usage: python3 squad_bridge_cross.py <sender> <target> <space> <message>")
        print(f"         python3 squad_bridge_cross.py --list")
        print()
        _list_spaces()
        sys.exit(1)

    sender = sys.argv[1].strip()
    target = sys.argv[2].strip()
    space = sys.argv[3].strip().lower()
    message = " ".join(sys.argv[4:])
    cid = _make_correlation_id()
    current = _detect_current_space()

    # ── Validate space ────────────────────────────────────────
    if space not in SPACE_CONFIG:
        print(f"❌ 未知空间 '{space}'。可用: {', '.join(SPACE_CONFIG.keys())}")
        sys.exit(1)

    space_cfg = SPACE_CONFIG[space]
    relay_url = space_cfg["url"]
    token = os.environ.get(space_cfg["token_env"], "").strip()

    if not token:
        print(f"❌ Token 缺失: {space_cfg['token_env']}")
        print(f"   当前空间: {current}")
        print(f"   可用 RELAY 变量: {[k for k in sorted(os.environ) if 'RELAY' in k]}")
        sys.exit(3)  # exit 3 = permission_denied (same as squad_bridge.py)

    # ── Guard: don't relay to self-space ──────────────────────
    if space == current:
        print(f"⚠️  目标空间 '{space}' 就是当前空间，建议用 squad_bridge.py（WS 直连更快）")
        print(f"   继续用 HTTP relay 也可，但非最优。")
        # Not a hard error — proceed anyway

    # ── Send ──────────────────────────────────────────────────
    print(f"═══ Cross-Space Bridge v1.0 ═══")
    print(f"  cid:      {cid}")
    print(f"  sender:   {sender}  ({current})")
    print(f"  target:   {target}  ({space})")
    print(f"  message:  {message[:120]}{'…' if len(message) > 120 else ''}")

    payload = json.dumps({
        "sender": sender,
        "target": target,
        "message": message,
        "correlation_id": cid,
    }).encode()

    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                relay_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Squad-Token": token,
                },
                method="POST",
            )
            start = time.time()
            with urllib.request.urlopen(req, timeout=RELAY_TIMEOUT) as resp:
                elapsed = time.time() - start
                body = json.loads(resp.read().decode())

            status_code = resp.status if hasattr(resp, 'status') else 200
            status = body.get("status", "")

            if status == "delivered":
                response_text = body.get("target_response", "")
                print(f"\n  ✅ [{attempt}] 送达 ({elapsed:.1f}s)")
                print(f"  📨 {target}@{space}:")
                print(f"  {response_text}")
                sys.exit(0)
            elif status in ("permission_denied", "unauthorized"):
                print(f"\n  🚫 [{attempt}] 权限拒绝"
                      f"\n  {body.get('error', 'unknown')}")
                # Permanent — don't retry
                sys.exit(3)
            elif status in ("roster_miss", "agent_offline", "bad_request"):
                print(f"\n  ⚠️ [{attempt}] {status}: {body.get('error', '')}")
                # Permanent — don't retry
                sys.exit(2)
            else:
                print(f"\n  ⚠️ [{attempt}] 未知状态: {status}")
                print(f"  {json.dumps(body, ensure_ascii=False)[:300]}")
                sys.exit(0)

        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode()[:300]
            except Exception:
                pass
            print(f"\n  ❌ [{attempt}] HTTP {e.code}: {body_text}")
            last_error = f"http_{e.code}"

            if e.code in PERMANENT_HTTP_CODES:
                print(f"  ⛔ 永久性错误，不重试。")
                break

        except urllib.error.URLError as e:
            print(f"\n  ❌ [{attempt}] 连接失败: {e.reason}")
            last_error = f"url:{e.reason}"

        except Exception as e:
            print(f"\n  ❌ [{attempt}] 异常: {e}")
            last_error = str(e)[:100]

        if attempt < MAX_RETRIES:
            delay = RETRY_BACKOFF[attempt - 1]
            print(f"  🔄 {delay}s 后重试…")
            time.sleep(delay)

    print(f"\n💀 跨空间 relay 失败 ({MAX_RETRIES} 次尝试): {last_error}")
    sys.exit(2)


if __name__ == "__main__":
    main()

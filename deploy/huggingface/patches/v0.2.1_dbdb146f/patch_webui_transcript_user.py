#!/usr/bin/env python3
"""
Patch: write user messages to webui transcript at _dispatch_envelope.
========================================================================
In squad deployments, the gatekeeper proxies WebSocket messages to the
agent.  ``_dispatch_envelope`` detects ``webui: true`` and sets
``metadata["webui"] = True``, but the flag may be lost before
``_handle_message`` (which normally writes the user event to transcript).

This patch writes the user message transcript event *immediately* at the
detection point inside ``_dispatch_envelope`` — a belt-and-suspenders fix.

Dual-target: site-packages + /app/nanobot/ (PYTHONPATH priority).
"""
import shutil
from pathlib import Path

TARGETS = [
    "/usr/local/lib/python3.12/site-packages/nanobot/channels/websocket.py",
    "/app/nanobot/channels/websocket.py",
]


def patch_one(target: Path) -> bool:
    if not target.is_file():
        print(f"⏭️  [{target.name}] {target}: not found, skipping")
        return False

    shutil.copy2(str(target), str(target) + ".bak")
    print(f"📦 backup: {target}.bak")

    content = target.read_text()

    old = """            if envelope.get("webui") is True:
                metadata["webui"] = True"""

    new = """            if envelope.get("webui") is True:
                metadata["webui"] = True
                # ── Legion: write user chat to webui transcript ──
                # In squad deployments the gateway proxies WS, and the
                # metadata/webui flag may not survive to _handle_message.
                # Write the user event directly at detection point.
                self._try_append_webui_transcript(
                    cid, {"event": "user", "chat_id": cid, "text": content})"""

    if old not in content:
        print(f"❌ [{target.name}] anchor not found — websocket.py may have changed")
        return False

    content = content.replace(old, new, 1)
    target.write_text(content)
    print(f"✅ [{target.name}] patched: user message transcript write")
    return True


def main() -> None:
    ok = 0
    for t in TARGETS:
        if patch_one(Path(t)):
            ok += 1
    if ok == 0:
        print("❌ [webui_transcript_user] FAILED (no targets patched)")
        raise SystemExit(1)
    print(f"✅ [webui_transcript_user] {ok}/{len(TARGETS)} targets patched")


if __name__ == "__main__":
    main()

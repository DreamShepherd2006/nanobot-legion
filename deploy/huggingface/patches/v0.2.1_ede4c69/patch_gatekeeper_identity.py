#!/usr/bin/env python3
"""
Patch: gatekeeper identity propagation — override sender_id from WS envelope.
==============================================================================
When the gatekeeper WS proxy injects ``sender_id`` and ``sender_name`` into the
message envelope, naively these are ignored by nanobot's ``_dispatch_envelope``.
This patch makes the WebSocket channel read them and:
1. Override ``client_id`` (the WS connection's anonymous ``anon-*`` id) with the
   authenticated ``sender_id`` from the gatekeeper
2. Attach ``sender_name`` to the message metadata

Without this, the LLM only sees ``anon-*`` as the sender and cannot distinguish
real authenticated users behind the gatekeeper proxy.

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

    # Anchor: the _handle_message call inside the "message" envelope branch.
    # This is the only place where sender_id=client_id appears in this context.
    old = """            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata=metadata,
                is_dm=False,
            )"""

    new = """            # ── Legion: gatekeeper identity propagation ──
            # When the gatekeeper injects sender_id/sender_name into the envelope,
            # override the anonymous WS client_id with the authenticated identity.
            envelope_sender_id = envelope.get("sender_id")
            if envelope_sender_id and isinstance(envelope_sender_id, str):
                client_id = envelope_sender_id
            envelope_sender_name = envelope.get("sender_name")
            if envelope_sender_name and isinstance(envelope_sender_name, str):
                metadata["sender_name"] = envelope_sender_name
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata=metadata,
                is_dm=False,
            )"""

    if old not in content:
        print(f"❌ [{target.name}] anchor not found — websocket.py may have changed")
        return False

    content = content.replace(old, new, 1)
    target.write_text(content)
    print(f"✅ [{target.name}] patched: sender_id override from gatekeeper envelope")
    return True


def main() -> None:
    ok = 0
    for t in TARGETS:
        if patch_one(Path(t)):
            ok += 1
    if ok == 0:
        print("❌ [gatekeeper_identity] FAILED (no targets patched)")
        raise SystemExit(1)
    print(f"✅ [gatekeeper_identity] {ok}/{len(TARGETS)} targets patched")


if __name__ == "__main__":
    main()

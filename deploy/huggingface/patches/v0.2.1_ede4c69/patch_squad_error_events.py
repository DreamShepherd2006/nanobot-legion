#!/usr/bin/env python3
"""
Patch: squad error events — emit structured "error" on WebSocket permission denial.
====================================================================================
Without this patch, BaseChannel._handle_message silently discards unallowed messages
(logging only a warning). The WebSocket client (squad_bridge) receives no error event
and must rely on timeout detection. This patch adds a pre-check in
WebSocketChannel._handle_typed_envelope so that permission failures emit a structured
error event before returning, enabling the bridge to distinguish framework denial from
network/timeout failures.

Dual-target: site-packages + /app/nanobot/ (PYTHONPATH priority).
"""
import re
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

    # Locate the permission check insertion point: right before
    # "await self._handle_message(" inside the "if t == "message":" branch
    old = """            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            await self._hydrate_after_subscribe(cid)
            metadata: dict[str, Any] = {"remote": getattr(connection, "remote_address", None)}"""

    new = """            # ── v6: permission pre-check (squad error events) ──
            # BaseChannel._handle_message silently discards unallowed messages
            # (logger.warning only). Emit a structured error event for the
            # WebSocket client so squad_bridge can distinguish framework denial.
            if not self.is_allowed(client_id):
                await self._send_event(
                    connection, "error",
                    detail="access_denied",
                    reason="Sender not in allowFrom list.",
                )
                return

            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            await self._hydrate_after_subscribe(cid)
            metadata: dict[str, Any] = {"remote": getattr(connection, "remote_address", None)}"""

    if old not in content:
        print(f"❌ [{target.name}] anchor text not found — websocket.py may have changed")
        return False

    content = content.replace(old, new, 1)
    target.write_text(content)
    print(f"✅ [{target.name}] patched: permission denial → error event")
    return True


def main() -> None:
    ok = 0
    for t in TARGETS:
        if patch_one(Path(t)):
            ok += 1
    if ok == 0:
        print("❌ [squad_error_events] FAILED (no targets patched)")
        raise SystemExit(1)
    print(f"✅ [squad_error_events] {ok}/{len(TARGETS)} targets patched")


if __name__ == "__main__":
    main()

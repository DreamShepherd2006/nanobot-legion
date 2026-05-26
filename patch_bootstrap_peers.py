#!/usr/bin/env python3
"""Patch: emit peers_update WS event from NANOBOT_PEER_* env vars (v5.0).

Replaces the old bootstrap-response injection with a WebSocket ``peers_update``
event emitted right after the WS handshake completes and hydration finishes.

Rationale (per upstream reviewer chengyongru's feedback on #3891):
  ``/webui/bootstrap`` is a human-facing token-endpoint; service discovery
  belongs on the already-authenticated WebSocket channel, not mixed into
  the per-agent auth path.  This keeps bootstrap clean and single-purpose
  while providing machine-level peer discovery at the correct layer.

Dual-target: patches BOTH site-packages AND /app (PYTHONPATH=/app causes
agents to load from /app/nanobot/ first).
"""
import shutil

TARGETS = [
    "/usr/local/lib/python3.12/site-packages/nanobot/channels/websocket.py",
    "/app/nanobot/channels/websocket.py",
]

READ_PEERS_FN = '''def _read_peers() -> dict | None:
    """Build a peer roster from NANOBOT_PEER_* environment variables.

    Each var should contain a JSON object with at least ``"id"``::

        NANOBOT_PEER_NEO={"id":"abc123","gateway_port":8000,"ws_port":8001}

    Returns a dict ``{name: {id, gateway_port, ws_port}}``, or ``None``
    when no NANOBOT_PEER_* variables are configured.  All values are
    defensively coerced to prevent frontend crashes.
    """
    peers: dict[str, dict[str, object]] = {}
    for key, value in sorted(os.environ.items()):
        if not key.startswith("NANOBOT_PEER_"):
            continue
        try:
            info = json.loads(value)
        except json.JSONDecodeError:
            continue
        if not isinstance(info, dict) or "id" not in info:
            continue
        name = key[len("NANOBOT_PEER_"):].lower()
        peers[name] = {
            "id": str(info["id"]),
            "gateway_port": int(info.get("gateway_port", 0)),
            "ws_port": int(info.get("ws_port", 0)),
        }
    return peers if peers else None
'''

total_patches = 0

for TARGET in TARGETS:
    try:
        with open(TARGET, "r") as f:
            _ = f.read(1)
    except FileNotFoundError:
        print(f"\n⏭️  {TARGET}: not found, skipping")
        continue

    shutil.copy2(TARGET, TARGET + ".bak")
    print(f"\n📦 backup: {TARGET}.bak")

    with open(TARGET, "r") as f:
        source = f.read()

    local_patches = 0

    # ── PATCH_0: import os ────────────────────────────────────────
    anchor_0 = "import json\n"
    if "import os\n" not in source:
        source = source.replace(anchor_0, anchor_0 + "import os\n", 1)
        print(f"  ✅ PATCH_0: +import os")
        local_patches += 1
    else:
        print(f"  ⏭️  PATCH_0 skip: import os already present")

    # ── PATCH_1: inject _read_peers() ─────────────────────────────
    anchor_1 = (
        "    return _default_model_name_from_config()\n"
        "\n"
        "\n"
        "def _parse_request_path"
    )

    if "_read_peers()" not in source:
        if anchor_1 in source:
            replacement = (
                "    return _default_model_name_from_config()\n"
                "\n"
                "\n"
                + READ_PEERS_FN +
                "\n"
                "def _parse_request_path"
            )
            source = source.replace(anchor_1, replacement, 1)
            print(f"  ✅ PATCH_1: _read_peers() inserted")
            local_patches += 1
        else:
            print(f"  ❌ PATCH_1 anchor not found — abort")
            raise SystemExit(1)
    else:
        print(f"  ⏭️  PATCH_1 skip: _read_peers already present")

    # ── PATCH_2: inject peers_update after WS hydration ───────────
    anchor_2 = "            await self._hydrate_after_subscribe(default_chat_id)\n"

    if '"event": "peers_update"' not in source:
        if anchor_2 in source:
            injection = (
                anchor_2 +
                "\n"
                "            # Emit peers_update for multi-agent discovery (Legion/NANOBOT_PEER_*).\n"
                "            peers = _read_peers()\n"
                "            if peers:\n"
                "                await connection.send(\n"
                "                    json.dumps(\n"
                '                        {"event": "peers_update", "peers": peers},\n'
                "                        ensure_ascii=False,\n"
                "                    )\n"
                "                )\n"
            )
            source = source.replace(anchor_2, injection, 1)
            print(f"  ✅ PATCH_2: peers_update WS event injected")
            local_patches += 1
        else:
            print(f"  ❌ PATCH_2 anchor not found — abort")
            # Find _hydrate_after_subscribe to help debug
            idx = source.find("_hydrate_after_subscribe")
            if idx >= 0:
                ctx = source[max(0, idx - 60):idx + 80]
                print(f"     context: {ctx!r}")
            raise SystemExit(1)
    else:
        print(f"  ⏭️  PATCH_2 skip: peers_update already present")

    # ── PATCH_3: REMOVE old bootstrap peers injection ─────────────
    anchor_3 = '                "peers": _read_peers(),\n'
    if anchor_3 in source:
        source = source.replace(anchor_3, "")
        print(f"  ✅ PATCH_3: removed old bootstrap peers")
        local_patches += 1
    else:
        print(f"  ⏭️  PATCH_3 skip: bootstrap peers not present (already clean)")

    with open(TARGET, "w") as f:
        f.write(source)

    print(f"  → {TARGET}: {local_patches} patch(es)")
    total_patches += local_patches

print(f"\n🎉 Done — {total_patches} total patch(es) across {len(TARGETS)} target(s)")

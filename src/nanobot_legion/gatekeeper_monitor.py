"""Legion health monitor with automatic agent resurrection.

Extracted from gatekeeper.py Batch 2 — standalone function taking Gatekeeper instance.
"""

import asyncio
import time

import httpx


async def legion_monitor_loop(gk) -> None:
    """Periodically health-check each agent's gateway_port.

    Triggers auto-resurrection for whitelisted agents via gk._mark_offline()
    after gk.RESURRECT_THRESHOLD seconds of continuous offline time.
    """
    await asyncio.sleep(gk.GRACE_SECONDS)
    gk._grace_ended = True
    gk._log(f"🛡️ 复活引擎就绪 (宽限期 {gk.GRACE_SECONDS}s 结束)")

    while True:
        now = time.time()

        # ── Cooldown expiry ──
        for name in list(gk._resurrecting.keys()):
            if gk._resurrecting[name] and name in gk._offline_since:
                if now - gk._offline_since[name] > gk.RESURRECT_COOLDOWN:
                    gk._log(f"⏰ [{name}] 复活冷却到期，允许重试")
                    gk._resurrecting[name] = False
                    gk._offline_since.pop(name, None)

        for name in gk.agent_names:
            info = gk.squad_roster.get(name)
            if not info:
                continue
            gw_port = info.get("gateway_port")
            if not gw_port:
                continue
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"http://127.0.0.1:{gw_port}/health")
                if resp.status_code == 200:
                    gk._gateway_ever_healthy.add(name)
                    if gk.legion_status.get(name) == "offline":
                        offline_sec = now - gk._offline_since.get(name, 0)
                        gk._log(f"✅ [{name}] 恢复上线 (离线 {offline_sec:.0f}s)")
                    gk.legion_status[name] = "online"
                    gk._offline_since.pop(name, None)
                    if gk._resurrecting.get(name):
                        gk._resurrecting[name] = False
                else:
                    gk._mark_offline(name, f"HTTP {resp.status_code}", now)
            except Exception as e:
                gk._mark_offline(name, str(e), now)

        await asyncio.sleep(10)

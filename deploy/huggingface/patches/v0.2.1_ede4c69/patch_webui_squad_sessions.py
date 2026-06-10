#!/usr/bin/env python3
"""Patch: WebUI — route /api/sessions → /api/squad/sessions for ModelScope.

Problem: ModelScope platform proxy blocks /api/sessions/* (returns MS homepage),
same as how it blocked /login and /auth.  Routing through /api/squad/*
bypasses this interception.

Additionally, MS platform proxy strips the Authorization header, so the
Bearer token never reaches the agent.  Fixed by also appending ?token=
as a query parameter fallback for session API calls.

Target (pre-compile, before hatch_build runs npm):
  - /app/webui/src/lib/api.ts
"""

from pathlib import Path

TARGET = Path("/app/webui/src/lib/api.ts")

if not TARGET.exists():
    print(f"❌ Target not found: {TARGET}")
    exit(1)

original = TARGET.read_text()

# Step 1: Replace path /api/sessions → /api/squad/sessions
patched = original.replace("/api/sessions", "/api/squad/sessions")

# Step 2: Add ?token= query param to fetchSessions
# Pattern after step 1:
#   `${base}/api/squad/sessions`,
#   token,
# Replace with:
#   `${base}/api/squad/sessions?token=${encodeURIComponent(token)}`,
#   token,
patched = patched.replace(
    "`${base}/api/squad/sessions`,\n    token,",
    "`${base}/api/squad/sessions?token=${encodeURIComponent(token)}`,\n    token,"
)

# Step 3: Add ?token= to fetchWebuiThread URL
patched = patched.replace(
    '`${base}/api/squad/sessions/${encodeURIComponent(key)}/webui-thread`',
    '`${base}/api/squad/sessions/${encodeURIComponent(key)}/webui-thread?token=${encodeURIComponent(token)}`'
)

# Step 4: Add ?token= to deleteSession URL
patched = patched.replace(
    '`${base}/api/squad/sessions/${encodeURIComponent(key)}/delete`',
    '`${base}/api/squad/sessions/${encodeURIComponent(key)}/delete?token=${encodeURIComponent(token)}`'
)

# Step 5: Add ?token= to fetchSidebarState URL (ModelScope strips Authorization header)
patched = patched.replace(
    "`${base}/api/webui/sidebar-state`,\n    token,",
    "`${base}/api/webui/sidebar-state?token=${encodeURIComponent(token)}`,\n    token,"
)

# Step 6: Add ?token= to updateSidebarState URL
patched = patched.replace(
    "`${base}/api/webui/sidebar-state/update?${query}`,\n    token,",
    "`${base}/api/webui/sidebar-state/update?${query}&token=${encodeURIComponent(token)}`,\n    token,"
)

if patched == original:
    print("⚠️  No changes — already patched?")
    exit(0)

TARGET.write_text(patched)
print(f"✅ Patched {TARGET}: /api/sessions → /api/squad/sessions + ?token= fallback + sidebar-state")

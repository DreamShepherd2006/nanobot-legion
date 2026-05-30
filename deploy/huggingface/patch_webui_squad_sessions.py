#!/usr/bin/env python3
"""Patch: WebUI — route /api/sessions → /api/squad/sessions for ModelScope.

Problem: ModelScope platform proxy blocks /api/sessions/* (returns MS homepage),
same as how it blocked /login and /auth.  Routing through /api/squad/*
bypasses this interception.

Target (pre-compile, before hatch_build runs npm):
  - /app/webui/src/lib/api.ts → replace "/api/sessions" with "/api/squad/sessions"

Dual-target NOT required: WebUI .ts patches are compiled into dist/,
only the /app/webui/src/ copy matters before the Vite build step.
"""

from pathlib import Path

TARGET = Path("/app/webui/src/lib/api.ts")

if not TARGET.exists():
    print(f"❌ Target not found: {TARGET}")
    exit(1)

original = TARGET.read_text()

patched = original.replace("/api/sessions", "/api/squad/sessions")

if patched == original:
    print("⚠️  No changes — already patched?")
    exit(0)

TARGET.write_text(patched)
print(f"✅ Patched {TARGET}: /api/sessions → /api/squad/sessions")

#!/usr/bin/env python3
"""Patch webui/package.json to add missing @radix-ui dependencies.
Upstream package.json is missing @radix-ui/react-avatar and
@radix-ui/react-scroll-area, but the corresponding .tsx files
still import them, causing tsc build errors.
"""
import json, sys

PKG = "/app/webui/package.json"

with open(PKG) as f:
    p = json.load(f)

added = []
for name, version in [
    ("@radix-ui/react-avatar", "^1.1.11"),
    ("@radix-ui/react-scroll-area", "^1.2.10"),
]:
    if name not in p.get("dependencies", {}):
        p.setdefault("dependencies", {})[name] = version
        added.append(name)

if added:
    with open(PKG, "w") as f:
        json.dump(p, f, indent=2)
        f.write("\n")
    print(f"✅ patched package.json: added {', '.join(added)}")
else:
    print("⏭  package.json already has radix-ui deps — skipping")

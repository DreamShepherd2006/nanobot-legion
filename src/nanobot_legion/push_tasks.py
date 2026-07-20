#!/usr/bin/env python3
"""
push_tasks.py — Push task list to Gatekeeper for WebUI display.

Neo agent tool: Commander issues task updates in natural language →
Neo generates JSON → calls this script to push to Gatekeeper.

Usage:
  1. As argument:  python3 push_tasks.py '<json_payload>'
  2. Via stdin:    echo '<json_payload>' | python3 push_tasks.py

Payload format:
  {
    "goal": "适配 upstream v0.2.0",
    "tasks": [
      {"id": "1", "title": "分析依赖", "agent": "trinity", "status": "done"},
      {"id": "2", "title": "编写patch", "agent": "assistant", "status": "in_progress"}
    ],
    "updated_by": "Commander"
  }

Env:
  SQUAD_RELAY_TOKEN — set in exec.allowed_env_keys for Neo agent
"""

import json
import os
import sys
import urllib.error
import urllib.request

GATEKEEPER_URL = os.environ.get("GATEKEEPER_URL", "http://127.0.0.1:7860")
RELAY_TOKEN = ""  # read from squad_config.json at module load


def _init_token():
    global RELAY_TOKEN
    config_path = os.environ.get("SQUAD_CONFIG_PATH", "/app/squad_config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        RELAY_TOKEN = str(cfg.get("squad_relay_token", "") or "")
    except Exception:
        RELAY_TOKEN = ""


_init_token()


def push(payload: dict) -> dict:
    """POST task payload to Gatekeeper."""
    url = f"{GATEKEEPER_URL}/api/squad/tasks"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Squad-Token", RELAY_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"status": "error", "http_status": e.code, "error": body}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    # Read payload from arg or stdin
    payload_str = None
    if len(sys.argv) > 1:
        payload_str = sys.argv[1]
    elif not sys.stdin.isatty():
        payload_str = sys.stdin.read().strip()

    if not payload_str:
        print("Usage: push_tasks.py '<json>' | echo '<json>' | push_tasks.py")
        sys.exit(1)

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"Invalid JSON: {e}"}))
        sys.exit(1)

    if not isinstance(payload.get("tasks"), list):
        print(json.dumps({"status": "error", "error": "Missing or invalid 'tasks' field"}))
        sys.exit(1)

    result = push(payload)
    print(json.dumps(result, indent=2))
    if result.get("status") == "ok":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

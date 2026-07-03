#!/usr/bin/env python3
"""Extract deploy assets from the nanobot-legion package to /app/."""
import os
from nanobot_legion.deploy import read_asset

TARGET = "/app"
ASSETS = [
    # Platform-specific squad configs
    'squad_config.hf-staging.json',
    'squad_config.hf-nightly.json',
    'squad_config.ms-staging.json',
    # Shell scripts
    'launch.sh',
    'scripts/resurrect_neo.sh',
]


def extract(target=TARGET):
    for name in ASSETS:
        dest = os.path.join(target, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w') as f:
            f.write(read_asset(name))
    for name in ['resurrect_agent.sh', 'resurrect_neo.sh']:
        src = os.path.join(target, 'scripts', name)
        if os.path.exists(src):
            os.chmod(src, 0o755)
    print(f'Assets extracted to {target}/')


if __name__ == '__main__':
    extract()

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
    ('launch.sh', 'deploy/huggingface/launch.sh'),
    'scripts/resurrect_neo.sh',
    ('entrypoint.sh', 'entrypoint.sh'),
]


def extract(target=TARGET):
    for spec in ASSETS:
        if isinstance(spec, tuple):
            src_name, dest_rel = spec
        else:
            src_name = dest_rel = spec
        dest = os.path.join(target, dest_rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w') as f:
            f.write(read_asset(src_name))
        if dest_rel.startswith('scripts/'):
            os.chmod(dest, 0o755)
    print(f'Assets extracted to {target}/')


if __name__ == '__main__':
    extract()

#!/usr/bin/env python3
"""Extract deploy assets from the nanobot-legion package to /app/."""
import os
from nanobot_legion.deploy import read_asset

TARGET = "/app"
os.makedirs(TARGET, exist_ok=True)

# Platform-specific squad configs
for name in [
    'squad_config.hf-staging.json',
    'squad_config.hf-nightly.json',
    'squad_config.ms-staging.json',
]:
    with open(os.path.join(TARGET, name), 'w') as f:
        f.write(read_asset(name))

# Scripts
os.makedirs(os.path.join(TARGET, 'scripts'), exist_ok=True)
with open(os.path.join(TARGET, 'scripts', 'resurrect_neo.sh'), 'w') as f:
    f.write(read_asset('scripts/resurrect_neo.sh'))

print('Assets extracted to /app/')

"""Extract deploy assets from the nanobot-legion package to /app/."""
import os
from nanobot_legion.deploy import read_asset

TARGET = "/app"
ASSETS = [
    # Default squad config (fallback for all spaces)
    'squad_config.json',
    # Platform-specific overrides
    'squad_config.hf-nightly.json',
    # Agent template
    ('_template_config.json', 'instances/_template/config.json'),
    # Shell scripts
    ('launch.sh', 'deploy/huggingface/launch.sh'),
    'scripts/resurrect_neo.sh',
    ('entrypoint.sh', 'entrypoint.sh'),
    # Python scripts
    'squad_bridge_cross.py',
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
        if dest_rel.startswith('scripts/') or dest_rel.endswith('.sh'):
            os.chmod(dest, 0o755)
    print(f'Assets extracted to {target}/')


if __name__ == '__main__':
    extract()

"""nanobot-legion — Squad multi-agent overlay for nanobot cloud deployments.

To keep backward compatibility with cloud-agent-gateway's bare imports
(e.g. ``from squad_config_loader import ...``), a lazy import hook
redirects bare module names to ``nanobot_legion.*``.
Once CAG is updated to use ``nanobot_legion.*`` imports, this shim
can be removed.
"""

import sys as _sys
from importlib.util import find_spec as _find_spec

__version__ = "0.1.0"

# ── Backward-compat shim ──
# cloud-agent-gateway (hf_staging.py, modelscope_squad.py) uses bare
# `from squad_config_loader import ...`.  Rather than updating CAG in
# lockstep, install a finder that redirects bare module names.

_BARE_MAP = {
    "agent_config":        "nanobot_legion.agent_config",
    "gatekeeper":          "nanobot_legion.gatekeeper",
    "squad_bridge":        "nanobot_legion.squad_bridge",
    "squad_bridge_cross":  "nanobot_legion.squad_bridge_cross",
    "squad_config_loader": "nanobot_legion.squad_config_loader",
    "squad_config_sync":   "nanobot_legion.squad_config_sync",
    "push_tasks":          "nanobot_legion.push_tasks",
}


class _LegionFinder:
    """Meta-path finder that redirects bare module names to nanobot_legion."""

    def find_spec(self, fullname, path, target=None):
        if fullname in _BARE_MAP:
            return _find_spec(_BARE_MAP[fullname])
        return None


_sys.meta_path.insert(0, _LegionFinder())

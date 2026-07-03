"""Deploy-time assets (shell scripts, config templates, etc.)."""

from importlib.resources import files as _files


def read_asset(name: str) -> str:
    """Read a deploy asset by name (e.g. 'launch.sh', '_template_config.json')."""
    return (_files(__package__) / name).read_text()


def asset_path(name: str) -> str:
    """Return the absolute filesystem path to a deploy asset."""
    return str(_files(__package__) / name)

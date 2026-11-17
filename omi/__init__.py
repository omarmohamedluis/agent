"""OMI: show-control agents and their central library."""

__version__ = "3.0.0.dev0"


def code_revision() -> str:
    """Immutable source commit embedded by the staged installer before packaging."""
    import json
    from pathlib import Path
    try:
        return json.loads(Path(__file__).with_name("_build.json").read_text(encoding="utf-8"))["commit"]
    except (OSError, ValueError, KeyError):
        return "development"

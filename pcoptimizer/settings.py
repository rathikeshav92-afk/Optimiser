"""Small persistent app settings shared by the app and the background helper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .platform_utils import app_data_dir

DEFAULTS: dict[str, Any] = {
    "screentime": True,      # record screen time (only while the background helper runs)
    "auto_gaming": False,    # switch to Gaming Mode while a profiled game runs
    "auto_care": True,       # check the PC regularly and report problems
    "auto_fix": True,        # let Auto Care fix safe problems by itself
    "show_advanced": False,  # show the expert tools in the sidebar
}


def _path(path: Path | None = None) -> Path:
    return path or app_data_dir() / "settings.json"


def load(path: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **(data if isinstance(data, dict) else {})}


def save(values: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    merged = {**load(path), **values}
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    tmp.replace(p)
    return merged

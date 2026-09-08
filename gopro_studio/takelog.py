"""A single aggregated JSON log of every download ('take'), written to
<output_dir>/takes_log.json - one growing manifest file per output
location, rather than a log per camera.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def log_path(output_dir: Path) -> Path:
    return output_dir / "takes_log.json"


def append_take_record(output_dir: Path, record: dict[str, Any]) -> Path:
    """Append one take's record to the manifest, creating it if needed.

    If the existing file is missing or corrupt, it's backed up (never
    silently discarded) and a fresh log is started so one bad write never
    loses the whole history.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = log_path(output_dir)
    records: list[dict[str, Any]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, list):
                records = loaded
        except (json.JSONDecodeError, OSError):
            backup = path.with_suffix(".json.bak")
            try:
                path.replace(backup)
            except OSError:
                pass
            records = []

    records.append(record)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    return path

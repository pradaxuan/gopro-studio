"""Simple JSON-backed configuration: which cameras belong to the rig,
their friendly labels, output directory, current settings, and saved
settings presets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from gopro_studio.rig import default_settings


class CameraEntry(TypedDict):
    serial: str
    label: str


CONFIG_DIR = Path.home() / ".config" / "gopro_studio"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _defaults() -> dict[str, Any]:
    return {
        "cameras": [],  # list[CameraEntry]
        "output_dir": str(Path.home() / "GoProStudio" / "sessions"),
        "current_settings": default_settings(),  # schema_key -> enum member name
        "presets": {},  # preset_name -> {schema_key: enum member name}
        "quick_presets": [],  # preset names shown as quick-apply buttons in Settings
        "preset_notes": {},  # preset_name -> free-text reminder shown when applied
        "last_actor": "",  # remembered between launches, prefilled in the Actor field
    }


def load() -> dict[str, Any]:
    config = _defaults()
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text())
        config.update(data)
        _migrate_legacy_resolution_fps(config, data)
        _migrate_legacy_favorite_preset(config, data)
    return config


def _migrate_legacy_resolution_fps(config: dict[str, Any], raw: dict[str, Any]) -> None:
    """Older config files stored top-level "resolution"/"fps" strings
    instead of a "current_settings" dict. Fold them in transparently so
    existing installs don't lose their settings on upgrade."""
    if "resolution" in raw or "fps" in raw:
        if "resolution" in raw:
            config["current_settings"]["resolution"] = raw["resolution"]
        if "fps" in raw:
            config["current_settings"]["fps"] = raw["fps"]
        config.pop("resolution", None)
        config.pop("fps", None)


def _migrate_legacy_favorite_preset(config: dict[str, Any], raw: dict[str, Any]) -> None:
    """Older config files had a single "favorite_preset" string instead of
    the "quick_presets" list. Fold it in transparently."""
    if "favorite_preset" in raw:
        name = raw["favorite_preset"]
        if name and name not in config["quick_presets"]:
            config["quick_presets"].append(name)
        config.pop("favorite_preset", None)


def save(config: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def add_camera(config: dict[str, Any], serial: str, label: str) -> None:
    cams: list[CameraEntry] = config["cameras"]
    for c in cams:
        if c["serial"] == serial:
            c["label"] = label
            return
    cams.append({"serial": serial, "label": label})


def remove_camera(config: dict[str, Any], serial: str) -> None:
    config["cameras"] = [c for c in config["cameras"] if c["serial"] != serial]


def sort_cameras_alphabetically(config: dict[str, Any]) -> None:
    config["cameras"].sort(key=lambda c: c["label"].lower())


def move_camera(config: dict[str, Any], serial: str, direction: int) -> None:
    """Move a camera up (direction=-1) or down (direction=+1) in the list.
    No-op if it's already at that end - callers don't need to check bounds
    themselves."""
    cams: list[CameraEntry] = config["cameras"]
    idx = next((i for i, c in enumerate(cams) if c["serial"] == serial), None)
    if idx is None:
        return
    new_idx = idx + direction
    if 0 <= new_idx < len(cams):
        cams[idx], cams[new_idx] = cams[new_idx], cams[idx]


def save_preset(config: dict[str, Any], name: str, settings: dict[str, str]) -> None:
    config.setdefault("presets", {})[name] = dict(settings)


def delete_preset(config: dict[str, Any], name: str) -> None:
    config.get("presets", {}).pop(name, None)
    config.get("preset_notes", {}).pop(name, None)
    if name in config.get("quick_presets", []):
        config["quick_presets"].remove(name)


def list_presets(config: dict[str, Any]) -> list[str]:
    return sorted(config.get("presets", {}).keys())


def add_quick_preset(config: dict[str, Any], name: str) -> None:
    """Show a quick-apply button for this preset at the top of Settings.
    Multiple presets can be pinned at once."""
    quick = config.setdefault("quick_presets", [])
    if name not in quick:
        quick.append(name)


def remove_quick_preset(config: dict[str, Any], name: str) -> None:
    quick = config.get("quick_presets", [])
    if name in quick:
        quick.remove(name)


def set_preset_note(config: dict[str, Any], name: str, note: str) -> None:
    """A free-text reminder shown in the log whenever this preset is
    applied - use it for anything the app can't set for you (e.g. GoPro
    settings with no remote API, like Protune/ISO/white balance/shutter)."""
    if note:
        config.setdefault("preset_notes", {})[name] = note
    else:
        config.get("preset_notes", {}).pop(name, None)


def get_preset_note(config: dict[str, Any], name: str) -> str | None:
    return config.get("preset_notes", {}).get(name)


def find_matching_preset(config: dict[str, Any], settings: dict[str, str]) -> str | None:
    """Which saved preset (if any) exactly matches the given settings dict.
    Used for logging which preset was in effect for a take, when the
    current settings weren't hand-tweaked away from a saved preset."""
    for name, preset_settings in config.get("presets", {}).items():
        if preset_settings == settings:
            return name
    return None

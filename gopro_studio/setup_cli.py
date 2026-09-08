"""Command-line helper for managing which cameras belong to the rig.

Usage:
    gopro-studio-setup discover                  # find connected GoPros over USB
    gopro-studio-setup add <serial> <label>       # add/rename a camera
    gopro-studio-setup remove <serial>            # remove a camera
    gopro-studio-setup list                       # show configured cameras
    gopro-studio-setup output <path>              # set session output directory
    gopro-studio-setup import-presets             # load the bundled default presets
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import sys
from pathlib import Path

from gopro_studio import config as cfg
from gopro_studio.discover import discover_serials_sync
from gopro_studio.rig import SETTINGS_BY_KEY, settings_choices

BUNDLED_PRESETS_RESOURCE = ("gopro_studio", "data/default_presets.json")


def cmd_discover(_args: argparse.Namespace) -> None:
    print("Scanning mDNS for connected GoPros (a few seconds) ...")
    serials = discover_serials_sync()
    if not serials:
        print(
            "No cameras found. Check that:\n"
            "  - each GoPro is powered on and plugged into the USB hub\n"
            "  - `lsusb` shows each camera as a USB device\n"
            "  - `ip addr` shows a usbX/enxX interface per camera with an IP\n"
            "See the README's Linux networking notes if interfaces aren't getting IPs."
        )
        return
    print(f"Found {len(serials)} camera(s):")
    config = cfg.load()
    existing = {c["serial"] for c in config["cameras"]}
    for s in serials:
        marker = "(already configured)" if s in existing else "(new)"
        print(f"  {s} {marker}")
    print("\nAdd new ones with: gopro-studio-setup add <serial> <label>")


def cmd_add(args: argparse.Namespace) -> None:
    config = cfg.load()
    cfg.add_camera(config, args.serial, args.label)
    cfg.save(config)
    print(f"Added/updated {args.serial} -> '{args.label}'. Config: {cfg.CONFIG_FILE}")


def cmd_remove(args: argparse.Namespace) -> None:
    config = cfg.load()
    cfg.remove_camera(config, args.serial)
    cfg.save(config)
    print(f"Removed {args.serial}.")


def cmd_list(_args: argparse.Namespace) -> None:
    config = cfg.load()
    if not config["cameras"]:
        print("No cameras configured yet.")
        return
    for c in config["cameras"]:
        print(f"  {c['label']:<20} {c['serial']}")
    print(f"\nOutput dir: {config['output_dir']}")
    print("Current settings:")
    for key, value in config["current_settings"].items():
        print(f"  {key:<14} {value}")
    presets = cfg.list_presets(config)
    if presets:
        print(f"\nSaved presets:")
        for name in presets:
            note = cfg.get_preset_note(config, name)
            print(f"  {name}" + (f"  (note: {note})" if note else ""))
    quick = config.get("quick_presets", [])
    if quick:
        print(f"\nQuick-apply presets: {', '.join(quick)}")


def cmd_output(args: argparse.Namespace) -> None:
    config = cfg.load()
    config["output_dir"] = args.path
    cfg.save(config)
    print(f"Output dir set to {args.path}")


def cmd_preset_save(args: argparse.Namespace) -> None:
    config = cfg.load()
    settings: dict[str, str] = {}
    for kv in args.settings:
        if "=" not in kv:
            print(f"Skipping '{kv}': expected key=value, e.g. resolution=NUM_1080")
            continue
        key, value = kv.split("=", 1)
        field = SETTINGS_BY_KEY.get(key)
        if field is None:
            print(f"Unknown setting '{key}'. Valid keys: {', '.join(SETTINGS_BY_KEY)}")
            return
        valid_values = settings_choices(field)
        if value not in valid_values:
            print(f"Invalid value '{value}' for '{key}'. Valid values: {', '.join(valid_values)}")
            return
        settings[key] = value

    if not settings:
        print("No valid key=value settings given, nothing saved.")
        return

    cfg.save_preset(config, args.name, settings)
    if args.note:
        cfg.set_preset_note(config, args.name, args.note)
    cfg.save(config)
    print(f"Saved preset '{args.name}':")
    for key, value in settings.items():
        print(f"  {key:<14} {value}")
    if args.note:
        print(f"Note: {args.note}")


def cmd_preset_delete(args: argparse.Namespace) -> None:
    config = cfg.load()
    cfg.delete_preset(config, args.name)
    cfg.save(config)
    print(f"Deleted preset '{args.name}' (if it existed).")


def cmd_preset_show_keys(_args: argparse.Namespace) -> None:
    for key, field in SETTINGS_BY_KEY.items():
        print(f"{key:<14} {field.label:<16} valid: {', '.join(settings_choices(field))}")


def cmd_import_presets(args: argparse.Namespace) -> None:
    config = cfg.load()
    if args.path:
        text = Path(args.path).read_text()
        source = args.path
    else:
        package, resource = BUNDLED_PRESETS_RESOURCE
        text = importlib.resources.files(package).joinpath(resource).read_text()
        source = "bundled defaults"

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Could not parse '{source}' as JSON: {exc}")
        return

    imported = []
    for name, settings in data.get("presets", {}).items():
        cfg.save_preset(config, name, settings)
        imported.append(name)
    for name, note in data.get("preset_notes", {}).items():
        cfg.set_preset_note(config, name, note)
    for name in data.get("quick_presets", []):
        cfg.add_quick_preset(config, name)

    cfg.save(config)
    if imported:
        print(f"Imported {len(imported)} preset(s) from {source}: {', '.join(imported)}")
    else:
        print(f"No presets found in {source}.")
    quick = config.get("quick_presets", [])
    if quick:
        print(f"Quick-apply presets: {', '.join(quick)}")


def cmd_export_presets(args: argparse.Namespace) -> None:
    config = cfg.load()
    data = {
        "presets": config.get("presets", {}),
        "preset_notes": config.get("preset_notes", {}),
        "quick_presets": config.get("quick_presets", []),
    }
    Path(args.path).write_text(json.dumps(data, indent=2))
    print(f"Exported {len(data['presets'])} preset(s) to {args.path}")


def cmd_favorite(args: argparse.Namespace) -> None:
    config = cfg.load()
    if args.name not in config.get("presets", {}):
        print(f"No preset named '{args.name}'. Known presets: {', '.join(cfg.list_presets(config)) or '(none)'}")
        return
    cfg.add_quick_preset(config, args.name)
    cfg.save(config)
    quick = config.get("quick_presets", [])
    print(f"'{args.name}' will now show as a quick-apply button in Settings.")
    print(f"Current quick-apply presets: {', '.join(quick)}")


def cmd_unfavorite(args: argparse.Namespace) -> None:
    config = cfg.load()
    cfg.remove_quick_preset(config, args.name)
    cfg.save(config)
    print(f"'{args.name}' removed from quick-apply buttons (if it was there).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="gopro-studio-setup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="find GoPros connected over USB").set_defaults(func=cmd_discover)

    p_add = sub.add_parser("add", help="add or rename a camera")
    p_add.add_argument("serial", help="last 3-4 digits of the camera's serial number")
    p_add.add_argument("label", help="friendly name, e.g. cam1_front")
    p_add.set_defaults(func=cmd_add)

    p_remove = sub.add_parser("remove", help="remove a camera")
    p_remove.add_argument("serial")
    p_remove.set_defaults(func=cmd_remove)

    sub.add_parser("list", help="show configured cameras").set_defaults(func=cmd_list)

    p_output = sub.add_parser("output", help="set session output directory")
    p_output.add_argument("path")
    p_output.set_defaults(func=cmd_output)

    p_favorite = sub.add_parser(
        "favorite", help="pin a preset as a quick-apply button in Settings (can pin more than one)"
    )
    p_favorite.add_argument("name", help="preset name")
    p_favorite.set_defaults(func=cmd_favorite)

    p_unfavorite = sub.add_parser("unfavorite", help="unpin a preset's quick-apply button")
    p_unfavorite.add_argument("name")
    p_unfavorite.set_defaults(func=cmd_unfavorite)

    p_preset_save = sub.add_parser(
        "preset-save", help="create/update a preset from the command line, without opening the TUI"
    )
    p_preset_save.add_argument("name", help="preset name, e.g. 'Skelly Mocap'")
    p_preset_save.add_argument(
        "settings",
        nargs="+",
        help="key=value pairs, e.g. resolution=NUM_1080 fps=NUM_60_0 (see 'preset-keys' for valid keys/values)",
    )
    p_preset_save.add_argument(
        "--note",
        default=None,
        help="reminder shown every time this preset is applied, e.g. for settings the app can't push "
        "(Protune/ISO/white balance/shutter speed have no remote API - set them by hand on each camera)",
    )
    p_preset_save.set_defaults(func=cmd_preset_save)

    p_preset_delete = sub.add_parser("preset-delete", help="delete a saved preset")
    p_preset_delete.add_argument("name")
    p_preset_delete.set_defaults(func=cmd_preset_delete)

    sub.add_parser(
        "preset-keys", help="list valid setting keys and values for preset-save"
    ).set_defaults(func=cmd_preset_show_keys)

    p_import = sub.add_parser(
        "import-presets",
        help="load presets from a JSON file (defaults to the presets bundled with this install)",
    )
    p_import.add_argument(
        "path", nargs="?", default=None, help="path to a presets JSON file; omit to use the bundled defaults"
    )
    p_import.set_defaults(func=cmd_import_presets)

    p_export = sub.add_parser("export-presets", help="save your current presets to a JSON file")
    p_export.add_argument("path", help="where to write the presets JSON file")
    p_export.set_defaults(func=cmd_export_presets)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())

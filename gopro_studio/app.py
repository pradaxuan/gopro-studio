"""
GoPro Studio - a terminal control panel for a synchronized multi-GoPro
USB recording rig, built for the "record now, align in FreeMoCap later"
workflow.

Run with:  gopro-studio          (after `pip install -e .`)
      or:  python -m gopro_studio
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
)

from gopro_studio import config as cfg
from gopro_studio import takelog
from gopro_studio.rig import SETTINGS_SCHEMA, Camera, Rig, settings_choices

COLUMNS = ("Label", "Serial", "State", "Battery")


class ConfirmModal(ModalScreen[bool]):
    """A small yes/no confirmation dialog. Dismisses with True/False."""

    CSS = """
    ConfirmModal {
        align: center middle;
    }
    #dialog {
        width: 90%;
        max-width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    #dialog Static {
        margin-bottom: 1;
    }
    #dialog Horizontal {
        height: auto;
        align: right middle;
    }
    #dialog Button {
        margin-left: 1;
    }
    """

    def __init__(self, message: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.message, markup=True)
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class CameraManagerModal(ModalScreen[bool]):
    """Add, rename, and remove cameras in the rig config, without touching
    the CLI. Every action here saves immediately - there's no separate
    "cancel" for individual edits, only "Close" once you're done. Dismisses
    with True if anything changed (so the caller should rebuild its Rig),
    False otherwise.
    """

    CSS = """
    CameraManagerModal {
        align: center middle;
    }
    #camera_dialog {
        width: 90%;
        max-width: 84;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    .camera_row {
        height: 3;
        align: left middle;
    }
    .camera_row .cam_serial {
        width: 24;
        color: $text-muted;
    }
    .camera_row Input {
        width: 1fr;
        margin-right: 1;
    }
    .camera_row Button {
        margin-left: 1;
    }
    #add_row {
        height: auto;
        align: left middle;
        margin-bottom: 1;
        border-bottom: solid $accent;
        padding-bottom: 1;
    }
    #add_row Input {
        width: 1fr;
        margin-right: 1;
    }
    #feedback {
        height: auto;
        padding: 0 0 1 0;
        color: $text-muted;
    }
    #close_row {
        dock: bottom;
        height: auto;
        align: right middle;
        background: $surface;
        padding-top: 1;
        border-top: solid $accent;
    }
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = config
        self._changed = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="camera_dialog"):
            yield Static("[bold]Manage cameras[/]", markup=True)
            with Horizontal(id="add_row"):
                yield Input(placeholder="serial (last 3-4 digits or full)", id="new_serial_input")
                yield Input(placeholder="label, e.g. cam1", id="new_label_input")
                yield Button("Add camera", id="add_camera", variant="success")
            yield Static("", id="feedback")
            if not self.config["cameras"]:
                yield Static("[dim]No cameras configured yet - add one above.[/]", markup=True)
            for cam in self.config["cameras"]:
                serial = cam["serial"]
                with Horizontal(classes="camera_row"):
                    yield Static(serial, classes="cam_serial")
                    yield Input(value=cam["label"], id=f"label__{serial}")
                    yield Button("Rename", id=f"rename__{serial}")
                    yield Button("Remove", id=f"remove__{serial}", variant="error")
            with Horizontal(id="close_row"):
                yield Button("Close", id="close", variant="primary")

    def _feedback(self, msg: str) -> None:
        self.query_one("#feedback", Static).update(msg)

    async def _add_camera(self) -> None:
        serial_input = self.query_one("#new_serial_input", Input)
        label_input = self.query_one("#new_label_input", Input)
        serial = serial_input.value.strip()
        label = label_input.value.strip() or serial
        if not serial:
            self._feedback("[yellow]Enter a serial first.[/]")
            return
        if len(serial) < 3:
            self._feedback("[yellow]Serial should be at least the last 3-4 digits of the camera's serial number.[/]")
            return
        is_new = not any(c["serial"] == serial for c in self.config["cameras"])
        cfg.add_camera(self.config, serial, label)
        cfg.save(self.config)
        self._changed = True
        await self.recompose()
        self._feedback(f"[green]{'Added' if is_new else 'Updated'} '{label}' ({serial}).[/]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("new_serial_input", "new_label_input"):
            await self._add_camera()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "close":
            self.dismiss(self._changed)
        elif button_id == "add_camera":
            await self._add_camera()
        elif button_id.startswith("rename__"):
            serial = button_id.removeprefix("rename__")
            label_input = self.query_one(f"#label__{serial}", Input)
            new_label = label_input.value.strip()
            if not new_label:
                self._feedback("[yellow]Label can't be empty.[/]")
                return
            cfg.add_camera(self.config, serial, new_label)
            cfg.save(self.config)
            self._changed = True
            self._feedback(f"[green]Renamed {serial} to '{new_label}'.[/]")
        elif button_id.startswith("remove__"):
            serial = button_id.removeprefix("remove__")
            cfg.remove_camera(self.config, serial)
            cfg.save(self.config)
            self._changed = True
            await self.recompose()
            self._feedback(f"[green]Removed {serial}.[/]")


class SettingsModal(ModalScreen[Optional[tuple[dict[str, str], Optional[str]]]]):
    """Lets the user tweak every setting in SETTINGS_SCHEMA at once, and
    save/load/delete named presets. Dismisses with (settings, note) to
    apply - note is a reminder to show in the log, e.g. for values the app
    can't push (Protune/ISO/etc have no remote API) - or None if cancelled.
    """

    CSS = """
    SettingsModal {
        align: center middle;
    }
    #settings_dialog {
        width: 90%;
        max-width: 74;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #quick_apply_row {
        height: auto;
        margin-bottom: 1;
    }
    .quick_apply_btn {
        width: 1fr;
        height: 3;
        text-style: bold;
        margin-right: 1;
    }
    .setting_row {
        height: 3;
        align: left middle;
    }
    .setting_row Label {
        width: 16;
    }
    .setting_row Select {
        width: 1fr;
    }
    #preset_row, #save_row {
        height: auto;
        margin-top: 1;
    }
    #save_row Input {
        width: 1fr;
    }
    #save_row Button {
        width: auto;
        margin-left: 1;
    }
    #button_row {
        dock: bottom;
        height: auto;
        align: right middle;
        background: $surface;
        padding-top: 1;
        border-top: solid $accent;
    }
    #button_row Button {
        margin-left: 1;
    }
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = config
        # button id -> preset name, built in compose(), used by on_button_pressed
        self._quick_buttons: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="settings_dialog"):
            quick_names = [
                n for n in self.config.get("quick_presets", []) if n in self.config.get("presets", {})
            ]
            if quick_names:
                with Horizontal(id="quick_apply_row"):
                    for i, name in enumerate(quick_names):
                        btn_id = f"quick_apply_{i}"
                        self._quick_buttons[btn_id] = name
                        yield Button(f"⚡ {name}", id=btn_id, classes="quick_apply_btn", variant="success")
            yield Static("[bold]Camera settings[/] (applied to every connected camera)", markup=True)
            for field in SETTINGS_SCHEMA:
                current = self.config["current_settings"].get(field.key, field.recommended)
                options = [(name, name) for name in settings_choices(field)]
                with Horizontal(classes="setting_row"):
                    yield Label(field.label)
                    yield Select(options, value=current, id=f"select_{field.key}", allow_blank=False)
            with Horizontal(id="preset_row"):
                yield Label("Preset: ")
                yield Select(
                    [(n, n) for n in cfg.list_presets(self.config)],
                    id="preset_select",
                    prompt="(choose)",
                )
                yield Button("Load", id="load_preset")
                yield Button("Delete", id="delete_preset", variant="error")
            with Horizontal(id="save_row"):
                yield Input(placeholder="new preset name", id="preset_name_input")
                yield Button("Save as preset", id="save_preset")
            with Horizontal(id="button_row"):
                yield Button("Cancel", id="cancel")
                yield Button("Apply to all cameras", id="apply", variant="success")

    def _current_field_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for field in SETTINGS_SCHEMA:
            select = self.query_one(f"#select_{field.key}", Select)
            if select.value is not Select.BLANK:
                values[field.key] = str(select.value)
        return values

    def _refresh_preset_options(self) -> None:
        preset_select = self.query_one("#preset_select", Select)
        preset_select.set_options([(n, n) for n in cfg.list_presets(self.config)])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "cancel":
            self.dismiss(None)
        elif button_id in self._quick_buttons:
            name = self._quick_buttons[button_id]
            preset = self.config.get("presets", {}).get(name, {})
            note = cfg.get_preset_note(self.config, name)
            self.dismiss((dict(preset), note))
        elif button_id == "apply":
            self.dismiss((self._current_field_values(), None))
        elif button_id == "load_preset":
            preset_select = self.query_one("#preset_select", Select)
            if preset_select.value is Select.BLANK:
                return
            preset = self.config.get("presets", {}).get(str(preset_select.value), {})
            for field in SETTINGS_SCHEMA:
                if field.key in preset:
                    self.query_one(f"#select_{field.key}", Select).value = preset[field.key]
        elif button_id == "save_preset":
            name_input = self.query_one("#preset_name_input", Input)
            name = name_input.value.strip()
            if not name:
                return
            cfg.save_preset(self.config, name, self._current_field_values())
            cfg.save(self.config)
            name_input.value = ""
            self._refresh_preset_options()
        elif button_id == "delete_preset":
            preset_select = self.query_one("#preset_select", Select)
            if preset_select.value is Select.BLANK:
                return
            cfg.delete_preset(self.config, str(preset_select.value))
            cfg.save(self.config)
            self._refresh_preset_options()


class GoProStudioApp(App):
    """Main TUI application."""

    TITLE = "ILP Labs GoPro Studio"

    CSS = """
    #main {
        height: 1fr;
    }
    .controls_row {
        height: auto;
        padding: 1 1 0 1;
    }
    .controls_row Button {
        margin-right: 1;
    }
    #session_panel {
        height: auto;
        padding: 0 1;
        border: solid $accent;
        margin: 1 1 0 1;
    }
    #session_panel .field_row {
        height: 3;
        align: left middle;
    }
    #session_panel .field_row Label {
        width: 14;
    }
    #output_dir_input {
        width: 1fr;
    }
    #actor_input {
        width: 1fr;
    }
    #take_input {
        width: 10;
        margin-left: 1;
    }
    #session_preview {
        height: auto;
        padding: 0 0 1 14;
        color: $text-muted;
    }
    #recording_timer {
        height: 1;
        padding: 0 1;
        text-style: bold;
    }
    #log {
        height: 12;
        border: solid $accent;
    }
    """

    BINDINGS = [
        ("m", "manage_cameras", "Cameras"),
        ("c", "connect_all", "Connect all"),
        ("s", "open_settings", "Settings"),
        ("r", "start_recording", "Start rec (sync)"),
        ("x", "stop_recording", "Stop rec (sync)"),
        ("d", "download_all", "Download session"),
        ("f", "format_cards", "Format SD cards"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = cfg.load()
        serials = [c["serial"] for c in self.config["cameras"]]
        labels = {c["serial"]: c["label"] for c in self.config["cameras"]}
        self.rig = Rig(serials, labels)
        self._recording_start: Optional[datetime] = None
        self._recording_stop: Optional[datetime] = None
        self._recording_timer = None  # textual.timer.Timer while recording

    # ---------------------------------------------------------------- UI ---

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="main"):
            yield DataTable(id="camera_table")
            with Vertical(id="session_panel"):
                with Horizontal(classes="field_row"):
                    yield Label("Output dir: ")
                    yield Input(value=self.config["output_dir"], id="output_dir_input")
                with Horizontal(classes="field_row"):
                    yield Label("Actor: ")
                    yield Input(
                        value=self.config.get("last_actor", ""),
                        placeholder="actor name",
                        id="actor_input",
                    )
                    yield Label(" Take: ")
                    yield Input(value="1", type="integer", id="take_input")
                yield Static(id="session_preview")
            with Horizontal(id="controls_row1", classes="controls_row"):
                yield Button("Cameras...", id="btn_cameras")
                yield Button("Connect all", id="btn_connect", variant="primary")
                yield Button("Settings...", id="btn_settings")
                yield Button("● Start rec (sync)", id="btn_start", variant="success")
                yield Button("■ Stop rec (sync)", id="btn_stop", variant="error")
            with Horizontal(id="controls_row2", classes="controls_row"):
                yield Button("Download session", id="btn_download")
                yield Button("Format SD cards", id="btn_format", variant="error")
                yield Button("Refresh status", id="btn_refresh")
                yield Button("Quit", id="btn_quit", variant="error")
            yield Static(id="recording_timer")
            yield RichLog(id="log", wrap=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#camera_table", DataTable)
        table.add_columns(*COLUMNS)
        table.cursor_type = "row"
        self.refresh_table()
        self._update_session_preview()
        if not self.rig.cameras:
            self.log_msg(
                "[yellow]No cameras configured yet.[/] Add serials to "
                f"{cfg.CONFIG_FILE} (or use `gopro-studio-add <serial> <label>`), "
                "then restart."
            )
        else:
            self.log_msg(f"Loaded {len(self.rig.cameras)} camera(s) from config. Press 'c' to connect.")

    def log_msg(self, msg: str) -> None:
        self.query_one("#log", RichLog).write(msg)

    # ------------------------------------------------------- session naming ---

    @staticmethod
    def _sanitize_for_path(text: str) -> str:
        text = text.strip()
        text = re.sub(r"\s+", "_", text)
        # Strip characters that are unsafe in filenames or awkward in an
        # unquoted shell context. Deliberately NOT restricted to ASCII, so
        # accented letters (ö, å, ä, etc.) are preserved as typed.
        text = re.sub(r"""[\\/:*?"'<>|]""", "", text)
        return text

    def _current_take(self) -> int:
        raw = self.query_one("#take_input", Input).value.strip()
        try:
            take = int(raw)
        except ValueError:
            take = 1
        return max(take, 1)

    def _current_actor(self) -> str:
        return self._sanitize_for_path(self.query_one("#actor_input", Input).value)

    def _compute_folder_name(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        take = self._current_take()
        actor = self._current_actor()
        parts = []
        if actor:
            parts.append(actor)
        parts.append(f"take{take:02d}")
        parts.append(timestamp)
        return "_".join(parts)

    def _update_session_preview(self) -> None:
        preview = self.query_one("#session_preview", Static)
        preview.update(f"Will save to: {self.config['output_dir']}/{self._compute_folder_name()}")

    def _bump_take_number(self) -> None:
        take_input = self.query_one("#take_input", Input)
        take_input.value = str(self._current_take() + 1)
        self._update_session_preview()

    def refresh_table(self) -> None:
        table = self.query_one("#camera_table", DataTable)
        table.clear()
        for cam in self.rig.cameras:
            st = cam.status
            battery = f"{st.battery_pct}%" if st.battery_pct is not None else "-"
            state = st.state_text
            style_state = state
            if state == "RECORDING":
                style_state = "[bold red]RECORDING[/]"
            elif state == "ready":
                style_state = "[green]ready[/]"
            elif state.startswith("error"):
                style_state = f"[red]{state}[/]"
            table.add_row(cam.label, cam.serial, style_state, battery)

    # ----------------------------------------------------------- actions ---

    def action_manage_cameras(self) -> None:
        self.manage_cameras()

    def action_connect_all(self) -> None:
        self.run_connect_all()

    def action_open_settings(self) -> None:
        self.open_settings()

    def action_start_recording(self) -> None:
        self.run_start_recording()

    def action_stop_recording(self) -> None:
        self.run_stop_recording()

    def action_download_all(self) -> None:
        self.run_download_session()

    def action_format_cards(self) -> None:
        self.confirm_and_format()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn_connect": self.run_connect_all,
            "btn_cameras": self.manage_cameras,
            "btn_settings": self.open_settings,
            "btn_start": self.run_start_recording,
            "btn_stop": self.run_stop_recording,
            "btn_download": self.run_download_session,
            "btn_format": self.confirm_and_format,
            "btn_refresh": self.run_refresh_status,
            "btn_quit": self.exit,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def on_input_changed(self, event: Input.Changed) -> None:
        input_id = event.input.id
        if input_id == "output_dir_input":
            self.config["output_dir"] = event.value
            cfg.save(self.config)
            self._update_session_preview()
        elif input_id == "actor_input":
            self.config["last_actor"] = event.value
            cfg.save(self.config)
            self._update_session_preview()
        elif input_id == "take_input":
            self._update_session_preview()

    # ------------------------------------------------------------ workers --

    @work(exclusive=True)
    async def manage_cameras(self) -> None:
        changed = await self.push_screen_wait(CameraManagerModal(self.config))
        if not changed:
            return
        await self.rig.close_all()
        serials = [c["serial"] for c in self.config["cameras"]]
        labels = {c["serial"]: c["label"] for c in self.config["cameras"]}
        self.rig = Rig(serials, labels)
        self.refresh_table()
        self.log_msg(
            f"[green]Camera list updated ({len(self.rig.cameras)} configured).[/] "
            "Press 'c' to (re)connect."
        )

    @work(exclusive=True)
    async def run_connect_all(self) -> None:
        if not self.rig.cameras:
            self.log_msg("[yellow]No cameras configured.[/]")
            return
        self.log_msg("Connecting to all cameras over USB ...")

        def on_done(cam: Camera, err: BaseException | None) -> None:
            if err:
                self.log_msg(f"[red]{cam.label}: failed to connect - {err}[/]")
            else:
                self.log_msg(f"[green]{cam.label}: connected[/]")

        await self.rig.connect_all(on_each_done=on_done)
        await self.rig.refresh_all_status()
        self.refresh_table()

    @work(exclusive=True)
    async def run_refresh_status(self) -> None:
        await self.rig.refresh_all_status()
        self.refresh_table()

    @work(exclusive=True)
    async def open_settings(self) -> None:
        result = await self.push_screen_wait(SettingsModal(self.config))
        if result is None:
            self.log_msg("Settings unchanged.")
            return
        new_settings, note = result

        self.config["current_settings"] = new_settings
        cfg.save(self.config)

        if not any(c.status.connected for c in self.rig.cameras):
            self.log_msg("[green]Settings saved.[/] Connect cameras to push them.")
            if note:
                self.log_msg(f"[yellow]Reminder:[/] {note}")
            return

        self.log_msg("Pushing settings to all connected cameras ...")
        per_camera = await self.rig.apply_settings_all(new_settings)
        failed_bits = []
        for label, per_key in per_camera.items():
            errors = {k: v for k, v in per_key.items() if v is not None}
            if errors:
                failed_bits.append(f"{label}: {errors}")
        if failed_bits:
            self.log_msg(f"[red]Some settings failed to apply:[/] {'; '.join(failed_bits)}")
        else:
            self.log_msg("[green]Settings applied to all connected cameras.[/]")
        if note:
            self.log_msg(f"[yellow]Reminder:[/] {note}")

    @work(exclusive=True)
    async def run_start_recording(self) -> None:
        self.log_msg("Arming all cameras ...")
        results = await self.rig.synced_start()
        errors = [r for r in results if r]
        if errors:
            self.log_msg(f"[red]{len(errors)} camera(s) failed to start: {errors}[/]")
        else:
            self.log_msg(
                "[bold green]All cameras recording.[/] "
                "Clap or flash now for post-hoc sync in FreeMoCap's SkellySync."
            )
            self._start_timer()
        await self.rig.refresh_all_status()
        self.refresh_table()

    @work(exclusive=True)
    async def run_stop_recording(self) -> None:
        results = await self.rig.synced_stop()
        errors = [r for r in results if r]
        if errors:
            self.log_msg(f"[red]{len(errors)} camera(s) failed to stop: {errors}[/]")
        else:
            self.log_msg("[green]All cameras stopped.[/]")
        self._stop_timer()
        await self.rig.refresh_all_status()
        self.refresh_table()

    # ------------------------------------------------------- recording timer ---

    def _start_timer(self) -> None:
        self._recording_start = datetime.now()
        self._recording_stop = None
        if self._recording_timer:
            self._recording_timer.stop()
        self._recording_timer = self.set_interval(1.0, self._tick_timer)
        self._tick_timer()

    def _stop_timer(self) -> None:
        if self._recording_timer:
            self._recording_timer.stop()
            self._recording_timer = None
        self._recording_stop = datetime.now()
        self.query_one("#recording_timer", Static).update("")

    def _tick_timer(self) -> None:
        if not self._recording_start:
            return
        elapsed = int((datetime.now() - self._recording_start).total_seconds())
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        text = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        self.query_one("#recording_timer", Static).update(f"[bold red]● RECORDING  {text}[/]")

    @property
    def _last_recording_duration_seconds(self) -> Optional[float]:
        if self._recording_start and self._recording_stop:
            return (self._recording_stop - self._recording_start).total_seconds()
        return None

    @work(exclusive=True)
    async def run_download_session(self) -> None:
        folder_name = self._compute_folder_name()
        session_dir = Path(self.config["output_dir"]) / folder_name
        self.log_msg(f"Downloading new media into {session_dir} ...")
        result = await self.rig.download_session(session_dir)
        any_downloaded = False
        for label, files in result.items():
            if files:
                any_downloaded = True
                self.log_msg(f"[green]{label}: downloaded {len(files)} file(s)[/]")
            else:
                self.log_msg(f"{label}: nothing new to download")
        self.log_msg("[bold]Done.[/] Feed this folder's per-camera videos into FreeMoCap's SkellySync.")
        if any_downloaded:
            self._write_take_log(folder_name, session_dir, result)
            self._bump_take_number()

    def _write_take_log(
        self, folder_name: str, session_dir: Path, result: dict[str, list[dict[str, Any]]]
    ) -> None:
        settings = dict(self.config["current_settings"])
        record = {
            "session_folder": folder_name,
            "actor": self._current_actor() or None,
            "take_number": self._current_take(),
            "downloaded_at": datetime.now().isoformat(),
            "recording_started_at": self._recording_start.isoformat() if self._recording_start else None,
            "recording_stopped_at": self._recording_stop.isoformat() if self._recording_stop else None,
            "recording_duration_seconds": self._last_recording_duration_seconds,
            "preset_used": cfg.find_matching_preset(self.config, settings),
            "settings": settings,
            "output_dir": str(self.config["output_dir"]),
            "cameras": [
                {
                    "label": cam.label,
                    "serial": cam.serial,
                    "battery_pct_at_last_check": cam.status.battery_pct,
                    "files": result.get(cam.label, []),
                }
                for cam in self.rig.cameras
                if cam.label in result
            ],
        }
        try:
            log_file = takelog.append_take_record(Path(self.config["output_dir"]), record)
            self.log_msg(f"Logged take metadata to {log_file}")
        except OSError as exc:
            self.log_msg(f"[red]Failed to write take log: {exc}[/]")

    @work(exclusive=True)
    async def confirm_and_format(self) -> None:
        if not any(c.status.connected for c in self.rig.cameras):
            self.log_msg("[yellow]No connected cameras to format.[/]")
            return

        # Get fresh status before deciding whether it's safe to proceed.
        await self.rig.refresh_all_status()
        self.refresh_table()
        recording = [c.label for c in self.rig.cameras if c.status.connected and c.status.encoding]
        if recording:
            self.log_msg(
                f"[red]Refusing to format: {', '.join(recording)} still recording. Stop first.[/]"
            )
            return

        connected_labels = [c.label for c in self.rig.cameras if c.status.connected]
        message = (
            "[bold red]This permanently deletes ALL media[/] on the SD card of:\n"
            f"  {', '.join(connected_labels)}\n\n"
            "Make sure everything has been downloaded already. This cannot be undone."
        )
        confirmed = await self.push_screen_wait(ConfirmModal(message, confirm_label="Delete everything"))
        if not confirmed:
            self.log_msg("Format cancelled.")
            return

        self.log_msg("Formatting SD cards on all connected cameras ...")
        results = await self.rig.format_all_sd_cards()
        errors = [r for r in results if r]
        if errors:
            self.log_msg(f"[red]{len(errors)} camera(s) failed to format: {errors}[/]")
        else:
            self.log_msg("[green]All connected cameras' SD cards cleared.[/]")

    async def on_unmount(self) -> None:
        await self.rig.close_all()


def main() -> None:
    GoProStudioApp().run()


if __name__ == "__main__":
    main()

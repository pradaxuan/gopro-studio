# ILP Labs GoPro Studio

A terminal app for controlling a rig of GoPros over USB from Linux (or macOS/Windows): connect,
push identical settings (with saveable presets), start/stop recording in sync with a live timer,
download footage into cleanly-named per-take folders with a full JSON take log, and format SD
cards — all from one screen, ready to hand off to FreeMoCap's **SkellySync** for frame-accurate
alignment and 3D reconstruction.

It does **not** try to replace SkellySync's alignment — GoPros have no hardware genlock, so this
app focuses on getting all cameras rolling as close together as command-level sync allows
(typically well under a frame of skew over USB), and reminds you to clap or flash at the start of
each take so SkellySync's audio-cross-correlation / brightness-flash detection can nail exact
frame alignment on import.

Built on GoPro's official [`open-gopro`](https://gopro.github.io/OpenGoPro/python_sdk/) Python
SDK, using its `WiredGoPro` (USB HTTP) transport — no BLE/WiFi contention with 6+ cameras — and
[Textual](https://github.com/Textualize/textual) for the terminal UI.

<img width="1246" height="688" alt="screenshot-2026-09-08_13-40-48" src="https://github.com/user-attachments/assets/9fc1b55b-420b-4ec0-ad68-b726fba0a87c" />


## Features

- **Synced start/stop** across all connected cameras using a ready/go barrier (same pattern
  GoPro's own official multi-camera demo uses), with a live `MM:SS` recording timer in the UI.
- **Settings management**: adjust resolution, fps, lens/FOV, stabilization, bit rate/depth,
  anti-flicker, and aspect ratio for every camera at once, with save/load/delete presets and
  pinnable one-click **quick-apply buttons** for your go-to presets.
- **Session naming**: actor name + take number + timestamp, editable output directory
  (defaults to wherever you last set it — handy for an external drive), auto-incrementing
  take number.
- **Clean per-camera downloads**: `cam1.MP4` per take (numbered suffixes only if a camera has
  multiple files in one batch), organized as `<output_dir>/<actor>_take<NN>_<timestamp>/<camera>/`.
- **A single aggregated JSON take log** (`takes_log.json` at the root of your output directory,
  not split per camera) recording everything about every take: actor, take number, recording
  start/stop/duration, preset used, full settings, and per-camera file details (original GoPro
  filename, local filename, size).
- **Format SD cards** on all connected cameras at once, with a confirmation dialog and a guard
  against formatting mid-recording.
- **In-app camera manager**: add, rename, or remove as many cameras as you have from a dedicated
  window in the app itself (`m`) — no CLI required, though the CLI is there too if you prefer it.
- **mDNS discovery** to find connected cameras automatically, or add them by serial.

## Install

```bash
git clone https://github.com/<your-org>/gopro-studio.git
cd gopro-studio
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requires Python 3.11–3.13. If your system Python is newer (3.14+), use
[`uv`](https://github.com/astral-sh/uv) to get a compatible interpreter without touching your
system install:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e .
```

## Set up your rig

Plug in your cameras via a powered USB hub, power them on, then either scan for them:

```bash
gopro-studio-setup discover
```

or add them by serial (find each camera's serial under Settings → About on the camera, or the
tail of its default WiFi SSID, e.g. `GoPro 3421` → serial ends in `3421`):

```bash
gopro-studio-setup add C3501325384654 cam1
gopro-studio-setup add C3501325375142 cam3
# ...etc for the rest of your rig
gopro-studio-setup output ~/GoProStudio/sessions
```

(Prefer a UI? Skip straight to `gopro-studio` and press `m` — the in-app camera manager does
the same add/rename/remove without touching the CLI at all.)

### Load the bundled presets

This repo ships two ready-made presets — load them straight into your config:

```bash
gopro-studio-setup import-presets
```

This adds:

- **Skelly Mocap** — 1080p/60fps, Linear lens, stabilization off, tuned for FreeMoCap's Charuco
  calibration and triangulation (see the note attached to the preset for the reasoning).
- **HMC Stereo** — 9:16, 1080p/50fps, Wide lens, stabilization off. Note: Protune, white balance,
  ISO, and shutter speed have no remote API on GoPro hardware, so these must be set by hand on
  each camera — the app shows this reminder automatically whenever you quick-apply this preset.

Both are pinned as quick-apply buttons, so `gopro-studio` → `s` shows them right at the top of
Settings. Check what landed with:

```bash
gopro-studio-setup list
```

Want to share your own presets with someone else, or back them up? `export-presets <path>` writes
your current presets/notes/pins to a JSON file; `import-presets <path>` loads any such file
(including one you've customized from the bundled defaults).

## Run it

```bash
gopro-studio
```

Keyboard shortcuts (also available as buttons): `m` manage cameras, `c` connect all, `s`
settings, `r` start
recording (synced), `x` stop recording (synced), `d` download session, `f` format SD cards,
`q` quit.

Typical take:
1. `c` to connect, `s` to push a preset once at the start of a shoot.
2. Set the actor name and take number in the session panel (take auto-increments after each
   download).
3. `r` to start — watch the timer appear, clap or flash once all cameras confirm recording.
4. Perform the capture, `x` to stop.
5. Repeat for more takes; `d` to download when ready — files land in
   `<output_dir>/<actor>_take<NN>_<timestamp>/<camera_label>/`, and `takes_log.json` in
   `<output_dir>` gets a new entry with everything about that take.

## Linux networking notes

- The kernel's `rndis_host`/`cdc_ether` drivers handle GoPro's USB network interfaces
  automatically on virtually all modern distros — no special udev rules needed.
- NetworkManager should auto-configure each camera's interface. If `gopro-studio-setup discover`
  finds nothing but `lsusb` shows all your cameras, check `ip addr` for `usbX`/`enxX` interfaces
  without an IP, and that NetworkManager isn't set to ignore USB interfaces
  (`nmcli device status`). You don't strictly need discovery to work, though — connecting by
  serial (`gopro-studio-setup add <serial> <label>`) computes each camera's IP directly and skips
  mDNS entirely.
- A **quality powered USB hub matters** — GoPro engineers have noted that at large camera counts,
  powered hubs "tend to enumerate flakily," recommending PCIe USB cards instead for 80-camera
  rigs. At 6 cameras a decent powered hub is normally fine, but if you see intermittent connect
  failures, try splitting cameras across two hubs/host controllers rather than daisy chaining.

## Feeding into FreeMoCap

1. After downloading, each camera's clips live in their own subfolder under the take directory.
2. Run FreeMoCap's `skelly_synchronize` (SkellySync in the FreeMoCap v2 UI) against that
   folder — pick audio cross-correlation (recommended if you clapped) or brightness detection
   (if you used a flash).
3. Import the resulting synchronized, frame-trimmed videos into FreeMoCap for calibration (a
   Charuco-board recording, done the same way) and then for 3D reconstruction of your actual
   takes.

## Project layout

```
gopro_studio/
  rig.py         # Camera + Rig: connect, settings, synced start/stop, format, download
  discover.py    # mDNS scan for connected cameras
  config.py      # JSON config: cameras, output dir, current settings, presets, notes
  takelog.py     # Single aggregated JSON take log (takes_log.json)
  app.py         # Textual TUI
  setup_cli.py   # gopro-studio-setup CLI for managing config.json and presets
  data/
    default_presets.json   # bundled presets loaded via `import-presets`
```

## Extending

- `SETTINGS_SCHEMA` in `rig.py` is the full list of settings the app can push (resolution, fps,
  lens, stabilization, bit rate/depth, anti-flicker, aspect ratio) — extend it there if a future
  `open-gopro` release exposes more of the `HttpSettings` surface (Protune/ISO/white
  balance/shutter aren't exposed as of this writing).
- The synced-start barrier lives in `Rig.synced_start` / `Camera._arm_and_fire` — if you want a
  physical trigger (footswitch, MIDI pad) instead of a keybinding, hook it to call
  `app.run_start_recording()`.
- `takelog.append_take_record` is a plain function taking an output dir and a dict — easy to call
  from anywhere else you might want to log additional metadata.

## Visual guide

- Launch the app.
- Click on add cameras. Here you can add yuour cameras based on their serial number and re-name them, order them and dele them.

<img width="1246" height="688" alt="screenshot-2026-09-08_13-41-13" src="https://github.com/user-attachments/assets/37303ebc-818d-4025-9027-4f598aede5f5" />

- Click on connec all. On the top, you will see the state and battery levels for all connected cameras.

<img width="1246" height="688" alt="screenshot-2026-09-08_13-41-36" src="https://github.com/user-attachments/assets/1ed1b8e3-6724-4490-817d-e1eabfff5d11" />

- Click on settings. here you can manually control the settings all of your cameras at once, create and load presets, and delete unwanted presets. Two presets are created for you, one for freemoca (Skelly Mocap) and one for Metahumans (HMC Stereo).

<img width="1246" height="688" alt="screenshot-2026-09-08_13-41-48" src="https://github.com/user-attachments/assets/ae388eb0-7393-474a-bd89-a43b8e398569" />

- Click on record to start a new session, and click on stop to stop the recording.
- Click on download to transfer the files from all cameras and the recording log to a desired working directory.

<img width="1256" height="701" alt="screenshot-2026-09-08_13-43-09" src="https://github.com/user-attachments/assets/a124247e-115b-4140-8c83-cb21b37f71b0" />

- Click on format SD cards to delete all the footage in all the cameras at once.

<img width="1246" height="688" alt="screenshot-2026-09-08_13-43-27" src="https://github.com/user-attachments/assets/eeda6a12-7324-4578-b99b-a3eadd232667" />

## License

MIT — see [LICENSE](LICENSE).

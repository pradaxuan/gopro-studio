"""
Core control layer for a multi-GoPro USB rig.

Built on GoPro's official `open-gopro` Python SDK (WiredGoPro / USB HTTP
transport). Each camera enumerates over USB as its own RNDIS network
interface with its own private /24 subnet, so many cameras can be driven
from a single powered USB hub with no WiFi/BLE contention.

Design notes
------------
- All I/O is asyncio-based (the SDK is asyncio-native), so we drive all
  cameras concurrently with asyncio.gather / asyncio.Event barriers instead
  of the multiprocessing approach GoPro's own multi-camera demo uses (that
  demo needs multiprocessing mainly to work around BLE's one-connection
  limitation; pure USB doesn't have that problem).
- "Synced start" uses a ready/go barrier: every camera connection is armed
  first, then a single shared asyncio.Event releases all of them at once.
  This does not give hardware-genlock-level precision (GoPros have none),
  but it minimizes command skew across cameras. Combine with a clap/flash
  at the start of every take and let FreeMoCap's SkellySync
  (audio cross-correlation / brightness-flash detection) do frame-accurate
  alignment on import - see the project README for why this split works.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from open_gopro import WiredGoPro
from open_gopro.models import constants, proto
from open_gopro.models.constants import StatusId
from open_gopro.models.constants import settings as gp_settings

logger = logging.getLogger("gopro_studio.rig")

# ---------------------------------------------------------------------------
# Settings schema
#
# The Open GoPro API exposes many more settings than resolution/fps, but not
# everything a camera menu shows (manual ISO/shutter/white-balance aren't
# exposed as simple settings in this API version - only as part of a more
# involved custom-preset protobuf message). The fields below are the ones
# that matter most for a multi-camera mocap rig: keeping resolution, fps,
# lens/FOV, and stabilization identical (and stabilization OFF) across every
# camera is what actually affects triangulation quality.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SettingField:
    key: str  # stable key used in config.json / presets
    label: str  # shown in the UI
    http_attr: str  # attribute name on gopro.http_setting
    enum_cls: type
    recommended: str  # sane default for a synced mocap rig


SETTINGS_SCHEMA: list[SettingField] = [
    SettingField("resolution", "Resolution", "video_resolution", gp_settings.VideoResolution, "NUM_4K"),
    SettingField("fps", "Frame rate", "frames_per_second", gp_settings.FramesPerSecond, "NUM_60_0"),
    SettingField("lens", "Lens / FOV", "video_lens", gp_settings.VideoLens, "LINEAR"),
    SettingField("stabilization", "Stabilization", "hypersmooth", gp_settings.Hypersmooth, "OFF"),
    SettingField("bit_rate", "Bit rate", "video_bit_rate", gp_settings.VideoBitRate, "HIGH"),
    SettingField("bit_depth", "Bit depth", "bit_depth", gp_settings.BitDepth, "NUM_8_BIT"),
    SettingField("anti_flicker", "Anti-flicker", "anti_flicker", gp_settings.Anti_Flicker, "NUM_60HZ"),
    SettingField("aspect_ratio", "Aspect ratio", "video_aspect_ratio", gp_settings.VideoAspectRatio, "NUM_16_9"),
]

SETTINGS_BY_KEY: dict[str, SettingField] = {f.key: f for f in SETTINGS_SCHEMA}


def default_settings() -> dict[str, str]:
    """The recommended starting point for a synced mocap rig."""
    return {f.key: f.recommended for f in SETTINGS_SCHEMA}


def settings_choices(field: SettingField) -> list[str]:
    """All valid enum member names for a given field, for building a UI."""
    return [x for x in dir(field.enum_cls) if not x.startswith("_") and x.isupper()]


# Convenience re-exports so callers don't need to dig through the SDK's
# module layout.
VideoResolution = gp_settings.VideoResolution
FramesPerSecond = gp_settings.FramesPerSecond


@dataclasses.dataclass
class CameraStatus:
    serial: str
    label: str
    connected: bool = False
    encoding: bool = False
    busy: bool = False
    battery_pct: Optional[int] = None
    error: Optional[str] = None

    @property
    def state_text(self) -> str:
        if self.error:
            return f"error: {self.error}"
        if not self.connected:
            return "disconnected"
        if self.encoding:
            return "RECORDING"
        if self.busy:
            return "busy"
        return "ready"


class Camera:
    """Wraps a single WiredGoPro connection and its last-known status."""

    def __init__(self, serial: str, label: str | None = None):
        self.serial = serial
        self.label = label or serial
        self.gopro: WiredGoPro | None = None
        self.status = CameraStatus(serial=serial, label=self.label)

    async def connect(self, timeout: int = 15, retries: int = 3) -> None:
        """Open the USB HTTP connection to this camera.

        `serial` should be at least the last 3-4 digits of the camera's
        serial number (visible under Settings > About on the camera, and
        matches the last digits of its default WiFi SSID).
        """
        self.gopro = WiredGoPro(self.serial)
        try:
            await self.gopro.open(timeout=timeout, retries=retries)
            self.status.connected = True
            self.status.error = None
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.status.connected = False
            self.status.error = str(exc)
            raise

    async def refresh_status(self) -> None:
        if not self.gopro or not self.status.connected:
            return
        try:
            state = (await self.gopro.http_command.get_camera_state()).data
            self.status.encoding = bool(state.get(StatusId.ENCODING, False))
            self.status.busy = bool(state.get(StatusId.BUSY, False))
            batt = state.get(StatusId.INTERNAL_BATTERY_PERCENTAGE)
            self.status.battery_pct = int(batt) if batt is not None else None
            self.status.error = None
        except Exception as exc:  # noqa: BLE001
            self.status.error = str(exc)

    async def apply_settings(self, settings: dict[str, str]) -> dict[str, Optional[BaseException]]:
        """Push a dict of {schema_key: enum_member_name} to this camera.

        Unknown keys are ignored (forward-compatible with older configs);
        each field is applied independently so one bad value doesn't stop
        the rest from being applied. Returns a per-key error dict (None on
        success) so the caller can report exactly what failed.
        """
        assert self.gopro, "camera is not connected"
        await self.gopro.http_command.load_preset_group(
            group=proto.EnumPresetGroup.PRESET_GROUP_ID_VIDEO
        )
        results: dict[str, Optional[BaseException]] = {}
        for key, value_name in settings.items():
            field = SETTINGS_BY_KEY.get(key)
            if field is None:
                continue
            try:
                enum_value = getattr(field.enum_cls, value_name)
                http_setting = getattr(self.gopro.http_setting, field.http_attr)
                await http_setting.set(enum_value)
                results[key] = None
            except Exception as exc:  # noqa: BLE001
                results[key] = exc
        return results

    async def _arm_and_fire(self, ready_event: asyncio.Event, go_event: asyncio.Event) -> None:
        """Signal ready, block on the shared barrier, then start recording."""
        assert self.gopro
        ready_event.set()
        await go_event.wait()
        await self.gopro.http_command.set_shutter(shutter=constants.Toggle.ENABLE)

    async def stop_recording(self) -> None:
        assert self.gopro
        await self.gopro.http_command.set_shutter(shutter=constants.Toggle.DISABLE)

    async def format_sd_card(self) -> None:
        """Delete every file on the SD card.

        Note: the public Open GoPro API only exposes "delete all media",
        not a true low-level reformat (that's camera-menu-only: Preferences
        > Reset > Format SD Card). For clearing a card between sessions
        this has the same practical effect - the card ends up empty - but
        it doesn't touch the filesystem/partition itself.
        """
        assert self.gopro
        await self.gopro.http_command.delete_all_media()

    async def download_new_media(self, dest_dir: Path) -> list[dict[str, Any]]:
        """Download every file currently on the camera that isn't already
        present locally.

        Since each camera already gets its own subfolder (dest_dir is e.g.
        .../cam1/), files are named just "cam1.MP4" when there's a single
        clip to grab. If a camera has more than one file in this batch
        (e.g. a long take GoPro split into multiple chapters, or the SD
        card wasn't formatted between sessions), files get a numbered
        suffix ("cam1_01.MP4", "cam1_02.MP4", ...) instead of colliding on
        the same name.

        Returns one dict per downloaded file with everything useful for a
        take log: local filename/path, the camera's own original filename,
        and the file size in bytes.
        """
        assert self.gopro
        dest_dir.mkdir(parents=True, exist_ok=True)
        media = (await self.gopro.http_command.get_media_list()).data.files
        downloaded: list[dict[str, Any]] = []
        multiple = len(media) > 1
        for index, item in enumerate(media, start=1):
            suffix = Path(item.filename).suffix
            local_name = f"{self.label}_{index:02d}{suffix}" if multiple else f"{self.label}{suffix}"
            local_path = dest_dir / local_name
            if local_path.exists():
                continue
            resp = await self.gopro.http_command.download_file(
                camera_file=item.filename, local_file=local_path
            )
            result_path = resp.data
            try:
                size_bytes = result_path.stat().st_size
            except OSError:
                size_bytes = None
            downloaded.append(
                {
                    "local_filename": result_path.name,
                    "local_path": str(result_path),
                    "camera_filename": item.filename,
                    "size_bytes": size_bytes,
                }
            )
        return downloaded

    async def close(self) -> None:
        if self.gopro:
            await self.gopro.close()


class Rig:
    """Coordinates a fixed set of Camera objects as one rig."""

    def __init__(self, serials: list[str], labels: dict[str, str] | None = None):
        labels = labels or {}
        self.cameras = [Camera(s, labels.get(s)) for s in serials]

    async def connect_all(
        self, on_each_done: Callable[[Camera, Optional[BaseException]], None] | None = None
    ) -> list[Optional[BaseException]]:
        async def _connect_one(cam: Camera) -> Optional[BaseException]:
            err: Optional[BaseException] = None
            try:
                await cam.connect()
            except Exception as exc:  # noqa: BLE001
                err = exc
            if on_each_done:
                on_each_done(cam, err)
            return err

        return await asyncio.gather(*(_connect_one(c) for c in self.cameras))

    async def refresh_all_status(self) -> None:
        await asyncio.gather(*(c.refresh_status() for c in self.cameras))

    async def apply_settings_all(
        self, settings: dict[str, str]
    ) -> dict[str, dict[str, Optional[BaseException]]]:
        """Push `settings` (schema_key -> enum member name) to every
        connected camera. Returns {camera_label: {setting_key: error|None}}
        so the UI can report failures per-camera and per-setting."""

        async def _one(cam: Camera) -> tuple[str, dict[str, Optional[BaseException]]]:
            try:
                result = await cam.apply_settings(settings)
            except Exception as exc:  # noqa: BLE001
                result = {"_connection": exc}
            return cam.label, result

        pairs = await asyncio.gather(*(_one(c) for c in self.cameras if c.status.connected))
        return dict(pairs)

    async def synced_start(self) -> list[Optional[BaseException]]:
        """Arm every connected camera, then release them all at once."""
        connected = [c for c in self.cameras if c.status.connected]
        go_event = asyncio.Event()
        ready_events = [asyncio.Event() for _ in connected]
        results: list[Optional[BaseException]] = [None] * len(connected)

        async def _run(idx: int, cam: Camera, ready: asyncio.Event) -> None:
            try:
                await cam._arm_and_fire(ready, go_event)
            except Exception as exc:  # noqa: BLE001
                results[idx] = exc

        tasks = [
            asyncio.create_task(_run(i, cam, ready))
            for i, (cam, ready) in enumerate(zip(connected, ready_events))
        ]
        # Wait until every camera has confirmed it's armed and waiting...
        await asyncio.gather(*(e.wait() for e in ready_events))
        # ...then release them all in the same event loop tick.
        go_event.set()
        await asyncio.gather(*tasks)
        return results

    async def synced_stop(self) -> list[Optional[BaseException]]:
        async def _one(cam: Camera) -> Optional[BaseException]:
            try:
                await cam.stop_recording()
                return None
            except Exception as exc:  # noqa: BLE001
                return exc

        return await asyncio.gather(*(_one(c) for c in self.cameras if c.status.connected))

    async def format_all_sd_cards(self) -> list[Optional[BaseException]]:
        """Delete all media on every connected camera's SD card, in parallel.

        Refuses to touch a camera that is currently encoding (recording),
        to avoid wiping a card mid-take if this is ever triggered by
        mistake during a recording.
        """

        async def _one(cam: Camera) -> Optional[BaseException]:
            try:
                if cam.status.encoding:
                    raise RuntimeError("refusing to format while recording")
                await cam.format_sd_card()
                return None
            except Exception as exc:  # noqa: BLE001
                return exc

        return await asyncio.gather(*(_one(c) for c in self.cameras if c.status.connected))

    async def download_session(self, session_dir: Path) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[Path]] = {}

        async def _dl(cam: Camera) -> None:
            result[cam.label] = await cam.download_new_media(session_dir / cam.label)

        await asyncio.gather(*(_dl(c) for c in self.cameras if c.status.connected))
        return result

    async def close_all(self) -> None:
        await asyncio.gather(*(c.close() for c in self.cameras))

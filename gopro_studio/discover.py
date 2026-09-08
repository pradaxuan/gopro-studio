"""Discover every GoPro currently reachable over USB via mDNS.

Each HERO9+ camera enumerates over USB as its own network interface and
advertises an `_gopro-web._tcp.local.` mDNS service named after its serial
number. This module browses for that service for a few seconds and
collects every unique serial found, so you don't have to type serials by
hand for a 6-camera rig.

Note: on Linux this requires the interfaces to actually be configured with
link-local/DHCP addresses. NetworkManager normally does this automatically
for each camera's USB RNDIS interface; see the README if discovery finds
zero cameras despite `lsusb` showing all of them.
"""

from __future__ import annotations

import asyncio

import zeroconf
import zeroconf.asyncio

_SERVICE = "_gopro-web._tcp.local."


async def discover_serials(timeout: float = 6.0) -> list[str]:
    """Browse mDNS for `timeout` seconds and return every unique GoPro
    serial (well, mDNS instance name) seen."""
    found: set[str] = set()

    class _Listener(zeroconf.ServiceListener):
        def add_service(self, zc: zeroconf.Zeroconf, type_: str, name: str) -> None:
            found.add(name.split(".")[0])

        def update_service(self, *_a: object) -> None:
            pass

        def remove_service(self, *_a: object) -> None:
            pass

    with zeroconf.Zeroconf(unicast=True) as zc:
        zeroconf.asyncio.AsyncServiceBrowser(zc, _SERVICE, _Listener())
        await asyncio.sleep(timeout)

    return sorted(found)


def discover_serials_sync(timeout: float = 6.0) -> list[str]:
    return asyncio.run(discover_serials(timeout))

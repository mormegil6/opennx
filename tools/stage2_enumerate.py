#!/usr/bin/env python3
"""
stage2_enumerate.py - Stage 2: GATT enumeration.

Connects to a device by address/UUID and prints the complete GATT map: every
service, every characteristic with its properties (read/write/notify/indicate),
and the raw value of everything readable (hex + UTF-8 attempt). Descriptors are
listed too, with the Client Characteristic Configuration and User Description
read when present.

This is the full inventory of what the device exposes.

    python tools/stage2_enumerate.py <ADDRESS-OR-UUID>
    python tools/stage2_enumerate.py            # uses NX_ADDRESS below
"""

import argparse
import asyncio
import sys

from bleak import BleakClient
from bleak.uuids import normalize_uuid_str

# Pinned down in Stage 1 by the power-off differential. This is a macOS
# CoreBluetooth per-host UUID (not a MAC); it is stable for this Mac+device.
# Advertised name "Nx Tracker", service 0xA010, mfg company 0x00AE "WavesNX001".
NX_ADDRESS = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"  # set to your device address (per-Mac UUID on macOS, shown during a scan)

# A few well-known 16-bit UUIDs so the output is readable without a lookup.
KNOWN = {
    "00001800-0000-1000-8000-00805f9b34fb": "Generic Access",
    "00002a00-0000-1000-8000-00805f9b34fb": "Device Name",
    "00002a01-0000-1000-8000-00805f9b34fb": "Appearance",
    "00001801-0000-1000-8000-00805f9b34fb": "Generic Attribute",
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer Name",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model Number",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial Number",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Revision",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Revision",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Revision",
    "0000180f-0000-1000-8000-00805f9b34fb": "Battery Service",
    "00002a19-0000-1000-8000-00805f9b34fb": "Battery Level",
    "00002902-0000-1000-8000-00805f9b34fb": "Client Characteristic Config",
    "00002901-0000-1000-8000-00805f9b34fb": "Characteristic User Description",
}


def label(uuid):
    return KNOWN.get(normalize_uuid_str(uuid), "")


def show_value(raw):
    hexs = raw.hex(" ")
    try:
        txt = raw.decode("utf-8")
        printable = "".join(c if 32 <= ord(c) < 127 else "." for c in txt)
        return f"{hexs}   utf8={printable!r}   len={len(raw)}"
    except Exception:
        return f"{hexs}   len={len(raw)}"


async def main(address):
    print(f"[gatt] connecting to {address} ...")
    async with BleakClient(address, timeout=20.0) as client:
        print(f"[gatt] connected: {client.is_connected}\n")

        for service in client.services:
            slabel = label(service.uuid)
            print("=" * 78)
            print(f"SERVICE  {service.uuid}  {slabel}")
            print("=" * 78)

            for ch in service.characteristics:
                props = ",".join(ch.properties)
                clabel = label(ch.uuid)
                print(f"  CHAR   {ch.uuid}  [{props}]"
                      + (f"  {clabel}" if clabel else ""))
                print(f"         handle={ch.handle}")

                if "read" in ch.properties:
                    try:
                        raw = await client.read_gatt_char(ch)
                        print(f"         value: {show_value(raw)}")
                    except Exception as e:
                        print(f"         value: <read failed: {e}>")

                for d in ch.descriptors:
                    dlabel = label(d.uuid)
                    try:
                        raw = await client.read_gatt_descriptor(d.handle)
                        print(f"         desc {d.uuid}"
                              + (f" ({dlabel})" if dlabel else "")
                              + f": {show_value(raw)}")
                    except Exception as e:
                        print(f"         desc {d.uuid}"
                              + (f" ({dlabel})" if dlabel else "")
                              + f": <read failed: {e}>")
            print()

        print("[gatt] enumeration complete.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 2: GATT enumeration")
    ap.add_argument("address", nargs="?", default=NX_ADDRESS,
                    help="device address / CoreBluetooth UUID")
    args = ap.parse_args()
    if not args.address:
        print("Provide an address: python tools/stage2_enumerate.py <ADDRESS>")
        sys.exit(1)
    asyncio.run(main(args.address))

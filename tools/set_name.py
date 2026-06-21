#!/usr/bin/env python3
"""
set_name.py - read or change the Waves Nx tracker's advertised name.

The name lives in characteristic 0xA018 (read/write) as up to 16 bytes, null
padded. This is the same characteristic the Waves Nx app used to rename a
tracker; the new name persists across power cycles. Re-pair / re-scan after a
change to see the new advertised name.

    python tools/set_name.py <ADDRESS>                 # print current name
    python tools/set_name.py <ADDRESS> --name "Studio Nx"
"""

import argparse
import asyncio
import sys

from bleak import BleakClient

NX_ADDRESS = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
NAME_CHAR = "0000a018-5761-7665-7341-7564696f4c74"
NAME_LEN = 16


async def main(address, new_name):
    async with BleakClient(address, timeout=20.0) as client:
        current = await client.read_gatt_char(NAME_CHAR)
        print(f"current name: {current.rstrip(bytes([0])).decode('utf-8', 'replace')!r}")

        if new_name is None:
            return

        payload = new_name.encode("utf-8")[:NAME_LEN].ljust(NAME_LEN, b"\x00")
        await client.write_gatt_char(NAME_CHAR, payload, response=True)

        readback = await client.read_gatt_char(NAME_CHAR)
        name = readback.rstrip(bytes([0])).decode("utf-8", "replace")
        print(f"new name:     {name!r}")
        print("Power-cycle or re-scan the tracker to see the new advertised name.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="read or set the Nx tracker name")
    ap.add_argument("address", nargs="?", default=NX_ADDRESS)
    ap.add_argument("--name", help=f"new name (max {NAME_LEN} bytes UTF-8)")
    args = ap.parse_args()
    if not args.address:
        print("Provide an address.")
        sys.exit(1)
    asyncio.run(main(args.address, args.name))

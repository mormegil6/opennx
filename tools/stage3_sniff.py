#!/usr/bin/env python3
"""
stage3_sniff.py - Stage 3: notification sniffer.

Subscribes to EVERY characteristic that supports notify/indicate at once and
prints each incoming packet as:

    timestamp | short-id | hex bytes | length

NOTE on "short id": the task spec suggests the last 8 hex chars of the UUID, but
every custom Nx characteristic shares the SAME trailing base
(`...7564696f4c74` = "WavesAudioLt"), so the tail does not distinguish them. The
distinguishing part is the 16-bit assigned number at the FRONT (a015, a052, ...),
so that is what we print. The full UUID is printed once in the subscription list.

By default it sends NO commands first, to answer "does it stream on subscribe?".
Use --init to also write a candidate start command (Stage 5 experiments).

    python tools/stage3_sniff.py <UUID> --duration 20
    python tools/stage3_sniff.py <UUID> --duration 30 --log captures/move.txt
    python tools/stage3_sniff.py <UUID> --duration 20 --init a011:0301010000
"""

import argparse
import asyncio
import time
import sys

from bleak import BleakClient
from bleak.uuids import normalize_uuid_str

NX_ADDRESS = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"  # set to your device address (per-Mac UUID on macOS, shown during a scan)


def short_id(uuid):
    """16-bit assigned-number portion, e.g. '0000a015-....' -> 'a015'."""
    u = normalize_uuid_str(uuid)
    return u[4:8]


class Sniffer:
    def __init__(self, logfile=None):
        self.t0 = time.monotonic()
        self.count = {}          # short id -> packet count
        self.last_t = {}         # short id -> last timestamp (for rate)
        self.intervals = {}      # short id -> list of inter-packet gaps
        self.logfile = logfile

    def handle(self, sid):
        def cb(_sender, data):
            now = time.monotonic()
            t = now - self.t0
            self.count[sid] = self.count.get(sid, 0) + 1
            if sid in self.last_t:
                self.intervals.setdefault(sid, []).append(now - self.last_t[sid])
            self.last_t[sid] = now
            line = (f"{t:8.3f} | {sid} | {data.hex(' ')} | len={len(data)}")
            print(line)
            if self.logfile:
                self.logfile.write(line + "\n")
        return cb

    def summary(self):
        print("\n" + "=" * 60)
        print("SUMMARY  (packets per characteristic, approx rate)")
        print("=" * 60)
        for sid in sorted(self.count):
            n = self.count[sid]
            gaps = self.intervals.get(sid, [])
            if gaps:
                avg = sum(gaps) / len(gaps)
                rate = 1.0 / avg if avg > 0 else 0
                print(f"  {sid}: {n:5d} packets   ~{rate:6.1f} Hz   "
                      f"(avg gap {avg*1000:.1f} ms)")
            else:
                print(f"  {sid}: {n:5d} packets")
        if not self.count:
            print("  (no notifications received)")


async def main(address, duration, logpath, init_cmds):
    sniffer = None
    logfile = open(logpath, "w") if logpath else None
    try:
        print(f"[sniff] connecting to {address} ...")
        async with BleakClient(address, timeout=20.0) as client:
            print(f"[sniff] connected: {client.is_connected}")
            sniffer = Sniffer(logfile)

            # Discover every notify/indicate characteristic and subscribe.
            notifiable = []
            for service in client.services:
                for ch in service.characteristics:
                    if "notify" in ch.properties or "indicate" in ch.properties:
                        notifiable.append(ch)

            print(f"[sniff] subscribing to {len(notifiable)} characteristic(s):")
            for ch in notifiable:
                sid = short_id(ch.uuid)
                print(f"        {sid}  {ch.uuid}  [{','.join(ch.properties)}]")
            print()

            for ch in notifiable:
                try:
                    await client.start_notify(ch, sniffer.handle(short_id(ch.uuid)))
                except Exception as e:
                    print(f"[sniff] could not subscribe {short_id(ch.uuid)}: {e}")

            # Optional Stage-5 start commands: "a011:0301010000,a032:01"
            for target, payload in init_cmds:
                try:
                    full = normalize_uuid_str("0000" + target + "-5761-7665-7341-7564696f4c74")
                    await client.write_gatt_char(full, bytes.fromhex(payload),
                                                 response=True)
                    print(f"[sniff] wrote {payload} -> {target}")
                except Exception as e:
                    print(f"[sniff] write {target} failed: {e}")

            print(f"[sniff] listening {duration:.0f}s ... "
                  f"(move the tracker to correlate)\n")
            await asyncio.sleep(duration)

            for ch in notifiable:
                try:
                    await client.stop_notify(ch)
                except Exception:
                    pass

        sniffer.summary()
    finally:
        if logfile:
            logfile.close()
            print(f"\n[sniff] log written to {logpath}")


def parse_init(items):
    out = []
    for it in items or []:
        target, _, payload = it.partition(":")
        out.append((target.strip().lower(), payload.strip()))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 3: notification sniffer")
    ap.add_argument("address", nargs="?", default=NX_ADDRESS)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--log", default=None, help="write packets to this file too")
    ap.add_argument("--init", action="append",
                    help="start command(s) shortid:hex, e.g. a011:0301010000")
    args = ap.parse_args()
    if not args.address:
        print("Provide an address.")
        sys.exit(1)
    asyncio.run(main(args.address, args.duration, args.log, parse_init(args.init)))

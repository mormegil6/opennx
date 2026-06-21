#!/usr/bin/env python3
"""
stage4_decode.py - Stage 4: packet decoding & axis verification.

The Nx orientation characteristic 0xA015 streams 10-byte packets. From the
NXOSC app parser (and confirmed against live data):

    bytes [0:8] = 4 x signed int16 little-endian  -> quaternion q0..q3
    bytes [8:10]= status (calibration/quality), not used for orientation
    component value = int16 / 32767 * 2  ==  int16 / 16383.5  (Q14, |q|<=1)

This script can either:
  * stream live: connect, send start cmd, print decoded q + yaw/pitch/roll, or
  * analyse a capture log (lines "t | a015 | hexbytes | len=..") offline and
    report the min/max swing of each quantity, to isolate which axis moved.

    python tools/stage4_decode.py                 # live, ~20 s
    python tools/stage4_decode.py --duration 8
    python tools/stage4_decode.py --analyse captures/yaw.txt
"""

import argparse
import asyncio
import math
import struct
import time

from bleak import BleakClient

NX_ADDRESS = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
A011 = "0000a011-5761-7665-7341-7564696f4c74"   # control (write start)
A015 = "0000a015-5761-7665-7341-7564696f4c74"   # orientation (notify)
START_CMD = bytes.fromhex("3200000001")          # config: rate=50Hz(0x32), run=1
SCALE = 32767.0                                   # from the NXOSC binary


def decode(data):
    """Return (q0,q1,q2,q3 raw ints), (normalised quaternion), status bytes."""
    q = struct.unpack_from("<4h", data, 0)        # 4 signed int16 LE
    f = [v / SCALE * 2.0 for v in q]              # Q14 -> [-1, 1]
    n = math.sqrt(sum(c * c for c in f)) or 1.0
    fn = [c / n for c in f]
    status = data[8:10]
    return q, fn, n, status


def quat_to_ypr(w, x, y, z):
    """Quaternion (w,x,y,z) -> yaw,pitch,roll degrees, ZYX."""
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.asin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


# Mapping from the NXOSC app: output (w,x,y,z) = (q0, q2, -q1, q3).
def app_quat(fn):
    q0, q1, q2, q3 = fn
    return (q0, q2, -q1, q3)


def report(qraw, fn, status, prefix=""):
    w, x, y, z = app_quat(fn)
    yaw, pitch, roll = quat_to_ypr(w, x, y, z)
    print(f"{prefix}raw[{qraw[0]:6d} {qraw[1]:6d} {qraw[2]:6d} {qraw[3]:6d}] "
          f"q(wxyz)[{w:+.3f} {x:+.3f} {y:+.3f} {z:+.3f}] "
          f"ypr[{yaw:+7.1f} {pitch:+7.1f} {roll:+7.1f}] "
          f"st={status.hex()}")


# ---------------------------------------------------------------------------
# Offline analysis of a capture log
# ---------------------------------------------------------------------------
def analyse(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            if "| a015 |" not in line:
                continue
            hexpart = line.split("|")[2].strip()
            data = bytes.fromhex(hexpart)
            if len(data) < 8:
                continue
            qraw, fn, n, status = decode(data)
            w, x, y, z = app_quat(fn)
            yaw, pitch, roll = quat_to_ypr(w, x, y, z)
            rows.append((yaw, pitch, roll, n))
    if not rows:
        print(f"[analyse] no a015 packets in {path}")
        return
    ys = [r[0] for r in rows]; ps = [r[1] for r in rows]; rs = [r[2] for r in rows]
    ns = [r[3] for r in rows]
    def rng(v): return max(v) - min(v)
    print(f"[analyse] {path}: {len(rows)} packets")
    print(f"  yaw  : min {min(ys):+7.1f}  max {max(ys):+7.1f}  swing {rng(ys):6.1f}")
    print(f"  pitch: min {min(ps):+7.1f}  max {max(ps):+7.1f}  swing {rng(ps):6.1f}")
    print(f"  roll : min {min(rs):+7.1f}  max {max(rs):+7.1f}  swing {rng(rs):6.1f}")
    print(f"  |q|  : min {min(ns):.3f}  max {max(ns):.3f}  (should be ~1.0)")
    swings = {"yaw": rng(ys), "pitch": rng(ps), "roll": rng(rs)}
    dom = max(swings, key=swings.get)
    print(f"  --> dominant axis: {dom.upper()}  (swing {swings[dom]:.1f} deg)")


def timeline(path, bucket=1.0):
    """Print mean yaw/pitch/roll per `bucket` seconds, to correlate a narrated
    motion sequence (still -> yaw-left -> still -> pitch-up -> ...)."""
    buckets = {}
    with open(path) as fh:
        for line in fh:
            if "| a015 |" not in line:
                continue
            parts = line.split("|")
            try:
                t = float(parts[0].strip())
            except ValueError:
                continue
            data = bytes.fromhex(parts[2].strip())
            if len(data) < 8:
                continue
            _, fn, _, _ = decode(data)
            w, x, y, z = app_quat(fn)
            yaw, pitch, roll = quat_to_ypr(w, x, y, z)
            b = int(t / bucket)
            buckets.setdefault(b, []).append((yaw, pitch, roll))
    print(f"[timeline] {path}  (mean per {bucket:.0f}s)")
    print(f"  {'t(s)':>5} {'yaw':>8} {'pitch':>8} {'roll':>8}  n")
    for b in sorted(buckets):
        rows = buckets[b]
        n = len(rows)
        ya = sum(r[0] for r in rows) / n
        pa = sum(r[1] for r in rows) / n
        ra = sum(r[2] for r in rows) / n
        print(f"  {b*bucket:5.0f} {ya:+8.1f} {pa:+8.1f} {ra:+8.1f}  {n}")


# ---------------------------------------------------------------------------
# Live streaming
# ---------------------------------------------------------------------------
async def live(address, duration):
    last = [0.0]
    norms = []

    def handler(_s, data):
        qraw, fn, n, status = decode(data)
        norms.append(n)
        now = time.monotonic()
        if now - last[0] >= 0.15:           # ~6 Hz print
            last[0] = now
            report(qraw, fn, status, prefix="\r")

    print(f"[live] connecting to {address} ...")
    async with BleakClient(address, timeout=20.0) as client:
        await client.start_notify(A015, handler)
        await client.write_gatt_char(A011, START_CMD, response=True)
        print("[live] streaming. Move the tracker to verify axes.\n")
        await asyncio.sleep(duration)
        try:
            await client.stop_notify(A015)
        except Exception:
            pass
    if norms:
        print(f"\n[live] |q| over {len(norms)} packets: "
              f"min {min(norms):.3f} max {max(norms):.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 4: packet decoding")
    ap.add_argument("address", nargs="?", default=NX_ADDRESS)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--analyse", metavar="LOGFILE",
                    help="analyse a stage3 capture log instead of streaming")
    ap.add_argument("--timeline", metavar="LOGFILE",
                    help="print mean yaw/pitch/roll per second from a log")
    args = ap.parse_args()
    if args.analyse:
        analyse(args.analyse)
    elif args.timeline:
        timeline(args.timeline)
    else:
        asyncio.run(live(args.address, args.duration))

#!/usr/bin/env python3
"""
stage4_guided.py - Stage 4: voice-guided axis & sign verification.

Speaks each instruction aloud (macOS `say`) so movements line up exactly with
the data - no guessing when to move. Streams 0xA015, decodes the quaternion,
and after the run prints the mean yaw/pitch/roll change for each motion phase
(measured during the steady "hold" at the end of each phase, relative to the
still baseline). That directly reveals which physical motion drives which axis
and with what sign.

Hold/clip the tracker in its WORN orientation (as on the headphones) so device
axes match head yaw/pitch/roll.

    python tools/stage4_guided.py
    python tools/stage4_guided.py --no-voice     # print cues instead of speaking
"""

import argparse
import asyncio
import math
import os
import struct
import sys
import time

from bleak import BleakClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage4_decode import decode, quat_to_ypr, app_quat, A011, A015, START_CMD

# (label, seconds). "still"/"center" are references; the rest are measured.
PHASES = [
    ("hold still, looking forward", 5),
    ("yaw left", 4), ("center", 3),
    ("yaw right", 4), ("center", 3),
    ("pitch up", 4), ("center", 3),
    ("pitch down", 4), ("center", 3),
    ("roll right", 4), ("center", 3),
    ("roll left", 4), ("center", 3),
]
HOLD_WINDOW = 1.8   # seconds at the end of each phase used as the steady reading


async def speak(text, use_voice):
    print(f"  >>> {text}")
    if not use_voice:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "say", "-r", "210", text,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
    except Exception:
        pass


async def main(address, use_voice, logpath):
    samples = []        # (t, yaw, pitch, roll)
    t0 = time.monotonic()

    def handler(_s, data):
        if len(data) < 8:
            return
        _, fn, _, _ = decode(data)
        w, x, y, z = app_quat(fn)
        yaw, pitch, roll = quat_to_ypr(w, x, y, z)
        samples.append((time.monotonic() - t0, yaw, pitch, roll))

    print(f"[guided] connecting to {address} ...")
    async with BleakClient(address, timeout=20.0) as client:
        await client.start_notify(A015, handler)
        await client.write_gatt_char(A011, START_CMD, response=True)
        await speak("Hold the tracker as worn. Starting in 3, 2, 1.", use_voice)

        schedule = []   # (label, t_start, t_end)
        for label, dur in PHASES:
            tstart = time.monotonic() - t0
            await speak(label, use_voice)
            await asyncio.sleep(dur)
            tend = time.monotonic() - t0
            schedule.append((label, tstart, tend))
        await speak("done", use_voice)

        try:
            await client.stop_notify(A015)
        except Exception:
            pass

    if logpath:
        with open(logpath, "w") as fh:
            for t, y, p, r in samples:
                fh.write(f"{t:.3f},{y:.3f},{p:.3f},{r:.3f}\n")
        print(f"[guided] {len(samples)} samples -> {logpath}")

    # Mean over the steady hold window at the end of each phase.
    def mean_window(t_end):
        rows = [s for s in samples if t_end - HOLD_WINDOW <= s[0] <= t_end]
        if not rows:
            return None
        n = len(rows)
        return (sum(r[1] for r in rows) / n,
                sum(r[2] for r in rows) / n,
                sum(r[3] for r in rows) / n)

    # Baseline = first "still" phase.
    base = mean_window(schedule[0][2]) or (0, 0, 0)

    def d180(a):  # wrap delta into [-180,180]
        return (a + 180) % 360 - 180

    print("\n" + "=" * 74)
    print(f"AXIS / SIGN RESULTS  (delta vs baseline yaw={base[0]:+.1f} "
          f"pitch={base[1]:+.1f} roll={base[2]:+.1f})")
    print("=" * 74)
    print(f"  {'motion':<14} {'d_yaw':>8} {'d_pitch':>8} {'d_roll':>8}   dominant")
    for label, _ts, te in schedule:
        if label in ("center", "hold still, looking forward"):
            continue
        m = mean_window(te)
        if not m:
            print(f"  {label:<14}  (no samples)")
            continue
        dy, dp, dr = d180(m[0] - base[0]), d180(m[1] - base[1]), d180(m[2] - base[2])
        dom = max((("yaw", dy), ("pitch", dp), ("roll", dr)), key=lambda kv: abs(kv[1]))
        print(f"  {label:<14} {dy:+8.1f} {dp:+8.1f} {dr:+8.1f}   "
              f"{dom[0]} {'+' if dom[1] >= 0 else '-'}")
    print("=" * 74)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 4: voice-guided axis test")
    ap.add_argument("address", nargs="?", default=None)
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--log", default="captures/guided.csv")
    args = ap.parse_args()
    from stage4_decode import NX_ADDRESS
    addr = args.address or NX_ADDRESS
    asyncio.run(main(addr, not args.no_voice, args.log))

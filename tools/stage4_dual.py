#!/usr/bin/env python3
"""
stage4_dual.py - Stage 4: axis/sign mapping against the MMRL tracker.

Streams the Waves Nx AND an Mbientlab MMRL (MetaWear) at the same time. With
both trackers mounted together (aligned) and software-tared at neutral, a shared
physical rotation should produce matching head-frame motion. MMRL's bridge is
known-good, so its yaw/pitch/roll is the (noisy) ground truth; we read off how
the Nx quaternion must be mapped to match it.

Voice cues (macOS `say`) drive the movements so they line up with the data.

    python tools/stage4_dual.py
    python tools/stage4_dual.py --no-voice

Mount the Nx and MMRL together pointing the same way (both 'up' up, both
'forward' forward) before running.
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
from stage4_decode import decode as nx_decode, quat_to_ypr, app_quat, \
    A011, A015, START_CMD, NX_ADDRESS

# --- MMRL / MetaWear (from mmrl_osc.py) ---
MMRL_ADDRESS   = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"  # set to your device address (per-Mac UUID on macOS, shown during a scan)
MW_CMD_CHAR    = "326a9001-85cb-9195-d9dd-464cfbbae75a"
MW_NOTIFY_CHAR = "326a9006-85cb-9195-d9dd-464cfbbae75a"
FUSION_START_SEQ = [
    bytearray([0x19, 0x02, 0x01, 0x13]),
    bytearray([0x03, 0x03, 0x28, 0x0c]),
    bytearray([0x13, 0x03, 0x28, 0x00]),
    bytearray([0x15, 0x04, 0x04, 0x0e]),
    bytearray([0x15, 0x03, 0x02]),
    bytearray([0x03, 0x02, 0x01, 0x00]),
    bytearray([0x13, 0x02, 0x01, 0x00]),
    bytearray([0x15, 0x02, 0x01, 0x00]),
    bytearray([0x03, 0x01, 0x01]),
    bytearray([0x13, 0x01, 0x01]),
    bytearray([0x15, 0x01, 0x01]),
    bytearray([0x19, 0x03, 0x08, 0x00]),
    bytearray([0x19, 0x01, 0x01]),
    bytearray([0x19, 0x07, 0x01]),
]

PHASES = [
    ("hold still, looking forward", 5),
    ("yaw left", 4), ("center", 3),
    ("yaw right", 4), ("center", 3),
    ("pitch up", 4), ("center", 3),
    ("pitch down", 4), ("center", 3),
    ("roll right", 4), ("center", 3),
    ("roll left", 4), ("center", 3),
]
HOLD_WINDOW = 1.8


def qconj(q): w, x, y, z = q; return (w, -x, -y, -z)
def qmul(a, b):
    aw, ax, ay, az = a; bw, bx, by, bz = b
    return (aw*bw-ax*bx-ay*by-az*bz, aw*bx+ax*bw+ay*bz-az*by,
            aw*by-ax*bz+ay*bw+az*bx, aw*bz+ax*by-ay*bx+az*bw)
def qnorm(q):
    n = math.sqrt(sum(c*c for c in q)) or 1.0
    return tuple(c/n for c in q)


async def speak(text, use_voice):
    print(f"  >>> {text}")
    if not use_voice:
        return
    try:
        p = await asyncio.create_subprocess_exec(
            "say", "-r", "210", text,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.wait()
    except Exception:
        pass


async def main(use_voice, logpath):
    nx_q = [None]      # latest raw normalised Nx quaternion in app (w,x,y,z) order
    mm_q = [None]      # latest MMRL quaternion (w,x,y,z)
    samples = []       # (t, nx(wxyz), mm(wxyz))
    t0 = time.monotonic()

    def nx_handler(_s, data):
        if len(data) < 8:
            return
        _, fn, _, _ = nx_decode(data)
        nx_q[0] = qnorm(app_quat(fn))

    def mm_handler(_s, data):
        if len(data) >= 18 and data[0] == 0x19 and data[1] == 0x07:
            w, x, y, z = struct.unpack_from("<ffff", data, 2)
            mm_q[0] = (w, x, y, z)

    print("[dual] connecting to Nx and MMRL ...")
    async with BleakClient(NX_ADDRESS, timeout=20.0) as nx, \
               BleakClient(MMRL_ADDRESS, timeout=20.0) as mm:
        print("[dual] both connected. Starting streams ...")
        await nx.start_notify(A015, nx_handler)
        await nx.write_gatt_char(A011, START_CMD, response=True)
        await mm.start_notify(MW_NOTIFY_CHAR, mm_handler)
        for cmd in FUSION_START_SEQ:
            await mm.write_gatt_char(MW_CMD_CHAR, cmd, response=False)
            await asyncio.sleep(0.05)

        await asyncio.sleep(1.0)
        await speak("Hold the pair still. Taring now.", use_voice)
        await asyncio.sleep(1.5)
        nx_ref = nx_q[0]
        mm_ref = mm_q[0]
        if nx_ref is None or mm_ref is None:
            print(f"[dual] missing data (nx={nx_ref}, mm={mm_ref}). "
                  "Are both awake/streaming?")
            return
        print(f"[dual] tared. nx_ref={tuple(round(v,3) for v in nx_ref)} "
              f"mm_ref={tuple(round(v,3) for v in mm_ref)}")

        # Sampling loop in the background while the voice script runs.
        stop = asyncio.Event()

        async def sampler():
            while not stop.is_set():
                if nx_q[0] and mm_q[0]:
                    dq_nx = qmul(qconj(nx_ref), nx_q[0])
                    dq_mm = qmul(qconj(mm_ref), mm_q[0])
                    samples.append((time.monotonic() - t0, dq_nx, dq_mm))
                await asyncio.sleep(0.02)

        samp_task = asyncio.create_task(sampler())
        schedule = []
        for label, dur in PHASES:
            ts = time.monotonic() - t0
            await speak(label, use_voice)
            await asyncio.sleep(dur)
            schedule.append((label, ts, time.monotonic() - t0))
        await speak("done", use_voice)
        stop.set()
        await samp_task

        for c, ch in ((nx, A015), (mm, MW_NOTIFY_CHAR)):
            try:
                await c.stop_notify(ch)
            except Exception:
                pass

    if logpath:
        with open(logpath, "w") as fh:
            fh.write("t,nxw,nxx,nxy,nxz,mmw,mmx,mmy,mmz\n")
            for t, a, b in samples:
                fh.write(f"{t:.3f}," + ",".join(f"{v:.4f}" for v in a+b) + "\n")
        print(f"[dual] {len(samples)} samples -> {logpath}")

    def window(te):
        return [s for s in samples if te - HOLD_WINDOW <= s[0] <= te]

    print("\n" + "=" * 80)
    print("PER-PHASE: MMRL ypr (ground truth)  vs  Nx delta-quaternion vector part")
    print("=" * 80)
    print(f"  {'motion':<12} | {'MMRL  yaw   pitch   roll':<26} | "
          f"{'Nx dq  x       y       z':<24}")
    for label, _ts, te in schedule:
        if label in ("center", "hold still, looking forward"):
            continue
        rows = window(te)
        if not rows:
            print(f"  {label:<12} | (no samples)")
            continue
        n = len(rows)
        mm = tuple(sum(r[2][i] for r in rows)/n for i in range(4))
        nx = tuple(sum(r[1][i] for r in rows)/n for i in range(4))
        myaw, mpit, mrol = quat_to_ypr(*mm)
        print(f"  {label:<12} | {myaw:+7.1f} {mpit:+7.1f} {mrol:+7.1f}       | "
              f"{nx[1]:+7.3f} {nx[2]:+7.3f} {nx[3]:+7.3f}")
    print("=" * 80)
    print("Read: which Nx vector component (x/y/z) tracks each MMRL axis, and sign.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Stage 4: dual Nx+MMRL axis mapping")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--log", default="captures/dual.csv")
    args = ap.parse_args()
    asyncio.run(main(not args.no_voice, args.log))

#!/usr/bin/env python3
"""
opennx.py - OpenNx: an open OSC bridge for the Waves Nx Head Tracker.

Connects to a Waves Nx Head Tracker over BLE (bleak: CoreBluetooth on macOS,
BlueZ on Linux, WinRT on Windows), starts its orientation stream, and sends the
head-tracking quaternion as OSC for IEM SceneRotator, SPARTA/Atmoky (/ypr) and
APL Virtuoso.

The only ready-made OSC bridge, NXOSC (https://audiooo.com/nxosc), is a
third-party macOS-only app, and Waves has discontinued its own Nx applications.
This is an independent reimplementation that speaks the Nx GATT protocol directly.
It is built on bleak and developed and tested on macOS; Windows and Linux are
untested. The protocol was reverse-engineered from the device and is documented in
docs/PROTOCOL.md.

Requires: bleak, python-osc  (pip install bleak python-osc)
"""

import argparse
import asyncio
import math
import signal
import struct
import sys
import time

from bleak import BleakScanner, BleakClient
from pythonosc.udp_client import SimpleUDPClient


# ---------------------------------------------------------------------------
# Waves Nx GATT protocol (see docs/PROTOCOL.md)
# ---------------------------------------------------------------------------
# Custom 128-bit UUIDs share the base "...-5761-7665-7341-7564696f4c74"
# ("WavesAudioLt"). Orientation streams from NX_DATA_CHAR; streaming is armed by
# writing a start command to NX_CTRL_CHAR.
NX_SERVICE   = "0000a010-5761-7665-7341-7564696f4c74"
NX_DATA_CHAR = "0000a015-5761-7665-7341-7564696f4c74"  # orientation (notify)
NX_CTRL_CHAR = "0000a011-5761-7665-7341-7564696f4c74"  # control point (write)
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"  # standard battery level

# Control point 0xA011 takes a 5-byte config (the official app's sendIMUParams):
#   [rate_Hz, standby, 0x00, identify, run]
#     rate_Hz   output rate in Hz (default 50; verified live: 25/50/100 map 1:1)
#     standby   standby/sleep timeout byte (default 0)
#     identify  1 = blink the LED red ~10x to locate the unit (verified)
#     run       1 = start streaming, 0 = stop
# NXOSC's "32 00 00 00 01" is just this with rate=50 (0x32) and run=1. The device
# does NOT stream on connect and needs no keep-alive once started.
DEFAULT_RATE = 50


def build_config(rate=DEFAULT_RATE, standby=0, identify=0, run=1):
    """Build the 5-byte 0xA011 control-point payload."""
    return bytes([rate & 0xFF, standby & 0xFF, 0x00,
                  1 if identify else 0, 1 if run else 0])

# Orientation packet: 10 bytes = 4x int16 LE quaternion + 2 status bytes.
# Each component = raw / 32767 * 2  (Q14-ish, |q| <= 1). ~50 Hz.
QUAT_SCALE = 32767.0

# Names the tracker may advertise (renameable in the Nx app); also matched by
# the advertised service UUID 0xA010, which is the reliable signal.
NAME_HINTS = ("nx tracker", "nx head tracker", "waves nx", "wavesnx")


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
osc = None                # SimpleUDPClient, created in run()
tare_quat = None          # offset quaternion (w, x, y, z) applied to output, or None
tare_request = False      # set by the Enter key
last_print = 0.0          # last terminal update time (5 Hz throttle)


# ---------------------------------------------------------------------------
# Quaternion math
# ---------------------------------------------------------------------------
def quat_conjugate(q):
    """Conjugate (the inverse for a unit quaternion)."""
    w, x, y, z = q
    return (w, -x, -y, -z)


def quat_multiply(a, b):
    """Hamilton product a * b."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def quat_to_ypr(q):
    """Convert a quaternion (w, x, y, z) to yaw/pitch/roll in degrees (ZYX)."""
    w, x, y, z = q

    # roll (rotation about x)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # pitch (rotation about y), clamped to avoid NaN at the poles
    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)

    # yaw (rotation about z)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def decode_packet(data):
    """Decode a 10-byte 0xA015 notification into a head-frame quaternion.

    Layout: 4x int16 LE quaternion q0..q3, then 2 status bytes (ignored).
    Raw -> float via /32767*2, then the verified head-frame remap
        q_head = (q0, q2, -q3, -q1)
    which makes standard ZYX yaw/pitch/roll come out with yaw-left negative and
    pitch-up positive (see docs/PROTOCOL.md). Returns a normalised (w,x,y,z), or
    None if the packet is too short.
    """
    if len(data) < 8:
        return None
    q0, q1, q2, q3 = (v / QUAT_SCALE * 2.0 for v in struct.unpack_from("<4h", data, 0))
    return quat_normalize((q0, q2, -q3, -q1))


# ---------------------------------------------------------------------------
# Quaternion handling: tare, OSC output, terminal display
# ---------------------------------------------------------------------------
def process_quaternion(q):
    """Apply tare, send OSC, and update the terminal line."""
    global tare_quat, tare_request, last_print

    # Tare: store the current orientation as the zero reference.
    if tare_request:
        tare_quat = q
        tare_request = False
        print("\n[tare] heading zeroed")

    # output = inverse(reference) * current
    if tare_quat is not None:
        q = quat_multiply(quat_conjugate(tare_quat), q)

    qw, qx, qy, qz = q
    yaw, pitch, roll = quat_to_ypr(q)

    osc.send_message("/SceneRotator/quaternions", [qw, qx, qy, qz])  # IEM Plugin Suite
    osc.send_message("/ypr", [yaw, pitch, roll])                     # SPARTA/Atmoky/dearVR
    osc.send_message("/Virtuoso/quat", [qw, qx, qy, qz])             # APL Virtuoso

    # Update the terminal at ~5 Hz.
    now = time.monotonic()
    if now - last_print >= 0.2:
        last_print = now
        print(f"\r  yaw {yaw:+7.1f}  pitch {pitch:+7.1f}  roll {roll:+7.1f}   ",
              end="", flush=True)


def notification_handler(_sender, data):
    """Decode an orientation notification and forward it."""
    q = decode_packet(data)
    if q is not None:
        process_quaternion(q)


# ---------------------------------------------------------------------------
# Scanning and device selection
# ---------------------------------------------------------------------------
async def scan_and_pick(scan_time, show_all=False):
    """Scan for Waves Nx devices and return the chosen address/UUID.

    Matches on the advertised 0xA010 service UUID or a known name. With show_all,
    lists every BLE device when no Nx is found.
    """
    while True:
        print(f"[scan] scanning {scan_time:.0f}s for Waves Nx devices...")
        discovered = await BleakScanner.discover(timeout=scan_time, return_adv=True)
        items = list(discovered.values())

        def is_nx(dev, adv):
            name = (adv.local_name or dev.name or "").lower()
            if any(h in name for h in NAME_HINTS):
                return True
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            return NX_SERVICE.lower() in uuids

        found = [(d, a) for (d, a) in items if is_nx(d, a)]

        # --all fallback: list everything, strongest signal first.
        if not found and show_all:
            print("[scan] no Nx match; listing ALL devices (--all).")
            found = sorted(items, key=lambda da: -(da[1].rssi or -999))

        if not found:
            print("[scan] no Waves Nx devices found.")
            print("       Wake the tracker (press its button; the LED should blink),")
            print("       make sure it isn't connected to the Waves Nx app, and that")
            print("       it's charged. Use --all to list every BLE device.")
            choice = input("Press Enter to rescan, or 'q' to quit: ").strip().lower()
            if choice == "q":
                return None
            continue

        print("\nFound devices:")
        for i, (d, a) in enumerate(found):
            name = a.local_name or d.name or "(no name)"
            print(f"  [{i}] {name:<18} {d.address}   rssi {a.rssi}")

        sel = input("\nSelect device number (r=rescan, q=quit): ").strip().lower()
        if sel == "q":
            return None
        if sel == "r":
            continue
        if sel.isdigit() and int(sel) < len(found):
            return found[int(sel)][0].address
        print("Invalid selection.")


# ---------------------------------------------------------------------------
# Streaming session (one connection); returns on disconnect so the caller retries
# ---------------------------------------------------------------------------
async def stream(address, rate=DEFAULT_RATE, standby=0, identify=0):
    """Connect, read battery, start the orientation stream, run until dropped."""
    disconnected = asyncio.Event()

    def on_disconnect(_client):
        print("\n[ble] disconnected")
        disconnected.set()

    async with BleakClient(address, disconnected_callback=on_disconnect) as client:
        print(f"[ble] connected to {address}")

        # Battery: raw byte. The device reports an uncalibrated value that can
        # exceed 100 (see docs/PROTOCOL.md), so show raw and a clamped estimate.
        try:
            raw = await client.read_gatt_char(BATTERY_CHAR)
            b = raw[0]
            print(f"[battery] raw {b}" +
                  (f"  (~{min(b, 100)}%)" if b <= 100 else "  (>100, uncalibrated)"))
        except Exception as e:
            print(f"[battery] unavailable ({e})")

        # Subscribe first, then arm streaming with the requested config.
        await client.start_notify(NX_DATA_CHAR, notification_handler)
        await client.write_gatt_char(
            NX_CTRL_CHAR, build_config(rate, standby, identify, run=1), response=True)

        print(f"[nx] streaming orientation (~{rate} Hz).")
        print("     Press Enter to tare (zero the heading).  Ctrl-C to quit.\n")

        try:
            await disconnected.wait()       # until the device drops or this is cancelled
        finally:
            # Best-effort: stop streaming and unsubscribe.
            if client.is_connected:
                try:
                    await client.write_gatt_char(
                        NX_CTRL_CHAR, build_config(rate, standby, identify, run=0),
                        response=True)
                    await client.stop_notify(NX_DATA_CHAR)
                except Exception:
                    pass


async def tare_listener():
    """Request a tare on each Enter keypress."""
    global tare_request
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":             # EOF (stdin not a TTY): stop listening
            return
        tare_request = True


async def run(address, port, rate=DEFAULT_RATE, standby=0, identify=0):
    """Maintain the connection, reconnecting every 3 s on drop."""
    global osc
    osc = SimpleUDPClient("127.0.0.1", port)
    print(f"[osc] sending to 127.0.0.1:{port}  "
          f"(/SceneRotator/quaternions, /ypr, /Virtuoso/quat)")

    # Cancel on SIGINT/SIGTERM so the teardown runs and the sensor is released.
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except (NotImplementedError, RuntimeError):
            pass

    tare_task = asyncio.create_task(tare_listener())
    try:
        while True:
            try:
                await stream(address, rate, standby, identify)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"\n[ble] connection error: {e}")
            print("[ble] reconnecting in 3 s...")
            await asyncio.sleep(3)
    finally:
        tare_task.cancel()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OpenNx - Waves Nx Head Tracker OSC bridge (bleak; tested on macOS)")
    parser.add_argument("--device", metavar="ADDR",
                        help="BLE address / CoreBluetooth UUID to connect to "
                             "(skips scanning)")
    parser.add_argument("--port", type=int, default=8000,
                        help="OSC UDP port on localhost (default: 8000)")
    parser.add_argument("--scan-time", type=float, default=8.0,
                        help="BLE scan duration in seconds (default: 8)")
    parser.add_argument("--all", action="store_true", dest="show_all",
                        help="if no Nx is found, list all BLE devices to pick from")
    parser.add_argument("--rate", type=int, default=DEFAULT_RATE, metavar="HZ",
                        help=f"orientation output rate in Hz (default: {DEFAULT_RATE})")
    parser.add_argument("--standby", type=int, default=0, metavar="N",
                        help="standby-timeout byte written to the device (default: 0)")
    parser.add_argument("--identify", action="store_true",
                        help="blink the tracker LED (red, ~10x) to locate it, on connect")
    args = parser.parse_args()

    async def main_async():
        address = args.device
        if not address:
            address = await scan_and_pick(args.scan_time, show_all=args.show_all)
            if not address:
                print("No device selected. Exiting.")
                return
        await run(address, args.port, args.rate, args.standby, args.identify)

    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[exit] stopping and disconnecting...")


if __name__ == "__main__":
    main()

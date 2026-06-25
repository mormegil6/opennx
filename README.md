[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)]() [![bleak](https://img.shields.io/badge/bleak-BLE-1F6FEB.svg)]() [![python-osc](https://img.shields.io/badge/python--osc-OSC-1F6FEB.svg)]() [![macOS](https://img.shields.io/badge/macOS-tested-000000.svg?logo=apple&logoColor=white)]() [![Windows | Linux](https://img.shields.io/badge/Windows%20%7C%20Linux-untested-lightgrey.svg)]() [![Device](https://img.shields.io/badge/device-Waves%20Nx%20%C2%B7%20nRF51822%20%2B%20BNO055-8A2BE2.svg)]() [![Protocol](https://img.shields.io/badge/protocol-reverse--engineered-007808.svg)](docs/PROTOCOL.md) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

# OpenNx - an open OSC bridge for the Waves Nx Head Tracker

**OpenNx** lets you use a **Waves Nx Head Tracker** as a head tracker for spatial
audio. It is built on bleak, so it is meant to be cross-platform, but so far it is
**developed and tested on macOS (Apple Silicon) only** - Windows and Linux are
untested (see [Future work](#future-work)).

> **There's a GUI app: Busola** ([GitLab](https://git.pg.edu.pl/p829296/busola-app) / [GitHub](https://github.com/mormegil6/busola-app)). A menu-bar app that drives this tracker - plus MetaMotion RL, Supperware and MrHeadTracker - with device memory, live profile-switching and CSV logging. This repo is the bridge it's built on.

`opennx.py` connects to the Nx tracker over Bluetooth LE, starts its on-board
orientation stream, and sends the head-tracking orientation as OSC to a chosen
spatial-audio renderer. Each renderer wants a particular OSC address, argument
order, per-axis sign convention and UDP port, so OpenNx uses selectable
**profiles** (`--profile`) rather than a few fixed addresses.

<details>
<summary>Supported renderers (19 profiles; click to expand, or run <code>--list-profiles</code>)</summary>

| Profile | OSC address | Default port |
|---|---|---|
| `IEM SceneRotator (quaternion)` (default) | `/SceneRotator/qw,qx,qy,qz` | 9000 |
| `IEM SceneRotator (YPR)` | `/SceneRotator/yaw,pitch,roll` | 9000 |
| `SPARTA` | `/ypr` | 9000 |
| `APL Virtuoso` | `/Virtuoso/quat` | 8000 |
| `Dolby Atmos Renderer` | `/ypr` | 8000 |
| `dearVR` | `/ypr` | 7001 |
| `EAR Production Suite` | `/ypr` | 8000 |
| `Mach1 Monitor` | `/orientation` | 9898 |
| `Nuendo (HeadPose 25Hz)` | `/head_pose` | 7000 |
| `SPAT Revolution` | `/room/1/ypr` | 8000 |
| `Quaternion (generic)` | `/quaternion` | 8000 |
| `YPR (generic)` | `/ypr` | 8000 |
| `a1Rotate` | `/yaw,pitch,roll` | 9001 |
| `Ambi Head HD` | `/yaw,pitch,roll` | 4040 |
| `Audio Brewers` | `/yaw,pitch,roll` | 8585 |
| `DaVinci Resolve` | `/ypr` | 8000 |
| `Genelec Aural ID` | `/euler_x,euler_y,euler_z` | 5005 |
| `Mach1 VideoPlayer` | `/orientation` | 9902 |
| `Spatial Audio Designer` | `/yaw` | 7000 |

</details>

Each profile applies the correct address, per-axis sign/swap and port. Axis and
sign conventions are verified against
[Supperware Bridgehead](https://supperware.co.uk/headtracker-bridgehead)'s
profile list. You can add your own in `~/Library/Application
Support/opennx/profiles.txt` (four lines per profile - name, address, args,
port - in Supperware's `Profiles.txt` format); `opennx.py` loads them on top of
the built-in profiles. The profile set is shared verbatim with mmrl-osc and Busola.

**Protocol:** the full reverse-engineered BLE protocol and hardware notes are in
[docs/PROTOCOL.md](docs/PROTOCOL.md).

OpenNx ships two scripts: `opennx.py` (the bridge) and `osc_monitor.py` (a test
listener for the OSC output).

## Why this exists

The only ready-made OSC bridge, [NXOSC](https://audiooo.com/nxosc), is a
third-party **macOS-only** app by Katsuhiro Chiba. The official Waves Nx desktop
app supported two tracking modes, selectable in software: webcam-based optical
tracking and this Bluetooth hardware tracker. Waves has now
[discontinued the Waves Nx applications entirely](https://www.waves.com/support/waves-nx-applications-discontinued),
leaving the tracker with no maintained, cross-platform software. This project
talks to the tracker's Bluetooth GATT interface directly with
[`bleak`](https://github.com/hbldh/bleak), so it can run on any platform bleak
supports (CoreBluetooth on macOS, BlueZ on Linux, WinRT on Windows) and needs no
Waves software at all. (Verified on macOS so far; see [Future work](#future-work).)

The protocol was reverse-engineered from the device; the full write-up is in
[docs/PROTOCOL.md](docs/PROTOCOL.md) so it can be reimplemented in any language.

## What was discovered (short version)

- The tracker advertises service **`0xA010`** (128-bit base spells
  `WavesAudioLt`). Orientation streams from characteristic **`0xA015`**.
- Streaming is armed by a **5-byte config** written to control point **`0xA011`**:
  `[rate_Hz, standby, 0x00, identify, run]`. So `32 00 00 00 01` is just
  **rate 50, run 1** (the `0x32` is the rate, not an opcode). Rate is verified
  live: bytes 25/50/100 give 24.7/49.6/96.7 Hz. It does **not** stream on connect
  and needs no keep-alive.
- Each notification is **10 bytes**: a **quaternion as 4x int16 LE**
  (`value = raw / 32767 * 2`, roughly Q14, normalised to unit length) plus a
  constant 2-byte marker (`00 03`; not live status - verified invariant).
- Verified axis/sign convention (tracker worn): **yaw-left = negative**,
  **pitch-up = positive**, roll right-ear-down = negative.
- Battery (`0x2A19`) is exposed raw and is **uncalibrated** (can read >100).
- The physical button is not a BLE input: pressing it produces no notification on
  any characteristic (verified by probing every notify/indicate char while
  pressing). It only wakes the device from sleep, so taring is keyboard-only.

Full details, including the head-frame quaternion remap and example packets, are
in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Addressing

On macOS, CoreBluetooth addresses peripherals by a stable **per-Mac UUID**, not
by their MAC address, so pass that UUID to `--device`. On Linux/Windows the
address is the device **MAC**. Either way the value is printed during the scan.

## Features

- Scan and pick the Nx tracker (by name or its `0xA010` service), or connect
  directly by address with `--device`.
- Battery level read on connect (raw; see the quirk above).
- Orientation quaternion streaming, default 50 Hz, selectable with `--rate`.
- Selectable per-renderer OSC profiles (`--profile` / `--list-profiles`); several
  at once with port-collision detection.
- Yaw/pitch/roll shown in the terminal at about 5 Hz.
- Tare (zero the heading) with the **Enter** key.
- Locate the unit by blinking its LED red (`--identify`).
- Auto-reconnect every 3 s if the link drops.
- Clean shutdown on Ctrl-C or kill (SIGTERM): stops streaming before
  disconnecting.

## Usage (from source)

Requires Python 3.9 or newer.

```bash
python3 -m venv opennx-venv
source opennx-venv/bin/activate            # Windows: opennx-venv\Scripts\activate
pip install -r requirements.txt

python opennx.py                           # scan, then pick a device by number
```

Skip the scan with a known address/UUID (printed during the scan):

```bash
python opennx.py --device XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX   # macOS UUID
python opennx.py --profile SPARTA          # emit the SPARTA profile (port 9000)
python opennx.py --profile "APL Virtuoso" --port 8001   # override the port
python opennx.py --list-profiles           # show every profile and exit
python opennx.py --rate 100                # request 100 Hz instead of 50
python opennx.py --all                     # list all BLE devices if no Nx is found
```

Choosing a renderer:

- `--profile NAME` picks the OSC profile (default `IEM SceneRotator (quaternion)`).
  Names match case-insensitively and by unique substring, so `--profile sparta`
  or `--profile virtuoso` work; `--list-profiles` prints them all.
- The profile sets the address, mapping **and port**; `--port` is an optional
  override of that port.
- `--profile` can be repeated to drive several renderers at once. If two selected
  profiles share a port they would collide on the same UDP socket, so OpenNx
  refuses and lists the clash; give them different ports or pass
  `--force-collision` to send anyway.

`--rate HZ` sets the output rate (verified 25-100 Hz; the device caps at ~100).
`--identify` blinks the tracker LED red (~10 times) on connect to locate the
unit. `--standby N` writes the standby-timeout byte; it is decoded from the
official app but its effect is not separately verified here.

Point the renderer's OSC receive at `127.0.0.1` on the profile's port (shown by
`--list-profiles`). Press **Enter** while looking forward to zero the heading.

### Testing without a plugin

`osc_monitor.py` prints whatever arrives on a port:

```bash
python osc_monitor.py --port 8000          # in a second terminal
```

### Renaming the tracker

The advertised name is stored in characteristic `0xA018` and can be changed,
which matters once the Waves Nx app (the usual way to rename) is gone:

```bash
python tools/set_name.py <ADDRESS>                  # print current name
python tools/set_name.py <ADDRESS> --name "Studio Nx"
```

The new name persists on the device; re-scan to see it advertised.

## Bluetooth permission (macOS)

The first BLE scan triggers a permission prompt for the app running it (Terminal,
iTerm, VS Code, or a built binary). Allow it. If scanning finds nothing, check
System Settings > Privacy & Security > Bluetooth.

## Troubleshooting

- **Scan finds nothing:** the Nx only advertises when it is awake (press its
  button, the blue LED blinks; it sleeps quickly when still) and not connected
  to the Waves Nx app. Quit the Waves Nx app first. Use `--all` to list every
  BLE device.
- **Connected but no angles:** the tracker needs the start command, which the
  bridge sends automatically (`32 00 00 00 01` to `0xA011`). If you still see
  nothing, the device may have dropped, and it auto-reconnects every 3 s.
- **Plugin does not move:** check the OSC port and that the plugin listens on the
  matching address. IEM SceneRotator uses `/SceneRotator/quaternions`.
- **Wrong rotation axis or direction:** the convention is verified for the
  normal worn orientation. If you mount the tracker differently, adjust the
  remap in `decode_packet()` (see [docs/PROTOCOL.md](docs/PROTOCOL.md)).
- **Disconnects often:** keep the tracker charged and within a few metres of the
  computer; the bridge reconnects automatically.

## Future work

- **Windows and Linux testing.** OpenNx uses bleak (WinRT on Windows, BlueZ on
  Linux), so it should run there, but it has only been verified on macOS (Apple
  Silicon). Testing and fixes on other platforms are welcome.
- **Standalone app with a GUI.** Package OpenNx as a self-contained, double-click
  application (e.g. PyInstaller) with a small GUI for device selection, tare, rate,
  and a live yaw/pitch/roll readout, so it needs no Python install or command line.
- **Host-side fusion (VQF).** The tracker only emits a fused quaternion; running a
  host-side filter would require custom firmware to stream raw IMU data. See
  [docs/HOST_VQF.md](docs/HOST_VQF.md).

## Reverse-engineering tools

The `tools/` directory holds the staged scripts used to work out the protocol:
device discovery, GATT enumeration, a notification sniffer, packet decoding, a
voice-guided axis-verification tool, and a name-setter. They are not needed to
run the bridge but document how every finding was obtained. See
[docs/PROTOCOL.md](docs/PROTOCOL.md).

## Files

| File | Purpose |
|---|---|
| `opennx.py` | the head tracker bridge |
| `profiles.py` | OSC renderer profiles (shared file, kept in sync with mmrl-osc) |
| `osc_monitor.py` | OSC listener for testing |
| `requirements.txt` | bleak, python-osc |
| `docs/PROTOCOL.md` | full reverse-engineered protocol |
| `docs/HOST_VQF.md` | host-side VQF / raw-data pathway notes (exploratory) |
| `tools/` | staged reverse-engineering scripts |

## References

- [Waves Nx Head Tracker user guide](https://assets.wavescdn.com/pdf/hardware/nx-head-tracker-user-guide.pdf) (official) - pairing, button/LED behaviour, charging, renaming.
- [Waves Nx applications discontinued](https://www.waves.com/support/waves-nx-applications-discontinued) (Waves support notice).
- [NXOSC](https://audiooo.com/nxosc) - third-party macOS-only OSC bridge by Katsuhiro Chiba.
- [docs/PROTOCOL.md](docs/PROTOCOL.md) - the reverse-engineered BLE protocol and hardware notes.
- [docs/HOST_VQF.md](docs/HOST_VQF.md) - notes on a host-side VQF / raw-data pathway (exploratory).

## Related projects

Part of a set of open head-tracking tools for spatial audio:

- **Busola** ([GitLab](https://git.pg.edu.pl/p829296/busola-app) / [GitHub](https://github.com/mormegil6/busola-app)) - the menu-bar **app**: one GUI for several head trackers (MetaMotion RL, Waves Nx, Supperware, MrHeadTracker), with device discovery, remembered devices, live profile-switching and CSV logging - the conveniences these CLI bridges leave out
- **mmrl-osc** ([GitLab](https://git.pg.edu.pl/p829296/mmrl-osc) / [GitHub](https://github.com/mormegil6/mmrl-osc)) - Mbientlab MetaMotion RL -> OSC head tracker with host-side VQF sensor fusion
- **jabra-elite10-re** ([GitLab](https://git.pg.edu.pl/p829296/jabra-elite10-re) / [GitHub](https://github.com/mormegil6/jabra-elite10-re)) - Jabra Elite 10 Gen 2 BLE GATT protocol reverse-engineering (head-tracking service + Fast Pair auth)

## License

MIT. See [LICENSE](LICENSE). Independent, clean-room reimplementation for
interoperability; not affiliated with or endorsed by Waves Audio Ltd.

## Contact

Bartłomiej Mróz · bartlomiej.mroz@pg.edu.pl · Department of Multimedia Systems, Gdańsk University of Technology · [bmroz.eu](https://bmroz.eu)

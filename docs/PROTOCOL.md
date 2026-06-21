# Waves Nx Head Tracker BLE GATT Protocol

Reverse-engineered protocol for the **Waves Nx Head Tracker** so the device can
be used without NXOSC (the third-party macOS-only bridge by Katsuhiro Chiba) or
the now-discontinued official Waves Nx app. All
findings below were obtained empirically with `bleak` on macOS (Apple Silicon,
macOS 14) and are documented with the raw evidence that produced them.

> Status: **verified.** Device identity, GATT map, start/tare commands, packet
> format, scale, axis/sign convention and update rate are all confirmed against
> live data and physical motion. Battery encoding is the one documented quirk.

---

## Device identity

| Field | Value |
|-------|-------|
| Advertised name | `Nx Tracker` (renameable via the Nx app) |
| Advertised service | `0xA010` (`0000a010-0000-1000-8000-00805f9b34fb`) |
| Manufacturer data | company `0x00AE`, payload `57617665734e5830303100` = ASCII `WavesNX001` |
| Manufacturer Name (0x2A29) | `Waves Audio` |
| Model Number (0x2A24) | `1` |
| Hardware Revision (0x2A27) | `v4.4` |
| Firmware Revision (0x2A26) | `v100` |
| Software Revision (0x2A28) | `A v1.17 B v1.13` |

The device unit tested reports the above. Firmware/hardware revisions may differ
on other units.

### Addressing
- macOS addresses BLE peripherals by a per-host **CoreBluetooth UUID**, not a MAC
  address. The unit tested enumerates as `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`
  on this Mac; this value will differ on other machines. On Linux/Windows the
  address is the device MAC.
- The tracker **sleeps when left still** and stops advertising; press its button
  (blue LED blinks) to wake it before scanning/connecting.

---

## GATT map

All custom 128-bit UUIDs share the base `0000XXXX-5761-7665-7341-7564696f4c74`.
The base bytes `57 61 76 65 73 41 75 64 69 6f 4c 74` are ASCII **`WavesAudioLt`**
(Waves Audio Ltd). Below, custom characteristics are written by their 16-bit
short code (`0xA015` etc.); expand with the base to get the full UUID.

### Service 0xA010: orientation + control (advertised)
| Char | Handle | Properties | Purpose | Initial value |
|------|--------|------------|---------|---------------|
| `0xA015` | 13 | notify, read | **Orientation stream** (10-byte quaternion packets, ~50 Hz) | `00 00 00 00 00 00 01 00 ff ff` (idle) |
| `0xA011` | 16 | write, read | **Control point** (5-byte rate/standby/identify/run config) | idle value `03 01 0a 00 00` |
| `0xA018` | 18 | notify, write, read | Device name (write to rename) | `"Nx Tracker"` padded to 16 bytes |

### Service 0xA050: status
| Char | Handle | Properties | Purpose (working hypothesis) | Initial value |
|------|--------|------------|------------------------------|---------------|
| `0xA051` | 22 | write, read | (unknown, empty) | `` (len 0) |
| `0xA052` | 24 | notify, read | Button / status (1 byte) | `00` |

### Service 0xA030: command channel
| Char | Handle | Properties | Purpose (working hypothesis) |
|------|--------|------------|------------------------------|
| `0xA031` | 28 | notify, write | command response / notifications |
| `0xA032` | 31 | write-without-response | command input (high throughput) |

### Service 0x180F: Battery
| Char | Handle | Properties | Notes |
|------|--------|------------|-------|
| `0x2A19` Battery Level | 34 | notify, read | Read `0x72` = **114**, which is outside the standard 0-100 % range; interpretation TBD, flagged as a quirk. |

### Service 0x180A: Device Information
Standard read-only strings; see the Device identity table above.

---

## Control command (write to 0xA011)

The tracker does **not** stream on connect; `0xA015` stays silent (even while
moving) until a config is written. There is **one** control write: a **5-byte
config** to `0xA011`. It is not an opcode + args; every byte is a field. This is
the official Waves Nx app's `sendIMUParams` (in `libBLEManager`), and what NXOSC
sends too:

```
offset  field      meaning
0       rate       output rate in Hz          (default 0x32 = 50)
1       standby    standby/sleep timeout byte  (default 0x00)
2       0x00       reserved / constant
3       identify   1 = blink LED red ~10x      (default 0x00; locate the unit)
4       run        1 = start streaming, 0 = stop
```

So the familiar `32 00 00 00 01` is simply **rate 50 Hz, run 1** - the `0x32`
that looks like an opcode is just the rate (decimal 50). Common writes:

| Intent | Bytes |
|--------|-------|
| Start at 50 Hz | `32 00 00 00 01` |
| Start at 100 Hz | `64 00 00 00 01` |
| Start at 25 Hz | `19 00 00 00 01` |
| Stop streaming | `<rate> 00 00 00 00` |
| Identify (blink) | `<rate> 00 00 01 01` |

**Rate is verified live**: writing rate bytes 100 / 25 / 50 produced 96.7 / 24.7 /
49.6 Hz on `0xA015`. The default 0x32 (50) matches the measured default rate. The
field is a single byte, but the **effective ceiling is ~100 Hz**: requests of
150 / 200 / 255 all delivered ~100 Hz (BLE/firmware limited), so 100 is the
practical max. **`identify` is verified**: writing `identify = 1` makes the LED
blink red ~10 times (a "locate this unit" feature), without interrupting the
stream. `standby` is decoded from `sendIMUParams` but its effect is unconfirmed:
writing nonzero values did not change the data stream, and `standby = 5` with the
stream stopped did not sleep or drop a held connection within 50 s (so it is not
a seconds counter, and it does not govern connected-idle sleep observably here).

**No raw-sensor mode.** Every config tested (all rates, and nonzero standby /
identify / reserved bytes) produced the same 10-byte quaternion packet. There is
no accel/gyro/magnetometer or other raw-IMU output: the sensor fusion runs on
the device (consistent with the official app, whose BLE layer only ever consumes
a quaternion). The 2 trailing bytes are a constant `00 03` (a fixed marker, not
live temperature or calibration - see the packet-format section), the only
non-orientation payload.

NXOSC's "Calibrate" button writes `... 00` then `... 01`, i.e. it just toggles
`run` off then on (a stream restart). That is why it does **not** re-zero the
heading.

Field provenance: the 5-byte layout and defaults are from the official Waves Nx
app's `libBLEManager` (`-[IMUController sendIMUParams:]`, `setDefaultParams`,
`setTrackRate:`, `setStandByTimeOut:`, `toggleIdentification:`); the literal
`32 00 00 00 01` / `... 00` bytes also appear in the third-party `Nxosc.app`
(by Katsuhiro Chiba, <https://audiooo.com/nxosc>).

**No keep-alive is needed.** Once streaming, data flows continuously for as long
as the connection is held with no further writes (verified over >50 s).

**No hardware heading-zero.** There is no command that re-zeros yaw; the
"calibrate" restart above does not. A stationary tracker reports a non-zero yaw
(~+173 deg). Pitch and roll are gravity-referenced (absolute, ~0 deg when level);
**yaw is a free-running relative heading**. A bridge that wants a
"look-forward = 0" reference must apply its own **software tare** (store the
current quaternion, output `inverse(ref) * current`).

## Other operations

These are not orientation commands but complete the picture (all from the
official Waves Nx app's `libBLEManager`):

| Operation | How | Characteristic |
|-----------|-----|----------------|
| Rename device | write raw UTF-8 name bytes (`setDeviceName:`) | `0xA018` |
| Read firmware version | GATT read (`getFirmwareVersion:`) | `0x2A26` ("v100") |
| Read SD/SW version | GATT read (`getSDFWVersion:`) | `0x2A28` ("A v1.17 B v1.13", two components) |
| Read / subscribe battery | read + notify (`getBatteryLevel:`) | `0x2A19` (raw byte, see quirk) |
| Firmware update (OTA) | multi-step block transfer | `0xA030` / `0xA031` / `0xA032` |

The official app builds an internal characteristic table in
`didDiscoverCharacteristics`; index 0 is the `0xA011` control point (where the
config write goes) and index 1 (`0xA015`) is subscribed for orientation. The
OTA path is fully present in `libBLEManager` (`updatePeripheralFirmware`,
`doOTAU`) but is out of scope here and untouched. No firmware download URL is
hard-coded in the app; the firmware images ship with the desktop installer and
land locally at `/Library/Application Support/Waves/Firmware/`
(`IMU-Rev1_app_FW` ~38 KB application + `IMU-Rev1_bl_FW` ~22 KB bootloader, ARM
Cortex-M, dated 2017). Flashing them is the one operation that can brick the
device, so it is intentionally not implemented here.

---

## Orientation packet format (0xA015 notifications)

Each notification is **10 bytes** at **~50 Hz** (measured 496 packets / 9.9 s):

```
offset  bytes        meaning
0..1    int16 LE     q0   (quaternion component, signed)
2..3    int16 LE     q1
4..5    int16 LE     q2
6..7    int16 LE     q3
8..9    uint8 x2     constant marker, always 00 03 (not orientation; see below)
```

- Each component is **Q14-ish fixed point**: `value = raw_int16 / 32767 * 2`
  (about `raw / 16384`), giving unit-quaternion components in [-1, 1]. The divisor
  `32767.0` and the `*2` are exactly what the NXOSC binary does
  (`scvtf; fdiv #32767; fadd self`). Norm of the decoded quaternion is **1.000**
  across all captured packets.
- The trailing `00 03` bytes are not used for orientation by the NXOSC parser
  (loop reads exactly 4 int16), and they are **constant**: verified invariant at
  `00 03` across 6687 captured packets spanning stillness, vigorous motion, and a
  deliberate BNO055 calibration routine (gyro-still, 6-face accel, figure-8 mag)
  during which the quaternion swept the full range. They therefore carry **no live
  temperature and no live calibration status** - a live `CALIB_STAT` would have
  changed during that routine - and appear to be a fixed marker/format field. The
  BNO055's `TEMP` (reg `0x34`) and `CALIB_STAT` (reg `0x35`) exist on-chip but are
  not forwarded over BLE; reading them would need custom firmware (see HOST_VQF.md).

Example packet `f5 da 44 0a 2c f7 67 32 00 03`:
`q = (-0.579, 0.160, -0.138, 0.788)`, norm `1.000`.

### Raw quaternion to axis convention
The device's native quaternion order is `[q0, q1, q2, q3]` with `q0` the scalar
(w). The verified mapping to a head-frame quaternion `q_head = (w, x, y, z)` that
yields the required Euler signs is:

```
q_head = ( q0,  q2,  -q3,  -q1 )
```

(Equivalently: the NXOSC app's intermediate remap is `(q0, q2, -q1, q3)`;
applying a further +90 deg basis rotation about X gives the head frame above.)

Then standard **ZYX** `quat_to_ypr(q_head)` gives yaw/pitch/roll in degrees.

### Axis / sign convention (verified against physical motion)
Captured with the tracker mounted in its worn orientation and moved on cue
(cross-checked against an MMRL tracker mounted alongside):

| Physical motion | Output axis | Sign |
|-----------------|-------------|------|
| **yaw left** | yaw | **negative** (yaw right gives positive) |
| **pitch up** | pitch | **positive** (pitch down gives negative) |
| **roll right** (right ear down) | roll | negative (roll left gives positive) |

yaw-left = negative and pitch-up = positive match the target requirement. Roll
sign is fixed by keeping a right-handed, quaternion-consistent frame (so the
quaternion and Euler outputs agree); right-ear-down comes out negative.

---

## Battery (0x2A19): quirk

`0x2A19` reads a single byte and also notifies roughly every **5 s**. The
official Waves Nx app's `libBLEManager` `handleBatteryValue:` reads the **first
byte and forwards it raw, with no scaling** (`movb (ptr); movzbl; callback`), so the
protocol exposes the device's raw byte and any "%" is a later UI choice.

Observed on the test unit: **~115 at the start of a session, dropping to ~108
after ~20 min of heavy use**, i.e. a real battery metric that *drains*, but
**outside the standard 0-100 % range** (full reads >100; exact full/empty
calibration unknown). A bridge should surface the raw value and treat any
percentage as approximate (e.g. clamp the display to 100). It also fires
unsolicited notifications ~every 5 s.

### Button / status characteristics
The physical button does **not** emit BLE notifications: tapping it during
streaming produced nothing on `0xA052` or `0xA031` (and did not interrupt the
`0xA015` stream). So there is **no device-button tare**; use a software tare.
`0xA030/0xA031/0xA032` and `0xA050/0xA051/0xA052` are used by the official Waves
Nx app for OTA firmware update / device control, not orientation.

---

## Summary for implementers

1. Scan, connect (CoreBluetooth UUID on macOS; MAC elsewhere).
2. Subscribe (enable notifications) on `0xA015`.
3. Write the 5-byte config `[rate, 0, 0, 0, 1]` to `0xA011` to start streaming
   (`32 00 00 00 01` = 50 Hz).
4. For each 10-byte notification: read 4x int16 LE into `q0..q3`,
   `value = raw/32767*2`, normalise.
5. `q_head = (q0, q2, -q3, -q1)`; apply software tare; `quat_to_ypr` (ZYX).
6. No keep-alive required. On shutdown, optionally write `[rate, 0, 0, 0, 0]`
   (run = 0) to stop.

---

## Reproduction

```bash
python tools/stage1_scan.py             # discover; power-off differential to ID the unit
python tools/stage2_enumerate.py <UUID> # full GATT dump
python tools/stage3_sniff.py <UUID> --init a011:3200000001 --log captures/x.txt
python tools/stage4_decode.py --analyse captures/x.txt
python tools/stage4_dual.py             # voice-guided Nx+MMRL axis verification
```

---

## Hardware (reverse-engineered from the firmware)

The firmware images shipped with the desktop app were disassembled (read-only
static analysis; nothing was modified or flashed).

- **MCU: Nordic nRF51822** (ARM Cortex-M0). The app image loads at flash
  `0x00020000` with initial SP `0x20004000` (16 KB RAM); the DFU bootloader is at
  `0x0003C000` - a standard nRF51 SoftDevice + app + bootloader layout. Image
  integrity is a CRC16 in the 23-byte header (`CImageHeader_t`); no cryptographic
  signature was found. Version fields decode as app `0x0113` (1.19) / bootloader
  `0x010d` (1.13); the bootloader matches the device's reported "B v1.13", while
  the device's app reads "A v1.17" (either a pending update or a version-string
  rendering difference). The firmware images are **byte-identical (same SHA-256)
  across the Windows 1.0.18 (2015) and macOS 1.0.25 (2017) installers**, so this
  app 1.19 / bl 1.13 build is the only firmware that exists for the unit.
- **IMU: Bosch BNO055** on the nRF51 I2C/TWI bus. Identified from: the on-chip
  fusion quaternion format (4x int16, 2^14 LSB = Q14) matching the BNO055
  quaternion registers `0x20-0x27` exactly; the chip-id constant `0xA0`; and BNO055
  `OPR_MODE` register/value patterns in the driver. The BNO055 runs its own NDOF
  fusion; the nRF51 reads its quaternion registers and forwards them over BLE,
  followed by a constant 2-byte marker (`00 03`; not a live status - see the
  orientation-packet section). (The trailing `0x03` happens to coincide with a
  plausible `CALIB_STAT` value but is constant, so it is not used as ID evidence.)

### Implication for raw IMU data
Because fusion happens on the BNO055 and the nRF51 only forwards the quaternion,
**raw gyro/accel/mag are not available over the stock BLE protocol**. The BNO055
itself exposes raw data (accel `0x08`, mag `0x0E`, gyro `0x14`, or raw-only
`OPR_MODE = AMG 0x07`), so getting raw data - e.g. to run a host-side filter such
as VQF - would require custom nRF51 firmware that reads those registers and
streams them. The nRF51 is reflashable via SWD or the OTA-DFU path
(`0xA030-0xA032`); the CRC-only image and SoftDevice-preserving DFU make that
feasible, but it is out of scope here and is the one operation that can brick the
unit. A pathway sketch for raw data + a host-side filter is in `HOST_VQF.md`.

## Notes & attribution

- Findings were obtained with `bleak` on macOS 15 (Apple Silicon) against a unit
  reporting HW `v4.4` / FW `v100`. Other firmware/hardware revisions may differ.
- Two distinct binaries were used as cross-references. They are separate programs
  by different authors; do not conflate them:
  - **`Nxosc.app`** (`/Applications/Nxosc.app`): a third-party, macOS-only OSC
    bridge by Katsuhiro Chiba, <https://audiooo.com/nxosc>. Source of the
    orientation decode: the `32767`/`*2` quaternion scale, the `(q0, q2, -q1, q3)`
    intermediate remap, and the `32 00 00 00 01` start / `caribButtonPressed`
    calibrate bytes.
  - **`libBLEManager.dylib`**: a private library bundled inside, and loaded by,
    the **official Waves Nx app** (`/Applications/WavesNx/Waves Nx.app`, bundle id
    `waves.Waves-Nx`). It is the app's Bluetooth layer (class `IMUController`;
    exports `BLEScan`, `BLEConnectDevice`, `BLEGetBatteryLevel`,
    `BLEGetFirmwareVersion`, `BLESetIMUName`, `BLEUpdateFirmware`,
    `startReceivingIMUDataFromDevice:`). Source of the raw battery-byte handling
    and the OTA/control role of the `0xA030`/`0xA050` services. The sibling
    `libHeadTrackerLib.dylib` in the same bundle is the separate OpenCV
    webcam-tracking engine; the official app offers webcam and Bluetooth tracking
    as selectable sensor types (`HeadTrackerSetSensorType`).
- The axis/sign convention was verified empirically against physical motion (it
  was not taken from either binary).
- Waves discontinued the Waves Nx applications, leaving the tracker without
  maintained software: <https://www.waves.com/support/waves-nx-applications-discontinued>
- This is an independent, clean-room interoperability description. Not affiliated
  with or endorsed by Waves Audio Ltd. MIT-licensed (see `../LICENSE`).

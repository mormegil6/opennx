# Host-side VQF: a possible pathway (notes)

Status: **exploratory, not implemented.** This documents how one *could* run a
host-side orientation filter such as VQF on this tracker, and why it is not
possible with the stock device. It is a research note, not a feature.

## Why it is not possible out of the box

The tracker's orientation is computed *inside the IMU* (Bosch BNO055, on-chip
NDOF sensor fusion). The nRF51822 host MCU only reads the BNO055's quaternion
(plus a calibration-status byte) and forwards it over BLE. The stock BLE protocol
exposes **no raw gyro/accel/mag** - confirmed: no control-config value changes
the 10-byte quaternion packet. See the Hardware section of `PROTOCOL.md`.

So a host-side filter has nothing to filter: there is no raw data to feed it.

## What VQF needs

VQF (Laidig & Seel, 2022) estimates orientation from raw **gyroscope +
accelerometer**, optionally **+ magnetometer**, at a steady, timestamped rate. To
run it on the laptop those raw signals must be streamed from the device.

## Pathway (requires custom firmware)

1. **Custom nRF51 firmware.** Put the BNO055 into a raw mode (`OPR_MODE = AMG`,
   `0x07`) or read the raw registers in NDOF, and stream gyro (`0x14`), accel
   (`0x08`) and mag (`0x0E`) over a BLE notify characteristic, with a sample
   counter or timestamp.
2. **Flash it.** Two routes:
   - **SWD (wired debug probe):** full control; back up the existing flash first.
     Watch for nRF51 readback protection (APPROTECT) and the SoftDevice, which is
     not in the bundled firmware images.
   - **OTA-DFU (`0xA030-0xA032`):** replaces only the app, leaving the SoftDevice
     and bootloader intact (recoverable). Requires reverse-engineering the Waves
     DFU framing plus a valid `CImageHeader_t` + CRC16.
3. **Host pipeline.** Read the raw stream, feed VQF (Python `vqf` package, or port
   the reference C), get a quaternion, and reuse this bridge's tare + OSC output.

## Risk / effort / payoff

- **Effort:** a genuine embedded project (firmware + flashing + host pipeline).
- **Risk:** flashing is the only brick-risk step; it is lowest via OTA-DFU (the
  SoftDevice is untouched and the bootloader is re-enterable). Wired SWD is safest
  when a debug probe and a flash backup are available.
- **Payoff:** raw 9-DOF at up to ~100 Hz (BLE-limited) with full control of the
  fusion (drift, magnetic-disturbance handling, tuning) instead of the BNO055
  black box.

## Lower-effort middle grounds

- Keep the BNO055 quaternion and post-process on the host (extra smoothing, yaw
  drift handling, fusing the tracker with another source). No firmware change.
- The BNO055 has selectable fusion modes (IMU without mag vs NDOF with mag).
  Switching those is still a firmware change, but far smaller than full raw
  streaming + host-side VQF.

## Bottom line

Technically feasible, but it is firmware work with real (if low) brick risk, and
the stock quaternion stream is already good. Worth it only if you specifically
want host-controlled fusion. The safe first step is building and validating the
host VQF pipeline against recorded raw data before touching any firmware.

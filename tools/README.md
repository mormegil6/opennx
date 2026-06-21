# Reverse-engineering tools

Staged scripts used to work out the Waves Nx BLE protocol. They are **not needed
to run the bridge** (`../opennx.py`); they document how each finding in
[../docs/PROTOCOL.md](../docs/PROTOCOL.md) was obtained, and are handy for
re-verifying on another unit. The hard-coded device address in each script is
the author's macOS CoreBluetooth UUID; pass your own as an argument (most
scripts accept one) or edit the constant.

| Script | Stage | What it does |
|--------|-------|--------------|
| `stage1_scan.py` | 1 | Scan BLE, print name / address / RSSI / service UUIDs / manufacturer data. Power-off differential identifies the tracker. |
| `stage2_enumerate.py` | 2 | Connect and dump the full GATT map: services, characteristics, properties, readable values, descriptors. |
| `stage3_sniff.py` | 3 | Subscribe to every notify/indicate characteristic and log `timestamp \| short-id \| hex \| len`. `--init a011:3200000001` arms streaming; `--log` writes a capture. |
| `stage4_decode.py` | 4 | Decode `0xA015` packets to quaternion + yaw/pitch/roll. Live, or `--analyse`/`--timeline` over a stage3 log. |
| `stage4_guided.py` | 4 | Voice-guided (macOS `say`) single-device axis/sign test; auto-summarises each motion phase. |
| `stage4_dual.py` | 4 | Streams Nx + an MMRL tracker together (voice-guided) to cross-check the axis mapping against a known-good tracker. |
| `set_name.py` | - | Read or change the tracker's advertised name (characteristic `0xA018`). |

## Typical flow

```bash
python stage1_scan.py
python stage2_enumerate.py <ADDRESS>
python stage3_sniff.py <ADDRESS> --init a011:3200000001 --log ../captures/move.txt
python stage4_decode.py --timeline ../captures/move.txt
python stage4_guided.py            # or stage4_dual.py for the MMRL cross-check
```

Captures are written to `../captures/` (git-ignored).

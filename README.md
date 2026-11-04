# AT6301 Bench Supply

Windows desktop controller + logger for the Abestop AT6301 (60 V / 10 A / 300 W) bench power supply.
Live V/I/P readout and rolling plot, voltage / current-limit / output control, CSV logging with phase marks.

The Python itself is cross-platform; only the installer and shortcuts are Windows-specific.

## Install
Run `install.bat`. Needs [uv](https://astral.sh/uv) on PATH (it creates a private venv under
`%LOCALAPPDATA%\Programs\at6301-bench-supply`, adds Start Menu + Desktop shortcuts).

Uninstall: `uninstall.bat` (also copied into the install dir).

Run from source without installing: `uv run --with pyserial --with matplotlib python at6301_gui.py`

## Protocol
SCPI over the rear USB port (CH340, 115200 8N1, `\n` terminated). Verified commands:

| Command | Purpose |
|---|---|
| `*IDN?` | `Abestop,AT6301,<serial>,FV:V6.0.0` |
| `MEAS:ALL?` | `<V>,<I>` live readback (~18 Hz max) |
| `MEAS:VOLT?` `MEAS:CURR?` `MEAS:POW?` | individual readbacks |
| `VOLT <v>` / `VOLT?` | voltage setpoint |
| `CURR <i>` / `CURR?` | current limit |
| `OUTP ON|OFF` / `OUTP?` | output enable |
| `SYST:ERR?` | error word |

OVP / OCP are **not** settable over serial on firmware V6.0.0 - set them on the front panel.
Set commands return nothing; query the setpoint to confirm.

## Log format
CSV, UTF-8, two `#` header lines then:

```
timestamp_iso, t_s, phase, voltage_V, current_A, power_W, note
```

`phase` is whatever label was last applied with **Mark**; a `MARK` row is written at each change.

`at6301_log.py` is the headless CLI version of the same logger (`--set`, `--limit`, `--on`, type a label + Enter to mark).

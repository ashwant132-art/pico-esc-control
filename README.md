# Pico ESC Control

Control an ESC's motor speed from a PC, using a Raspberry Pi Pico as the PWM
signal generator. Works with **any ESC that accepts a standard PWM throttle
signal** — BLHeli_S, BLHeli_32 (in PWM mode), SimonK, brushed-motor ESCs, and
so on. Includes Pico firmware and a desktop app with a throttle slider,
hold-to-ramp buttons, a calibration panel, and a built-in console

##Installation

Grab the exe from the releases and run it this will be your GUI

For installation on pico:

Grab the uf2 file from releases then restart your pico while holding bootsel then put the firmware file in the RPI-RP2 drive

No Arduino IDE, no board packages, nothing installed locally. If you'd rather
build it yourself instead (e.g. after editing the sketch), see
[Building the firmware manually](#building-the-firmware-manually) below.

## Wiring

- Pico **GP17** → ESC signal wire
- Pico **GND** → ESC GND wire
- Do **not** connect the ESC's 5V BEC output to the Pico
- ESC power comes from a battery or bench supply, never from USB

Take the propeller off before testing anything here.

## The app

`app/esc_control.py` — a desktop app: throttle slider, hold-to-ramp buttons
(with an adjustable ramp rate), a calibration panel, and a console that
doubles as a full CLI.

```
pip install pyserial
python app/esc_control.py
```

To build a standalone Windows `.exe`, run `app/build_exe.bat` on Windows (it
needs Python for Windows installed; the script bootstraps `pip` and
PyInstaller itself, and closes any already-running copy of the exe before
rebuilding it).

### Features

- **Throttle slider** — click, drag, or scroll to set 0–100%. Buttons for
  ±1/±5 and jump-to 0/25/50/75/100.
- **Hold-to-ramp** — hold the ▲/▼ buttons (or keyboard R/F) to ramp throttle
  up or down smoothly at an adjustable rate (%/second), release to hold
  wherever it stopped.
- **STOP** — cuts throttle immediately (Space or Esc also work).
- **Calibration panel** — set and apply the pulse widths for 0% (off), 1%
  (where the motor starts), and 100% (where speed stops increasing), plus the
  ramp-limit and failsafe timeout. A raw-pulse test box lets you probe a
  specific microsecond value directly.
- **Save to Pico** — writes calibration to the Pico's flash so it survives a
  power cycle; **Defaults** restores the factory values.
- **Live status** — connection state, actual vs. target pulse width, and
  whether the current settings are saved, modified, or still factory default.
- **Console / CLI** — every Pico command below works from the input box at the
  bottom. `ports`, `connect [PORT]`, `disconnect`, `clear`, and `quit`/`exit`
  are handled locally; everything else is sent straight to the Pico.

## Serial command reference

Talk to the Pico directly from the app's console, or any serial terminal at
115200 baud with `\n` line endings:

```
<0-100>        set throttle % (silent, used by the app)
s <0-100>      set throttle % (with confirmation)
us <800-2200>  set a raw pulse width in microseconds (debug)
stop           cut throttle immediately
status         print current state
min <us>       set the 0% (off) pulse - default 1000
start <us>     set the 1% pulse = where the motor starts - default 1187
max <us>       set the 100% pulse = where speed stops rising - default 1830
slew <1-100>   ramp limit, % per 20 ms - default 1
timeout <ms>   failsafe time, 200-60000, 0 = disable - default 1000
debug on|off   stream status twice a second
save           store min/start/max/slew/timeout in flash (kept after power-off)
defaults       restore factory values, erase what was saved
arm            hold the min pulse for 3 s (use right after ESC power-up)
ping           refresh the failsafe timer (used internally by the app)
reboot         restart the Pico
bootsel        restart into USB bootloader mode (for reflashing)
```

The Pico expects a `ping` (or any command) at least once a second, or it cuts
throttle to 0 as a failsafe — the app does this automatically in the
background. Use `timeout 0` to disable that if you're typing commands by hand
in a plain serial terminal.

## Building the firmware manually

### Option A: Arduino IDE

1. Install the [Arduino IDE](https://www.arduino.cc/en/software) (2.x).
2. **File → Preferences** → in "Additional boards manager URLs" add:
   ```
   https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json
   ```
3. **Tools → Board → Boards Manager**, search "pico", install **Raspberry Pi
   Pico/RP2040** (by Earle F. Philhower, III).
4. **File → Open**, pick `firmware/pico_esc_throttle/pico_esc_throttle.ino`.
5. **Tools → Board**, select **Raspberry Pi Pico**.
6. Either:
   - Plug the Pico in (hold BOOTSEL on first connect) and hit **Upload** to
     flash it directly, or
   - **Sketch → Export Compiled Binary** to produce a `.uf2` in the sketch
     folder without flashing anything — drag that onto the Pico's `RPI-RP2`
     drive later, same as the CI-built one.

### Option B: arduino-cli (command line)

Same toolchain the CI workflow uses, if you'd rather script it or don't want
the full IDE installed.

1. Install [arduino-cli](https://arduino.github.io/arduino-cli/latest/installation/).
2. Add the board index and install the core:
   ```bash
   arduino-cli config init --additional-urls https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json
   arduino-cli core update-index
   arduino-cli core install rp2040:rp2040
   ```
3. Compile:
   ```bash
   arduino-cli compile --fqbn rp2040:rp2040:rpipico \
     --output-dir build \
     firmware/pico_esc_throttle
   ```
   `build/pico_esc_throttle.ino.uf2` is your flashable file — drag it onto the
   Pico's `RPI-RP2` drive (hold BOOTSEL while plugging in to get that drive to
   show up).
4. Or flash directly over USB without a manual BOOTSEL step, using
   [picotool](https://github.com/raspberrypi/picotool):
   ```bash
   arduino-cli upload --fqbn rp2040:rp2040:rpipico:uploadmethod=picotool \
     -p <serial-port> \
     firmware/pico_esc_throttle
   ```
   `<serial-port>` is the Pico's port when it's already running the current
   firmware (e.g. `/dev/ttyACM0` on Linux, `COM3` on Windows) — `picotool`
   reboots it into bootloader mode automatically.

## Repo layout

```
firmware/pico_esc_throttle/       Pico sketch
app/esc_control.py                desktop app
app/build_exe.bat                 Windows PyInstaller build script
.github/workflows/build-uf2.yml   CI: compiles the sketch, publishes a .uf2
```

## License

See LICENSE.

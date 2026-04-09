# PC Oscilloscope Viewer (Python)

This script opens a desktop oscilloscope window that periodically sends the MCU `capture` command, redraws the plot when a full frame arrives, and includes a basic serial terminal.

## 1) Install dependencies

```bash
python3 -m pip install pyserial matplotlib
```

## 2) Run

```bash
python3 pc_oscilloscope.py --port /dev/ttyACM0 --baud 115200 --samples 500 --sample-rate 0 --channel voltage
```

If you omit `--port`, the script tries to auto-detect a serial device.

The app exposes text fields for:

- sample count
- sample rate (`0` means auto from DDS)
- capture channel
- DDS frequency on a logarithmic slider from `100 Hz` to `100 kHz`

Use `Apply / Capture` to update the auto-capture request immediately.

By default the script repeatedly sends one of these commands:

```text
capture <samples> <sample-rate> <channel>
capturepair <samples> <sample-rate>
```

with a `100 ms` gap between completed requests.

Before each manual capture or measurement, the app sends `dds <freq> 1` first.
During auto-capture, it sends the DDS command once when the frequency changes, then reuses that setting for later frames.

Selecting `both` in the channel field switches the app to `capturepair` mode and overlays voltage/current traces on the same plot.

The right side of the window also includes:

- a serial terminal log showing both `TX>` and `RX>` lines
- a command entry field that sends on `Enter`
- a `Send` button for manual serial commands
- a simple `Model` readout showing `Resistive`, `Series RL`, or `Series RC`

## 3) Expected serial format

The script expects MCU replies like:

```text
sample_rate_hz,1000000
index,value
0,1234
1,1228
...
99,1190
```

The `sample_rate_hz` line lets the viewer convert sample index to real time (`Time (s)` on x-axis).
Other non-CSV lines are ignored.

## Useful options

- `--samples 500` number of samples requested in each `capture`
- `--sample-rate 0` requested MCU capture rate in Hz, or `0` to auto-select from DDS
- `--channel voltage` capture mode: `voltage`, `current`, `both`, `v`, or `i`
- `--request-interval-ms 100` delay between automatic capture requests
- `--refresh-ms 50` UI update period
- `--vref 3.3` ADC reference voltage
- `--adc-max 4095` maximum ADC code (12-bit)

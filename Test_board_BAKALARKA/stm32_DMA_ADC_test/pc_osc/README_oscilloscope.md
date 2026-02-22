# PC Oscilloscope Viewer (Python)

This script receives ADC samples from STM32 over serial and redraws the plot every time a full frame arrives.

## 1) Install dependencies

```bash
python3 -m pip install pyserial matplotlib
```

## 2) Run

```bash
python3 pc_oscilloscope.py --port /dev/ttyACM0 --baud 115200 --samples 100
```

If you omit `--port`, the script tries to auto-detect a serial device.

## 3) Expected serial format

The script is built for output like:

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

- `--refresh-ms 50` UI update period
- `--vref 3.3` ADC reference voltage
- `--adc-max 4095` maximum ADC code (12-bit)

# STM32F103 LCR Meter Firmware

This project contains firmware for an STM32F103C8-based LCR meter implemented primarily with the Arduino framework and selective STM32 HAL use where timing-sensitive peripherals need tighter control.

The current firmware focuses on acquisition and hardware control:

- synchronous burst capture of the AC measurement channels
- slow sampling of the DC offset channels
- DDS source control
- amplitude and offset control
- shunt and PGA control
- status LEDs
- serial command interface

The firmware is intended to be programmed with ST-Link.

## Project Purpose

The instrument measures impedance by observing voltage and current signals from the analog frontend.

Two analog channels carry the AC measurement waveforms:

- `PA1` = voltage highpass
- `PA4` = current highpass

These two channels must be sampled synchronously so that voltage/current phase information is preserved.

Two additional analog channels are used for slow offset monitoring:

- `PA2` = voltage lowpass
- `PA3` = current lowpass

All analog signals are biased around approximately `1.65 V`.

## Firmware Structure

The firmware is split into a few focused modules:

- `src/main.cpp`
  Starts the system, initializes modules, prints the startup banner, and runs the main loop.
- `src/acquisition_engine.cpp`
  Implements synchronous burst acquisition of the two AC channels using STM32F1 dual-ADC mode, DMA, and timer triggering.
- `src/source_control.cpp`
  Controls the AD9837 DDS, MCP4561 digipot, offset PWM, shunt selection, and PGA selection lines.
- `src/status_leds.cpp`
  Drives the NeoPixels and the heartbeat LED.
- `src/command_interface.cpp`
  Exposes the serial CLI using `SimpleCLI`.
- `include/*.h`
  Shared declarations and board configuration.

## Hardware Mapping

### Analog Inputs

- `PA1` = `V_highpass`
- `PA2` = `V_lowpass`
- `PA3` = `I_lowpass`
- `PA4` = `I_highpass`

### Signal Source Control

- `PA7` = analog offset PWM
- `PA5` = AD9837 `SDATA`
- `PA6` = AD9837 `FSYNC`
- `PA8` = AD9837 `SCLK`
- `PB6/PB7` = MCP4561 I2C

### Analog Frontend Control

- `PB8/PB9` = shunt range select
- `PB10/PB11` = current PGA select
- `PB12/PB13` = voltage PGA select

### Status Outputs

- `PB14` = NeoPixel data
- `PB15` = simple heartbeat LED

## Acquisition Model

The firmware does not run continuous high-speed acquisition.

Instead, it exposes a burst-based acquisition engine:

- the host requests a sample count
- the host requests a target sample rate
- the firmware captures a synchronous burst from `PA1` and `PA4`
- the firmware returns the captured data over serial

This model is a better fit for impedance measurements than continuous text streaming because it preserves timing while keeping host-side control simple.

### AC Burst Capture

The AC path uses:

- `ADC1` for voltage highpass
- `ADC2` for current highpass
- dual regular simultaneous mode
- DMA transfer into a packed sample buffer
- `TIM3` update events as the sampling trigger

Each DMA word contains one voltage sample and one current sample from the same trigger instant.

Burst sample-rate requests are currently limited to `1000 Hz` through `1000000 Hz`.
The requested rate is quantized onto the nearest `TIM3` divider setting, and the actual configured rate is reported back as `sample_rate_hz`.
With the current STM32F103 clocking, `TIM3` runs from a `72 MHz` timer clock, so many requested rates are approximate rather than exact.

### DC Sampling

The lowpass channels are read separately with `analogRead()` and optional averaging.

They are intended for:

- DC offset monitoring
- slow telemetry
- support values for future impedance processing and calibration

## Source and Frontend Control

### DDS

The waveform source is an `AD9837`.

The current implementation bit-bangs the serial control interface on:

- `PA5`
- `PA6`
- `PA8`

It currently supports:

- setting frequency
- enabling/disabling the DDS output logic via control word writes

### Amplitude

Amplitude is controlled by an `MCP4561` digipot over I2C.
`amp 255` corresponds to approximately `3.55 Vpp`.

The firmware writes the volatile wiper register at the fixed 7-bit I2C address `0x2F` (`47` decimal).

### DC Offset

The generated source offset is controlled by PWM on `PA7`. The PWM output is assumed to be low-pass filtered in hardware before being summed into the analog source path.
`offset 255` corresponds to approximately `3.3 V`.

### Range and Gain Control

The analog frontend control lines are treated as 2-bit selectors:

- shunt range: `0..3`
- voltage PGA: `0..3`
- current PGA: `0..3`

The exact mapping from selector value to physical range/gain depends on the analog hardware truth table.

## Status Indication

The firmware uses:

- two NeoPixels on `PB14`
- one simple LED on `PB15`

Current behavior:

- `PB15` runs a non-blocking breathing pattern as a heartbeat
- NeoPixels can be set from the CLI
- on acquisition init failure, the NeoPixels are set red at startup
- on normal startup, the NeoPixels are set dim blue

## Serial Interface

The instrument is controlled from the serial CLI implemented in `src/command_interface.cpp`.

At startup the firmware prints:

```text
ready,stm32f103_lcr_meter
```

The CLI reference is documented separately in [`CLI.md`](CLI.md).
The `capturepair` acquisition path is described in [`CAPTUREPAIR.md`](CAPTUREPAIR.md).
The impedance measurement and calibration flow is described in [`MEASUREMENT.md`](MEASUREMENT.md).

## Oscilloscope Workflow

The project already contains a Python viewer for single-channel CSV burst output:

- [`pc_osc/README_oscilloscope.md`](pc_osc/README_oscilloscope.md)
- `pc_osc/pc_oscilloscope.py`

The `capture` command outputs data in a compatible format:

```text
sample_rate_hz,200000
index,value
0,2048
1,2074
...
```

That makes it possible to inspect the voltage or current burst quickly from a PC without writing a custom host tool.

## Current Limitations

This firmware is an acquisition/control milestone, not yet a full finished LCR measurement stack.

Not implemented yet:

- automatic range switching
- binary data transfer
- persistent configuration storage
- verified frontend truth tables for each PGA/range code

## Build Notes

The project is configured for PlatformIO with:

- board: `bluepill_f103c8`
- framework: `arduino`
- upload protocol: `stlink`

Current library dependencies:

- `SimpleCLI`
- `Adafruit NeoPixel`

## Suggested Next Steps

- verify the code builds on the target machine with PlatformIO
- confirm the ADC dual-mode burst path on real hardware
- verify DDS and digipot command behavior on the assembled board
- document the analog truth tables for range/gain codes
- replace placeholder PGA gain tables with measured analog gains
- validate open/short correction accuracy on real standards

## Measurement

This document describes the firmware-side impedance calculation path added on top of the synchronous `capturepair` acquisition engine.

## Command

The measurement flow is driven by:

- `measure <count> <rate>`

`measure` captures a burst and computes voltage amplitude, current amplitude, phase, and complex impedance.

## Signal Conversion

Each ADC sample is converted with:

```text
v_adc = raw * 3.3 / 4095
v_signal = v_adc - 1.65
```

The voltage channel is then scaled by the configured voltage-path gain.

The current channel is first interpreted as shunt voltage, then converted to current with:

```text
i_signal = v_current_channel / (current_path_gain * r_shunt)
```

Current shunt selection:

- `0 -> 100 ohm`
- `1 -> 1 kohm`
- `2 -> 10 kohm`
- `3 -> 100 kohm`

The initial firmware uses placeholder PGA gain tables with value `1.0` for all four settings. Replace those tables in `src/impedance_analyzer.cpp` with measured analog gains when they are known.

## DFT Math

The analyzer uses the actual capture rate returned by the acquisition engine together with the active `dds_frequency_hz`.

For each channel, it computes one-bin DFT components at the excitation frequency:

```text
Uc = (2 / sample_count) * sum(u[n] * cos(2*pi*f*n/fs))
Us = (2 / sample_count) * sum(u[n] * sin(2*pi*f*n/fs))
A = sqrt(Uc^2 + Us^2)
phi = atan2(-Us, Uc)
```

This produces:

- voltage amplitude in volts
- current amplitude in amps
- voltage phase in radians/degrees
- current phase in radians/degrees

The phase difference is:

```text
phase_diff = phi_v - phi_i
```

In the current hardware, the current analog path is inverted, so the firmware compensates the final phase offset by `+180 deg` before impedance is calculated and reported.

## Impedance Calculation

The complex impedance is reconstructed from the amplitude ratio and phase difference:

```text
|Z| = A_v / A_i
Re(Z) = |Z| * cos(phase_diff)
Im(Z) = |Z| * sin(phase_diff)
```

The CLI prints the complex impedance directly in the `impedance_*` fields.

## Recommended Workflow

1. Set the source frequency and frontend settings:

```text
dds 10000 1
range 1
vpga 0
ipga 0
```

2. Measure the DUT:

```text
measure 512 200000
```

## Current Limits

- PGA gain tables are placeholders until real analog gains are entered
- the analyzer expects `dds_frequency_hz` to be below Nyquist for the chosen sample rate

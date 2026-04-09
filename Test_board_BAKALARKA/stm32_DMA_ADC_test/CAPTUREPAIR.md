## `capturepair` System

This document explains how the `capturepair` path works across the firmware and the Python host viewer.

## Purpose

`capturepair` exists to fetch the two fast AC channels as synchronous sample pairs:

- voltage highpass on `PA1`
- current highpass on `PA4`

Unlike `capture`, which streams only one selected channel, `capturepair` preserves both waveforms from each trigger instant in one response. That makes it the better fit for later impedance and phase processing.

## End-To-End Flow

1. A host sends `capturepair <count> <rate>` over the serial CLI.
2. `CommandInterface::onCapturePair()` parses the arguments and calls `AcquisitionEngine::captureBurst()`.
3. `AcquisitionEngine` configures dual-ADC sampling with DMA and a `TIM3` trigger.
4. Each timer update causes `ADC1` and `ADC2` to sample at the same instant.
5. DMA stores one packed 32-bit word per sample index into the burst buffer.
6. After the burst completes, the CLI prints metadata and then emits CSV rows as `index,v_raw,i_raw`.
7. The Python oscilloscope detects that header, collects paired rows, and builds one frame containing both traces.

## Firmware Entry Point

The command is registered in `CommandInterface::begin()` in `src/command_interface.cpp`:

```text
capturepair [count] [rate]
```

The handler uses the same persisted defaults as `capture`:

- `sample_count` starts from `state_.acquisition.sample_count`
- `sample_rate_hz` starts from `state_.acquisition.sample_rate_hz`

After a successful burst, the handler updates those stored values.

## Serial Output Format

`capturepair` returns:

```text
sample_rate_hz,<actual_rate>
index,v_raw,i_raw
0,2042,2037
1,2068,2059
...
```

Important details:

- `sample_rate_hz` is the actual configured timer rate, not necessarily the exact requested rate.
- The CSV body contains one synchronous voltage/current pair per sample index.

## Acquisition Engine Internals

The burst engine lives in `src/acquisition_engine.cpp`.

### Channel Mapping

- `ADC1` samples `ADC_CHANNEL_1`, which is `PA1` and represents voltage highpass.
- `ADC2` samples `ADC_CHANNEL_4`, which is `PA4` and represents current highpass.

Those mappings come from `board_config.h` and `configureBurstPath()`.

### Triggering

The firmware configures:

- dual regular simultaneous ADC mode
- `TIM3` update event as the `ADC1` external trigger
- DMA on `ADC1` in normal mode

`ADC2` is started separately, then `HAL_ADCEx_MultiModeStart_DMA()` starts the dual conversion stream through `ADC1`. When `TIM3` runs, each update event triggers one synchronous conversion pair.

### Buffer Layout

The DMA destination is `buffer_`, declared as:

```cpp
std::array<uint32_t, board::kMaxBurstSamples> buffer_
```

Each element holds one packed pair:

- low 16 bits: voltage sample from `ADC1`
- high 16 bits: current sample from `ADC2`

The accessor methods unpack it like this:

```cpp
uint16_t AcquisitionEngine::voltageSample(std::size_t index) const {
  return static_cast<uint16_t>(buffer_[index] & 0xFFFFU);
}

uint16_t AcquisitionEngine::currentSample(std::size_t index) const {
  return static_cast<uint16_t>((buffer_[index] >> 16U) & 0xFFFFU);
}
```

That packed format is the core of the `capturepair` system: one DMA write corresponds to one synchronized measurement instant.

### Rate Selection

`configureTimerForSampleRate()` converts the requested rate into a `TIM3` prescaler and auto-reload value based on the timer clock. Because the divider must be integer, the real rate is quantized and stored in `last_sample_rate_hz_`.

### Completion

DMA completion is reported through the HAL callback chain:

- `HAL_ADC_ConvCpltCallback()` marks the transfer complete
- `HAL_ADC_ErrorCallback()` marks an error
- `captureBurst()` busy-waits until one of those flags changes or a timeout occurs

When the burst ends, the timer and ADC DMA path are stopped and the sample count is recorded.

## Why `capturepair` Reuses `captureBurst()`

There is no separate low-level acquisition engine for `capturepair`.

Both `capture` and `capturepair` call the same `captureBurst()` function. The difference is only in how the CLI formats the already-captured buffer:

- `capture` chooses either `voltageSample()` or `currentSample()` and prints `index,value`
- `capturepair` prints both `voltageSample()` and `currentSample()` as `index,v_raw,i_raw`

This means both commands share:

- the same timing behavior
- the same sample-rate limits
- the same DMA buffer
- the same synchronous acquisition guarantees

## Interaction With Firmware State

`FirmwareState` stores:

- `sample_rate_hz`
- `sample_count`
- `stream_channel`

`capturepair` updates the first two values, but does not use or change `stream_channel`. That field only affects the single-channel `capture` command and the remembered default for that command.

## Host-Side Parsing

The Python viewer in `pc_osc/pc_oscilloscope.py` recognizes `capturepair` in two places.

### Command Generation

When the user selects `both`, the app sends:

```text
capturepair <samples> <sample-rate>
```

The serial worker marks the expected frame as `"pair"` and remembers the expected sample count.

### Frame Detection

When the worker receives:

```text
index,v_raw,i_raw
```

it switches into pair-collection mode. Each following line is matched against a three-column regex:

```text
index,voltage,current
```

The worker stores:

- voltage values in `primary_values_by_index`
- current values in `secondary_values_by_index`

Once the requested count has been received, it emits a `Frame` object with:

- `primary_label = "Voltage"`
- `secondary_label = "Current"`
- one shared sample index array
- the returned `sample_rate_hz`

The UI then overlays both traces on the same time axis.

## Practical Summary

If you need one waveform for quick viewing, use `capture`.

If you need synchronized voltage and current from the same timestamps, use `capturepair`. In the current codebase it is the protocol layer on top of the shared dual-ADC burst engine, and the packed DMA buffer is what guarantees the pairing.

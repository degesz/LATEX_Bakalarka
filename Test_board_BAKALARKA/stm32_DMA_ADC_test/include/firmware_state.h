#pragma once

#include "board_config.h"
#include "impedance_analyzer.h"

#include <cstdint>

enum class StreamChannel : uint8_t {
  Voltage = 0,
  Current = 1,
};

struct RgbColor {
  uint8_t r = 0;
  uint8_t g = 0;
  uint8_t b = 0;
};

struct AcquisitionSettings {
  uint32_t sample_rate_hz = board::kDefaultSampleRateHz;
  uint32_t sample_count = board::kDefaultBurstSamples;
  StreamChannel stream_channel = StreamChannel::Voltage;
};

struct DcMeasurements {
  uint16_t voltage_lowpass_raw = 0;
  uint16_t current_lowpass_raw = 0;
  uint32_t updated_at_ms = 0;
};

struct OutputSettings {
  uint8_t shunt_range = 0;
  uint8_t voltage_pga = 0;
  uint8_t current_pga = 0;
  uint8_t offset_pwm = 0;
  uint8_t amplitude_wiper = 128;
  uint32_t dds_frequency_hz = 1000;
  bool dds_enabled = true;
  RgbColor pixels[board::kNeoPixelCount] = {};
};

struct FirmwareState {
  AcquisitionSettings acquisition = {};
  DcMeasurements dc = {};
  OutputSettings outputs = {};
  MeasurementCalibrationStore calibration = {};
};

inline const char* streamChannelName(StreamChannel channel) {
  return (channel == StreamChannel::Voltage) ? "voltage" : "current";
}

#pragma once

#include "board_config.h"
#include "impedance_analyzer.h"

#include <cstdint>

enum class StreamChannel : uint8_t {
  Voltage = 0,
  Current = 1,
};

struct AcquisitionSettings {
  uint32_t sample_rate_hz = board::kDefaultSampleRateHz;
  uint32_t sample_count = board::kDefaultBurstSamples;
  StreamChannel stream_channel = StreamChannel::Voltage;
};

struct OutputSettings {
  uint8_t shunt_range = 0;
  uint8_t voltage_pga = 0;
  uint8_t current_pga = 0;
  uint8_t offset_pwm = 0;
  uint8_t amplitude_wiper = 128;
  uint32_t dds_frequency_hz = 1000;
  bool dds_enabled = true;
};

struct FirmwareState {
  AcquisitionSettings acquisition = {};
  OutputSettings outputs = {};
};

inline const char* streamChannelName(StreamChannel channel) {
  return (channel == StreamChannel::Voltage) ? "voltage" : "current";
}

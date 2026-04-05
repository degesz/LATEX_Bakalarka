#pragma once

#include "board_config.h"

#include <cstdint>

class SourceControl {
 public:
  void begin();

  bool setAmplitudeWiper(uint8_t value);
  void setOffsetPwm(uint8_t value);

  void setShuntRange(uint8_t value);
  void setVoltagePga(uint8_t value);
  void setCurrentPga(uint8_t value);

  void setDdsEnabled(bool enabled);
  void setDdsFrequency(uint32_t frequency_hz);

 private:
  void writeBitPair(uint8_t lsb_pin, uint8_t msb_pin, uint8_t value);
  bool writeMcpRegister(uint8_t reg, uint16_t value);

  void writeDdsWord(uint16_t word);
  void applyDdsConfiguration();

  uint8_t mcp4561_address_ = board::kMcp4561DefaultAddress;
  uint8_t amplitude_wiper_ = 128;
  uint8_t offset_pwm_ = 0;
  uint8_t shunt_range_ = 0;
  uint8_t voltage_pga_ = 0;
  uint8_t current_pga_ = 0;
  uint32_t dds_frequency_hz_ = 1000;
  bool dds_enabled_ = true;
};

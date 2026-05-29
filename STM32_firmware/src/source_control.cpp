#include "source_control.h"

#include <Arduino.h>
#include <Wire.h>

namespace {

constexpr uint8_t kMcpVolatileWiper0Reg = 0x00;
constexpr uint16_t kDdsControlReset = 0x2100;
constexpr uint16_t kDdsControlRun = 0x2000;
constexpr uint16_t kDdsFreqReg0 = 0x4000;
constexpr unsigned int kDdsEdgeDelayUs = 1;

}  // namespace

void SourceControl::begin() {
  pinMode(board::kShuntRangeLsbPin, OUTPUT);
  pinMode(board::kShuntRangeMsbPin, OUTPUT);
  pinMode(board::kCurrentPgaLsbPin, OUTPUT);
  pinMode(board::kCurrentPgaMsbPin, OUTPUT);
  pinMode(board::kVoltagePgaLsbPin, OUTPUT);
  pinMode(board::kVoltagePgaMsbPin, OUTPUT);

  pinMode(board::kOffsetPwmPin, OUTPUT);
  analogWrite(board::kOffsetPwmPin, offset_pwm_);

  pinMode(board::kDdsDataPin, OUTPUT);
  pinMode(board::kDdsFsyncPin, OUTPUT);
  pinMode(board::kDdsClockPin, OUTPUT);
  digitalWrite(board::kDdsFsyncPin, HIGH);
  digitalWrite(board::kDdsClockPin, HIGH);
  digitalWrite(board::kDdsDataPin, LOW);

  Wire.begin();
  Wire.setClock(400000);

  setShuntRange(shunt_range_);
  setVoltagePga(voltage_pga_);
  setCurrentPga(current_pga_);
  setAmplitudeWiper(amplitude_wiper_);
  applyDdsConfiguration();
}

bool SourceControl::setAmplitudeWiper(uint8_t value) {
  amplitude_wiper_ = value;
  return writeMcpRegister(kMcpVolatileWiper0Reg, amplitude_wiper_);
}

void SourceControl::setOffsetPwm(uint8_t value) {
  offset_pwm_ = value;
  analogWrite(board::kOffsetPwmPin, offset_pwm_);
}

void SourceControl::setShuntRange(uint8_t value) {
  shunt_range_ = value & 0x03U;
  writeBitPair(board::kShuntRangeLsbPin, board::kShuntRangeMsbPin, shunt_range_);
}

void SourceControl::setVoltagePga(uint8_t value) {
  voltage_pga_ = value & 0x03U;
  writeBitPair(board::kVoltagePgaLsbPin, board::kVoltagePgaMsbPin, voltage_pga_);
}

void SourceControl::setCurrentPga(uint8_t value) {
  current_pga_ = value & 0x03U;
  writeBitPair(board::kCurrentPgaLsbPin, board::kCurrentPgaMsbPin, current_pga_);
}

void SourceControl::setDdsEnabled(bool enabled) {
  dds_enabled_ = enabled;
  applyDdsConfiguration();
}

void SourceControl::setDdsFrequency(uint32_t frequency_hz) {
  dds_frequency_hz_ = frequency_hz;
  applyDdsConfiguration();
}

void SourceControl::writeBitPair(uint8_t lsb_pin, uint8_t msb_pin, uint8_t value) {
  digitalWrite(lsb_pin, (value & 0x01U) ? HIGH : LOW);
  digitalWrite(msb_pin, (value & 0x02U) ? HIGH : LOW);
}

bool SourceControl::writeMcpRegister(uint8_t reg, uint16_t value) {
  const uint8_t command = static_cast<uint8_t>(((reg & 0x0FU) << 4) |
                                               ((value >> 8U) & 0x03U));

  Wire.beginTransmission(mcp4561_address_);
  Wire.write(command);
  Wire.write(static_cast<uint8_t>(value & 0xFFU));
  return Wire.endTransmission() == 0;
}

void SourceControl::writeDdsWord(uint16_t word) {
  digitalWrite(board::kDdsFsyncPin, LOW);
  delayMicroseconds(kDdsEdgeDelayUs);

  for (int bit = 15; bit >= 0; --bit) {
    digitalWrite(board::kDdsDataPin, ((word >> bit) & 0x01U) ? HIGH : LOW);
    delayMicroseconds(kDdsEdgeDelayUs);
    digitalWrite(board::kDdsClockPin, LOW);
    delayMicroseconds(kDdsEdgeDelayUs);
    digitalWrite(board::kDdsClockPin, HIGH);
  }

  delayMicroseconds(kDdsEdgeDelayUs);
  digitalWrite(board::kDdsDataPin, LOW);
  digitalWrite(board::kDdsClockPin, HIGH);
  digitalWrite(board::kDdsFsyncPin, HIGH);
}

void SourceControl::applyDdsConfiguration() {
  const uint32_t freq_word =
      static_cast<uint32_t>((((static_cast<uint64_t>(dds_frequency_hz_) << 28U) +
                              (board::kDdsMasterClockHz / 2ULL)) /
                             board::kDdsMasterClockHz) &
                            0x0FFFFFFFULL);

  writeDdsWord(kDdsControlReset);
  writeDdsWord(kDdsFreqReg0 | static_cast<uint16_t>(freq_word & 0x3FFFU));
  writeDdsWord(kDdsFreqReg0 | static_cast<uint16_t>((freq_word >> 14U) & 0x3FFFU));
  writeDdsWord(dds_enabled_ ? kDdsControlRun : kDdsControlReset);
}

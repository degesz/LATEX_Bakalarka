#pragma once

#include <Arduino.h>

#include <cstddef>
#include <cstdint>

namespace board {

constexpr uint8_t kVoltageHighpassPin = PA1;
constexpr uint8_t kVoltageLowpassPin = PA2;
constexpr uint8_t kCurrentLowpassPin = PA3;
constexpr uint8_t kCurrentHighpassPin = PA4;

constexpr uint8_t kOffsetPwmPin = PA7;

constexpr uint8_t kDdsDataPin = PA5;
constexpr uint8_t kDdsFsyncPin = PA6;
constexpr uint8_t kDdsClockPin = PA8;

constexpr uint8_t kShuntRangeLsbPin = PB8;
constexpr uint8_t kShuntRangeMsbPin = PB9;
constexpr uint8_t kCurrentPgaLsbPin = PB10;
constexpr uint8_t kCurrentPgaMsbPin = PB11;
constexpr uint8_t kVoltagePgaLsbPin = PB12;
constexpr uint8_t kVoltagePgaMsbPin = PB13;

constexpr uint8_t kNeoPixelPin = PB14;
constexpr std::size_t kNeoPixelCount = 2;

constexpr uint8_t kHeartbeatLedPin = PB15;
constexpr bool kHeartbeatActiveHigh = true;

constexpr uint8_t kMcp4561DefaultAddress = 0x2F;

constexpr uint32_t kSerialBaud = 115200;
constexpr uint32_t kDdsMasterClockHz = 16000000UL;

constexpr float kAdcReferenceVolts = 3.3f;
constexpr uint16_t kAdcMaxCode = 4095;
constexpr uint16_t kAdcCenterCode = 2048;

constexpr std::size_t kMaxBurstSamples = 2048;
constexpr uint32_t kDefaultBurstSamples = 256;
constexpr uint32_t kDefaultSampleRateHz = 200000UL;
constexpr uint32_t kMinSampleRateHz = 1000UL;
constexpr uint32_t kMaxSampleRateHz = 1000000UL;

}  // namespace board

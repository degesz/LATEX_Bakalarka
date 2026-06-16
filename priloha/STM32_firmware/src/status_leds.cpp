#include "status_leds.h"

#include "board_config.h"

#include <Arduino.h>

#include <cmath>

namespace {

constexpr uint32_t kBreathPeriodMs = 2000;

}  // namespace

StatusLeds::StatusLeds() = default;

void StatusLeds::begin() {
  pinMode(board::kHeartbeatLedPin, OUTPUT);
  digitalWrite(board::kHeartbeatLedPin, board::kHeartbeatActiveHigh ? LOW : HIGH);
}

void StatusLeds::update() {
  const uint32_t now_ms = millis();
  const float phase = static_cast<float>(now_ms % kBreathPeriodMs) /
                      static_cast<float>(kBreathPeriodMs);
  const float triangle = (phase < 0.5f) ? (phase * 2.0f) : ((1.0f - phase) * 2.0f);
  const uint8_t brightness =
      static_cast<uint8_t>(16.0f + (triangle * triangle * 239.0f));
  const uint8_t pwm_phase = static_cast<uint8_t>((micros() / 64U) & 0xFFU);
  const bool led_on = pwm_phase < brightness;

  digitalWrite(board::kHeartbeatLedPin,
               led_on == board::kHeartbeatActiveHigh ? HIGH : LOW);
}

void StatusLeds::setPixel(std::size_t index, uint8_t r, uint8_t g, uint8_t b) {
  (void)index;
  (void)r;
  (void)g;
  (void)b;
}

void StatusLeds::fill(uint8_t r, uint8_t g, uint8_t b) {
  (void)r;
  (void)g;
  (void)b;
}

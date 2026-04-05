#pragma once

#include "board_config.h"

#include <Adafruit_NeoPixel.h>

#include <cstddef>
#include <cstdint>

class StatusLeds {
 public:
  StatusLeds();

  void begin();
  void update();

  void setPixel(std::size_t index, uint8_t r, uint8_t g, uint8_t b);
  void fill(uint8_t r, uint8_t g, uint8_t b);

 private:
  Adafruit_NeoPixel pixels_;
};

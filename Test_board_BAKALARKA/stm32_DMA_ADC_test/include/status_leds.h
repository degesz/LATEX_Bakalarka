#pragma once

#include <cstddef>
#include <cstdint>

class StatusLeds {
 public:
  StatusLeds();

  void begin();
  void update();

  void setPixel(std::size_t index, uint8_t r, uint8_t g, uint8_t b);
  void fill(uint8_t r, uint8_t g, uint8_t b);
};

#pragma once

#include "board_config.h"

#include <Arduino.h>

#include <array>
#include <cstddef>
#include <cstdint>

class AcquisitionEngine {
 public:
  AcquisitionEngine();

  bool begin();

  bool captureBurst(uint32_t sample_rate_hz, std::size_t sample_count);
  void sampleDc(uint16_t& voltage_lowpass_raw, uint16_t& current_lowpass_raw,
                std::size_t average_count = 16);

  static AcquisitionEngine* instance();
  DMA_HandleTypeDef* dmaHandle();

  std::size_t maxSampleCount() const;
  std::size_t lastSampleCount() const;
  uint32_t lastSampleRateHz() const;

  uint16_t voltageSample(std::size_t index) const;
  uint16_t currentSample(std::size_t index) const;

  const char* lastError() const;

  void onDmaTransferComplete();
  void onDmaTransferError();

 private:
  bool configureBurstPath();
  bool configureTimerForSampleRate(uint32_t sample_rate_hz);
  void setError(const char* message);
  uint32_t timerClockHz() const;

  static AcquisitionEngine* instance_;

  std::array<uint32_t, board::kMaxBurstSamples> buffer_ = {};
  std::size_t last_sample_count_ = 0;
  uint32_t last_sample_rate_hz_ = 0;

  volatile bool transfer_complete_ = false;
  volatile bool transfer_error_ = false;

  const char* last_error_ = "ok";

  bool initialized_ = false;

  ADC_HandleTypeDef adc1_handle_ = {};
  ADC_HandleTypeDef adc2_handle_ = {};
  DMA_HandleTypeDef dma1_channel1_handle_ = {};
  TIM_HandleTypeDef tim3_handle_ = {};
};

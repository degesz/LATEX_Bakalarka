#include "acquisition_engine.h"

#include <Arduino.h>

#include <algorithm>
#include <cstring>

namespace {

constexpr uint32_t kAdcSampleTime = ADC_SAMPLETIME_1CYCLE_5;
constexpr uint32_t kBurstTimeoutMs = 1000;

AcquisitionEngine* acquisitionSingleton() {
  return AcquisitionEngine::instance();
}

void configureAnalogInputPins() {
  GPIO_InitTypeDef gpio_init = {};

  __HAL_RCC_GPIOA_CLK_ENABLE();

  gpio_init.Mode = GPIO_MODE_ANALOG;
  gpio_init.Speed = GPIO_SPEED_FREQ_HIGH;
  gpio_init.Pin = GPIO_PIN_1 | GPIO_PIN_2 | GPIO_PIN_3 | GPIO_PIN_4;
  HAL_GPIO_Init(GPIOA, &gpio_init);
}

}  // namespace

AcquisitionEngine* AcquisitionEngine::instance_ = nullptr;

AcquisitionEngine::AcquisitionEngine() {
  instance_ = this;
}

AcquisitionEngine* AcquisitionEngine::instance() {
  return instance_;
}

DMA_HandleTypeDef* AcquisitionEngine::dmaHandle() {
  return &dma1_channel1_handle_;
}

bool AcquisitionEngine::begin() {
  analogReadResolution(12);

  __HAL_RCC_AFIO_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_DMA1_CLK_ENABLE();
  __HAL_RCC_ADC1_CLK_ENABLE();
  __HAL_RCC_ADC2_CLK_ENABLE();
  __HAL_RCC_TIM3_CLK_ENABLE();
  __HAL_RCC_ADC_CONFIG(RCC_ADCPCLK2_DIV4);

  if (!configureBurstPath()) {
    return false;
  }

  if (HAL_ADCEx_Calibration_Start(&adc1_handle_) != HAL_OK) {
    setError("adc1 calibration failed");
    return false;
  }

  if (HAL_ADCEx_Calibration_Start(&adc2_handle_) != HAL_OK) {
    setError("adc2 calibration failed");
    return false;
  }

  initialized_ = true;
  last_error_ = "ok";
  return true;
}

bool AcquisitionEngine::captureBurst(uint32_t sample_rate_hz, std::size_t sample_count) {
  if (!initialized_ && !begin()) {
    return false;
  }

  if ((sample_count == 0U) || (sample_count > buffer_.size())) {
    setError("invalid burst sample count");
    return false;
  }

  if ((sample_rate_hz < board::kMinSampleRateHz) ||
      (sample_rate_hz > board::kMaxSampleRateHz)) {
    setError("invalid burst sample rate");
    return false;
  }

  if (!configureBurstPath()) {
    return false;
  }

  if (!configureTimerForSampleRate(sample_rate_hz)) {
    return false;
  }

  std::fill(buffer_.begin(), buffer_.begin() + sample_count, 0U);

  transfer_complete_ = false;
  transfer_error_ = false;
  last_error_ = "ok";

  if (HAL_ADC_Start(&adc2_handle_) != HAL_OK) {
    setError("adc2 start failed");
    return false;
  }

  if (HAL_ADCEx_MultiModeStart_DMA(&adc1_handle_, buffer_.data(), sample_count) != HAL_OK) {
    HAL_ADC_Stop(&adc2_handle_);
    setError("dual adc dma start failed");
    return false;
  }

  __HAL_TIM_SET_COUNTER(&tim3_handle_, 0);
  if (HAL_TIM_Base_Start(&tim3_handle_) != HAL_OK) {
    HAL_ADCEx_MultiModeStop_DMA(&adc1_handle_);
    HAL_ADC_Stop(&adc2_handle_);
    setError("timer start failed");
    return false;
  }

  const uint32_t start_ms = HAL_GetTick();
  while (!transfer_complete_ && !transfer_error_) {
    if ((HAL_GetTick() - start_ms) > kBurstTimeoutMs) {
      transfer_error_ = true;
      setError("burst timeout");
      break;
    }
  }

  HAL_TIM_Base_Stop(&tim3_handle_);
  HAL_ADCEx_MultiModeStop_DMA(&adc1_handle_);
  HAL_ADC_Stop(&adc2_handle_);

  if (transfer_error_) {
    if (last_error_ == nullptr || strcmp(last_error_, "ok") == 0) {
      setError("burst dma error");
    }
    return false;
  }

  last_sample_count_ = sample_count;
  return true;
}

void AcquisitionEngine::sampleDc(uint16_t& voltage_lowpass_raw, uint16_t& current_lowpass_raw,
                                 std::size_t average_count) {
  average_count = (average_count == 0U) ? 1U : average_count;

  uint32_t voltage_acc = 0;
  uint32_t current_acc = 0;

  for (std::size_t i = 0; i < average_count; ++i) {
    voltage_acc += static_cast<uint16_t>(analogRead(board::kVoltageLowpassPin));
    current_acc += static_cast<uint16_t>(analogRead(board::kCurrentLowpassPin));
  }

  voltage_lowpass_raw = static_cast<uint16_t>(voltage_acc / average_count);
  current_lowpass_raw = static_cast<uint16_t>(current_acc / average_count);
}

std::size_t AcquisitionEngine::maxSampleCount() const {
  return buffer_.size();
}

std::size_t AcquisitionEngine::lastSampleCount() const {
  return last_sample_count_;
}

uint32_t AcquisitionEngine::lastSampleRateHz() const {
  return last_sample_rate_hz_;
}

uint16_t AcquisitionEngine::voltageSample(std::size_t index) const {
  if (index >= last_sample_count_) {
    return 0;
  }

  return static_cast<uint16_t>(buffer_[index] & 0xFFFFU);
}

uint16_t AcquisitionEngine::currentSample(std::size_t index) const {
  if (index >= last_sample_count_) {
    return 0;
  }

  return static_cast<uint16_t>((buffer_[index] >> 16U) & 0xFFFFU);
}

const char* AcquisitionEngine::lastError() const {
  return last_error_;
}

void AcquisitionEngine::onDmaTransferComplete() {
  transfer_complete_ = true;
}

void AcquisitionEngine::onDmaTransferError() {
  transfer_error_ = true;
  setError("dma transfer error");
}

bool AcquisitionEngine::configureBurstPath() {
  adc1_handle_ = {};
  adc2_handle_ = {};
  dma1_channel1_handle_ = {};
  tim3_handle_ = {};

  configureAnalogInputPins();

  adc1_handle_.Instance = ADC1;
  adc1_handle_.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  adc1_handle_.Init.ScanConvMode = ADC_SCAN_DISABLE;
  adc1_handle_.Init.ContinuousConvMode = DISABLE;
  adc1_handle_.Init.NbrOfConversion = 1;
  adc1_handle_.Init.DiscontinuousConvMode = DISABLE;
  adc1_handle_.Init.NbrOfDiscConversion = 0;
  adc1_handle_.Init.ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO;

  adc2_handle_ = adc1_handle_;
  adc2_handle_.Instance = ADC2;
  adc2_handle_.Init.ExternalTrigConv = ADC_SOFTWARE_START;

  if (HAL_ADC_Init(&adc1_handle_) != HAL_OK) {
    setError("adc1 init failed");
    return false;
  }

  if (HAL_ADC_Init(&adc2_handle_) != HAL_OK) {
    setError("adc2 init failed");
    return false;
  }

  dma1_channel1_handle_.Instance = DMA1_Channel1;
  dma1_channel1_handle_.Init.Direction = DMA_PERIPH_TO_MEMORY;
  dma1_channel1_handle_.Init.PeriphInc = DMA_PINC_DISABLE;
  dma1_channel1_handle_.Init.MemInc = DMA_MINC_ENABLE;
  dma1_channel1_handle_.Init.PeriphDataAlignment = DMA_PDATAALIGN_WORD;
  dma1_channel1_handle_.Init.MemDataAlignment = DMA_MDATAALIGN_WORD;
  dma1_channel1_handle_.Init.Mode = DMA_NORMAL;
  dma1_channel1_handle_.Init.Priority = DMA_PRIORITY_HIGH;
  if (HAL_DMA_Init(&dma1_channel1_handle_) != HAL_OK) {
    setError("adc dma init failed");
    return false;
  }
  __HAL_LINKDMA(&adc1_handle_, DMA_Handle, dma1_channel1_handle_);

  HAL_NVIC_SetPriority(DMA1_Channel1_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Channel1_IRQn);

  ADC_ChannelConfTypeDef channel_config = {};
  channel_config.Rank = ADC_REGULAR_RANK_1;
  channel_config.SamplingTime = kAdcSampleTime;

  channel_config.Channel = ADC_CHANNEL_1;
  if (HAL_ADC_ConfigChannel(&adc1_handle_, &channel_config) != HAL_OK) {
    setError("adc1 channel config failed");
    return false;
  }

  channel_config.Channel = ADC_CHANNEL_4;
  if (HAL_ADC_ConfigChannel(&adc2_handle_, &channel_config) != HAL_OK) {
    setError("adc2 channel config failed");
    return false;
  }

  ADC_MultiModeTypeDef multimode = {};
  multimode.Mode = ADC_DUALMODE_REGSIMULT;
  if (HAL_ADCEx_MultiModeConfigChannel(&adc1_handle_, &multimode) != HAL_OK) {
    setError("dual adc mode config failed");
    return false;
  }

  tim3_handle_.Instance = TIM3;
  tim3_handle_.Init.Prescaler = 0;
  tim3_handle_.Init.CounterMode = TIM_COUNTERMODE_UP;
  tim3_handle_.Init.Period = 0xFFFFU;
  tim3_handle_.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  tim3_handle_.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;

  if (HAL_TIM_Base_Init(&tim3_handle_) != HAL_OK) {
    setError("tim3 init failed");
    return false;
  }

  TIM_MasterConfigTypeDef master_config = {};
  master_config.MasterOutputTrigger = TIM_TRGO_UPDATE;
  master_config.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&tim3_handle_, &master_config) != HAL_OK) {
    setError("tim3 trgo config failed");
    return false;
  }

  last_error_ = "ok";
  return true;
}

bool AcquisitionEngine::configureTimerForSampleRate(uint32_t sample_rate_hz) {
  const uint32_t timer_clock_hz = timerClockHz();
  if (timer_clock_hz == 0U) {
    setError("timer clock unavailable");
    return false;
  }

  uint64_t divider = (timer_clock_hz + (sample_rate_hz / 2U)) / sample_rate_hz;
  if (divider == 0U) {
    divider = 1U;
  }

  uint32_t prescaler = static_cast<uint32_t>((divider - 1U) / 65536U);
  if (prescaler > 0xFFFFU) {
    setError("sample rate too low");
    return false;
  }

  uint32_t period =
      static_cast<uint32_t>((divider / static_cast<uint64_t>(prescaler + 1U)) - 1U);

  if (period > 0xFFFFU) {
    period = 0xFFFFU;
  }

  __HAL_TIM_DISABLE(&tim3_handle_);
  __HAL_TIM_SET_PRESCALER(&tim3_handle_, prescaler);
  __HAL_TIM_SET_AUTORELOAD(&tim3_handle_, period);
  __HAL_TIM_SET_COUNTER(&tim3_handle_, 0);
  __HAL_TIM_CLEAR_FLAG(&tim3_handle_, TIM_FLAG_UPDATE);
  if (HAL_TIM_GenerateEvent(&tim3_handle_, TIM_EVENTSOURCE_UPDATE) != HAL_OK) {
    setError("timer update event failed");
    return false;
  }

  last_sample_rate_hz_ =
      timer_clock_hz / ((prescaler + 1U) * (period + 1U));
  return true;
}

void AcquisitionEngine::setError(const char* message) {
  last_error_ = message;
}

uint32_t AcquisitionEngine::timerClockHz() const {
  const uint32_t pclk1_hz = HAL_RCC_GetPCLK1Freq();
  const uint32_t ppre1 = RCC->CFGR & RCC_CFGR_PPRE1;
  return (ppre1 == RCC_CFGR_PPRE1_DIV1) ? pclk1_hz : (pclk1_hz * 2U);
}

extern "C" void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef* hadc) {
  auto* self = acquisitionSingleton();
  if ((self != nullptr) && (hadc->Instance == ADC1)) {
    self->onDmaTransferComplete();
  }
}

extern "C" void HAL_ADC_ErrorCallback(ADC_HandleTypeDef* hadc) {
  auto* self = acquisitionSingleton();
  if ((self != nullptr) && (hadc->Instance == ADC1)) {
    self->onDmaTransferError();
  }
}

extern "C" void DMA1_Channel1_IRQHandler(void) {
  auto* self = acquisitionSingleton();
  if (self != nullptr) {
    HAL_DMA_IRQHandler(self->dmaHandle());
  }
}

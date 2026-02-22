#include <Arduino.h>
#include <cstring>

constexpr uint32_t kTargetSampleRateHz = 100000;  // 1 kHz trigger target
constexpr uint16_t kSampleCount = 1000;
constexpr uint32_t kCaptureTimeoutMs = 1000;
constexpr uint32_t kPauseBetweenCapturesMs = 1000;

ADC_HandleTypeDef hadc1;
DMA_HandleTypeDef hdma_adc1;
TIM_HandleTypeDef htim3;
// DMA writes each converted sample here.
uint16_t adcSamples[kSampleCount];
uint32_t gActualSampleRateHz = kTargetSampleRateHz;

bool configureAdcClock() {
  RCC_PeriphCLKInitTypeDef periphClkInit = {};
  periphClkInit.PeriphClockSelection = RCC_PERIPHCLK_ADC;
  // On many Bluepill clock setups this gives ADCCLK=12 MHz (72/6).
  periphClkInit.AdcClockSelection = RCC_ADCPCLK2_DIV6;
  return HAL_RCCEx_PeriphCLKConfig(&periphClkInit) == HAL_OK;
}

bool initAdcDma() {
  // Enable clocks for: analog input GPIO, DMA engine, and ADC peripheral.
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_DMA1_CLK_ENABLE();
  __HAL_RCC_ADC1_CLK_ENABLE();

  GPIO_InitTypeDef gpioInit = {};
  gpioInit.Pin = GPIO_PIN_0;  // PA0 (A0)
  gpioInit.Mode = GPIO_MODE_ANALOG;
  HAL_GPIO_Init(GPIOA, &gpioInit);

  // DMA1 Channel1 is hard-wired to ADC1 on STM32F103.
  hdma_adc1.Instance = DMA1_Channel1;
  hdma_adc1.Init.Direction = DMA_PERIPH_TO_MEMORY;
  hdma_adc1.Init.PeriphInc = DMA_PINC_DISABLE;
  hdma_adc1.Init.MemInc = DMA_MINC_ENABLE;
  hdma_adc1.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
  hdma_adc1.Init.MemDataAlignment = DMA_MDATAALIGN_HALFWORD;
  // Normal mode performs one finite transfer of kSampleCount values.
  hdma_adc1.Init.Mode = DMA_NORMAL;
  hdma_adc1.Init.Priority = DMA_PRIORITY_HIGH;
  if (HAL_DMA_Init(&hdma_adc1) != HAL_OK) {
    return false;
  }

  hadc1.Instance = ADC1;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  // External timer trigger controls conversion timing.
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.NbrOfConversion = 1;
  if (HAL_ADC_Init(&hadc1) != HAL_OK) {
    return false;
  }

  // Connect ADC handle with DMA handle so HAL_ADC_Start_DMA can use it.
  __HAL_LINKDMA(&hadc1, DMA_Handle, hdma_adc1);

  ADC_ChannelConfTypeDef channelCfg = {};
  channelCfg.Channel = ADC_CHANNEL_0;
  channelCfg.Rank = ADC_REGULAR_RANK_1;
  // Very short sample time for high-speed capture (source impedance must be low).
  channelCfg.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
  if (HAL_ADC_ConfigChannel(&hadc1, &channelCfg) != HAL_OK) {
    return false;
  }

  // F1 ADC requires calibration after initialization for best accuracy.
  if (HAL_ADCEx_Calibration_Start(&hadc1) != HAL_OK) {
    return false;
  }

  return true;
}

bool initTimer3Trigger() {
  __HAL_RCC_TIM3_CLK_ENABLE();

  const uint32_t pclk1 = HAL_RCC_GetPCLK1Freq();
  const uint32_t ppre1 = (RCC->CFGR & RCC_CFGR_PPRE1);
  // On STM32 timers: if APB prescaler != 1, timer clock is APB clock * 2.
  const uint32_t timClock = (ppre1 == RCC_CFGR_PPRE1_DIV1) ? pclk1 : (pclk1 * 2UL);

  if (timClock < kTargetSampleRateHz) {
    return false;
  }

  // Update event frequency: timClock / (Prescaler + 1) / (Period + 1).
  // Pick prescaler/period so both fit 16-bit TIM3 registers.
  const uint64_t totalDiv =
      (static_cast<uint64_t>(timClock) + (kTargetSampleRateHz / 2UL)) / kTargetSampleRateHz;
  if (totalDiv == 0) {
    return false;
  }

  const uint32_t prescaler = static_cast<uint32_t>((totalDiv - 1ULL) / 65536ULL);
  if (prescaler > 0xFFFFUL) {
    return false;
  }

  uint32_t periodCounts = static_cast<uint32_t>(totalDiv / (prescaler + 1UL));
  if (periodCounts == 0) {
    periodCounts = 1;
  }
  const uint32_t period = periodCounts - 1UL;
  if (period > 0xFFFFUL) {
    return false;
  }

  // Actual trigger frequency may differ slightly due to integer timer division.
  gActualSampleRateHz = timClock / ((prescaler + 1UL) * (period + 1UL));

  htim3.Instance = TIM3;
  htim3.Init.Prescaler = prescaler;
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim3.Init.Period = period;
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim3) != HAL_OK) {
    return false;
  }

  TIM_MasterConfigTypeDef masterCfg = {};
  // Emit TRGO on every update event -> each update triggers one ADC conversion.
  masterCfg.MasterOutputTrigger = TIM_TRGO_UPDATE;
  masterCfg.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &masterCfg) != HAL_OK) {
    return false;
  }

  return true;
}

void printSamples() {
  Serial.print("sample_rate_hz,");
  Serial.println(gActualSampleRateHz);
  Serial.println("index,value");
  for (uint16_t i = 0; i < kSampleCount; ++i) {
    Serial.print(i);
    Serial.print(',');
    Serial.println(adcSamples[i]);
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("ADC DMA capture start (PA0, TIM3 trigger)");

  if (!configureAdcClock()) {
    Serial.println("WARN: ADC clock config failed, using default clock setup");
  }

  if (!initAdcDma()) {
    Serial.println("ERROR: ADC/DMA init failed");
    while (true) {
      delay(1000);
    }
  }

  if (!initTimer3Trigger()) {
    Serial.println("ERROR: TIM3 trigger init failed");
    while (true) {
      delay(1000);
    }
  }
}

void loop() {
  // Clear the destination buffer so stale values are easy to spot in output.
  memset(adcSamples, 0, sizeof(adcSamples));

  // Arm ADC+DMA first: ADC stores each conversion result into adcSamples[].
  if (HAL_ADC_Start_DMA(&hadc1, reinterpret_cast<uint32_t *>(adcSamples), kSampleCount) != HAL_OK) {
    Serial.println("ERROR: HAL_ADC_Start_DMA failed");
    delay(kPauseBetweenCapturesMs);
    return;
  }

  // Start TIM3 updates; each update event triggers one ADC sample.
  if (HAL_TIM_Base_Start(&htim3) != HAL_OK) {
    Serial.println("ERROR: HAL_TIM_Base_Start failed");
    HAL_ADC_Stop_DMA(&hadc1);
    delay(kPauseBetweenCapturesMs);
    return;
  }

  const uint32_t startMs = millis();
  // DMA counter counts down to 0 as samples are written.
  while (__HAL_DMA_GET_COUNTER(&hdma_adc1) > 0U) {
    if ((millis() - startMs) > kCaptureTimeoutMs) {
      Serial.println("ERROR: Capture timeout");
      break;
    }
  }

  // Stop trigger source first, then stop ADC+DMA transfer.
  HAL_TIM_Base_Stop(&htim3);
  HAL_ADC_Stop_DMA(&hadc1);

  printSamples();
  Serial.println("Capture done. Waiting 5 seconds...\n");
  delay(kPauseBetweenCapturesMs);
}
#include "impedance_analyzer.h"

#include "acquisition_engine.h"
#include "board_config.h"

#include <cmath>

namespace {

constexpr float kPi = 3.14159265358979323846f;
constexpr float kTwoPi = 2.0f * kPi;
constexpr float kAmplitudeEpsilon = 1.0e-9f;

// Placeholder frontend gains. Replace these with measured analog gains later.
constexpr float kVoltagePathGainTable[kMeasurementSettingCount] = {1.0f, 2.0f, 5.0f, 10.0f};
constexpr float kCurrentPathGainTable[kMeasurementSettingCount] = {1.0f, 2.0f, 5.0f, 10.0f};
constexpr float kShuntResistanceTable[kMeasurementSettingCount] = {100.0f, 1000.0f, 10000.0f,
                                                                   100000.0f};

ComplexValue makeComplex(float real, float imag) {
  return {real, imag};
}

float normalizePhase(float phase_rad) {
  while (phase_rad > kPi) {
    phase_rad -= kTwoPi;
  }
  while (phase_rad < -kPi) {
    phase_rad += kTwoPi;
  }
  return phase_rad;
}

}  // namespace

bool ImpedanceAnalyzer::analyze(const AcquisitionEngine& acquisition,
                                const MeasurementContext& context,
                                MeasurementResult& result) const {
  result = {};

  const std::size_t sample_count = acquisition.lastSampleCount();
  if (sample_count == 0U) {
    result.error = "no captured samples";
    return false;
  }

  if ((context.sample_rate_hz == 0U) || (context.excitation_frequency_hz == 0U)) {
    result.error = "invalid measurement frequency";
    return false;
  }

  if (context.excitation_frequency_hz >= (context.sample_rate_hz / 2U)) {
    result.error = "sample rate too low for excitation frequency";
    return false;
  }

  if ((context.shunt_range >= kMeasurementSettingCount) ||
      (context.voltage_pga >= kMeasurementSettingCount) ||
      (context.current_pga >= kMeasurementSettingCount)) {
    result.error = "invalid frontend setting";
    return false;
  }

  const float voltage_gain = voltagePathGain(context.voltage_pga);
  const float current_gain = currentPathGain(context.current_pga);
  const float shunt_resistance = shuntResistanceOhms(context.shunt_range);
  if ((voltage_gain <= 0.0f) || (current_gain <= 0.0f) || (shunt_resistance <= 0.0f)) {
    result.error = "invalid scaling constants";
    return false;
  }

  const float angle_step =
      kTwoPi * static_cast<float>(context.excitation_frequency_hz) /
      static_cast<float>(context.sample_rate_hz);
  const float scale = 2.0f / static_cast<float>(sample_count);

  float v_cos_acc = 0.0f;
  float v_sin_acc = 0.0f;
  float i_cos_acc = 0.0f;
  float i_sin_acc = 0.0f;

  for (std::size_t index = 0; index < sample_count; ++index) {
    const float angle = angle_step * static_cast<float>(index);
    const float cosine = cosf(angle);
    const float sine = sinf(angle);

    const float voltage_signal =
        centeredSignalVolts(acquisition.voltageSample(index)) / voltage_gain;
    const float current_shunt_voltage =
        centeredSignalVolts(acquisition.currentSample(index)) / current_gain;
    const float current_signal = current_shunt_voltage / shunt_resistance;

    v_cos_acc += voltage_signal * cosine;
    v_sin_acc += voltage_signal * sine;
    i_cos_acc += current_signal * cosine;
    i_sin_acc += current_signal * sine;
  }

  result.voltage.cosine = scale * v_cos_acc;
  result.voltage.sine = scale * v_sin_acc;
  result.voltage.amplitude = sqrtf((result.voltage.cosine * result.voltage.cosine) +
                                   (result.voltage.sine * result.voltage.sine));
  result.voltage.phase_rad = atan2f(-result.voltage.sine, result.voltage.cosine);

  result.current.cosine = scale * i_cos_acc;
  result.current.sine = scale * i_sin_acc;
  result.current.amplitude = sqrtf((result.current.cosine * result.current.cosine) +
                                   (result.current.sine * result.current.sine));
  result.current.phase_rad = atan2f(-result.current.sine, result.current.cosine);

  result.samples_per_period =
      static_cast<float>(context.sample_rate_hz) /
      static_cast<float>(context.excitation_frequency_hz);
  result.captured_cycles = static_cast<float>(sample_count) / result.samples_per_period;
  // The current analog chain is inverted, so compensate the measured phase by 180 degrees.
  result.phase_difference_rad =
      normalizePhase(result.voltage.phase_rad - result.current.phase_rad + kPi);

  if (result.current.amplitude <= kAmplitudeEpsilon) {
    result.error = "current amplitude too small";
    return false;
  }

  const float impedance_magnitude = result.voltage.amplitude / result.current.amplitude;
  result.impedance.real = impedance_magnitude * cosf(result.phase_difference_rad);
  result.impedance.imag = impedance_magnitude * sinf(result.phase_difference_rad);

  result.success = true;
  result.error = "ok";
  return true;
}

float ImpedanceAnalyzer::shuntResistanceOhms(uint8_t shunt_range) {
  return (shunt_range < kMeasurementSettingCount) ? kShuntResistanceTable[shunt_range] : 0.0f;
}

float ImpedanceAnalyzer::voltagePathGain(uint8_t voltage_pga) {
  return (voltage_pga < kMeasurementSettingCount) ? kVoltagePathGainTable[voltage_pga] : 0.0f;
}

float ImpedanceAnalyzer::currentPathGain(uint8_t current_pga) {
  return (current_pga < kMeasurementSettingCount) ? kCurrentPathGainTable[current_pga] : 0.0f;
}

float ImpedanceAnalyzer::adcToVolts(uint16_t raw) {
  return static_cast<float>(raw) * board::kAdcReferenceVolts /
         static_cast<float>(board::kAdcMaxCode);
}

float ImpedanceAnalyzer::centeredSignalVolts(uint16_t raw) {
  return adcToVolts(raw) - (board::kAdcReferenceVolts * 0.5f);
}

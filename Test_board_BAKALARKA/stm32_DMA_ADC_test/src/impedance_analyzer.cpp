#include "impedance_analyzer.h"

#include "acquisition_engine.h"
#include "board_config.h"

#include <cmath>

namespace {

constexpr float kPi = 3.14159265358979323846f;
constexpr float kTwoPi = 2.0f * kPi;
constexpr float kAmplitudeEpsilon = 1.0e-9f;

// Placeholder frontend gains. Replace these with measured analog gains later.
constexpr float kVoltagePathGainTable[kMeasurementSettingCount] = {1.0f, 1.0f, 1.0f, 1.0f};
constexpr float kCurrentPathGainTable[kMeasurementSettingCount] = {1.0f, 1.0f, 1.0f, 1.0f};
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

ComplexValue subtractComplex(ComplexValue lhs, ComplexValue rhs) {
  return {lhs.real - rhs.real, lhs.imag - rhs.imag};
}

float complexMagnitude(ComplexValue value) {
  return sqrtf((value.real * value.real) + (value.imag * value.imag));
}

bool reciprocalComplex(ComplexValue value, ComplexValue& out) {
  const float denominator = (value.real * value.real) + (value.imag * value.imag);
  if (denominator <= kAmplitudeEpsilon) {
    return false;
  }

  out.real = value.real / denominator;
  out.imag = -value.imag / denominator;
  return true;
}

bool applyOpenShortCorrection(ComplexValue raw_impedance, ComplexValue open_impedance,
                              ComplexValue short_impedance, ComplexValue& corrected_impedance) {
  const ComplexValue shifted_raw = subtractComplex(raw_impedance, short_impedance);
  const ComplexValue shifted_open = subtractComplex(open_impedance, short_impedance);

  ComplexValue shifted_raw_admittance = {};
  ComplexValue open_parasitic_admittance = {};
  if (!reciprocalComplex(shifted_raw, shifted_raw_admittance) ||
      !reciprocalComplex(shifted_open, open_parasitic_admittance)) {
    return false;
  }

  ComplexValue dut_admittance = subtractComplex(shifted_raw_admittance, open_parasitic_admittance);
  return reciprocalComplex(dut_admittance, corrected_impedance);
}

}  // namespace

bool ImpedanceAnalyzer::analyze(const AcquisitionEngine& acquisition,
                                const MeasurementContext& context,
                                const MeasurementCalibrationStore& calibration_store,
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
  result.raw_impedance.real = impedance_magnitude * cosf(result.phase_difference_rad);
  result.raw_impedance.imag = impedance_magnitude * sinf(result.phase_difference_rad);
  result.calibrated_impedance = result.raw_impedance;
  result.calibration_status = "missing_open";

  const CalibrationRecord& open_record =
      calibrationRecord(calibration_store, CalibrationKind::Open, context);
  const CalibrationRecord& short_record =
      calibrationRecord(calibration_store, CalibrationKind::Short, context);

  if (!open_record.valid) {
    result.calibration_status = "missing_open";
  } else if (!short_record.valid) {
    result.calibration_status = "missing_short";
  } else if (open_record.excitation_frequency_hz != context.excitation_frequency_hz) {
    result.calibration_status = "open_freq_mismatch";
  } else if (short_record.excitation_frequency_hz != context.excitation_frequency_hz) {
    result.calibration_status = "short_freq_mismatch";
  } else if (applyOpenShortCorrection(result.raw_impedance, open_record.impedance,
                                      short_record.impedance,
                                      result.calibrated_impedance)) {
    result.calibrated = true;
    result.calibration_status = "applied";
  } else {
    result.calibration_status = "correction_failed";
  }

  result.success = true;
  result.error = "ok";
  return true;
}

CalibrationRecord& ImpedanceAnalyzer::calibrationRecord(
    MeasurementCalibrationStore& calibration_store, CalibrationKind kind,
    const MeasurementContext& context) const {
  if (kind == CalibrationKind::Open) {
    return calibration_store.open_records[context.shunt_range][context.voltage_pga]
                                         [context.current_pga];
  }

  return calibration_store.short_records[context.shunt_range][context.voltage_pga]
                                       [context.current_pga];
}

const CalibrationRecord& ImpedanceAnalyzer::calibrationRecord(
    const MeasurementCalibrationStore& calibration_store, CalibrationKind kind,
    const MeasurementContext& context) const {
  if (kind == CalibrationKind::Open) {
    return calibration_store.open_records[context.shunt_range][context.voltage_pga]
                                         [context.current_pga];
  }

  return calibration_store.short_records[context.shunt_range][context.voltage_pga]
                                       [context.current_pga];
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

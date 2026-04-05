#pragma once

#include <cstddef>
#include <cstdint>

class AcquisitionEngine;

constexpr std::size_t kMeasurementSettingCount = 4;

struct ComplexValue {
  float real = 0.0f;
  float imag = 0.0f;
};

struct DftComponent {
  float cosine = 0.0f;
  float sine = 0.0f;
  float amplitude = 0.0f;
  float phase_rad = 0.0f;
};

struct MeasurementContext {
  uint32_t sample_rate_hz = 0;
  uint32_t excitation_frequency_hz = 0;
  uint8_t shunt_range = 0;
  uint8_t voltage_pga = 0;
  uint8_t current_pga = 0;
};

struct CalibrationRecord {
  bool valid = false;
  uint32_t excitation_frequency_hz = 0;
  ComplexValue impedance = {};
};

struct MeasurementCalibrationStore {
  CalibrationRecord open_records[kMeasurementSettingCount][kMeasurementSettingCount]
                                [kMeasurementSettingCount] = {};
  CalibrationRecord short_records[kMeasurementSettingCount][kMeasurementSettingCount]
                                 [kMeasurementSettingCount] = {};
};

struct MeasurementResult {
  bool success = false;
  const char* error = "ok";
  DftComponent voltage = {};
  DftComponent current = {};
  float phase_difference_rad = 0.0f;
  float samples_per_period = 0.0f;
  float captured_cycles = 0.0f;
  ComplexValue raw_impedance = {};
  ComplexValue calibrated_impedance = {};
  bool calibrated = false;
  const char* calibration_status = "not_applied";
};

enum class CalibrationKind : uint8_t {
  Open = 0,
  Short = 1,
};

class ImpedanceAnalyzer {
 public:
  bool analyze(const AcquisitionEngine& acquisition, const MeasurementContext& context,
               const MeasurementCalibrationStore& calibration_store,
               MeasurementResult& result) const;

  CalibrationRecord& calibrationRecord(MeasurementCalibrationStore& calibration_store,
                                       CalibrationKind kind,
                                       const MeasurementContext& context) const;

  const CalibrationRecord& calibrationRecord(
      const MeasurementCalibrationStore& calibration_store, CalibrationKind kind,
      const MeasurementContext& context) const;

  static float shuntResistanceOhms(uint8_t shunt_range);
  static float voltagePathGain(uint8_t voltage_pga);
  static float currentPathGain(uint8_t current_pga);

 private:
  static float adcToVolts(uint16_t raw);
  static float centeredSignalVolts(uint16_t raw);
};

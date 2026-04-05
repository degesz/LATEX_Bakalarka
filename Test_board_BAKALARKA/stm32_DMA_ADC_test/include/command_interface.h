#pragma once

#include "acquisition_engine.h"
#include "firmware_state.h"
#include "impedance_analyzer.h"
#include "source_control.h"
#include "status_leds.h"

#include <SimpleCLI.h>

class CommandInterface {
 public:
  CommandInterface(FirmwareState& state, AcquisitionEngine& acquisition,
                   SourceControl& source_control, StatusLeds& status_leds);

  static CommandInterface* instance();

  void begin();
  void poll();

 private:
  static CommandInterface* instance_;

  static void onHelp(cmd* command);
  static void onId(cmd* command);
  static void onStatus(cmd* command);
  static void onAdc(cmd* command);
  static void onCapture(cmd* command);
  static void onCapturePair(cmd* command);
  static void onMeasure(cmd* command);
  static void onCalibrate(cmd* command);
  static void onDds(cmd* command);
  static void onAmplitude(cmd* command);
  static void onOffset(cmd* command);
  static void onRange(cmd* command);
  static void onVoltagePga(cmd* command);
  static void onCurrentPga(cmd* command);
  static void onLed(cmd* command);
  static void onParseError(cmd_error* error);

  bool parseUnsigned(const String& text, uint32_t& value) const;
  bool parseChannel(const String& text, StreamChannel& channel) const;
  bool parseCalibrationKind(const String& text, CalibrationKind& kind) const;
  float rawToVolts(uint16_t raw) const;

  MeasurementContext currentMeasurementContext() const;
  bool captureAndAnalyze(uint32_t sample_rate, uint32_t sample_count, MeasurementResult& result);
  void printMeasurement(const MeasurementResult& result) const;
  void printStatus();
  void updateDcMeasurements(std::size_t average_count);

  FirmwareState& state_;
  AcquisitionEngine& acquisition_;
  SourceControl& source_control_;
  StatusLeds& status_leds_;
  ImpedanceAnalyzer analyzer_;
  SimpleCLI cli_;
  String input_line_;
};

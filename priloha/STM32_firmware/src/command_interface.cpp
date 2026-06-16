#include "command_interface.h"

#include "board_config.h"
#include "serial_console.h"

#include <Arduino.h>

#include <cmath>
#include <cstdlib>

namespace {

Stream& console() {
  return consolePort();
}

CommandInterface* commandInterface() {
  return CommandInterface::instance();
}

void printKeyValue(const char* key, uint32_t value) {
  console().print(key);
  console().print(',');
  console().println(value);
}

void printFloatValue(const char* key, float value, int precision = 6) {
  console().print(key);
  console().print(',');
  console().println(value, precision);
}

void printTextValue(const char* key, const char* value) {
  console().print(key);
  console().print(',');
  console().println(value);
}

float radiansToDegrees(float radians) {
  return radians * 180.0f / 3.14159265358979323846f;
}

uint32_t defaultSampleRateForDds(uint32_t dds_frequency_hz) {
  if (dds_frequency_hz == 0U) {
    return board::kDefaultSampleRateHz;
  }

  const uint64_t target_rate =
      static_cast<uint64_t>(dds_frequency_hz) * board::kTargetSamplesPerPeriod;
  if (target_rate > board::kMaxSampleRateHz) {
    return board::kMaxSampleRateHz;
  }
  if (target_rate < board::kMinSampleRateHz) {
    return board::kMinSampleRateHz;
  }
  return static_cast<uint32_t>(target_rate);
}

uint32_t resolvedSampleRate(uint32_t requested_rate_hz, uint32_t dds_frequency_hz) {
  return (requested_rate_hz == 0U) ? defaultSampleRateForDds(dds_frequency_hz) : requested_rate_hz;
}

}  // namespace

CommandInterface* CommandInterface::instance_ = nullptr;

CommandInterface::CommandInterface(FirmwareState& state, AcquisitionEngine& acquisition,
                                   SourceControl& source_control, StatusLeds& status_leds)
    : state_(state),
      acquisition_(acquisition),
      source_control_(source_control),
      status_leds_(status_leds),
      cli_(),
      input_line_() {
  instance_ = this;
}

CommandInterface* CommandInterface::instance() {
  return instance_;
}

void CommandInterface::begin() {
  cli_.setOnError(onParseError);

  Command help = cli_.addCmd("help", onHelp);
  help.setDescription("Show available commands.");

  Command id = cli_.addCmd("id", onId);
  id.setDescription("Show firmware identity.");

  Command status = cli_.addCmd("status", onStatus);
  status.setDescription("Show current acquisition and output state.");

  Command capture = cli_.addCmd("capture", onCapture);
  capture.addPosArg("count", "500");
  capture.addPosArg("rate", "0");
  capture.addPosArg("channel", "voltage");
  capture.setDescription("Capture a burst and stream one AC channel as CSV.");

  Command capture_pair = cli_.addCmd("capturepair", onCapturePair);
  capture_pair.addPosArg("count", "500");
  capture_pair.addPosArg("rate", "0");
  capture_pair.setDescription("Capture a burst and stream both AC channels.");

  Command measure = cli_.addCmd("measure", onMeasure);
  measure.addPosArg("count", "500");
  measure.addPosArg("rate", "0");
  measure.setDescription("Capture a burst and calculate voltage/current amplitude, phase, and impedance.");

  Command dds = cli_.addCmd("dds", onDds);
  dds.addPosArg("freq_hz", "1000");
  dds.addPosArg("enable", "1");
  dds.setDescription("Set DDS frequency and enable state.");

  Command amp = cli_.addCmd("amp", onAmplitude);
  amp.addPosArg("value", "128");
  amp.setDescription("Set MCP4561 amplitude wiper 0..255.");

  Command offset = cli_.addCmd("offset", onOffset);
  offset.addPosArg("value", "0");
  offset.setDescription("Set PA7 offset PWM 0..255.");

  Command range = cli_.addCmd("range", onRange);
  range.addPosArg("value", "0");
  range.setDescription("Set shunt range 0..3.");

  Command vpga = cli_.addCmd("vpga", onVoltagePga);
  vpga.addPosArg("value", "0");
  vpga.setDescription("Set voltage PGA 0..3.");

  Command ipga = cli_.addCmd("ipga", onCurrentPga);
  ipga.addPosArg("value", "0");
  ipga.setDescription("Set current PGA 0..3.");

  input_line_.reserve(96);
}

void CommandInterface::poll() {
  while (console().available() > 0) {
    const char ch = static_cast<char>(console().read());
    if (ch == '\r') {
      continue;
    }

    if (ch == '\n') {
      if (input_line_.length() > 0) {
        cli_.parse(input_line_);
        console().println();
        input_line_.remove(0);
      }
      continue;
    }

    input_line_ += ch;
  }
}

void CommandInterface::onHelp(cmd*) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  console().println(self->cli_.toString());
}

void CommandInterface::onId(cmd*) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  console().println("device,stm32f103_lcr_meter");
  console().println("firmware,acquisition_control");
  printKeyValue("max_burst_samples", self->acquisition_.maxSampleCount());
}

void CommandInterface::onStatus(cmd*) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  self->printStatus();
}

void CommandInterface::onCapture(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t sample_count = self->state_.acquisition.sample_count;
  uint32_t sample_rate = self->state_.acquisition.sample_rate_hz;
  StreamChannel channel = self->state_.acquisition.stream_channel;

  if (!self->parseUnsigned(cmd.getArgument("count").getValue(), sample_count) ||
      !self->parseUnsigned(cmd.getArgument("rate").getValue(), sample_rate) ||
      !self->parseChannel(cmd.getArgument("channel").getValue(), channel)) {
    console().println("error,invalid capture arguments");
    return;
  }

  sample_rate = resolvedSampleRate(sample_rate, self->state_.outputs.dds_frequency_hz);

  if (!self->acquisition_.captureBurst(sample_rate, sample_count)) {
    console().print("error,");
    console().println(self->acquisition_.lastError());
    return;
  }

  self->state_.acquisition.sample_count = sample_count;
  self->state_.acquisition.sample_rate_hz = self->acquisition_.lastSampleRateHz();
  self->state_.acquisition.stream_channel = channel;

  console().print("sample_rate_hz,");
  console().println(self->acquisition_.lastSampleRateHz());
  console().println("index,value");
  for (std::size_t i = 0; i < self->acquisition_.lastSampleCount(); ++i) {
    const uint16_t value = (channel == StreamChannel::Voltage)
                               ? self->acquisition_.voltageSample(i)
                               : self->acquisition_.currentSample(i);
    console().print(i);
    console().print(',');
    console().println(value);
  }
}

void CommandInterface::onCapturePair(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t sample_count = self->state_.acquisition.sample_count;
  uint32_t sample_rate = self->state_.acquisition.sample_rate_hz;

  if (!self->parseUnsigned(cmd.getArgument("count").getValue(), sample_count) ||
      !self->parseUnsigned(cmd.getArgument("rate").getValue(), sample_rate)) {
    console().println("error,invalid capturepair arguments");
    return;
  }

  sample_rate = resolvedSampleRate(sample_rate, self->state_.outputs.dds_frequency_hz);

  if (!self->acquisition_.captureBurst(sample_rate, sample_count)) {
    console().print("error,");
    console().println(self->acquisition_.lastError());
    return;
  }

  self->state_.acquisition.sample_count = sample_count;
  self->state_.acquisition.sample_rate_hz = self->acquisition_.lastSampleRateHz();

  console().print("sample_rate_hz,");
  console().println(self->acquisition_.lastSampleRateHz());
  console().println("index,v_raw,i_raw");
  for (std::size_t i = 0; i < self->acquisition_.lastSampleCount(); ++i) {
    console().print(i);
    console().print(',');
    console().print(self->acquisition_.voltageSample(i));
    console().print(',');
    console().println(self->acquisition_.currentSample(i));
  }
}

void CommandInterface::onMeasure(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t sample_count = self->state_.acquisition.sample_count;
  uint32_t sample_rate = self->state_.acquisition.sample_rate_hz;

  if (!self->parseUnsigned(cmd.getArgument("count").getValue(), sample_count) ||
      !self->parseUnsigned(cmd.getArgument("rate").getValue(), sample_rate)) {
    console().println("error,invalid measure arguments");
    return;
  }

  sample_rate = resolvedSampleRate(sample_rate, self->state_.outputs.dds_frequency_hz);

  MeasurementResult result = {};
  if (!self->captureAndAnalyze(sample_rate, sample_count, result)) {
    console().print("error,");
    console().println(result.error);
    return;
  }

  self->printMeasurement(result);
}

void CommandInterface::onDds(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t frequency_hz = self->state_.outputs.dds_frequency_hz;
  uint32_t enabled = self->state_.outputs.dds_enabled ? 1U : 0U;

  if (!self->parseUnsigned(cmd.getArgument("freq_hz").getValue(), frequency_hz) ||
      !self->parseUnsigned(cmd.getArgument("enable").getValue(), enabled)) {
    console().println("error,invalid dds arguments");
    return;
  }

  self->source_control_.setDdsFrequency(frequency_hz);
  self->source_control_.setDdsEnabled(enabled != 0U);
  self->state_.outputs.dds_frequency_hz = frequency_hz;
  self->state_.outputs.dds_enabled = enabled != 0U;
  console().println("ok,dds");
}

void CommandInterface::onAmplitude(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t value = self->state_.outputs.amplitude_wiper;
  if (!self->parseUnsigned(cmd.getArgument("value").getValue(), value) || (value > 255U)) {
    console().println("error,invalid amp value");
    return;
  }

  if (!self->source_control_.setAmplitudeWiper(static_cast<uint8_t>(value))) {
    console().println("error,mcp4561 write failed");
    return;
  }

  self->state_.outputs.amplitude_wiper = static_cast<uint8_t>(value);
  console().println("ok,amp");
}

void CommandInterface::onOffset(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t value = self->state_.outputs.offset_pwm;
  if (!self->parseUnsigned(cmd.getArgument("value").getValue(), value) || (value > 255U)) {
    console().println("error,invalid offset value");
    return;
  }

  self->source_control_.setOffsetPwm(static_cast<uint8_t>(value));
  self->state_.outputs.offset_pwm = static_cast<uint8_t>(value);
  console().println("ok,offset");
}

void CommandInterface::onRange(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t value = self->state_.outputs.shunt_range;
  if (!self->parseUnsigned(cmd.getArgument("value").getValue(), value) || (value > 3U)) {
    console().println("error,invalid range value");
    return;
  }

  self->source_control_.setShuntRange(static_cast<uint8_t>(value));
  self->state_.outputs.shunt_range = static_cast<uint8_t>(value);
  console().println("ok,range");
}

void CommandInterface::onVoltagePga(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t value = self->state_.outputs.voltage_pga;
  if (!self->parseUnsigned(cmd.getArgument("value").getValue(), value) || (value > 3U)) {
    console().println("error,invalid vpga value");
    return;
  }

  self->source_control_.setVoltagePga(static_cast<uint8_t>(value));
  self->state_.outputs.voltage_pga = static_cast<uint8_t>(value);
  console().println("ok,vpga");
}

void CommandInterface::onCurrentPga(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t value = self->state_.outputs.current_pga;
  if (!self->parseUnsigned(cmd.getArgument("value").getValue(), value) || (value > 3U)) {
    console().println("error,invalid ipga value");
    return;
  }

  self->source_control_.setCurrentPga(static_cast<uint8_t>(value));
  self->state_.outputs.current_pga = static_cast<uint8_t>(value);
  console().println("ok,ipga");
}

void CommandInterface::onParseError(cmd_error* error) {
  CommandError command_error(error);
  console().print("error,");
  console().println(command_error.toString());
}

bool CommandInterface::parseUnsigned(const String& text, uint32_t& value) const {
  char* end = nullptr;
  const unsigned long parsed = strtoul(text.c_str(), &end, 0);
  if ((end == text.c_str()) || (*end != '\0')) {
    return false;
  }

  value = static_cast<uint32_t>(parsed);
  return true;
}

bool CommandInterface::parseChannel(const String& text, StreamChannel& channel) const {
  String lowered = text;
  lowered.toLowerCase();

  if ((lowered == "v") || (lowered == "voltage")) {
    channel = StreamChannel::Voltage;
    return true;
  }

  if ((lowered == "i") || (lowered == "current")) {
    channel = StreamChannel::Current;
    return true;
  }

  return false;
}

MeasurementContext CommandInterface::currentMeasurementContext() const {
  MeasurementContext context = {};
  context.sample_rate_hz = acquisition_.lastSampleRateHz();
  context.excitation_frequency_hz = state_.outputs.dds_frequency_hz;
  context.shunt_range = state_.outputs.shunt_range;
  context.voltage_pga = state_.outputs.voltage_pga;
  context.current_pga = state_.outputs.current_pga;
  return context;
}

bool CommandInterface::captureAndAnalyze(uint32_t sample_rate, uint32_t sample_count,
                                         MeasurementResult& result) {
  if (!acquisition_.captureBurst(sample_rate, sample_count)) {
    result = {};
    result.error = acquisition_.lastError();
    return false;
  }

  state_.acquisition.sample_count = sample_count;
  state_.acquisition.sample_rate_hz = acquisition_.lastSampleRateHz();

  MeasurementContext context = currentMeasurementContext();
  return analyzer_.analyze(acquisition_, context, result);
}

void CommandInterface::printMeasurement(const MeasurementResult& result) const {
  const MeasurementContext context = currentMeasurementContext();

  printKeyValue("sample_rate_hz", context.sample_rate_hz);
  printKeyValue("dds_frequency_hz", context.excitation_frequency_hz);
  printKeyValue("shunt_range", context.shunt_range);
  printKeyValue("voltage_pga", context.voltage_pga);
  printKeyValue("current_pga", context.current_pga);
  printFloatValue("shunt_resistance_ohm", analyzer_.shuntResistanceOhms(context.shunt_range));
  printFloatValue("samples_per_period", result.samples_per_period);
  printFloatValue("captured_cycles", result.captured_cycles);
  printFloatValue("voltage_amplitude_v", result.voltage.amplitude);
  printFloatValue("voltage_phase_deg", radiansToDegrees(result.voltage.phase_rad));
  printFloatValue("current_amplitude_a", result.current.amplitude);
  printFloatValue("current_phase_deg", radiansToDegrees(result.current.phase_rad));
  printFloatValue("phase_diff_deg", radiansToDegrees(result.phase_difference_rad));
  printFloatValue("impedance_mag_ohm", sqrtf((result.impedance.real * result.impedance.real) +
                                            (result.impedance.imag * result.impedance.imag)));
  printFloatValue("impedance_real_ohm", result.impedance.real);
  printFloatValue("impedance_imag_ohm", result.impedance.imag);
}

void CommandInterface::printStatus() {
  printKeyValue("sample_rate_hz", state_.acquisition.sample_rate_hz);
  printKeyValue("sample_count", state_.acquisition.sample_count);

  printKeyValue("dds_frequency_hz", state_.outputs.dds_frequency_hz);
  printKeyValue("dds_enabled", state_.outputs.dds_enabled ? 1U : 0U);
  printKeyValue("amp_wiper", state_.outputs.amplitude_wiper);
  printKeyValue("offset_pwm", state_.outputs.offset_pwm);
  printKeyValue("shunt_range", state_.outputs.shunt_range);
  printKeyValue("voltage_pga", state_.outputs.voltage_pga);
  printKeyValue("current_pga", state_.outputs.current_pga);
}

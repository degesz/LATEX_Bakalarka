#include "command_interface.h"

#include "board_config.h"

#include <Arduino.h>

#include <cstdlib>

namespace {

HardwareSerial& console() {
  return Serial;
}

CommandInterface* commandInterface() {
  return CommandInterface::instance();
}

void printKeyValue(const char* key, uint32_t value) {
  console().print(key);
  console().print(',');
  console().println(value);
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

  Command adc = cli_.addCmd("adc", onAdc);
  adc.addPosArg("avg", "16");
  adc.setDescription("Read slow lowpass DC channels.");

  Command capture = cli_.addCmd("capture", onCapture);
  capture.addPosArg("count", "256");
  capture.addPosArg("rate", "200000");
  capture.addPosArg("channel", "voltage");
  capture.setDescription("Capture a burst and stream one AC channel as CSV.");

  Command capture_pair = cli_.addCmd("capturepair", onCapturePair);
  capture_pair.addPosArg("count", "256");
  capture_pair.addPosArg("rate", "200000");
  capture_pair.setDescription("Capture a burst and stream both AC channels.");

  Command dds = cli_.addCmd("dds", onDds);
  dds.addPosArg("freq_hz", "1000");
  dds.addPosArg("enable", "1");
  dds.setDescription("Set DDS frequency and enable state.");

  Command amp = cli_.addCmd("amp", onAmplitude);
  amp.addPosArg("value", "128");
  amp.setDescription("Set MCP4561 amplitude wiper 0..255.");

  Command offset = cli_.addCmd("offset", onOffset);
  offset.addPosArg("value", "128");
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

  Command led = cli_.addCmd("led", onLed);
  led.addPosArg("r", "0");
  led.addPosArg("g", "0");
  led.addPosArg("b", "0");
  led.addPosArg("index", "-1");
  led.setDescription("Set NeoPixel color for one pixel or all pixels.");

  Command mcpaddr = cli_.addCmd("mcpaddr", onMcpAddress);
  mcpaddr.addPosArg("address", "46");
  mcpaddr.setDescription("Set MCP4561 7-bit I2C address (46 or 47 decimal).");

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

  self->updateDcMeasurements(16);
  self->printStatus();
}

void CommandInterface::onAdc(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t average_count = 16;
  if (!self->parseUnsigned(cmd.getArgument("avg").getValue(), average_count)) {
    console().println("error,invalid avg");
    return;
  }

  self->updateDcMeasurements(average_count);
  printKeyValue("v_lowpass_raw", self->state_.dc.voltage_lowpass_raw);
  printKeyValue("i_lowpass_raw", self->state_.dc.current_lowpass_raw);
  console().print("v_lowpass_volts,");
  console().println(self->rawToVolts(self->state_.dc.voltage_lowpass_raw), 6);
  console().print("i_lowpass_volts,");
  console().println(self->rawToVolts(self->state_.dc.current_lowpass_raw), 6);
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

  if (!self->acquisition_.captureBurst(sample_rate, sample_count)) {
    console().print("error,");
    console().println(self->acquisition_.lastError());
    return;
  }

  self->state_.acquisition.sample_count = sample_count;
  self->state_.acquisition.sample_rate_hz = self->acquisition_.lastSampleRateHz();
  self->state_.acquisition.stream_channel = channel;
  self->updateDcMeasurements(16);

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

  if (!self->acquisition_.captureBurst(sample_rate, sample_count)) {
    console().print("error,");
    console().println(self->acquisition_.lastError());
    return;
  }

  self->state_.acquisition.sample_count = sample_count;
  self->state_.acquisition.sample_rate_hz = self->acquisition_.lastSampleRateHz();
  self->updateDcMeasurements(16);

  console().print("sample_rate_hz,");
  console().println(self->acquisition_.lastSampleRateHz());
  printKeyValue("dc_voltage_lowpass_raw", self->state_.dc.voltage_lowpass_raw);
  printKeyValue("dc_current_lowpass_raw", self->state_.dc.current_lowpass_raw);
  console().println("index,v_raw,i_raw");
  for (std::size_t i = 0; i < self->acquisition_.lastSampleCount(); ++i) {
    console().print(i);
    console().print(',');
    console().print(self->acquisition_.voltageSample(i));
    console().print(',');
    console().println(self->acquisition_.currentSample(i));
  }
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

void CommandInterface::onLed(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t r = 0;
  uint32_t g = 0;
  uint32_t b = 0;
  uint32_t index = 0;

  if (!self->parseUnsigned(cmd.getArgument("r").getValue(), r) ||
      !self->parseUnsigned(cmd.getArgument("g").getValue(), g) ||
      !self->parseUnsigned(cmd.getArgument("b").getValue(), b)) {
    console().println("error,invalid led color");
    return;
  }

  const String index_text = cmd.getArgument("index").getValue();
  if (index_text == "-1") {
    self->status_leds_.fill(static_cast<uint8_t>(r), static_cast<uint8_t>(g),
                            static_cast<uint8_t>(b));
    for (std::size_t i = 0; i < board::kNeoPixelCount; ++i) {
      self->state_.outputs.pixels[i] = {static_cast<uint8_t>(r), static_cast<uint8_t>(g),
                                        static_cast<uint8_t>(b)};
    }
    console().println("ok,led");
    return;
  }

  if (!self->parseUnsigned(index_text, index) || (index >= board::kNeoPixelCount)) {
    console().println("error,invalid led index");
    return;
  }

  self->status_leds_.setPixel(index, static_cast<uint8_t>(r), static_cast<uint8_t>(g),
                              static_cast<uint8_t>(b));
  self->state_.outputs.pixels[index] = {static_cast<uint8_t>(r), static_cast<uint8_t>(g),
                                        static_cast<uint8_t>(b)};
  console().println("ok,led");
}

void CommandInterface::onMcpAddress(cmd* command) {
  auto* self = commandInterface();
  if (self == nullptr) {
    return;
  }

  Command cmd(command);
  uint32_t address = self->state_.outputs.mcp4561_address;
  if (!self->parseUnsigned(cmd.getArgument("address").getValue(), address) ||
      (address > 0x7FU)) {
    console().println("error,invalid mcp address");
    return;
  }

  if (!self->source_control_.setMcp4561Address(static_cast<uint8_t>(address))) {
    console().println("error,unsupported mcp address");
    return;
  }

  self->state_.outputs.mcp4561_address = static_cast<uint8_t>(address);
  console().println("ok,mcpaddr");
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

float CommandInterface::rawToVolts(uint16_t raw) const {
  return static_cast<float>(raw) * board::kAdcReferenceVolts /
         static_cast<float>(board::kAdcMaxCode);
}

void CommandInterface::printStatus() {
  printKeyValue("sample_rate_hz", state_.acquisition.sample_rate_hz);
  printKeyValue("sample_count", state_.acquisition.sample_count);
  console().print("stream_channel,");
  console().println(streamChannelName(state_.acquisition.stream_channel));

  printKeyValue("dds_frequency_hz", state_.outputs.dds_frequency_hz);
  printKeyValue("dds_enabled", state_.outputs.dds_enabled ? 1U : 0U);
  printKeyValue("amp_wiper", state_.outputs.amplitude_wiper);
  printKeyValue("offset_pwm", state_.outputs.offset_pwm);
  printKeyValue("shunt_range", state_.outputs.shunt_range);
  printKeyValue("voltage_pga", state_.outputs.voltage_pga);
  printKeyValue("current_pga", state_.outputs.current_pga);
  printKeyValue("mcp4561_address", state_.outputs.mcp4561_address);
  printKeyValue("v_lowpass_raw", state_.dc.voltage_lowpass_raw);
  printKeyValue("i_lowpass_raw", state_.dc.current_lowpass_raw);
}

void CommandInterface::updateDcMeasurements(std::size_t average_count) {
  acquisition_.sampleDc(state_.dc.voltage_lowpass_raw, state_.dc.current_lowpass_raw,
                        average_count);
  state_.dc.updated_at_ms = millis();
}

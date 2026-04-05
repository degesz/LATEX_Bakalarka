#include <Arduino.h>

#include "acquisition_engine.h"
#include "command_interface.h"
#include "firmware_state.h"
#include "source_control.h"
#include "status_leds.h"

namespace {

FirmwareState g_state;
StatusLeds g_status_leds;
SourceControl g_source_control;
AcquisitionEngine g_acquisition;
CommandInterface g_cli(g_state, g_acquisition, g_source_control, g_status_leds);

}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
#if defined(USBCON) || defined(PIO_FRAMEWORK_ARDUINO_ENABLE_CDC)
  const uint32_t usb_wait_start_ms = millis();
  while (!Serial && ((millis() - usb_wait_start_ms) < 1500U)) {
    delay(10);
  }
#endif

  g_status_leds.begin();
  g_source_control.begin();
  g_cli.begin();

  if (!g_acquisition.begin()) {
    g_status_leds.fill(32, 0, 0);
    Serial.print("error,");
    Serial.println(g_acquisition.lastError());
  } else {
    g_status_leds.fill(0, 0, 16);
  }
  Serial.println("ready,stm32f103_lcr_meter");
}

void loop() {
  g_cli.poll();
  g_status_leds.update();
}

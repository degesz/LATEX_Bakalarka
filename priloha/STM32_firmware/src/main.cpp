#include <Arduino.h>

#include "acquisition_engine.h"
#include "command_interface.h"
#include "firmware_state.h"
#include "serial_console.h"
#include "source_control.h"
#include "status_leds.h"

#if !defined(CONSOLE_USE_USB_SERIAL)
#error "CONSOLE_USE_USB_SERIAL must be defined by the build configuration."
#endif

namespace {

FirmwareState g_state;
StatusLeds g_status_leds;
SourceControl g_source_control;
AcquisitionEngine g_acquisition;
CommandInterface g_cli(g_state, g_acquisition, g_source_control, g_status_leds);

}  // namespace

Stream& consolePort() {
#if CONSOLE_USE_USB_SERIAL
  return Serial;
#else
  return Serial1;
#endif
}

void beginConsole() {
#if CONSOLE_USE_USB_SERIAL
  Serial.begin(board::kSerialBaud);
#else
  Serial1.begin(board::kSerialBaud);
#endif
}

bool consoleReady() {
#if CONSOLE_USE_USB_SERIAL
  return static_cast<bool>(Serial);
#else
  return true;
#endif
}

void setup() {
  beginConsole();
#if CONSOLE_USE_USB_SERIAL
#if defined(USBCON) || defined(PIO_FRAMEWORK_ARDUINO_ENABLE_CDC)
  const uint32_t usb_wait_start_ms = millis();
  while (!consoleReady() && ((millis() - usb_wait_start_ms) < 1500U)) {
    delay(10);
  }
#endif
#endif

  g_status_leds.begin();
  g_source_control.begin();
  g_cli.begin();

  if (!g_acquisition.begin()) {
    // g_status_leds.fill(32, 0, 0);
    consolePort().print("error,");
    consolePort().println(g_acquisition.lastError());
  } else {
    // g_status_leds.fill(0, 0, 16);
  }
  consolePort().println("ready,stm32f103_lcr_meter");
}

void loop() {
  g_cli.poll();
  g_status_leds.update();
}

Import("env")


def _define_name(entry):
    if isinstance(entry, tuple):
        return entry[0]
    return entry


defines = list(env.get("CPPDEFINES", []))
use_usb_serial = True

for define in defines:
    name = _define_name(define)
    if name == "CONSOLE_USE_USB_SERIAL":
        if isinstance(define, tuple):
            use_usb_serial = str(define[1]) not in ("0", "False", "false")
        else:
            use_usb_serial = True
        break


filtered_defines = [
    define
    for define in defines
    if _define_name(define)
    not in (
        "PIO_FRAMEWORK_ARDUINO_ENABLE_CDC",
        "PIO_FRAMEWORK_ARDUINO_ENABLE_CDC_WITHOUT_SERIAL",
        "USBCON",
        "USBD_USE_CDC",
        "HAL_PCD_MODULE_ENABLED",
        "DISABLE_GENERIC_SERIALUSB",
        "USB_VID",
        "USB_PID",
    )
]

if use_usb_serial:
    filtered_defines.extend(["PIO_FRAMEWORK_ARDUINO_ENABLE_CDC", "USBCON"])

env.Replace(CPPDEFINES=filtered_defines)

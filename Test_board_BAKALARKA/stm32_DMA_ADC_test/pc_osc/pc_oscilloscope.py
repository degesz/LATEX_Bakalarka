#!/usr/bin/env python3
"""Simple serial oscilloscope view with an interactive serial terminal."""

from __future__ import annotations

import argparse
from collections import deque
import math
import queue
import re
import threading
import time
from dataclasses import dataclass
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Optional

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib import font_manager
import serial
import serial.tools.list_ports


UI_BG = "#000000"
UI_FG = "#ff9f1c"
UI_ACCENT = "#ffbf69"
UI_MUTED = "#8a5a14"
UI_SUCCESS = "#52b788"
UI_MEASUREMENT_BG = "#0d1b2a"
UI_IMPEDANCE_BG = "#2b0f14"
UI_RLC_BG = "#112418"
UI_FONT = "Ioskeley Mono"
VOLTAGE_LINE_COLOR = "#ff0000"
CURRENT_LINE_COLOR = "#4ea8de"
AXIS_FONT_SIZE = 20
TICK_FONT_SIZE = 18
TERMINAL_MAX_LINES = 300
DDS_FREQ_MIN_HZ = 100
DDS_FREQ_MAX_HZ = 100_000
DDS_FREQ_STEPS_PER_DECADE = 10
DDS_FREQ_SLIDER_STEPS = 30
DDS_LABEL_STEPS = (0, 10, 20, 30)
DDS_SCALE_FONT_SIZE = 11
DDS_VALUE_FONT_SIZE = 10
DDS_SLIDER_CANVAS_HEIGHT = 108
DDS_SLIDER_LEFT_PAD = 28
DDS_SLIDER_RIGHT_PAD = 34
DDS_SLIDER_HANDLE_WIDTH = 22
DDS_SLIDER_HANDLE_HEIGHT = 26
FRONTEND_GAIN_TABLE = (1.0, 2.0, 5.0, 10.0)
SHUNT_RESISTANCE_TABLE = (100.0, 1000.0, 10000.0, 100000.0)
SERIAL_RESPONSE_TIMEOUT_S = 0.300
SERIAL_COMMAND_LOCKOUT_S = 0.300
WARNING_PANEL_DURATION_MS = 2000
RECOVER_RECONNECT_DELAY_MS = 250

VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
PAIR_VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")
SAMPLE_RATE_LINE_RE = re.compile(r"^\s*sample_rate_hz\s*,\s*(\d+)\s*$", re.IGNORECASE)
MEASURE_COMMAND_RE = re.compile(r"^\s*measure(?:\s+\d+\s+\d+)?\s*$", re.IGNORECASE)
MEASUREMENT_VALUE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(.+?)\s*$")
CAPTURE_COMMAND_RE = re.compile(
    r"^\s*capture\s+(\d+)\s+(\d+)\s+(voltage|current|v|i)\s*$", re.IGNORECASE
)
CAPTUREPAIR_COMMAND_RE = re.compile(r"^\s*capturepair\s+(\d+)\s+(\d+)\s*$", re.IGNORECASE)
DDS_COMMAND_RE = re.compile(r"^\s*dds\s+(\d+)\s+([01])\s*$", re.IGNORECASE)
RANGE_COMMAND_RE = re.compile(r"^\s*range\s+(\d+)\s*$", re.IGNORECASE)
VPGA_COMMAND_RE = re.compile(r"^\s*vpga\s+(\d+)\s*$", re.IGNORECASE)
IPGA_COMMAND_RE = re.compile(r"^\s*ipga\s+(\d+)\s*$", re.IGNORECASE)


@dataclass
class Frame:
    sample_indices: list[int]
    primary_values: list[int]
    primary_label: str = "Voltage"
    secondary_values: Optional[list[int]] = None
    secondary_label: Optional[str] = None
    sample_rate_hz: Optional[int] = None


@dataclass
class SerialLine:
    direction: str
    text: str


@dataclass
class Measurement:
    values: dict[str, str]


@dataclass
class WorkerEvent:
    kind: str
    message: str


def format_engineering(value: float, precision: int = 3) -> str:
    prefixes = (
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "u"),
        (1e-9, "n"),
        (1e-12, "p"),
    )

    magnitude = abs(value)
    for scale, prefix in prefixes:
        if magnitude >= scale:
            return f"{value / scale:.{precision}f}{prefix}"
    return f"{value / 1e-12:.{precision}f}p"


def dds_frequency_from_step(step: int) -> int:
    clamped_step = max(0, min(DDS_FREQ_SLIDER_STEPS, int(step)))
    return int(round(DDS_FREQ_MIN_HZ * (10 ** (clamped_step / DDS_FREQ_STEPS_PER_DECADE))))


def dds_step_from_frequency(frequency_hz: int) -> int:
    clamped_frequency = max(DDS_FREQ_MIN_HZ, min(DDS_FREQ_MAX_HZ, int(frequency_hz)))
    step = round(DDS_FREQ_STEPS_PER_DECADE * math.log10(clamped_frequency / DDS_FREQ_MIN_HZ))
    return max(0, min(DDS_FREQ_SLIDER_STEPS, step))


def time_axis_unit(frame_duration_s: float) -> tuple[str, float]:
    """Return ('ms' or 'us', scale factor from seconds to that unit)."""
    if frame_duration_s >= 0.01:
        return ("ms", 1e3)
    return ("us", 1e6)


def resolve_matplotlib_font_family(preferred_family: str) -> str:
    """Return a Matplotlib-known family name close to the requested UI font."""
    available_fonts = {}
    for entry in font_manager.fontManager.ttflist:
        name = entry.name.strip()
        available_fonts.setdefault(name.lower(), name)

    normalized = preferred_family.strip().lower()
    if normalized in available_fonts:
        return available_fonts[normalized]

    compact = normalized.replace(" ", "")
    for key, value in available_fonts.items():
        if key.replace(" ", "") == compact:
            return value

    for fallback in ("Consolas", "Cascadia Mono", "Courier New", "DejaVu Sans Mono"):
        resolved = available_fonts.get(fallback.lower())
        if resolved is not None:
            return resolved

    return "monospace"


def autodetect_port() -> Optional[str]:
    """Return the first likely serial device, or None."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None

    preferred = []
    fallback = []

    for p in ports:
        dev = (p.device or "").lower()
        desc = (p.description or "").lower()
        if any(token in dev for token in ("ttyacm", "ttyusb", "usbmodem", "com")):
            preferred.append(p.device)
        elif "stlink" in desc or "usb" in desc or "serial" in desc:
            fallback.append(p.device)

    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return ports[0].device


def available_ports() -> list[str]:
    """Return currently available serial port device names."""
    return [port.device for port in serial.tools.list_ports.comports() if port.device]


def serial_worker(
    port: str,
    baud: int,
    frame_queue: "queue.Queue[Frame]",
    measurement_queue: "queue.Queue[Measurement]",
    line_queue: "queue.Queue[SerialLine]",
    event_queue: "queue.Queue[WorkerEvent]",
    command_queue: "queue.Queue[str]",
    stop_event: threading.Event,
) -> None:
    primary_values_by_index = {}
    secondary_values_by_index = {}
    measurement_values: dict[str, str] = {}
    sample_rate_hz: Optional[int] = None
    collecting = False
    expected_count = 0
    awaiting_frame = False
    awaiting_measurement = False
    deferred_commands: deque[str] = deque()
    expected_frame_kind = "single"
    active_channel_label = "Voltage"
    awaiting_response = False
    response_deadline = 0.0
    lockout_until = 0.0
    pending_response_command = ""

    def enqueue_line(direction: str, text: str) -> None:
        try:
            line_queue.put_nowait(SerialLine(direction, text))
        except queue.Full:
            pass

    def enqueue_event(kind: str, message: str) -> None:
        try:
            event_queue.put_nowait(WorkerEvent(kind, message))
        except queue.Full:
            pass

    def response_received() -> None:
        nonlocal awaiting_response, response_deadline, pending_response_command
        awaiting_response = False
        response_deadline = 0.0
        pending_response_command = ""

    def send_command(command: str) -> bool:
        nonlocal primary_values_by_index, secondary_values_by_index, sample_rate_hz, measurement_values
        nonlocal collecting, expected_count, awaiting_frame, expected_frame_kind
        nonlocal active_channel_label, awaiting_measurement
        nonlocal awaiting_response, response_deadline, lockout_until, pending_response_command
        payload = f"{command.rstrip()}\n".encode("ascii", errors="replace")
        ser.write(payload)
        ser.flush()
        enqueue_line("tx", command.rstrip())
        awaiting_response = True
        response_deadline = time.monotonic() + SERIAL_RESPONSE_TIMEOUT_S
        pending_response_command = command.rstrip()

        capture_match = CAPTURE_COMMAND_RE.match(command)
        if capture_match:
            expected_count = int(capture_match.group(1))
            awaiting_frame = True
            sample_rate_hz = None
            primary_values_by_index = {}
            secondary_values_by_index = {}
            collecting = False
            expected_frame_kind = "single"
            requested_channel = capture_match.group(3).lower()
            active_channel_label = "Current" if requested_channel in ("current", "i") else "Voltage"
            lockout_until = time.monotonic() + SERIAL_COMMAND_LOCKOUT_S
            return True

        capturepair_match = CAPTUREPAIR_COMMAND_RE.match(command)
        if capturepair_match:
            expected_count = int(capturepair_match.group(1))
            awaiting_frame = True
            sample_rate_hz = None
            primary_values_by_index = {}
            secondary_values_by_index = {}
            collecting = False
            expected_frame_kind = "pair"
            active_channel_label = "Voltage"
            lockout_until = time.monotonic() + SERIAL_COMMAND_LOCKOUT_S
            return True

        if MEASURE_COMMAND_RE.match(command):
            sample_rate_hz = None
            measurement_values = {}
            awaiting_measurement = True
            lockout_until = time.monotonic() + SERIAL_COMMAND_LOCKOUT_S
        return True

    def flush_deferred_commands() -> bool:
        while deferred_commands:
            if awaiting_frame or awaiting_measurement or awaiting_response:
                return True
            if time.monotonic() < lockout_until:
                return True
            command = deferred_commands.popleft()
            send_command(command)
            if awaiting_frame or awaiting_measurement or awaiting_response:
                return True
        return True

    try:
        ser = serial.Serial(port, baudrate=baud, timeout=0.2)
    except serial.SerialException as exc:
        print(f"[serial] open error: {exc}")
        print("[serial] Check the port name or run without --port to use auto-detect.")
        return

    with ser:
        time.sleep(0.2)
        ser.reset_input_buffer()

        while not stop_event.is_set():
            now = time.monotonic()
            if awaiting_response and now >= response_deadline:
                enqueue_event(
                    "timeout",
                    f"No response within {int(SERIAL_RESPONSE_TIMEOUT_S * 1000)} ms after '{pending_response_command}'.",
                )
                break

            if deferred_commands and not awaiting_frame and not awaiting_measurement:
                try:
                    flush_deferred_commands()
                except serial.SerialException as exc:
                    print(f"[serial] write error: {exc}")
                    break

            try:
                while True:
                    command = command_queue.get_nowait()
                    if (
                        CAPTURE_COMMAND_RE.match(command)
                        or CAPTUREPAIR_COMMAND_RE.match(command)
                        or MEASURE_COMMAND_RE.match(command)
                        or DDS_COMMAND_RE.match(command)
                    ) and (awaiting_frame or awaiting_measurement):
                        deferred_commands.append(command)
                        continue
                    if awaiting_response or (time.monotonic() < lockout_until):
                        deferred_commands.append(command)
                        continue
                    send_command(command)
            except queue.Empty:
                pass
            except serial.SerialException as exc:
                print(f"[serial] write error: {exc}")
                break

            try:
                raw = ser.readline()
            except serial.SerialException as exc:
                print(f"[serial] read error: {exc}")
                break

            if not raw:
                continue

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except UnicodeDecodeError:
                continue

            if not line:
                continue

            enqueue_line("rx", line)
            response_received()
            try:
                flush_deferred_commands()
            except serial.SerialException as exc:
                print(f"[serial] write error: {exc}")
                break

            if line.lower().startswith("index,value"):
                collecting = True
                expected_frame_kind = "single"
                primary_values_by_index = {}
                secondary_values_by_index = {}
                continue

            if line.lower().startswith("index,v_raw,i_raw"):
                collecting = True
                expected_frame_kind = "pair"
                primary_values_by_index = {}
                secondary_values_by_index = {}
                continue

            measurement_match = MEASUREMENT_VALUE_RE.match(line)
            if awaiting_measurement and measurement_match:
                measurement_values[measurement_match.group(1)] = measurement_match.group(2)
                if measurement_match.group(1) == "impedance_imag_ohm":
                    try:
                        measurement_queue.put_nowait(Measurement(dict(measurement_values)))
                    except queue.Full:
                        pass
                    measurement_values = {}
                    awaiting_measurement = False
                    try:
                        flush_deferred_commands()
                    except serial.SerialException as exc:
                        print(f"[serial] write error: {exc}")
                        break
                continue

            sample_rate_match = SAMPLE_RATE_LINE_RE.match(line)
            if sample_rate_match:
                sample_rate_hz = int(sample_rate_match.group(1))
                continue

            if line.lower().startswith("error,"):
                print(f"[mcu] {line}")
                collecting = False
                primary_values_by_index = {}
                secondary_values_by_index = {}
                measurement_values = {}
                awaiting_frame = False
                awaiting_measurement = False
                try:
                    flush_deferred_commands()
                except serial.SerialException as exc:
                    print(f"[serial] write error: {exc}")
                    break
                continue

            pair_match = PAIR_VALUE_LINE_RE.match(line)
            if pair_match:
                if not collecting:
                    collecting = True
                    expected_frame_kind = "pair"
                    primary_values_by_index = {}
                    secondary_values_by_index = {}

                idx = int(pair_match.group(1))
                primary_values_by_index[idx] = int(pair_match.group(2))
                secondary_values_by_index[idx] = int(pair_match.group(3))
            else:
                match = VALUE_LINE_RE.match(line)
                if not match:
                    continue

                if not collecting:
                    collecting = True
                    expected_frame_kind = "single"
                    primary_values_by_index = {}
                    secondary_values_by_index = {}

                idx = int(match.group(1))
                primary_values_by_index[idx] = int(match.group(2))

            if len(primary_values_by_index) >= expected_count and expected_count > 0:
                indices = sorted(primary_values_by_index.keys())[:expected_count]
                if expected_frame_kind == "pair":
                    frame_queue.put(
                        Frame(
                            indices,
                            [primary_values_by_index[i] for i in indices],
                            primary_label="Voltage",
                            secondary_values=[secondary_values_by_index[i] for i in indices],
                            secondary_label="Current",
                            sample_rate_hz=sample_rate_hz,
                        )
                    )
                else:
                    frame_queue.put(
                        Frame(
                            indices,
                            [primary_values_by_index[i] for i in indices],
                            primary_label=active_channel_label,
                            sample_rate_hz=sample_rate_hz,
                        )
                    )
                collecting = False
                primary_values_by_index = {}
                secondary_values_by_index = {}
                awaiting_frame = False
                try:
                    flush_deferred_commands()
                except serial.SerialException as exc:
                    print(f"[serial] write error: {exc}")
                    break


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive serial ADC samples and show a live oscilloscope view."
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port (e.g. /dev/ttyACM0, /dev/ttyUSB0, COM5). If omitted, tries auto-detect.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200).",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=500,
        help="Samples requested per capture frame (default: 500).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=0,
        help="Requested capture sample rate in Hz (default: 0 = auto from DDS).",
    )
    parser.add_argument(
        "--channel",
        choices=("voltage", "current", "both", "v", "i"),
        default="both",
        help="Capture mode: voltage, current, or both via capturepair (default: both).",
    )
    parser.add_argument(
        "--request-interval-ms",
        type=int,
        default=100,
        help="Delay between automatic capture requests in milliseconds (default: 100).",
    )
    parser.add_argument(
        "--refresh-ms",
        type=int,
        default=50,
        help="UI polling period in milliseconds (default: 50).",
    )
    parser.add_argument(
        "--vref",
        type=float,
        default=3.3,
        help="ADC reference voltage for secondary Y axis display (default: 3.3V).",
    )
    parser.add_argument(
        "--adc-max",
        type=int,
        default=4095,
        help="Maximum ADC code for scaling to volts (default: 4095 for 12-bit).",
    )
    return parser


class OscilloscopeApp:
    def __init__(self, args: argparse.Namespace, port: Optional[str]) -> None:
        self.args = args
        self.port: Optional[str] = None
        self.frame_queue: "queue.Queue[Frame]" = queue.Queue(maxsize=8)
        self.measurement_queue: "queue.Queue[Measurement]" = queue.Queue(maxsize=8)
        self.line_queue: "queue.Queue[SerialLine]" = queue.Queue(maxsize=256)
        self.event_queue: "queue.Queue[WorkerEvent]" = queue.Queue(maxsize=32)
        self.command_queue: "queue.Queue[str]" = queue.Queue(maxsize=64)
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.available_port_values: list[str] = []

        self.root = tk.Tk()
        self.root.title("STM32 Serial Oscilloscope")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.configure(bg=UI_BG)
        self._maximize_window()

        self.mpl_font_family = resolve_matplotlib_font_family(UI_FONT)
        self.samples_var = tk.StringVar(value=str(args.samples))
        self.sample_rate_var = tk.StringVar(value=str(args.sample_rate))
        normalized_channel = {"v": "voltage", "i": "current"}.get(args.channel, args.channel)
        self.channel_var = tk.StringVar(value="both" if normalized_channel == "both" else normalized_channel)
        self.port_var = tk.StringVar(value=port or autodetect_port() or "")
        self.connect_button_var = tk.StringVar(value="Connect")
        self.command_var = tk.StringVar()
        self.auto_capture_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value=f"Disconnected | select a port and connect @ {args.baud} baud")
        self.dds_frequency_step_var = tk.IntVar(value=dds_step_from_frequency(1000))
        self.measurement_vars = {
            "sample_rate_hz": tk.StringVar(value=""),
            "dds_frequency_hz": tk.StringVar(value=""),
            "voltage_amplitude_v": tk.StringVar(value=""),
            "current_amplitude_a": tk.StringVar(value=""),
            "phase_diff_deg": tk.StringVar(value=""),
            "impedance_mag_ohm": tk.StringVar(value=""),
            "impedance_real_ohm": tk.StringVar(value=""),
            "impedance_imag_ohm": tk.StringVar(value=""),
            "rlc_model": tk.StringVar(value=""),
            "series_r_ohm": tk.StringVar(value=""),
            "series_l_h": tk.StringVar(value=""),
            "series_c_f": tk.StringVar(value=""),
        }
        self.dds_apply_pending = True

        self.latest: Optional[Frame] = None
        self.frame_counter = 0
        self.flash_ticks_remaining = 0
        self.flash_duration_ticks = 4
        self.capture_job: Optional[str] = None
        self.terminal_history: deque[str] = deque(maxlen=TERMINAL_MAX_LINES)
        self.terminal_dirty = False
        self.legend: Optional[object] = None
        self.legend_labels: tuple[str, ...] = ()
        self.shunt_range = 0
        self.voltage_pga = 0
        self.current_pga = 0
        self.warning_hide_job: Optional[str] = None
        self.reconnect_job: Optional[str] = None
        self.auto_reconnect_active = False

        self._build_ui()
        self._refresh_port_selector()
        self._poll_queues()
        self.root.after(100, self._attempt_initial_connect)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG, font=(UI_FONT, 10))
        style.configure(
            "TButton",
            background=UI_BG,
            foreground=UI_FG,
            bordercolor=UI_FG,
            focuscolor=UI_BG,
            font=(UI_FONT, 10),
        )
        style.map("TButton", background=[("active", UI_MUTED)], foreground=[("active", UI_ACCENT)])
        style.configure(
            "TCheckbutton", background=UI_BG, foreground=UI_FG, font=(UI_FONT, 10)
        )
        style.map(
            "TCheckbutton",
            background=[("active", UI_BG)],
            foreground=[("active", UI_ACCENT)],
            indicatorcolor=[("selected", UI_FG), ("!selected", UI_BG)],
        )
        style.configure(
            "TEntry",
            fieldbackground=UI_BG,
            foreground=UI_FG,
            insertcolor=UI_FG,
            bordercolor=UI_FG,
            lightcolor=UI_FG,
            darkcolor=UI_FG,
            font=(UI_FONT, 10),
        )
        style.map(
            "TEntry",
            fieldbackground=[("readonly", UI_BG)],
            foreground=[("readonly", UI_FG)],
        )
        style.configure(
            "Measurement.TEntry",
            fieldbackground=UI_BG,
            foreground=UI_FG,
            insertcolor=UI_FG,
            bordercolor=UI_FG,
            lightcolor=UI_FG,
            darkcolor=UI_FG,
            font=(UI_FONT, 10),
        )
        style.map(
            "Measurement.TEntry",
            fieldbackground=[("readonly", UI_BG)],
            foreground=[("readonly", UI_FG)],
        )
        style.configure(
            "TCombobox",
            fieldbackground=UI_BG,
            background=UI_BG,
            foreground=UI_FG,
            arrowcolor=UI_FG,
            bordercolor=UI_FG,
            lightcolor=UI_FG,
            darkcolor=UI_FG,
            font=(UI_FONT, 10),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", UI_BG)],
            foreground=[("readonly", UI_FG)],
            selectbackground=[("readonly", UI_BG)],
            selectforeground=[("readonly", UI_FG)],
        )
        style.configure(
            "TLabelframe", background=UI_BG, foreground=UI_FG, bordercolor=UI_FG
        )
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG, font=(UI_FONT, 10))
        style.configure(
            "Measure.TButton",
            background=UI_BG,
            foreground=UI_SUCCESS,
            bordercolor=UI_SUCCESS,
            focuscolor=UI_BG,
            font=(UI_FONT, 10),
        )
        style.map(
            "Measure.TButton",
            background=[("active", UI_BG)],
            foreground=[("active", UI_SUCCESS)],
        )

        self.root.columnconfigure(0, weight=5)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)

        controls = ttk.Frame(self.root, padding=10)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew")
        for column in range(11):
            controls.columnconfigure(column, weight=1 if column in (1, 4, 6, 8, 10) else 0)

        ttk.Label(controls, text="Port").grid(row=0, column=0, sticky="e")
        self.port_box = ttk.Combobox(
            controls,
            textvariable=self.port_var,
            width=12,
            state="readonly",
            postcommand=self._refresh_port_selector,
        )
        self.port_box.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.connect_button = ttk.Button(
            controls, textvariable=self.connect_button_var, command=self.toggle_connection
        )
        self.connect_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        ttk.Label(controls, text="Samples").grid(row=0, column=3, sticky="e", padx=(12, 4))
        samples_entry = ttk.Entry(controls, textvariable=self.samples_var, width=10)
        samples_entry.grid(row=0, column=4, sticky="ew")
        ttk.Label(controls, text="Sample rate (Hz)").grid(
            row=0, column=5, sticky="e", padx=(12, 4)
        )
        sample_rate_entry = ttk.Entry(
            controls, textvariable=self.sample_rate_var, width=12
        )
        sample_rate_entry.grid(row=0, column=6, sticky="ew")
        ttk.Label(controls, text="Channel").grid(row=0, column=7, sticky="e", padx=(12, 4))
        channel_box = ttk.Combobox(
            controls,
            textvariable=self.channel_var,
            values=("voltage", "current", "both"),
            width=10,
            state="readonly",
        )
        channel_box.grid(row=0, column=8, sticky="ew")
        ttk.Checkbutton(
            controls, text="Auto capture", variable=self.auto_capture_var
        ).grid(row=0, column=9, sticky="w", padx=(12, 4))
        ttk.Button(controls, text="Apply / Capture", command=self.apply_capture_settings).grid(
            row=0, column=10, sticky="e"
        )

        samples_entry.bind("<Return>", lambda _event: self.apply_capture_settings())
        sample_rate_entry.bind("<Return>", lambda _event: self.apply_capture_settings())
        channel_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_capture_settings())

        ttk.Label(controls, text="DDS freq").grid(row=1, column=0, sticky="ne", pady=(12, 0))
        self.dds_frame = ttk.Frame(controls)
        self.dds_frame.grid(row=1, column=1, columnspan=10, sticky="ew", pady=(10, 2))
        self.dds_frame.columnconfigure(0, weight=1)
        self.dds_canvas = tk.Canvas(
            self.dds_frame,
            height=DDS_SLIDER_CANVAS_HEIGHT,
            bg=UI_BG,
            highlightbackground=UI_FG,
            highlightcolor=UI_FG,
            highlightthickness=1,
            bd=0,
            relief="solid",
            cursor="hand2",
        )
        self.dds_canvas.grid(row=0, column=0, sticky="ew")
        self.dds_canvas.bind("<Configure>", self._redraw_dds_slider)
        self.dds_canvas.bind("<Button-1>", self._on_dds_slider_pointer)
        self.dds_canvas.bind("<B1-Motion>", self._on_dds_slider_pointer)

        plot_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        plot_frame.grid(row=1, column=0, sticky="nsew")
        plot_frame.rowconfigure(1, weight=1)
        plot_frame.columnconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, padding=(0, 0, 6, 10), width=360)
        sidebar.grid(row=1, column=1, sticky="nsew")
        sidebar.rowconfigure(1, weight=1)
        sidebar.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor=UI_BG)
        self.ax = self.figure.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self.ax.set_facecolor(UI_BG)
        self.ax2.set_facecolor(UI_BG)
        self.primary_line, = self.ax.plot([], [], lw=1.8, label="Voltage", color=VOLTAGE_LINE_COLOR)
        self.secondary_line, = self.ax2.plot([], [], lw=1.4, label="Current", color=CURRENT_LINE_COLOR)
        self.ax.set_title("STM32 Serial Oscilloscope")
        self.ax.set_xlabel("Sample Index")
        self.ax.grid(True, alpha=0.3, color=UI_MUTED)
        self.ax.tick_params(axis="x", colors=UI_FG, labelsize=TICK_FONT_SIZE)
        self.ax.tick_params(axis="y", colors=VOLTAGE_LINE_COLOR, labelsize=TICK_FONT_SIZE)
        self.ax2.tick_params(axis="y", colors=CURRENT_LINE_COLOR, labelsize=TICK_FONT_SIZE)
        self.ax.xaxis.label.set_color(UI_FG)
        self.ax.yaxis.label.set_color(VOLTAGE_LINE_COLOR)
        self.ax2.yaxis.label.set_color(CURRENT_LINE_COLOR)
        self.ax.xaxis.label.set_fontsize(AXIS_FONT_SIZE)
        self.ax.yaxis.label.set_fontsize(AXIS_FONT_SIZE)
        self.ax2.yaxis.label.set_fontsize(AXIS_FONT_SIZE)
        self.ax.xaxis.label.set_fontfamily(self.mpl_font_family)
        self.ax.yaxis.label.set_fontfamily(self.mpl_font_family)
        self.ax2.yaxis.label.set_fontfamily(self.mpl_font_family)
        self.ax.title.set_fontfamily(self.mpl_font_family)
        self.ax.title.set_color(UI_FG)
        self.ax.spines["left"].set_color(VOLTAGE_LINE_COLOR)
        self.ax.spines["bottom"].set_color(UI_FG)
        self.ax.spines["top"].set_color(UI_FG)
        self.ax.spines["right"].set_color(UI_FG)
        self.ax2.spines["right"].set_color(CURRENT_LINE_COLOR)
        self.ax2.spines["left"].set_color(VOLTAGE_LINE_COLOR)
        self.ax2.spines["top"].set_color(UI_FG)
        self.ax2.spines["bottom"].set_color(UI_FG)
        self._update_y_axes()
        self.base_facecolor = self.ax.get_facecolor()
        self.flash_facecolor = (0.18, 0.08, 0.0, 1.0)
        self.status_text = self.ax.text(
            0.01,
            0.98,
            "Waiting for data...",
            transform=self.ax.transAxes,
            va="top",
            fontsize=9,
            color=UI_FG,
            fontfamily=self.mpl_font_family,
        )
        self.rx_flash = self.ax.text(
            0.5,
            -0.12,
            "RX",
            transform=self.ax.transAxes,
            va="top",
            ha="center",
            fontsize=16,
            fontweight="bold",
            color=UI_FG,
            alpha=0.25,
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": UI_BG,
                "edgecolor": UI_FG,
                "linewidth": 1.5,
            },
            clip_on=False,
            fontfamily=self.mpl_font_family,
        )
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        measurement_frame = ttk.LabelFrame(sidebar, text="Measurement", padding=10)
        measurement_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for column in range(4):
            measurement_frame.columnconfigure(column, weight=1 if column in (1, 3) else 0)

        ttk.Button(
            measurement_frame, text="Measure", command=self.run_measurement, style="Measure.TButton"
        ).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        measurement_fields = [
            ("Sample rate", "sample_rate_hz"),
            ("DDS freq", "dds_frequency_hz"),
            ("Phase diff", "phase_diff_deg"),
            ("Voltage amp", "voltage_amplitude_v"),
            ("Current amp", "current_amplitude_a"),
            ("|Z|", "impedance_mag_ohm"),
            ("Re(Z)", "impedance_real_ohm"),
            ("Im(Z)", "impedance_imag_ohm"),
            ("Model", "rlc_model"),
            ("R", "series_r_ohm"),
            ("L", "series_l_h"),
            ("C", "series_c_f"),
        ]

        for index, (label, key) in enumerate(measurement_fields):
            row = 1 + index // 2
            col = (index % 2) * 2
            ttk.Label(measurement_frame, text=label).grid(
                row=row, column=col, sticky="e", padx=(0, 4), pady=2
            )
            value_box_color = self._measurement_field_background(key)
            tk.Entry(
                measurement_frame,
                textvariable=self.measurement_vars[key],
                width=14,
                state="readonly",
                readonlybackground=value_box_color,
                fg=UI_FG,
                bg=value_box_color,
                insertbackground=UI_FG,
                highlightbackground=UI_FG,
                highlightcolor=UI_FG,
                highlightthickness=1,
                disabledforeground=UI_FG,
                relief="solid",
                bd=1,
                font=(UI_FONT, 10),
            ).grid(row=row, column=col + 1, sticky="ew", pady=2)

        self.root.after(0, self._redraw_dds_slider)

        terminal_frame = ttk.LabelFrame(sidebar, text="Serial Terminal", padding=10)
        terminal_frame.grid(row=1, column=0, sticky="nsew")
        terminal_frame.rowconfigure(0, weight=1)
        terminal_frame.columnconfigure(0, weight=1)

        self.terminal_text = scrolledtext.ScrolledText(
            terminal_frame,
            wrap="word",
            height=20,
            width=36,
            state="disabled",
            font=(UI_FONT, 10),
            bg=UI_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            selectbackground=UI_MUTED,
            selectforeground=UI_ACCENT,
            highlightbackground=UI_FG,
            highlightcolor=UI_FG,
        )
        self.terminal_text.grid(row=0, column=0, columnspan=2, sticky="nsew")

        command_entry = ttk.Entry(terminal_frame, textvariable=self.command_var)
        command_entry.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        command_entry.bind("<Return>", self.send_terminal_command)
        ttk.Button(terminal_frame, text="Send", command=self.send_terminal_command).grid(
            row=1, column=1, sticky="e", padx=(8, 0), pady=(8, 0)
        )
        ttk.Label(
            terminal_frame, text="Press Enter to send a serial command."
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        status_bar = ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=10)
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")

        self.warning_panel = tk.Label(
            self.root,
            text="",
            bg="#7f1d1d",
            fg="#ffffff",
            anchor="w",
            padx=12,
            pady=8,
            font=(UI_FONT, 10, "bold"),
        )
        self.warning_panel.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.warning_panel.grid_remove()

    def _maximize_window(self) -> None:
        try:
            self.root.state("zoomed")
            return
        except tk.TclError:
            pass

        try:
            self.root.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass

        self.root.geometry("1600x900")

    def _append_terminal_line(self, prefix: str, text: str) -> None:
        self.terminal_history.append(f"{prefix}{text}")
        self.terminal_dirty = True

    def _flush_terminal(self) -> None:
        if not self.terminal_dirty:
            return

        self.terminal_text.configure(state="normal")
        self.terminal_text.delete("1.0", "end")
        if self.terminal_history:
            self.terminal_text.insert("1.0", "\n".join(self.terminal_history) + "\n")
        self.terminal_text.see("end")
        self.terminal_text.configure(state="disabled")
        self.terminal_dirty = False

    def _capture_command(self) -> str:
        samples = self._parse_positive_int(self.samples_var.get(), "samples")
        sample_rate = self._parse_nonnegative_int(self.sample_rate_var.get(), "sample rate")
        channel = self.channel_var.get().strip().lower() or "voltage"
        if channel == "both":
            return f"capturepair {samples} {sample_rate}"
        return f"capture {samples} {sample_rate} {channel}"

    def _measure_command(self) -> str:
        samples = self._parse_positive_int(self.samples_var.get(), "samples")
        sample_rate = self._parse_nonnegative_int(self.sample_rate_var.get(), "sample rate")
        return f"measure {samples} {sample_rate}"

    def _parse_positive_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {field_name}: {value!r}") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name.capitalize()} must be positive.")
        return parsed

    def _parse_nonnegative_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {field_name}: {value!r}") from exc
        if parsed < 0:
            raise ValueError(f"{field_name.capitalize()} must be zero or positive.")
        return parsed

    def _is_connected(self) -> bool:
        return self.worker is not None and self.worker.is_alive() and self.port is not None

    def _refresh_port_selector(self) -> None:
        ports = available_ports()
        preferred = self.port if self._is_connected() else (self.port_var.get().strip() or autodetect_port() or "")
        if preferred and preferred not in ports:
            ports.insert(0, preferred)
        self.available_port_values = ports
        self.port_box["values"] = ports
        if preferred:
            self.port_var.set(preferred)
        elif ports:
            self.port_var.set(ports[0])
        else:
            self.port_var.set("")

    def _clear_serial_queues(self) -> None:
        self.frame_queue = queue.Queue(maxsize=8)
        self.measurement_queue = queue.Queue(maxsize=8)
        self.line_queue = queue.Queue(maxsize=256)
        self.event_queue = queue.Queue(maxsize=32)
        self.command_queue = queue.Queue(maxsize=64)

    def _show_warning_panel(self, message: str) -> None:
        self.warning_panel.configure(text=message)
        self.warning_panel.grid()
        if self.warning_hide_job is not None:
            self.root.after_cancel(self.warning_hide_job)
        self.warning_hide_job = self.root.after(WARNING_PANEL_DURATION_MS, self._hide_warning_panel)

    def _hide_warning_panel(self) -> None:
        self.warning_hide_job = None
        self.warning_panel.grid_remove()

    def _handle_serial_timeout(self, message: str) -> None:
        if self.auto_reconnect_active:
            return

        self.auto_reconnect_active = True
        self._append_terminal_line("# ", f"serial recovery -> {message}")
        self._show_warning_panel(f"MCU response timeout. Reconnecting serial... {message}")
        self.disconnect_serial()
        if self.reconnect_job is not None:
            self.root.after_cancel(self.reconnect_job)
        self.reconnect_job = self.root.after(
            RECOVER_RECONNECT_DELAY_MS, self._reconnect_after_timeout
        )

    def _reconnect_after_timeout(self) -> None:
        self.reconnect_job = None
        self.connect_serial()
        self.auto_reconnect_active = False

    def _attempt_initial_connect(self) -> None:
        if self._is_connected():
            return
        if self.port_var.get().strip():
            self.connect_serial()

    def _current_dds_frequency_hz(self) -> int:
        return dds_frequency_from_step(self.dds_frequency_step_var.get())

    def _dds_command(self) -> str:
        return f"dds {self._current_dds_frequency_hz()} 1"

    def _format_dds_slider_label(self) -> str:
        return f"{format_engineering(float(self._current_dds_frequency_hz()), precision=2)}Hz"

    def _dds_slider_geometry(self) -> Optional[dict[str, float]]:
        if not hasattr(self, "dds_canvas"):
            return
        width = self.dds_canvas.winfo_width()
        height = self.dds_canvas.winfo_height()
        if width <= (DDS_SLIDER_LEFT_PAD + DDS_SLIDER_RIGHT_PAD + 1):
            return None
        return {
            "width": width,
            "height": height,
            "track_left": DDS_SLIDER_LEFT_PAD,
            "track_right": width - DDS_SLIDER_RIGHT_PAD,
            "track_y": 60,
            "small_tick_top": 44,
            "small_tick_bottom": 60,
            "major_tick_top": 36,
            "major_tick_bottom": 60,
            "label_y": 80,
            "readout_y": 10,
        }

    def _dds_slider_x_for_step(self, step: int) -> float:
        geometry = self._dds_slider_geometry()
        if geometry is None:
            return 0.0
        fraction = step / DDS_FREQ_SLIDER_STEPS
        return geometry["track_left"] + fraction * (geometry["track_right"] - geometry["track_left"])

    def _dds_slider_step_from_x(self, x_pos: float) -> int:
        geometry = self._dds_slider_geometry()
        if geometry is None:
            return self.dds_frequency_step_var.get()
        usable_width = geometry["track_right"] - geometry["track_left"]
        if usable_width <= 0:
            return self.dds_frequency_step_var.get()
        fraction = (x_pos - geometry["track_left"]) / usable_width
        step = round(fraction * DDS_FREQ_SLIDER_STEPS)
        return max(0, min(DDS_FREQ_SLIDER_STEPS, step))

    def _set_dds_slider_step(self, step: int, *, mark_pending: bool) -> None:
        clamped_step = max(0, min(DDS_FREQ_SLIDER_STEPS, int(step)))
        self.dds_frequency_step_var.set(clamped_step)
        if mark_pending:
            self.dds_apply_pending = True
        self._redraw_dds_slider()

    def _on_dds_slider_pointer(self, event: tk.Event) -> None:
        self._set_dds_slider_step(self._dds_slider_step_from_x(event.x), mark_pending=True)

    def _redraw_dds_slider(self, _event: object = None) -> None:
        if not hasattr(self, "dds_canvas"):
            return
        canvas = self.dds_canvas
        canvas.delete("all")
        canvas.configure(bg=UI_BG)
        geometry = self._dds_slider_geometry()
        if geometry is None:
            return

        canvas.create_line(
            geometry["track_left"],
            geometry["track_y"],
            geometry["track_right"],
            geometry["track_y"],
            fill=UI_FG,
            width=2,
        )

        labels = {
            0: "100",
            10: "1k",
            20: "10k",
            30: "100k",
        }
        for step in range(DDS_FREQ_SLIDER_STEPS + 1):
            x_pos = self._dds_slider_x_for_step(step)
            is_major = step in DDS_LABEL_STEPS
            canvas.create_line(
                x_pos,
                geometry["major_tick_top"] if is_major else geometry["small_tick_top"],
                x_pos,
                geometry["major_tick_bottom"] if is_major else geometry["small_tick_bottom"],
                fill=UI_FG,
                width=2 if is_major else 1,
            )
            if not is_major:
                continue
            anchor = "center"
            if step == DDS_LABEL_STEPS[0]:
                anchor = "w"
                x_pos = max(2, x_pos - 8)
            elif step == DDS_LABEL_STEPS[-1]:
                anchor = "e"
                x_pos = min(geometry["width"] - 2, x_pos + 8)
            canvas.create_text(
                x_pos,
                geometry["label_y"],
                text=labels[step],
                fill=UI_FG,
                font=(UI_FONT, DDS_SCALE_FONT_SIZE),
                anchor=anchor,
            )

        handle_x = self._dds_slider_x_for_step(self.dds_frequency_step_var.get())
        handle_left = handle_x - (DDS_SLIDER_HANDLE_WIDTH / 2)
        handle_right = handle_x + (DDS_SLIDER_HANDLE_WIDTH / 2)
        handle_top = geometry["track_y"] - (DDS_SLIDER_HANDLE_HEIGHT / 2)
        handle_bottom = geometry["track_y"] + (DDS_SLIDER_HANDLE_HEIGHT / 2)
        canvas.create_rectangle(
            handle_left,
            handle_top,
            handle_right,
            handle_bottom,
            outline=UI_ACCENT,
            fill=UI_BG,
            width=2,
        )
        canvas.create_text(
            handle_x,
            geometry["readout_y"],
            text=self._format_dds_slider_label(),
            fill=UI_ACCENT,
            font=(UI_FONT, DDS_VALUE_FONT_SIZE),
            anchor="n",
        )

    def _measurement_field_background(self, key: str) -> str:
        if key in {
            "sample_rate_hz",
            "dds_frequency_hz",
            "phase_diff_deg",
            "voltage_amplitude_v",
            "current_amplitude_a",
        }:
            return UI_MEASUREMENT_BG
        if key in {"impedance_mag_ohm", "impedance_real_ohm", "impedance_imag_ohm"}:
            return UI_IMPEDANCE_BG
        return UI_RLC_BG

    def _adc_to_voltage(self, raw: int) -> float:
        return raw * self.args.vref / self.args.adc_max

    def _center_voltage(self, voltage: float) -> float:
        return voltage - (self.args.vref * 0.5)

    def _current_gain(self) -> float:
        if 0 <= self.current_pga < len(FRONTEND_GAIN_TABLE):
            return FRONTEND_GAIN_TABLE[self.current_pga]
        return FRONTEND_GAIN_TABLE[0]

    def _shunt_resistance_ohms(self) -> float:
        if 0 <= self.shunt_range < len(SHUNT_RESISTANCE_TABLE):
            return SHUNT_RESISTANCE_TABLE[self.shunt_range]
        return SHUNT_RESISTANCE_TABLE[0]

    def _raw_to_voltage_display(self, raw: int) -> float:
        return self._center_voltage(self._adc_to_voltage(raw))

    def _raw_to_current_display(self, raw: int) -> float:
        shunt_resistance = self._shunt_resistance_ohms()
        current_gain = self._current_gain()
        if shunt_resistance <= 0.0 or current_gain <= 0.0:
            return 0.0
        signal_voltage = self._center_voltage(self._adc_to_voltage(raw))
        return signal_voltage / (current_gain * shunt_resistance)

    def _voltage_axis_limits(self) -> tuple[float, float]:
        half_scale = self.args.vref * 0.5
        return (-half_scale, half_scale)

    def _current_display_unit(self) -> tuple[str, float]:
        current_limit = self.args.vref * 0.5 / (self._current_gain() * self._shunt_resistance_ohms())
        magnitude = abs(current_limit)
        if magnitude < 1e-3:
            return ("uA", 1e6)
        if magnitude < 1.0:
            return ("mA", 1e3)
        return ("A", 1.0)

    def _current_axis_limits(self) -> tuple[float, float]:
        _, scale = self._current_display_unit()
        half_scale_current = self.args.vref * 0.5 / (self._current_gain() * self._shunt_resistance_ohms())
        return (-half_scale_current * scale, half_scale_current * scale)

    def _update_y_axes(self) -> None:
        current_unit, _ = self._current_display_unit()
        self.ax.set_ylabel("Voltage (V)")
        self.ax2.set_ylabel(f"Current ({current_unit})")
        self.ax.set_ylim(*self._voltage_axis_limits())
        self.ax2.set_ylim(*self._current_axis_limits())

    def _update_frontend_state_from_line(self, line: str) -> bool:
        match = MEASUREMENT_VALUE_RE.match(line)
        if match is None:
            return False

        key = match.group(1).lower()
        raw_value = match.group(2).strip()
        if key not in {"shunt_range", "voltage_pga", "current_pga"}:
            return False

        try:
            parsed = int(raw_value)
        except ValueError:
            return False

        changed = False
        if key == "shunt_range" and parsed != self.shunt_range:
            self.shunt_range = parsed
            changed = True
        elif key == "voltage_pga" and parsed != self.voltage_pga:
            self.voltage_pga = parsed
            changed = True
        elif key == "current_pga" and parsed != self.current_pga:
            self.current_pga = parsed
            changed = True

        return changed

    def _update_frontend_state_from_command(self, command: str) -> None:
        for pattern, attribute in (
            (RANGE_COMMAND_RE, "shunt_range"),
            (VPGA_COMMAND_RE, "voltage_pga"),
            (IPGA_COMMAND_RE, "current_pga"),
        ):
            match = pattern.match(command)
            if match is None:
                continue
            setattr(self, attribute, int(match.group(1)))
            return

    def toggle_connection(self) -> None:
        if self._is_connected():
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self) -> None:
        if self._is_connected():
            return
        port = self.port_var.get().strip()
        if not port:
            self._refresh_port_selector()
            port = self.port_var.get().strip()
        if not port:
            self.status_var.set("No serial port selected.")
            return

        try:
            with serial.Serial(port, baudrate=self.args.baud, timeout=0.2):
                pass
        except serial.SerialException as exc:
            self.status_var.set(f"Could not open {port}: {exc}")
            return

        self._clear_serial_queues()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=serial_worker,
            args=(
                port,
                self.args.baud,
                self.frame_queue,
                self.measurement_queue,
                self.line_queue,
                self.event_queue,
                self.command_queue,
                self.stop_event,
            ),
            daemon=True,
        )
        self.worker.start()
        self.port = port
        self.dds_apply_pending = True
        self.connect_button_var.set("Disconnect")
        self.port_box.state(["disabled"])
        self._append_terminal_line("# ", f"connected -> {port}")
        self.status_var.set(f"Connected to {port} @ {self.args.baud} baud")
        self._queue_command("status")
        self._schedule_capture()

    def disconnect_serial(self) -> None:
        if self.capture_job is not None:
            self.root.after_cancel(self.capture_job)
            self.capture_job = None

        if self.reconnect_job is not None and not self.auto_reconnect_active:
            self.root.after_cancel(self.reconnect_job)
            self.reconnect_job = None

        if self.stop_event is not None:
            self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=1.0)
        if self.port is not None:
            self._append_terminal_line("# ", f"disconnected <- {self.port}")

        self.worker = None
        self.port = None
        self.dds_apply_pending = True
        self.connect_button_var.set("Connect")
        self.port_box.state(["!disabled", "readonly"])
        self.status_var.set(f"Disconnected | select a port and connect @ {self.args.baud} baud")

    def _queue_command(self, command: str) -> None:
        if not self._is_connected():
            self.status_var.set("Not connected.")
            return
        try:
            self.command_queue.put_nowait(command)
        except queue.Full:
            self.status_var.set("Command queue full; try again.")

    def _queue_capture_sequence(self, command: str, *, for_autocapture: bool) -> bool:
        if not self._is_connected():
            self.status_var.set("Not connected.")
            return False
        should_send_dds = (not for_autocapture) or self.dds_apply_pending
        if should_send_dds:
            self._queue_command(self._dds_command())
            self.dds_apply_pending = False
        self._queue_command(command)
        return True

    def _queue_capture_command(self, reset_terminal: bool = True) -> bool:
        try:
            command = self._capture_command()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return False

        if reset_terminal:
            self._append_terminal_line("# ", f"auto capture -> {command}")
        return self._queue_capture_sequence(command, for_autocapture=not reset_terminal)

    def apply_capture_settings(self) -> None:
        if not self._is_connected():
            self.status_var.set(
                f"Capture settings ready: {self._capture_command()} | connect to start"
            )
            return
        if self._queue_capture_command():
            self.status_var.set(
                f"Capture request updated: {self._capture_command()} | auto={'on' if self.auto_capture_var.get() else 'off'}"
            )
            self._schedule_capture()

    def run_measurement(self) -> None:
        if not self._is_connected():
            self.status_var.set("Not connected.")
            return
        try:
            command = self._measure_command()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return

        for key in self.measurement_vars:
            self.measurement_vars[key].set("")
        self.measurement_vars["rlc_model"].set("measuring...")
        self._append_terminal_line("# ", f"manual measure -> {command}")
        self._queue_capture_sequence(command, for_autocapture=False)
        self.status_var.set(f"Measurement requested: {command}")

    def send_terminal_command(self, _event: object = None) -> None:
        command = self.command_var.get().strip()
        if not command:
            return
        dds_match = DDS_COMMAND_RE.match(command)
        if dds_match:
            self._set_dds_slider_step(
                dds_step_from_frequency(int(dds_match.group(1))),
                mark_pending=False,
            )
            self.dds_apply_pending = False
        self._update_frontend_state_from_command(command)
        self._queue_command(command)
        if (
            self._is_connected()
            and (
                RANGE_COMMAND_RE.match(command)
                or VPGA_COMMAND_RE.match(command)
                or IPGA_COMMAND_RE.match(command)
            )
        ):
            self._queue_command("status")
        if self._is_connected():
            self.command_var.set("")

    def _schedule_capture(self) -> None:
        if self.capture_job is not None:
            self.root.after_cancel(self.capture_job)
            self.capture_job = None

        if not self._is_connected() or not self.auto_capture_var.get():
            return

        delay_ms = max(self.args.request_interval_ms, 0)
        self.capture_job = self.root.after(delay_ms, self._auto_capture_tick)

    def _auto_capture_tick(self) -> None:
        self.capture_job = None
        if self._is_connected() and self.auto_capture_var.get():
            self._queue_capture_command(reset_terminal=False)
            self._schedule_capture()

    def _poll_queues(self) -> None:
        got_new_frame = False
        redraw_needed = False

        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if event.kind == "timeout":
                self._handle_serial_timeout(event.message)

        while True:
            try:
                line = self.line_queue.get_nowait()
            except queue.Empty:
                break
            prefix = "TX> " if line.direction == "tx" else "RX> "
            self._append_terminal_line(prefix, line.text)
            if line.direction == "rx" and self._update_frontend_state_from_line(line.text):
                self._update_y_axes()
                if self.latest is not None:
                    self._update_plot(self.latest)
                    redraw_needed = True

        self._flush_terminal()

        while True:
            try:
                measurement = self.measurement_queue.get_nowait()
            except queue.Empty:
                break
            self._update_measurement_fields(measurement)

        while True:
            try:
                self.latest = self.frame_queue.get_nowait()
                got_new_frame = True
            except queue.Empty:
                break

        if got_new_frame and self.latest is not None:
            self.frame_counter += 1
            self.flash_ticks_remaining = self.flash_duration_ticks
            self._update_plot(self.latest)
            redraw_needed = True

        redraw_needed = self._update_flash() or redraw_needed
        if redraw_needed:
            self.canvas.draw_idle()
        self.root.after(max(self.args.refresh_ms, 20), self._poll_queues)

    def _measurement_float(self, measurement: Measurement, key: str) -> Optional[float]:
        value = measurement.values.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _format_measurement_value(self, value: Optional[float]) -> str:
        if value is None:
            return ""
        if value == 0:
            return "0"
        return format_engineering(value)

    def _update_measurement_fields(self, measurement: Measurement) -> None:
        direct_keys = (
            "sample_rate_hz",
            "dds_frequency_hz",
            "voltage_amplitude_v",
            "current_amplitude_a",
            "phase_diff_deg",
            "impedance_mag_ohm",
            "impedance_real_ohm",
            "impedance_imag_ohm",
        )
        for key in direct_keys:
            raw_value = measurement.values.get(key, "")
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                self.measurement_vars[key].set(raw_value)
                continue

            if key in {"sample_rate_hz", "dds_frequency_hz"}:
                self.measurement_vars[key].set(format_engineering(numeric_value, precision=2))
            elif key == "phase_diff_deg":
                self.measurement_vars[key].set(f"{numeric_value:.3f}")
            else:
                self.measurement_vars[key].set(self._format_measurement_value(numeric_value))

        resistance = self._measurement_float(measurement, "impedance_real_ohm")
        reactance = self._measurement_float(measurement, "impedance_imag_ohm")
        frequency_hz = self._measurement_float(measurement, "dds_frequency_hz")

        self.measurement_vars["series_r_ohm"].set(self._format_measurement_value(resistance))
        self.measurement_vars["series_l_h"].set("")
        self.measurement_vars["series_c_f"].set("")

        if reactance is None:
            self.measurement_vars["rlc_model"].set("unknown")
        elif abs(reactance) < 1e-9:
            self.measurement_vars["rlc_model"].set("Resistive")
        elif frequency_hz is None or frequency_hz <= 0:
            self.measurement_vars["rlc_model"].set("invalid freq")
        else:
            omega = 2.0 * math.pi * frequency_hz
            if reactance > 0:
                inductance_h = reactance / omega
                self.measurement_vars["rlc_model"].set("Series RL")
                self.measurement_vars["series_l_h"].set(
                    self._format_measurement_value(inductance_h)
                )
            else:
                capacitance_f = -1.0 / (omega * reactance)
                self.measurement_vars["rlc_model"].set("Series RC")
                self.measurement_vars["series_c_f"].set(
                    self._format_measurement_value(capacitance_f)
                )

        self.status_var.set(
            f"Connected to {self.port} @ {self.args.baud} baud | measurement updated"
        )

    def _sync_legend(self, labels: list[str]) -> None:
        desired_labels = tuple(labels)
        if desired_labels == self.legend_labels:
            if self.legend is not None:
                self.legend.set_visible(bool(labels))
            return

        if self.legend is not None:
            self.legend.remove()
            self.legend = None

        self.legend_labels = desired_labels
        if not labels:
            return

        handles = []
        for label in labels:
            if label == "Voltage":
                handles.append(self.primary_line)
            elif label == "Current":
                handles.append(self.secondary_line)

        self.legend = self.ax.legend(handles=handles, labels=labels, loc="upper right")
        self.legend.get_frame().set_facecolor(UI_BG)
        self.legend.get_frame().set_edgecolor(UI_FG)
        for text in self.legend.get_texts():
            text.set_color(UI_FG)
            text.set_fontfamily(self.mpl_font_family)

    def _update_plot(self, frame: Frame) -> None:
        if frame.sample_rate_hz and frame.sample_rate_hz > 0:
            frame_duration_s = max(1, len(frame.sample_indices)) / frame.sample_rate_hz
            unit_label, unit_scale = time_axis_unit(frame_duration_s)
            x_data = [(idx / frame.sample_rate_hz) * unit_scale for idx in frame.sample_indices]
            self.ax.set_xlabel(f"Time ({unit_label})")
        else:
            x_data = frame.sample_indices
            self.ax.set_xlabel("Sample Index")

        self.primary_line.set_data([], [])
        self.secondary_line.set_data([], [])
        self.primary_line.set_visible(False)
        self.secondary_line.set_visible(False)

        visible_labels: list[str] = []

        if frame.primary_label == "Voltage":
            self.primary_line.set_data(x_data, [self._raw_to_voltage_display(value) for value in frame.primary_values])
            self.primary_line.set_label(frame.primary_label)
            self.primary_line.set_visible(True)
            visible_labels.append("Voltage")
        elif frame.primary_label == "Current":
            _, current_scale = self._current_display_unit()
            self.secondary_line.set_data(
                x_data, [self._raw_to_current_display(value) * current_scale for value in frame.primary_values]
            )
            self.secondary_line.set_label(frame.primary_label)
            self.secondary_line.set_visible(True)
            visible_labels.append("Current")

        if frame.secondary_values is not None and frame.secondary_label is not None:
            if frame.secondary_label == "Voltage":
                self.primary_line.set_data(
                    x_data, [self._raw_to_voltage_display(value) for value in frame.secondary_values]
                )
                self.primary_line.set_label(frame.secondary_label)
                self.primary_line.set_visible(True)
                if "Voltage" not in visible_labels:
                    visible_labels.append("Voltage")
            elif frame.secondary_label == "Current":
                _, current_scale = self._current_display_unit()
                self.secondary_line.set_data(
                    x_data, [self._raw_to_current_display(value) * current_scale for value in frame.secondary_values]
                )
                self.secondary_line.set_label(frame.secondary_label)
                self.secondary_line.set_visible(True)
                if "Current" not in visible_labels:
                    visible_labels.append("Current")

        self._sync_legend(visible_labels)
        x_min = min(x_data)
        x_max = max(x_data)
        if x_min == x_max:
            x_max += 1
        self.ax.set_xlim(x_min, x_max)
        self._update_y_axes()

        status = f"Frames: {self.frame_counter} | Samples: {len(frame.primary_values)} | Mode: {self.channel_var.get()}"
        if frame.sample_rate_hz and frame.sample_rate_hz > 0:
            status = f"{status} | Fs: {frame.sample_rate_hz} Hz"
        status = f"{status} | Shunt: {format_engineering(self._shunt_resistance_ohms(), precision=2)}ohm"
        self.status_text.set_text(status)
        self.status_var.set(
            f"Connected to {self.port} @ {self.args.baud} baud | latest frame: {len(frame.primary_values)} samples"
        )

    def _update_flash(self) -> bool:
        if self.flash_ticks_remaining > 0:
            self.rx_flash.set_alpha(1.0)
            blend = self.flash_ticks_remaining / self.flash_duration_ticks
            blended = tuple(
                base + (flash - base) * blend
                for base, flash in zip(self.base_facecolor, self.flash_facecolor)
            )
            self.ax.set_facecolor(blended)
            self.flash_ticks_remaining -= 1
            return True

        if self.rx_flash.get_alpha() != 0.25 or self.ax.get_facecolor() != self.base_facecolor:
            self.rx_flash.set_alpha(0.25)
            self.ax.set_facecolor(self.base_facecolor)
            return True

        return False

    def on_close(self) -> None:
        self.disconnect_serial()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    args = build_arg_parser().parse_args()
    app = OscilloscopeApp(args, args.port)
    app.run()


if __name__ == "__main__":
    main()

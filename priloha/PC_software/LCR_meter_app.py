#!/usr/bin/env python3
"""Qt-based STM32 LCR meter desktop application."""

from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSpinBox,
    QHeaderView,
    QAbstractItemView,
    QInputDialog,
)
import serial
import serial.tools.list_ports

from data_viewer import DataViewerWidget


APP_TITLE = "STM32 LCR Meter"
APP_ORG = "stm32_lcr_meter"
APP_ICON_PATH = Path(__file__).with_name("icon")
APP_CONFIG_PATH = Path.home() / ".config" / APP_ORG
PROFILE_PATH = APP_CONFIG_PATH / "profiles.json"

TERMINAL_MAX_BLOCKS = 1500
VOLTAGE_COLOR = "#ff3b30"
CURRENT_COLOR = "#3390ff"
ACCENT_COLOR = "#ffb000"
BORDER_COLOR = "#8a4f00"
FIELD_TEXT_COLOR = "#ff9f1c"
BG_COLOR = "#000000"
PANEL_COLOR = "#000000"
PANEL_ALT_COLOR = "#050505"
GRID_COLOR = "#3a2400"
TEXT_COLOR = "#ffd27a"
MUTED_COLOR = "#b8882d"
SUCCESS_COLOR = "#ffcc66"
WARNING_COLOR = "#ff9a1f"
ERROR_COLOR = "#ff5b1f"
APP_FONT_FAMILY = "Ioskeley Mono"
BASE_DPI = 96.0

FRONTEND_GAIN_TABLE = (1.0, 2.0, 5.0, 10.0)
SHUNT_RESISTANCE_TABLE = (100.0, 1000.0, 10000.0, 100000.0)
DDS_FREQ_MIN_HZ = 100
DDS_FREQ_MAX_HZ = 100_000
DDS_FREQUENCY_STEPS = tuple(
    [100 * multiplier for multiplier in range(1, 10)]
    + [1000 * multiplier for multiplier in range(1, 10)]
    + [10000 * multiplier for multiplier in range(1, 10)]
    + [100000]
)
DDS_SLIDER_STEPS = len(DDS_FREQUENCY_STEPS) - 1
SERIAL_IDLE_SLEEP_S = 0.01
STATUS_TIMEOUT_S = 1.0
ACK_TIMEOUT_S = 1.0
CAPTURE_TIMEOUT_S = 2.5
MEASURE_TIMEOUT_S = 2.5
AUTO_RECONNECT_PERIOD_MS = 1500
POLL_PERIOD_MS = 40

VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
PAIR_VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")
SAMPLE_RATE_LINE_RE = re.compile(r"^\s*sample_rate_hz\s*,\s*(\d+)\s*$", re.IGNORECASE)
KEY_VALUE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(.+?)\s*$")
CAPTURE_COMMAND_RE = re.compile(
    r"^\s*capture\s+(\d+)\s+(\d+)\s+(voltage|current|v|i)\s*$", re.IGNORECASE
)
CAPTUREPAIR_COMMAND_RE = re.compile(r"^\s*capturepair\s+(\d+)\s+(\d+)\s*$", re.IGNORECASE)
MEASURE_COMMAND_RE = re.compile(r"^\s*measure(?:\s+\d+\s+\d+)?\s*$", re.IGNORECASE)
DDS_COMMAND_RE = re.compile(r"^\s*dds\s+(\d+)\s+([01])\s*$", re.IGNORECASE)
AMP_COMMAND_RE = re.compile(r"^\s*amp\s+(\d+)\s*$", re.IGNORECASE)
OFFSET_COMMAND_RE = re.compile(r"^\s*offset\s+(\d+)\s*$", re.IGNORECASE)
RANGE_COMMAND_RE = re.compile(r"^\s*range\s+(\d+)\s*$", re.IGNORECASE)
VPGA_COMMAND_RE = re.compile(r"^\s*vpga\s+(\d+)\s*$", re.IGNORECASE)
IPGA_COMMAND_RE = re.compile(r"^\s*ipga\s+(\d+)\s*$", re.IGNORECASE)

STATUS_KEYS = {
    "sample_rate_hz",
    "sample_count",
    "dds_frequency_hz",
    "dds_enabled",
    "amp_wiper",
    "offset_pwm",
    "shunt_range",
    "voltage_pga",
    "current_pga",
}
MEASUREMENT_KEYS = {
    "sample_rate_hz",
    "dds_frequency_hz",
    "shunt_range",
    "voltage_pga",
    "current_pga",
    "shunt_resistance_ohm",
    "samples_per_period",
    "captured_cycles",
    "voltage_amplitude_v",
    "voltage_phase_deg",
    "current_amplitude_a",
    "current_phase_deg",
    "phase_diff_deg",
    "impedance_mag_ohm",
    "impedance_real_ohm",
    "impedance_imag_ohm",
}


@dataclass
class FrameData:
    indices: list[int]
    voltage_raw: Optional[list[int]]
    current_raw: Optional[list[int]]
    sample_rate_hz: Optional[int]


@dataclass
class MeasurementData:
    values: dict[str, str]


@dataclass
class DeviceStatus:
    values: dict[str, str]


@dataclass
class CommandRequest:
    kind: str
    command: str
    timeout_s: float
    label: str = ""
    expected_samples: int = 0
    capture_mode: str = "both"
    future: Future = field(default_factory=Future)


@dataclass
class SweepPoint:
    frequency_hz: float
    measurement: MeasurementData


@dataclass
class MeasurementPoint:
    index: int
    elapsed_s: float
    measurement: MeasurementData


class DecadeAxisItem(pg.AxisItem):
    def logTickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
        labels: list[str] = []
        for value in values:
            exponent = round(value)
            if abs(value - exponent) > 1e-6:
                labels.append("")
                continue
            actual = (10 ** exponent) * scale
            if actual < 100 or actual > 100000:
                labels.append("")
                continue
            labels.append(format_engineering(actual, precision=0))
        return labels


def format_engineering(value: float, precision: int = 3) -> str:
    prefixes = (
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    )
    magnitude = abs(value)
    for scale, prefix in prefixes:
        if magnitude >= scale:
            return f"{value / scale:.{precision}f}{prefix}"
    return f"{value / 1e-12:.{precision}f}p"


def format_engineering_unit(value: Optional[float], unit: str, precision: int = 3) -> str:
    if value is None:
        return ""
    return f"{format_engineering(value, precision)}{unit}"


def safe_float(values: dict[str, str], key: str) -> Optional[float]:
    raw = values.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def safe_int(values: dict[str, str], key: str) -> Optional[int]:
    raw = values.get(key)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def time_axis_unit(frame_duration_s: float) -> tuple[str, float]:
    if frame_duration_s >= 0.01:
        return ("ms", 1e3)
    if frame_duration_s >= 0.00001:
        return ("µs", 1e6)
    return ("ns", 1e9)


def compute_ui_scale() -> float:
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    dpi = max(screen.logicalDotsPerInch(), BASE_DPI)
    return max(1.0, min(dpi / BASE_DPI, 2.0))


def dds_frequency_from_step(step: int) -> int:
    clamped_step = max(0, min(DDS_SLIDER_STEPS, int(step)))
    return DDS_FREQUENCY_STEPS[clamped_step]


def dds_step_from_frequency(frequency_hz: int) -> int:
    clamped_frequency = max(DDS_FREQ_MIN_HZ, min(DDS_FREQ_MAX_HZ, int(frequency_hz)))
    return min(
        range(len(DDS_FREQUENCY_STEPS)),
        key=lambda index: abs(DDS_FREQUENCY_STEPS[index] - clamped_frequency),
    )


def normalize_channel(channel: str) -> str:
    lowered = channel.strip().lower()
    if lowered == "v":
        return "voltage"
    if lowered == "i":
        return "current"
    return lowered


def available_ports() -> list[str]:
    return [port.device for port in serial.tools.list_ports.comports() if port.device]


def autodetect_port() -> Optional[str]:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    preferred: list[str] = []
    fallback: list[str] = []
    for port in ports:
        dev = (port.device or "").lower()
        desc = (port.description or "").lower()
        if any(token in dev for token in ("ttyacm", "ttyusb", "usbmodem", "com")):
            preferred.append(port.device)
        elif any(token in desc for token in ("stlink", "usb", "serial")):
            fallback.append(port.device)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return ports[0].device


def profile_snapshot_from_dict(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "port": str(values.get("port", "")),
        "baud": int(values.get("baud", 115200)),
        "samples": int(values.get("samples", 100)),
        "sample_rate": int(values.get("sample_rate", 100000)),
        "sample_rate_auto": bool(values.get("sample_rate_auto", True)),
        "request_interval_ms": int(values.get("request_interval_ms", 100)),
        "auto_capture": bool(values.get("auto_capture", False)),
        "dds_frequency_hz": int(values.get("dds_frequency_hz", 1000)),
        "dds_enabled": bool(values.get("dds_enabled", True)),
        "amp_wiper": int(values.get("amp_wiper", 128)),
        "offset_pwm": int(values.get("offset_pwm", 0)),
        "shunt_range": int(values.get("shunt_range", 0)),
        "voltage_pga": int(values.get("voltage_pga", 0)),
        "current_pga": int(values.get("current_pga", 0)),
        "sweep_start_hz": float(values.get("sweep_start_hz", 100.0)),
        "sweep_stop_hz": float(values.get("sweep_stop_hz", 10000.0)),
        "sweep_points_per_decade": int(values.get("sweep_points_per_decade", 6)),
        "sweep_settle_ms": int(values.get("sweep_settle_ms", 120)),
        "sweep_samples": int(values.get("sweep_samples", 500)),
        "sweep_sample_rate": int(values.get("sweep_sample_rate", 0)),
        "measurement_count": int(values.get("measurement_count", 100)),
        "measurement_delay_s": float(values.get("measurement_delay_s", 0.0)),
    }


def derived_rlc_fields(values: dict[str, str]) -> tuple[str, str, str, str]:
    resistance = safe_float(values, "impedance_real_ohm")
    reactance = safe_float(values, "impedance_imag_ohm")
    frequency_hz = safe_float(values, "dds_frequency_hz")

    resistance_text = format_engineering_unit(resistance, "Ω") if resistance is not None else ""
    inductance_text = ""
    capacitance_text = ""
    model = "Unknown"

    if reactance is None:
        return (model, resistance_text, inductance_text, capacitance_text)
    if abs(reactance) < 1e-9:
        return ("Resistive", resistance_text, inductance_text, capacitance_text)
    if frequency_hz is None or frequency_hz <= 0.0:
        return ("Invalid freq", resistance_text, inductance_text, capacitance_text)

    omega = 2.0 * math.pi * frequency_hz
    if reactance > 0:
        inductance_h = reactance / omega
        inductance_text = format_engineering_unit(inductance_h, "H")
        model = "Series RL"
    else:
        capacitance_f = -1.0 / (omega * reactance)
        capacitance_text = format_engineering_unit(capacitance_f, "F")
        model = "Series RC"
    return (model, resistance_text, inductance_text, capacitance_text)


class ProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        profiles: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, dict):
                profiles[key] = profile_snapshot_from_dict(value)
        return profiles

    def save(self, profiles: dict[str, dict[str, Any]]) -> None:
        normalized = {name: profile_snapshot_from_dict(data) for name, data in profiles.items()}
        self.path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")


class SerialController:
    def __init__(self) -> None:
        self.event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=512)
        self.request_queue: "queue.Queue[CommandRequest]" = queue.Queue(maxsize=128)
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.port: Optional[str] = None
        self.baud = 115200
        self._lock = threading.Lock()
        self._active_request: Optional[CommandRequest] = None
        self._manual_shutdown = False

    def connect(self, port: str, baud: int) -> None:
        self.disconnect(manual=False)
        self.port = port
        self.baud = baud
        self.stop_event = threading.Event()
        self._manual_shutdown = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def disconnect(self, *, manual: bool = False) -> None:
        self._manual_shutdown = manual
        self.stop_event.set()
        try:
            self.request_queue.put_nowait(CommandRequest(kind="stop", command="", timeout_s=0.0))
        except queue.Full:
            pass
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None
        self._fail_pending(RuntimeError("Disconnected."))
        with self._lock:
            self._active_request = None

    def is_connected(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def is_idle(self) -> bool:
        with self._lock:
            return self._active_request is None and self.request_queue.empty()

    def submit(self, request: CommandRequest) -> Future:
        if not self.is_connected():
            request.future.set_exception(RuntimeError("Not connected."))
            return request.future
        try:
            self.request_queue.put_nowait(request)
        except queue.Full:
            request.future.set_exception(RuntimeError("Command queue is full."))
        return request.future

    def submit_wait(self, request: CommandRequest, timeout: Optional[float] = None) -> Any:
        future = self.submit(request)
        return future.result(timeout=timeout)

    def drain_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self.event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

    def _set_active(self, request: Optional[CommandRequest]) -> None:
        with self._lock:
            self._active_request = request

    def _fail_pending(self, exc: Exception) -> None:
        while True:
            try:
                request = self.request_queue.get_nowait()
            except queue.Empty:
                break
            if request.kind != "stop" and not request.future.done():
                request.future.set_exception(exc)

    def _run(self) -> None:
        if self.port is None:
            return
        try:
            ser = serial.Serial(self.port, baudrate=self.baud, timeout=0.05)
        except serial.SerialException as exc:
            self._emit({"kind": "error", "message": f"Could not open {self.port}: {exc}"})
            return

        request_state: dict[str, Any] = {}
        active_request: Optional[CommandRequest] = None
        self._emit({"kind": "connected", "port": self.port, "baud": self.baud})

        try:
            with ser:
                time.sleep(0.2)
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    if active_request is not None and now >= request_state["deadline"]:
                        message = f"Timed out waiting for '{active_request.command}'."
                        self._emit({"kind": "error", "message": message})
                        if not active_request.future.done():
                            active_request.future.set_exception(TimeoutError(message))
                        active_request = None
                        request_state = {}
                        self._set_active(None)

                    if active_request is None:
                        try:
                            next_request = self.request_queue.get(timeout=SERIAL_IDLE_SLEEP_S)
                        except queue.Empty:
                            next_request = None
                        if next_request is not None:
                            if next_request.kind == "stop":
                                break
                            try:
                                payload = f"{next_request.command.rstrip()}\n".encode(
                                    "ascii", errors="replace"
                                )
                                ser.write(payload)
                                ser.flush()
                            except serial.SerialException as exc:
                                self._emit({"kind": "error", "message": f"Serial write error: {exc}"})
                                if not next_request.future.done():
                                    next_request.future.set_exception(exc)
                                break
                            self._emit({"kind": "line", "direction": "tx", "text": next_request.command})
                            if next_request.kind == "raw":
                                if not next_request.future.done():
                                    next_request.future.set_result(None)
                            else:
                                active_request = next_request
                                request_state = self._initial_request_state(next_request)
                                self._set_active(active_request)

                    try:
                        raw = ser.readline()
                    except serial.SerialException as exc:
                        self._emit({"kind": "error", "message": f"Serial read error: {exc}"})
                        break

                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    self._emit({"kind": "line", "direction": "rx", "text": line})

                    if active_request is None:
                        continue

                    completed, result = self._process_active_line(active_request, request_state, line)
                    if not completed:
                        continue
                    if isinstance(result, Exception):
                        self._emit({"kind": "error", "message": str(result)})
                        if not active_request.future.done():
                            active_request.future.set_exception(result)
                    else:
                        if not active_request.future.done():
                            active_request.future.set_result(result)
                    active_request = None
                    request_state = {}
                    self._set_active(None)
        finally:
            if active_request is not None and not active_request.future.done():
                active_request.future.set_exception(RuntimeError("Disconnected."))
            self._fail_pending(RuntimeError("Disconnected."))
            self._set_active(None)
            self._emit(
                {
                    "kind": "disconnected",
                    "message": "Disconnected." if self._manual_shutdown else "Serial link closed.",
                    "manual": self._manual_shutdown,
                }
            )
            self._manual_shutdown = False

    def _initial_request_state(self, request: CommandRequest) -> dict[str, Any]:
        return {
            "deadline": time.monotonic() + request.timeout_s,
            "sample_rate_hz": None,
            "primary": {},
            "secondary": {},
            "measurement": {},
            "status": {},
        }

    def _process_active_line(
        self,
        request: CommandRequest,
        state: dict[str, Any],
        line: str,
    ) -> tuple[bool, Any]:
        if line.lower().startswith("error,"):
            return (True, RuntimeError(line))

        if request.kind == "ack":
            if line.lower().startswith("ok,"):
                self._emit({"kind": "ack", "label": request.label or request.command, "text": line})
                return (True, line)
            return (False, None)

        if request.kind == "status":
            kv_match = KEY_VALUE_RE.match(line)
            if kv_match is None:
                return (False, None)
            state["status"][kv_match.group(1)] = kv_match.group(2)
            if STATUS_KEYS.issubset(state["status"].keys()):
                status = DeviceStatus(dict(state["status"]))
                self._emit({"kind": "status", "status": status})
                return (True, status)
            return (False, None)

        if request.kind == "measure":
            kv_match = KEY_VALUE_RE.match(line)
            if kv_match is None:
                return (False, None)
            state["measurement"][kv_match.group(1)] = kv_match.group(2)
            if MEASUREMENT_KEYS.issubset(state["measurement"].keys()):
                measurement = MeasurementData(dict(state["measurement"]))
                self._emit({"kind": "measurement", "measurement": measurement})
                return (True, measurement)
            return (False, None)

        if request.kind == "capture":
            sample_rate_match = SAMPLE_RATE_LINE_RE.match(line)
            if sample_rate_match is not None:
                state["sample_rate_hz"] = int(sample_rate_match.group(1))
                return (False, None)

            if line.lower().startswith("index,value") or line.lower().startswith("index,v_raw,i_raw"):
                return (False, None)

            if request.capture_mode == "both":
                match = PAIR_VALUE_LINE_RE.match(line)
                if match is None:
                    return (False, None)
                index = int(match.group(1))
                state["primary"][index] = int(match.group(2))
                state["secondary"][index] = int(match.group(3))
            else:
                match = VALUE_LINE_RE.match(line)
                if match is None:
                    return (False, None)
                index = int(match.group(1))
                state["primary"][index] = int(match.group(2))

            if len(state["primary"]) < request.expected_samples or request.expected_samples <= 0:
                return (False, None)

            indices = sorted(state["primary"].keys())[: request.expected_samples]
            if request.capture_mode == "both":
                frame = FrameData(
                    indices=indices,
                    voltage_raw=[state["primary"][i] for i in indices],
                    current_raw=[state["secondary"][i] for i in indices],
                    sample_rate_hz=state["sample_rate_hz"],
                )
            elif request.capture_mode == "current":
                frame = FrameData(
                    indices=indices,
                    voltage_raw=None,
                    current_raw=[state["primary"][i] for i in indices],
                    sample_rate_hz=state["sample_rate_hz"],
                )
            else:
                frame = FrameData(
                    indices=indices,
                    voltage_raw=[state["primary"][i] for i in indices],
                    current_raw=None,
                    sample_rate_hz=state["sample_rate_hz"],
                )
            self._emit({"kind": "frame", "frame": frame})
            return (True, frame)

        return (False, None)


class SweepRunner(threading.Thread):
    def __init__(
        self,
        controller: SerialController,
        event_queue: "queue.Queue[dict[str, Any]]",
        settings: dict[str, Any],
    ) -> None:
        super().__init__(daemon=True)
        self.controller = controller
        self.event_queue = event_queue
        self.settings = settings
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

    def _sync_initial_state(self) -> None:
        commands = [
            CommandRequest("ack", f"amp {self.settings['amp_wiper']}", ACK_TIMEOUT_S, label="amp"),
            CommandRequest("ack", f"offset {self.settings['offset_pwm']}", ACK_TIMEOUT_S, label="offset"),
            CommandRequest("ack", f"range {self.settings['shunt_range']}", ACK_TIMEOUT_S, label="range"),
            CommandRequest("ack", f"vpga {self.settings['voltage_pga']}", ACK_TIMEOUT_S, label="vpga"),
            CommandRequest("ack", f"ipga {self.settings['current_pga']}", ACK_TIMEOUT_S, label="ipga"),
        ]
        for request in commands:
            if self.stop_event.is_set():
                return
            self.controller.submit_wait(request, timeout=request.timeout_s + 0.5)

    def _frequency_points(self) -> list[float]:
        start_hz = max(1.0, float(self.settings["sweep_start_hz"]))
        stop_hz = max(start_hz, float(self.settings["sweep_stop_hz"]))
        points_per_decade = max(1, int(self.settings["sweep_points_per_decade"]))
        decades = math.log10(stop_hz / start_hz) if stop_hz > start_hz else 0.0
        total_points = max(1, int(round(decades * points_per_decade)) + 1)
        if total_points == 1:
            return [start_hz]
        values = []
        for index in range(total_points):
            fraction = index / (total_points - 1)
            value = start_hz * (stop_hz / start_hz) ** fraction
            values.append(value)
        return values

    def run(self) -> None:
        try:
            self._emit({"kind": "sweep_started"})
            self._sync_initial_state()
            points = self._frequency_points()
            total = len(points)
            sample_count = int(self.settings["sweep_samples"])
            sample_rate = int(self.settings["sweep_sample_rate"])
            settle_ms = int(self.settings["sweep_settle_ms"])

            for index, frequency_hz in enumerate(points, start=1):
                if self.stop_event.is_set():
                    self._emit({"kind": "sweep_stopped"})
                    return

                dds_command = f"dds {int(round(frequency_hz))} 1"
                self.controller.submit_wait(
                    CommandRequest("ack", dds_command, ACK_TIMEOUT_S, label="dds"),
                    timeout=ACK_TIMEOUT_S + 0.5,
                )
                if settle_ms > 0:
                    time.sleep(settle_ms / 1000.0)

                measurement = self.controller.submit_wait(
                    CommandRequest(
                        "measure",
                        f"measure {sample_count} {sample_rate}",
                        MEASURE_TIMEOUT_S,
                    ),
                    timeout=MEASURE_TIMEOUT_S + 0.5,
                )
                if not isinstance(measurement, MeasurementData):
                    raise RuntimeError("Unexpected measurement result.")
                self._emit(
                    {
                        "kind": "sweep_point",
                        "point": SweepPoint(frequency_hz=frequency_hz, measurement=measurement),
                        "index": index,
                        "total": total,
                    }
                )
            self._emit({"kind": "sweep_complete"})
        except Exception as exc:  # noqa: BLE001
            self._emit({"kind": "sweep_error", "message": str(exc)})


class MeasurementRunner(threading.Thread):
    def __init__(
        self,
        controller: SerialController,
        event_queue: "queue.Queue[dict[str, Any]]",
        count: int,
        delay_s: float,
        samples: int,
        sample_rate: int,
    ) -> None:
        super().__init__(daemon=True)
        self.controller = controller
        self.event_queue = event_queue
        self.count = max(1, int(count))
        self.delay_s = max(0.0, float(delay_s))
        self.samples = int(samples)
        self.sample_rate = int(sample_rate)
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            pass

    def run(self) -> None:
        try:
            self._emit({"kind": "measurement_series_started", "total": self.count})
            start_time = time.monotonic()
            for index in range(1, self.count + 1):
                if self.stop_event.is_set():
                    self._emit({"kind": "measurement_series_stopped"})
                    return

                measurement = self.controller.submit_wait(
                    CommandRequest(
                        "measure",
                        f"measure {self.samples} {self.sample_rate}",
                        MEASURE_TIMEOUT_S,
                        label="measure",
                    ),
                    timeout=MEASURE_TIMEOUT_S + 0.5,
                )
                if not isinstance(measurement, MeasurementData):
                    raise RuntimeError("Unexpected measurement result.")
                self._emit(
                    {
                        "kind": "measurement_series_point",
                        "point": MeasurementPoint(
                            index=index,
                            elapsed_s=time.monotonic() - start_time,
                            measurement=measurement,
                        ),
                        "total": self.count,
                    }
                )

                if index < self.count and self.delay_s > 0.0:
                    if self.stop_event.wait(self.delay_s):
                        self._emit({"kind": "measurement_series_stopped"})
                        return
            self._emit({"kind": "measurement_series_complete"})
        except Exception as exc:  # noqa: BLE001
            self._emit({"kind": "measurement_series_error", "message": str(exc)})


class LivePlotPanel(QWidget):
    def __init__(self, ui_scale: float) -> None:
        super().__init__()
        self.ui_scale = ui_scale
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(max(8, int(round(8 * ui_scale))))

        self.summary_label = QLabel("Waiting for data...")
        self.summary_label.setObjectName("summaryLabel")
        layout.addWidget(self.summary_label)

        self.voltage_plot = pg.PlotWidget()
        self.current_plot = pg.PlotWidget()
        self.current_plot.setXLink(self.voltage_plot)
        pen_width = max(2, int(round(2 * ui_scale)))
        self.voltage_curve = self.voltage_plot.plot(pen=pg.mkPen(VOLTAGE_COLOR, width=pen_width))
        self.current_curve = self.current_plot.plot(pen=pg.mkPen(CURRENT_COLOR, width=pen_width))
        zero_pen = pg.mkPen(TEXT_COLOR, width=1, style=Qt.PenStyle.DotLine)
        self.voltage_zero_line = pg.InfiniteLine(pos=0.0, angle=0, pen=zero_pen, movable=False)
        self.current_zero_line = pg.InfiniteLine(pos=0.0, angle=0, pen=zero_pen, movable=False)
        self.voltage_plot.addItem(self.voltage_zero_line)
        self.current_plot.addItem(self.current_zero_line)

        for plot, title, color in (
            (self.voltage_plot, "Voltage", VOLTAGE_COLOR),
            (self.current_plot, "Current", CURRENT_COLOR),
        ):
            plot.setBackground(PANEL_COLOR)
            plot.showGrid(x=True, y=True, alpha=0.6)
            plot.getPlotItem().setLabel("left", title, color=FIELD_TEXT_COLOR)
            plot.getPlotItem().setLabel("bottom", "Sample", color=FIELD_TEXT_COLOR)
            plot.getPlotItem().getAxis("left").setTextPen(FIELD_TEXT_COLOR)
            plot.getPlotItem().getAxis("bottom").setTextPen(FIELD_TEXT_COLOR)
            plot.getPlotItem().getAxis("left").setPen(BORDER_COLOR)
            plot.getPlotItem().getAxis("bottom").setPen(BORDER_COLOR)
            plot.getPlotItem().getAxis("left").setTickFont(QFont(APP_FONT_FAMILY, max(10, int(round(10 * ui_scale)))))
            plot.getPlotItem().getAxis("bottom").setTickFont(QFont(APP_FONT_FAMILY, max(10, int(round(10 * ui_scale)))))
        self.voltage_plot.getPlotItem().setLabel("bottom", "", color=FIELD_TEXT_COLOR)
        self.voltage_plot.getPlotItem().getAxis("bottom").setStyle(showValues=False, tickLength=0)
        self.voltage_plot.getPlotItem().getAxis("bottom").setHeight(0)
        layout.addWidget(self.voltage_plot, 3)
        layout.addWidget(self.current_plot, 2)

    @staticmethod
    def _scaled_unit(max_abs: float, base_unit: str) -> tuple[str, float]:
        if max_abs < 1e-6:
            return (f"µ{base_unit}", 1e6)
        if max_abs < 1.0:
            return (f"m{base_unit}", 1e3)
        return (base_unit, 1.0)

    def update_frame(
        self,
        frame: FrameData,
        *,
        voltage_gain: float,
        current_gain: float,
        shunt_resistance: float,
        vref: float,
        adc_max: int,
    ) -> None:
        if frame.sample_rate_hz and frame.sample_rate_hz > 0:
            frame_duration_s = max(1, len(frame.indices)) / frame.sample_rate_hz
            unit_label, unit_scale = time_axis_unit(frame_duration_s)
            x_values = [(idx / frame.sample_rate_hz) * unit_scale for idx in frame.indices]
            self.current_plot.getPlotItem().setLabel("bottom", f"Time [{unit_label}]", color=FIELD_TEXT_COLOR)
        else:
            x_values = list(frame.indices)
            self.current_plot.getPlotItem().setLabel("bottom", "Sample Index", color=FIELD_TEXT_COLOR)

        if frame.voltage_raw is not None:
            physical_values = [
                ((value * vref / adc_max) - (vref * 0.5)) / max(voltage_gain, 1e-9)
                for value in frame.voltage_raw
            ]
            unit_label, unit_scale = self._scaled_unit(
                (vref * 0.5) / max(voltage_gain, 1e-9),
                "V",
            )
            self.voltage_plot.getPlotItem().setLabel("left", f"Voltage [{unit_label}]", color=FIELD_TEXT_COLOR)
            self.voltage_curve.setData(x_values, [value * unit_scale for value in physical_values])
            voltage_limit = ((vref * 0.5) / max(voltage_gain, 1e-9)) * unit_scale
            self.voltage_plot.setYRange(-voltage_limit, voltage_limit, padding=0.0)
            if x_values:
                self.voltage_plot.setXRange(min(x_values), max(x_values), padding=0.0)
            self.voltage_plot.show()
        else:
            self.voltage_curve.setData([], [])
            self.voltage_plot.hide()

        if frame.current_raw is not None:
            scale = max(current_gain * shunt_resistance, 1e-12)
            physical_values = [(((value * vref / adc_max) - (vref * 0.5)) / scale) for value in frame.current_raw]
            unit_label, unit_scale = self._scaled_unit(
                (vref * 0.5) / scale,
                "A",
            )
            self.current_plot.getPlotItem().setLabel("left", f"Current [{unit_label}]", color=FIELD_TEXT_COLOR)
            self.current_curve.setData(x_values, [value * unit_scale for value in physical_values])
            current_limit = ((vref * 0.5) / scale) * unit_scale
            self.current_plot.setYRange(-current_limit, current_limit, padding=0.0)
            if x_values:
                self.current_plot.setXRange(min(x_values), max(x_values), padding=0.0)
            self.current_plot.show()
        else:
            self.current_curve.setData([], [])
            self.current_plot.hide()

        sample_rate_text = (
            f" | Fs {format_engineering(frame.sample_rate_hz, 2)}Hz"
            if frame.sample_rate_hz and frame.sample_rate_hz > 0
            else ""
        )
        visible = []
        if frame.voltage_raw is not None:
            visible.append("voltage")
        if frame.current_raw is not None:
            visible.append("current")
        self.summary_label.setText(
            f"{len(frame.indices)} samples | {' + '.join(visible) or 'none'}{sample_rate_text}"
        )


class MeasurementPanel(QWidget):
    FIELD_ORDER = (
        ("|Z|", "impedance_mag_ohm"),
        ("Re(Z)", "impedance_real_ohm"),
        ("Im(Z)", "impedance_imag_ohm"),
        ("Phase diff", "phase_diff_deg"),
        ("Sample rate", "sample_rate_hz"),
        ("DDS freq", "dds_frequency_hz"),
        ("Samples/period", "samples_per_period"),
        ("Captured cycles", "captured_cycles"),
        ("Voltage amp", "voltage_amplitude_v"),
        ("Current amp", "current_amplitude_a"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, QLineEdit] = {}
        self.summary_fields: dict[str, QLineEdit] = {}
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        summary_layout = QGridLayout()
        summary_layout.setHorizontalSpacing(10)
        summary_layout.setVerticalSpacing(8)
        for index, label_text in enumerate(("R", "L", "C")):
            label = QLabel(label_text)
            field = QLineEdit()
            field.setReadOnly(True)
            field.setObjectName("measurementField")
            summary_layout.addWidget(label, 0, index * 2)
            summary_layout.addWidget(field, 0, (index * 2) + 1)
            self.summary_fields[label_text] = field
        root_layout.addLayout(summary_layout)

        self.model_label = QLabel("")
        self.model_label.setObjectName("summaryLabel")
        root_layout.addWidget(self.model_label)

        layout = QGridLayout()
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)
        for index, (label_text, key) in enumerate(self.FIELD_ORDER):
            row = index // 2
            col = (index % 2) * 2
            label = QLabel(label_text)
            field = QLineEdit()
            field.setReadOnly(True)
            field.setObjectName("measurementField")
            layout.addWidget(label, row, col)
            layout.addWidget(field, row, col + 1)
            self.fields[key] = field
        root_layout.addLayout(layout)

    def clear(self) -> None:
        for field in self.fields.values():
            field.clear()
        for field in self.summary_fields.values():
            field.clear()
        self.model_label.clear()

    def update_measurement(self, measurement: MeasurementData) -> None:
        values = dict(measurement.values)
        model, series_r, series_l, series_c = derived_rlc_fields(values)
        self.summary_fields["R"].setText(series_r)
        self.summary_fields["L"].setText(series_l)
        self.summary_fields["C"].setText(series_c)
        self.model_label.setText(f"Model: {model}" if model else "")

        for key, field in self.fields.items():
            raw = values.get(key, "")
            try:
                numeric = float(raw)
            except (TypeError, ValueError):
                field.setText(raw)
                continue
            if key in {"phase_diff_deg"}:
                field.setText(f"{numeric:.3f} deg")
            elif key in {"impedance_mag_ohm", "impedance_real_ohm", "impedance_imag_ohm"}:
                field.setText(format_engineering_unit(numeric, "Ω"))
            elif key in {"samples_per_period", "captured_cycles"}:
                field.setText(f"{numeric:.3f}")
            else:
                field.setText(format_engineering(numeric, 3))


def create_square_option_group(
    labels: list[str],
    callback: Any,
    ui_scale: float,
) -> tuple[QWidget, QButtonGroup]:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    group = QButtonGroup(container)
    group.setExclusive(True)
    button_width = max(76, int(round(76 * ui_scale)))
    button_height = max(40, int(round(40 * ui_scale)))
    for index, label in enumerate(labels):
        button = QPushButton(label)
        button.setCheckable(True)
        button.setProperty("squareOption", True)
        button.setMinimumSize(button_width, button_height)
        button.setMaximumSize(button_width, button_height)
        button.clicked.connect(lambda _checked=False, cb=callback: cb())
        group.addButton(button, index)
        layout.addWidget(button)
    layout.addStretch(1)
    return container, group


class MainWindow(QMainWindow):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.ui_scale = compute_ui_scale()
        self.controller = SerialController()
        self.profile_store = ProfileStore(PROFILE_PATH)
        self.profiles = self.profile_store.load()
        self.sweep_event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=128)
        self.measurement_event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=128)
        self.sweep_runner: Optional[SweepRunner] = None
        self.measurement_runner: Optional[MeasurementRunner] = None
        self.latest_frame: Optional[FrameData] = None
        self.latest_status: dict[str, str] = {}
        self.last_connected_port: str = args.port or autodetect_port() or ""
        self.manual_disconnect = False
        self.source_dirty = True
        self.frontend_dirty = True
        self._suspend_source_autoupdate = False
        self._suspend_frontend_autoupdate = False
        self.freeze_view = False
        self.sweep_results: list[SweepPoint] = []
        self.measurement_results: list[MeasurementPoint] = []

        self.setWindowTitle(APP_TITLE)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(
            int(round(1680 * self.ui_scale)),
            int(round(980 * self.ui_scale)),
        )
        self._configure_palette()
        self._build_ui()
        self._restore_initial_values()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_events)
        self.poll_timer.start(POLL_PERIOD_MS)

        self.auto_capture_timer = QTimer(self)
        self.auto_capture_timer.timeout.connect(self._auto_capture_tick)

        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.timeout.connect(self._attempt_reconnect)
        self.reconnect_timer.setInterval(AUTO_RECONNECT_PERIOD_MS)

        self._refresh_ports()
        self._update_profile_combo()
        self._update_status("Ready.")
        QTimer.singleShot(0, self._connect_on_startup)

    def _configure_palette(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        font_size = max(13, int(round(13 * self.ui_scale)))
        summary_size = max(12, int(round(12 * self.ui_scale)))
        panel_title_size = max(14, int(round(14 * self.ui_scale)))
        border_radius = 0
        group_radius = 0
        padding_y = max(7, int(round(7 * self.ui_scale)))
        padding_x = max(12, int(round(12 * self.ui_scale)))
        field_padding = max(6, int(round(6 * self.ui_scale)))
        title_margin = max(10, int(round(10 * self.ui_scale)))
        title_left = max(12, int(round(12 * self.ui_scale)))
        header_padding = max(6, int(round(6 * self.ui_scale)))
        app_font = QFont(APP_FONT_FAMILY)
        app_font.setPointSizeF(max(11.0, 11.0 * self.ui_scale))
        app.setFont(app_font)
        app.setStyleSheet(
            f"""
            QWidget {{
                background: {BG_COLOR};
                color: {TEXT_COLOR};
                font-size: {font_size}px;
                font-family: "{APP_FONT_FAMILY}";
            }}
            QMainWindow {{
                background: {BG_COLOR};
            }}
            QGroupBox {{
                border: 1px solid {BORDER_COLOR};
                border-radius: {group_radius}px;
                margin-top: {title_margin}px;
                padding-top: {title_margin}px;
                background: {PANEL_COLOR};
                font-weight: 600;
                font-size: {panel_title_size}px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: {title_left}px;
                padding: 0 4px;
            }}
            QPushButton, QToolButton {{
                background: {PANEL_ALT_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: {border_radius}px;
                padding: {padding_y}px {padding_x}px;
            }}
            QPushButton:hover, QToolButton:hover {{
                background: #2d1800;
                color: #fff1c7;
            }}
            QPushButton:pressed, QToolButton:pressed {{
                background: #5a2f00;
                color: #fff6dc;
            }}
            QPushButton:disabled {{
                color: #7d6024;
                border-color: #5b3a10;
            }}
            QPushButton[connectionState="connect"] {{
                background: #1b4a22;
                border: 1px solid #49a35a;
                color: #c8ffd0;
            }}
            QPushButton[connectionState="connect"]:hover {{
                background: #23612d;
                color: #e6ffea;
            }}
            QPushButton[connectionState="connect"]:pressed {{
                background: #2c7a39;
                color: #f4fff6;
            }}
            QPushButton[connectionState="disconnect"] {{
                background: #5a1717;
                border: 1px solid #c74a4a;
                color: #ffd0d0;
            }}
            QPushButton[connectionState="disconnect"]:hover {{
                background: #742020;
                color: #ffe1e1;
            }}
            QPushButton[connectionState="disconnect"]:pressed {{
                background: #962828;
                color: #fff0f0;
            }}
            QPushButton[savePrompt="true"] {{
                background: #123d73;
                border: 1px solid {CURRENT_COLOR};
                color: #d7ebff;
            }}
            QPushButton[savePrompt="true"]:hover {{
                background: #18508f;
                color: #eef7ff;
            }}
            QPushButton[savePrompt="true"]:pressed {{
                background: #2065b0;
                color: #ffffff;
            }}
            QCheckBox#autoCaptureCheckbox {{
                spacing: {max(8, int(round(8 * self.ui_scale)))}px;
            }}
            QCheckBox#autoCaptureCheckbox::indicator {{
                width: {max(16, int(round(16 * self.ui_scale)))}px;
                height: {max(16, int(round(16 * self.ui_scale)))}px;
                border: 1px solid {BORDER_COLOR};
                background: #000000;
            }}
            QCheckBox#autoCaptureCheckbox::indicator:checked {{
                background: #3a1f00;
                border: 1px solid {FIELD_TEXT_COLOR};
            }}
            QCheckBox#ddsEnabledCheckbox {{
                spacing: {max(8, int(round(8 * self.ui_scale)))}px;
                border: 1px solid #49a35a;
                padding: {field_padding}px;
                background: #1b4a22;
                color: #c8ffd0;
            }}
            QCheckBox#ddsEnabledCheckbox::indicator {{
                width: {max(16, int(round(16 * self.ui_scale)))}px;
                height: {max(16, int(round(16 * self.ui_scale)))}px;
                border: 1px solid #49a35a;
                background: #000000;
            }}
            QCheckBox#ddsEnabledCheckbox::indicator:checked {{
                background: #2c7a39;
                border: 1px solid #6ee787;
            }}
            QCheckBox#ddsEnabledCheckbox:unchecked {{
                background: #5a1717;
                color: #ffd0d0;
                border: 1px solid #c74a4a;
            }}
            QCheckBox#ddsEnabledCheckbox::indicator:unchecked {{
                border: 1px solid #c74a4a;
                background: #120000;
            }}
            QPushButton[sampleRateAuto="true"] {{
                min-width: {max(76, int(round(76 * self.ui_scale)))}px;
            }}
            QPushButton[sampleRateAuto="true"]:checked {{
                background: #3a1f00;
                color: #fff1c7;
                border: 1px solid {FIELD_TEXT_COLOR};
            }}
            QPushButton[squareOption="true"] {{
                background: #000000;
                color: {FIELD_TEXT_COLOR};
                border: 1px solid {BORDER_COLOR};
                padding: 4px;
                text-align: center;
                font-weight: 600;
            }}
            QPushButton[squareOption="true"]:hover {{
                background: #2d1800;
                color: #fff1c7;
            }}
            QPushButton[squareOption="true"]:pressed {{
                background: #5a2f00;
                color: #fff6dc;
            }}
            QPushButton[squareOption="true"]:checked {{
                background: #3a1f00;
                color: #fff1c7;
                border: 1px solid {FIELD_TEXT_COLOR};
            }}
            QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
                background: #000000;
                color: {FIELD_TEXT_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: {border_radius}px;
                padding: {field_padding}px;
                selection-background-color: #6b3f00;
            }}
            QSpinBox:disabled {{
                color: #7a5a22;
                border-color: #4d3612;
                background: #090909;
            }}
            QTabWidget::pane {{
                border: 1px solid {BORDER_COLOR};
                background: {PANEL_COLOR};
                border-radius: {group_radius}px;
                top: -1px;
            }}
            QTabBar::tab {{
                background: #080500;
                border: 1px solid #4d3612;
                color: {MUTED_COLOR};
                padding: {max(8, int(round(8 * self.ui_scale)))}px {max(14, int(round(14 * self.ui_scale)))}px;
                margin-right: 4px;
                border-top-left-radius: {border_radius}px;
                border-top-right-radius: {border_radius}px;
            }}
            QTabBar::tab:hover {{
                background: #1f1200;
                color: {TEXT_COLOR};
                border-color: {BORDER_COLOR};
            }}
            QTabBar::tab:selected {{
                background: #3a1f00;
                color: #fff1c7;
                border: 2px solid {FIELD_TEXT_COLOR};
                border-bottom: 2px solid {PANEL_COLOR};
                font-weight: 700;
            }}
            QStatusBar {{
                background: {PANEL_COLOR};
                border-top: 1px solid {BORDER_COLOR};
            }}
            QLabel#summaryLabel {{
                color: {MUTED_COLOR};
                font-size: {summary_size}px;
            }}
            QLineEdit#measurementField {{
                background: #000000;
                color: {FIELD_TEXT_COLOR};
                border-color: {BORDER_COLOR};
                font-weight: 600;
            }}
            QHeaderView::section {{
                background: #000000;
                border: none;
                border-bottom: 1px solid {BORDER_COLOR};
                padding: {header_padding}px;
                font-weight: 600;
            }}
            QTableWidget {{
                background: #000000;
                border: 1px solid {BORDER_COLOR};
                gridline-color: {GRID_COLOR};
                border-radius: {group_radius}px;
            }}
            """
        )
        pg.setConfigOptions(antialias=True, foreground=TEXT_COLOR)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        spacing = max(12, int(round(12 * self.ui_scale)))
        root_layout.setContentsMargins(spacing, spacing, spacing, spacing)
        root_layout.setSpacing(spacing)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        sidebar = self._build_sidebar()
        main_panel = self._build_main_panel()
        sidebar.setMinimumWidth(int(round(460 * self.ui_scale)))
        splitter.addWidget(sidebar)
        splitter.addWidget(main_panel)
        splitter.setSizes([int(round(460 * self.ui_scale)), int(round(1120 * self.ui_scale))])

        status = QStatusBar()
        self.setStatusBar(status)
        self.connection_label = QLabel("Disconnected")
        self.connection_label.setStyleSheet(f"color: {WARNING_COLOR}; font-weight: 700;")
        status.addPermanentWidget(self.connection_label)

    def _build_sidebar(self) -> QWidget:
        container = QScrollArea()
        container.setWidgetResizable(True)
        content = QWidget()
        container.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_capture_group())
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_advanced_group())
        layout.addWidget(self._build_profiles_group())
        layout.addStretch(1)
        return container

    def _build_connection_group(self) -> QWidget:
        box = QGroupBox("Connection")
        layout = QGridLayout(box)

        self.port_combo = QComboBox()
        self.refresh_ports_button = QPushButton("Refresh")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setProperty("connectionState", "connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.auto_reconnect_checkbox = QCheckBox("Auto reconnect")
        self.auto_reconnect_checkbox.setChecked(True)

        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(1200, 4_000_000)
        self.baud_spin.setValue(self.args.baud)

        self.sync_status_button = QPushButton("Read status")
        self.sync_status_button.clicked.connect(self.refresh_status)

        layout.addWidget(QLabel("Port"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1)
        layout.addWidget(self.refresh_ports_button, 0, 2)
        layout.addWidget(QLabel("Baud"), 1, 0)
        layout.addWidget(self.baud_spin, 1, 1)
        layout.addWidget(self.connect_button, 1, 2)
        layout.addWidget(self.auto_reconnect_checkbox, 2, 0, 1, 2)
        layout.addWidget(self.sync_status_button, 2, 2)
        return box

    def _build_capture_group(self) -> QWidget:
        box = QGroupBox("Live Capture")
        layout = QGridLayout(box)

        self.samples_spin = QSpinBox()
        self.samples_spin.setRange(1, 400)
        self.samples_spin.setValue(self.args.samples)

        self.sample_rate_spin = QSpinBox()
        self.sample_rate_spin.setRange(1, 2_000_000)
        self.sample_rate_spin.setValue(100000)

        self.sample_rate_auto_button = QPushButton("Auto")
        self.sample_rate_auto_button.setProperty("sampleRateAuto", True)
        self.sample_rate_auto_button.setCheckable(True)
        self.sample_rate_auto_button.setChecked(True)
        self.sample_rate_auto_button.toggled.connect(self._set_sample_rate_auto)
        self._set_sample_rate_auto(True)

        self.request_interval_spin = QSpinBox()
        self.request_interval_spin.setRange(20, 5000)
        self.request_interval_spin.setValue(self.args.request_interval_ms)
        self.auto_capture_checkbox = QCheckBox("Auto capture")
        self.auto_capture_checkbox.setObjectName("autoCaptureCheckbox")
        self.auto_capture_checkbox.setLayoutDirection(Qt.RightToLeft)
        self.auto_capture_checkbox.toggled.connect(self._sync_auto_capture_timer)

        self.capture_button = QPushButton("Capture now")
        self.capture_button.clicked.connect(self.capture_once)
        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self.run_measurement)
        self.measurement_count_spin = QSpinBox()
        self.measurement_count_spin.setRange(1, 10000)
        self.measurement_count_spin.setValue(100)
        self.measurement_delay_spin = QDoubleSpinBox()
        self.measurement_delay_spin.setRange(0.0, 3600.0)
        self.measurement_delay_spin.setDecimals(2)
        self.measurement_delay_spin.setSingleStep(0.01)
        self.measurement_delay_spin.setSuffix(" s")
        self.run_measurements_button = QPushButton("Run measurements")
        self.run_measurements_button.clicked.connect(self.start_measurement_series)
        self.stop_measurements_button = QPushButton("Stop")
        self.stop_measurements_button.clicked.connect(self.stop_measurement_series)
        self.stop_measurements_button.setEnabled(False)
        self.save_measurements_button = QPushButton("Save CSV")
        self.save_measurements_button.setProperty("savePrompt", False)
        self.save_measurements_button.setEnabled(False)
        self.save_measurements_button.clicked.connect(self.save_measurements_csv)

        layout.addWidget(QLabel("Samples"), 0, 0)
        layout.addWidget(self.samples_spin, 0, 1)
        layout.addWidget(QLabel("Sample rate"), 1, 0)
        sample_rate_row = QWidget()
        sample_rate_layout = QHBoxLayout(sample_rate_row)
        sample_rate_layout.setContentsMargins(0, 0, 0, 0)
        sample_rate_layout.setSpacing(6)
        sample_rate_layout.addWidget(self.sample_rate_spin, 1)
        sample_rate_layout.addWidget(self.sample_rate_auto_button, 0)
        layout.addWidget(sample_rate_row, 1, 1)
        layout.addWidget(QLabel("Auto interval"), 2, 0)
        layout.addWidget(self.request_interval_spin, 2, 1)
        layout.addWidget(self.auto_capture_checkbox, 3, 0, 1, 2)
        layout.addWidget(self.capture_button, 4, 0)
        layout.addWidget(self.measure_button, 4, 1)
        layout.addWidget(QLabel("Measurements"), 5, 0)
        layout.addWidget(self.measurement_count_spin, 5, 1)
        layout.addWidget(QLabel("Delay"), 6, 0)
        layout.addWidget(self.measurement_delay_spin, 6, 1)
        layout.addWidget(self.run_measurements_button, 7, 0)
        layout.addWidget(self.stop_measurements_button, 7, 1)
        layout.addWidget(self.save_measurements_button, 8, 0, 1, 2)
        return box

    def _build_source_group(self) -> QWidget:
        box = QGroupBox("Source")
        layout = QGridLayout(box)

        self.dds_spin = QSpinBox()
        self.dds_spin.setRange(DDS_FREQ_MIN_HZ, DDS_FREQ_MAX_HZ)
        self.dds_spin.setSingleStep(100)
        self.dds_spin.valueChanged.connect(self._dds_spin_changed)

        self.dds_slider = QSlider(Qt.Horizontal)
        self.dds_slider.setRange(0, DDS_SLIDER_STEPS)
        self.dds_slider.valueChanged.connect(self._dds_slider_changed)
        self.dds_slider.sliderReleased.connect(self._apply_source_if_needed)

        self.dds_enabled_checkbox = QCheckBox("DDS enabled")
        self.dds_enabled_checkbox.setObjectName("ddsEnabledCheckbox")
        self.dds_enabled_checkbox.setChecked(True)
        self.dds_enabled_checkbox.toggled.connect(self._source_control_changed)

        self.amp_spin = QSpinBox()
        self.amp_spin.setRange(0, 255)
        self.amp_spin.setValue(128)
        self.amp_spin.valueChanged.connect(self._source_control_changed)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(0, 255)
        self.offset_spin.valueChanged.connect(self._source_control_changed)

        layout.addWidget(QLabel("DDS frequency"), 0, 0)
        layout.addWidget(self.dds_spin, 0, 1)
        layout.addWidget(self.dds_slider, 1, 0, 1, 2)
        layout.addWidget(self.dds_enabled_checkbox, 2, 0, 1, 2)
        layout.addWidget(QLabel("Amplitude"), 3, 0)
        layout.addWidget(self.amp_spin, 3, 1)
        layout.addWidget(QLabel("Offset"), 4, 0)
        layout.addWidget(self.offset_spin, 4, 1)
        return box

    def _build_advanced_group(self) -> QWidget:
        box = QGroupBox("Advanced Frontend")
        layout = QGridLayout(box)

        shunt_labels = [
            f"{format_engineering(value, 0)} Ω"
            for value in SHUNT_RESISTANCE_TABLE
        ]
        self.shunt_selector, self.shunt_group = create_square_option_group(
            shunt_labels,
            self._frontend_selection_changed,
            self.ui_scale,
        )

        gain_labels = [f"×{gain:g}" for gain in FRONTEND_GAIN_TABLE]
        self.vpga_selector, self.vpga_group = create_square_option_group(
            gain_labels,
            self._frontend_selection_changed,
            self.ui_scale,
        )

        self.ipga_selector, self.ipga_group = create_square_option_group(
            gain_labels,
            self._frontend_selection_changed,
            self.ui_scale,
        )

        layout.addWidget(QLabel("Shunt range"), 0, 0)
        layout.addWidget(self.shunt_selector, 0, 1)
        layout.addWidget(QLabel("Voltage PGA"), 1, 0)
        layout.addWidget(self.vpga_selector, 1, 1)
        layout.addWidget(QLabel("Current PGA"), 2, 0)
        layout.addWidget(self.ipga_selector, 2, 1)
        return box

    def _build_profiles_group(self) -> QWidget:
        box = QGroupBox("Profiles")
        layout = QGridLayout(box)

        self.profile_combo = QComboBox()
        self.load_profile_button = QPushButton("Load")
        self.load_profile_button.clicked.connect(self.load_profile)
        self.save_profile_button = QPushButton("Save current")
        self.save_profile_button.clicked.connect(self.save_profile)
        self.delete_profile_button = QPushButton("Delete")
        self.delete_profile_button.clicked.connect(self.delete_profile)

        layout.addWidget(self.profile_combo, 0, 0, 1, 3)
        layout.addWidget(self.load_profile_button, 1, 0)
        layout.addWidget(self.save_profile_button, 1, 1)
        layout.addWidget(self.delete_profile_button, 1, 2)
        return box

    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.session_summary = QLabel("No device connected.")
        self.session_summary.setObjectName("summaryLabel")
        layout.addWidget(self.session_summary)

        self.tabs = QTabWidget()
        self.live_tab = self._build_live_tab()
        self.sweep_tab = self._build_sweep_tab()
        self.data_viewer_tab = DataViewerWidget(load_last=False)
        self.terminal_tab = self._build_terminal_tab()
        self.tabs.addTab(self.live_tab, "Live")
        # Sweep tab hidden intentionally; keep functionality available in code.
        # self.tabs.addTab(self.sweep_tab, "Sweep")
        self.tabs.addTab(self.data_viewer_tab, "Data Viewer")
        self.tabs.addTab(self.terminal_tab, "Terminal")
        layout.addWidget(self.tabs, 1)
        return panel

    def _build_live_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self.live_plot = LivePlotPanel(self.ui_scale)
        self.measurement_panel = MeasurementPanel()
        measurement_group = QGroupBox("Measurement")
        measurement_layout = QVBoxLayout(measurement_group)
        measurement_layout.addWidget(self.measurement_panel)
        self.measurement_series_label = QLabel("No repeated measurements.")
        self.measurement_series_label.setObjectName("summaryLabel")
        measurement_layout.addWidget(self.measurement_series_label)
        self.measurement_table = QTableWidget(0, 8)
        self.measurement_table.setHorizontalHeaderLabels(
            ["#", "Elapsed (s)", "|Z|", "Phase", "Model", "R", "L", "C"]
        )
        self.measurement_table.verticalHeader().setVisible(False)
        self.measurement_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.measurement_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.measurement_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        measurement_layout.addWidget(self.measurement_table, 1)

        layout.addWidget(self.live_plot, 3)
        layout.addWidget(measurement_group, 2)
        return tab

    def _build_sweep_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        controls = QGroupBox("Frequency Sweep")
        controls_layout = QGridLayout(controls)
        self.sweep_start_spin = QDoubleSpinBox()
        self.sweep_start_spin.setRange(1.0, 1_000_000.0)
        self.sweep_start_spin.setDecimals(1)
        self.sweep_start_spin.setValue(100.0)
        self.sweep_stop_spin = QDoubleSpinBox()
        self.sweep_stop_spin.setRange(1.0, 1_000_000.0)
        self.sweep_stop_spin.setDecimals(1)
        self.sweep_stop_spin.setValue(10_000.0)
        self.sweep_ppd_spin = QSpinBox()
        self.sweep_ppd_spin.setRange(1, 50)
        self.sweep_ppd_spin.setValue(6)
        self.sweep_settle_spin = QSpinBox()
        self.sweep_settle_spin.setRange(0, 5000)
        self.sweep_settle_spin.setValue(120)
        self.sweep_samples_spin = QSpinBox()
        self.sweep_samples_spin.setRange(1, 20000)
        self.sweep_samples_spin.setValue(self.args.samples)
        self.sweep_sample_rate_spin = QSpinBox()
        self.sweep_sample_rate_spin.setRange(0, 2_000_000)
        self.sweep_sample_rate_spin.setSpecialValueText("auto")
        self.sweep_sample_rate_spin.setValue(0)
        self.start_sweep_button = QPushButton("Run sweep")
        self.start_sweep_button.clicked.connect(self.start_sweep)
        self.stop_sweep_button = QPushButton("Stop")
        self.stop_sweep_button.clicked.connect(self.stop_sweep)
        self.stop_sweep_button.setEnabled(False)
        self.sweep_progress_label = QLabel("No sweep running.")
        self.sweep_progress_label.setObjectName("summaryLabel")

        controls_layout.addWidget(QLabel("Start Hz"), 0, 0)
        controls_layout.addWidget(self.sweep_start_spin, 0, 1)
        controls_layout.addWidget(QLabel("Stop Hz"), 0, 2)
        controls_layout.addWidget(self.sweep_stop_spin, 0, 3)
        controls_layout.addWidget(QLabel("Pts/decade"), 1, 0)
        controls_layout.addWidget(self.sweep_ppd_spin, 1, 1)
        controls_layout.addWidget(QLabel("Settle ms"), 1, 2)
        controls_layout.addWidget(self.sweep_settle_spin, 1, 3)
        controls_layout.addWidget(QLabel("Samples"), 2, 0)
        controls_layout.addWidget(self.sweep_samples_spin, 2, 1)
        controls_layout.addWidget(QLabel("Sample rate"), 2, 2)
        controls_layout.addWidget(self.sweep_sample_rate_spin, 2, 3)
        controls_layout.addWidget(self.start_sweep_button, 3, 0, 1, 2)
        controls_layout.addWidget(self.stop_sweep_button, 3, 2, 1, 2)
        controls_layout.addWidget(self.sweep_progress_label, 4, 0, 1, 4)

        self.sweep_mag_plot = pg.PlotWidget(axisItems={"bottom": DecadeAxisItem(orientation="bottom")})
        self.sweep_phase_plot = pg.PlotWidget(axisItems={"bottom": DecadeAxisItem(orientation="bottom")})
        for plot, label in ((self.sweep_mag_plot, "|Z| [Ω]"), (self.sweep_phase_plot, "Phase [deg]")):
            plot.setBackground(PANEL_COLOR)
            plot.showGrid(x=True, y=True, alpha=0.6)
            plot.getPlotItem().setLogMode(x=True, y=False)
            plot.getPlotItem().setLabel("left", label, color=FIELD_TEXT_COLOR)
            plot.getPlotItem().setLabel("bottom", "Frequency [Hz]", color=FIELD_TEXT_COLOR)
            plot.getPlotItem().getAxis("left").setTextPen(FIELD_TEXT_COLOR)
            plot.getPlotItem().getAxis("bottom").setTextPen(FIELD_TEXT_COLOR)
            plot.getPlotItem().getAxis("left").setPen(BORDER_COLOR)
            plot.getPlotItem().getAxis("bottom").setPen(BORDER_COLOR)
        self.sweep_mag_curve = self.sweep_mag_plot.plot(
            pen=pg.mkPen(ACCENT_COLOR, width=2), symbol="o", symbolBrush=ACCENT_COLOR
        )
        self.sweep_phase_curve = self.sweep_phase_plot.plot(
            pen=pg.mkPen(CURRENT_COLOR, width=2), symbol="o", symbolBrush=CURRENT_COLOR
        )

        self.sweep_table = QTableWidget(0, 7)
        self.sweep_table.setHorizontalHeaderLabels(
            ["Freq (Hz)", "|Z|", "Phase", "Model", "R", "L", "C"]
        )
        self.sweep_table.verticalHeader().setVisible(False)
        self.sweep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sweep_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sweep_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout.addWidget(controls)
        layout.addWidget(self.sweep_mag_plot, 2)
        layout.addWidget(self.sweep_phase_plot, 2)
        layout.addWidget(self.sweep_table, 3)
        return tab

    def _build_terminal_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.document().setMaximumBlockCount(TERMINAL_MAX_BLOCKS)
        terminal_font = QFont(APP_FONT_FAMILY)
        terminal_font.setPointSizeF(max(11.0, 11.0 * self.ui_scale))
        self.terminal_output.setFont(terminal_font)

        self.terminal_input = QLineEdit()
        self.terminal_input.returnPressed.connect(self.send_terminal_command)
        self.send_terminal_button = QPushButton("Send")
        self.send_terminal_button.clicked.connect(self.send_terminal_command)
        self.clear_terminal_button = QPushButton("Clear")
        self.clear_terminal_button.clicked.connect(self.terminal_output.clear)

        input_row = QHBoxLayout()
        input_row.addWidget(self.terminal_input, 1)
        input_row.addWidget(self.send_terminal_button)
        input_row.addWidget(self.clear_terminal_button)

        hint = QLabel("Terminal commands can still use the protocol-aware parser for status/capture/measure/set commands.")
        hint.setObjectName("summaryLabel")

        layout.addWidget(self.terminal_output, 1)
        layout.addLayout(input_row)
        layout.addWidget(hint)
        return tab

    def _restore_initial_values(self) -> None:
        self.dds_spin.setValue(1000)
        self.dds_slider.setValue(dds_step_from_frequency(1000))
        self._set_button_group_value(self.shunt_group, 0)
        self._set_button_group_value(self.vpga_group, 0)
        self._set_button_group_value(self.ipga_group, 0)

    def _update_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    @staticmethod
    def _button_group_value(group: QButtonGroup) -> int:
        checked_id = group.checkedId()
        return 0 if checked_id < 0 else checked_id

    @staticmethod
    def _set_button_group_value(group: QButtonGroup, value: int) -> None:
        button = group.button(value)
        if button is not None:
            button.setChecked(True)

    def _append_terminal_line(self, direction: str, text: str) -> None:
        cursor = self.terminal_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        prefix = "TX> " if direction == "tx" else "RX> "
        color = ACCENT_COLOR if direction == "tx" else TEXT_COLOR
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(f"{prefix}{text}\n", fmt)
        self.terminal_output.setTextCursor(cursor)
        self.terminal_output.ensureCursorVisible()

    def _append_note(self, text: str, *, color: str = MUTED_COLOR) -> None:
        cursor = self.terminal_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(f"# {text}\n", fmt)
        self.terminal_output.setTextCursor(cursor)
        self.terminal_output.ensureCursorVisible()

    def _refresh_ports(self) -> None:
        ports = available_ports()
        preferred = self.last_connected_port or self.port_combo.currentText() or autodetect_port() or ""
        if preferred and preferred not in ports:
            ports.insert(0, preferred)
        with QSignalBlocker(self.port_combo):
            self.port_combo.clear()
            self.port_combo.addItems(ports)
            if preferred:
                index = self.port_combo.findText(preferred)
                if index >= 0:
                    self.port_combo.setCurrentIndex(index)
        if not ports and preferred:
            self.port_combo.addItem(preferred)
            self.port_combo.setCurrentIndex(0)

    def _set_sample_rate_auto(self, checked: bool) -> None:
        self.sample_rate_spin.setEnabled(not checked)

    def _connect_on_startup(self) -> None:
        if self.controller.is_connected():
            return
        port = self.port_combo.currentText().strip()
        if not port:
            return
        self.connect_serial()

    def _requested_sample_rate(self) -> int:
        return 0 if self.sample_rate_auto_button.isChecked() else self.sample_rate_spin.value()

    def toggle_connection(self) -> None:
        if self.controller.is_connected():
            self.manual_disconnect = True
            self.reconnect_timer.stop()
            self.controller.disconnect(manual=True)
            self._set_connected_ui(False)
            self._update_status("Disconnected.")
            return
        self.connect_serial()

    def connect_serial(self) -> None:
        port = self.port_combo.currentText().strip()
        if not port:
            self._refresh_ports()
            port = self.port_combo.currentText().strip()
        if not port:
            self._update_status("No serial port selected.")
            return
        self.last_connected_port = port
        self.manual_disconnect = False
        self.controller.connect(port, self.baud_spin.value())
        self._append_note(f"connecting to {port} @ {self.baud_spin.value()} baud")

    def refresh_status(self) -> None:
        if not self.controller.is_connected():
            self._update_status("Not connected.")
            return
        self.controller.submit(CommandRequest("status", "status", STATUS_TIMEOUT_S, label="status"))
        self._update_status("Requested device status.")

    def capture_once(self) -> None:
        if not self.controller.is_connected():
            self._update_status("Not connected.")
            return
        self._queue_dirty_settings()
        count = self.samples_spin.value()
        sample_rate = self._requested_sample_rate()
        command = f"capturepair {count} {sample_rate}"
        request = CommandRequest("capture", command, CAPTURE_TIMEOUT_S, expected_samples=count, capture_mode="both")
        self.controller.submit(request)
        self._update_status(f"Queued {command}")

    def run_measurement(self) -> None:
        if not self.controller.is_connected():
            self._update_status("Not connected.")
            return
        if self.measurement_runner is not None:
            self._update_status("Repeated measurements are already running.")
            return
        self.measurement_panel.clear()
        self._queue_dirty_settings()
        command = f"measure {self.samples_spin.value()} {self._requested_sample_rate()}"
        self.controller.submit(CommandRequest("measure", command, MEASURE_TIMEOUT_S, label="measure"))
        self._update_status(f"Queued {command}")

    def start_measurement_series(self) -> None:
        if not self.controller.is_connected():
            self._update_status("Connect first.")
            return
        if self.measurement_runner is not None:
            self._update_status("Repeated measurements are already running.")
            return
        if self.sweep_runner is not None:
            self._update_status("Wait for sweep to finish before measuring.")
            return
        if not self.controller.is_idle():
            self._update_status("Wait for current activity to finish before measuring.")
            return

        self.auto_capture_checkbox.setChecked(False)
        self.measurement_panel.clear()
        self.measurement_results = []
        self.measurement_table.setRowCount(0)
        self._set_save_measurements_prompt(False)
        self._queue_dirty_settings()
        self.measurement_runner = MeasurementRunner(
            self.controller,
            self.measurement_event_queue,
            self.measurement_count_spin.value(),
            self.measurement_delay_spin.value(),
            self.samples_spin.value(),
            self._requested_sample_rate(),
        )
        self.measurement_runner.start()
        self.run_measurements_button.setEnabled(False)
        self.stop_measurements_button.setEnabled(True)
        self.tabs.setCurrentWidget(self.live_tab)
        self._update_status("Repeated measurements started.")

    def stop_measurement_series(self) -> None:
        if self.measurement_runner is not None:
            self.measurement_runner.stop()
            self._update_status("Stopping repeated measurements...")

    def save_measurements_csv(self) -> None:
        if not self.measurement_results:
            self._update_status("No repeated measurements to save.")
            return
        default_name = f"lcr_measurements_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path_text = self._choose_csv_save_path(default_name)
        if not path_text:
            return
        path = Path(path_text)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")

        headers = ["index", "elapsed_s", "model", "series_r", "series_l", "series_c", *MEASUREMENT_KEYS]
        try:
            with path.open("w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=headers)
                writer.writeheader()
                for point in self.measurement_results:
                    writer.writerow(self._measurement_csv_row(point))
        except OSError as exc:
            QMessageBox.critical(self, "CSV Error", f"Could not save measurements:\n{exc}")
            return
        self._set_save_measurements_prompt(False)
        self._update_status(f"Saved {len(self.measurement_results)} measurements to {path.name}.")

    def _measurement_csv_rows(self) -> list[dict[str, str]]:
        return [self._measurement_csv_row(point) for point in self.measurement_results]

    def _measurement_csv_row(self, point: MeasurementPoint) -> dict[str, str]:
        values = point.measurement.values
        model, _resistance, _inductance, _capacitance = derived_rlc_fields(values)
        row: dict[str, str] = {
            "index": str(point.index),
            "elapsed_s": f"{point.elapsed_s:.6f}",
            "model": model,
            "series_r": values.get("impedance_real_ohm", ""),
            "series_l": self._derived_inductance_h(values),
            "series_c": self._derived_capacitance_f(values),
        }
        row.update({key: values.get(key, "") for key in MEASUREMENT_KEYS})
        return row

    def _set_save_measurements_prompt(self, prompted: bool) -> None:
        has_measurements = bool(self.measurement_results)
        self.save_measurements_button.setEnabled(has_measurements)
        self.save_measurements_button.setProperty("savePrompt", prompted and has_measurements)
        self.save_measurements_button.style().unpolish(self.save_measurements_button)
        self.save_measurements_button.style().polish(self.save_measurements_button)

    def _choose_csv_save_path(self, default_name: str) -> str:
        default_path = str(Path.home() / default_name)
        if shutil.which("kdialog") is not None:
            result = subprocess.run(
                [
                    "kdialog",
                    "--getsavefilename",
                    default_path,
                    "CSV files (*.csv)",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""

        if shutil.which("zenity") is not None:
            result = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--save",
                    "--confirm-overwrite",
                    f"--filename={default_path}",
                    "--file-filter=CSV files | *.csv",
                    "--file-filter=All files | *",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""

        path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Measurements CSV",
            default_name,
            "CSV files (*.csv);;All files (*)",
        )
        return path_text

    @staticmethod
    def _derived_inductance_h(values: dict[str, str]) -> str:
        reactance = safe_float(values, "impedance_imag_ohm")
        frequency_hz = safe_float(values, "dds_frequency_hz")
        if reactance is None or reactance <= 0.0 or frequency_hz is None or frequency_hz <= 0.0:
            return ""
        return f"{reactance / (2.0 * math.pi * frequency_hz):.12g}"

    @staticmethod
    def _derived_capacitance_f(values: dict[str, str]) -> str:
        reactance = safe_float(values, "impedance_imag_ohm")
        frequency_hz = safe_float(values, "dds_frequency_hz")
        if reactance is None or reactance >= 0.0 or frequency_hz is None or frequency_hz <= 0.0:
            return ""
        return f"{-1.0 / (2.0 * math.pi * frequency_hz * reactance):.12g}"

    def send_terminal_command(self) -> None:
        command = self.terminal_input.text().strip()
        if not command:
            return
        if not self.controller.is_connected():
            self._update_status("Not connected.")
            return
        request = self._request_from_terminal(command)
        self.controller.submit(request)
        self.terminal_input.clear()

    def _request_from_terminal(self, command: str) -> CommandRequest:
        normalized = command.strip()
        lower = normalized.lower()
        capture_match = CAPTURE_COMMAND_RE.match(normalized)
        capturepair_match = CAPTUREPAIR_COMMAND_RE.match(normalized)
        if lower == "status":
            return CommandRequest("status", normalized, STATUS_TIMEOUT_S, label="status")
        if capturepair_match:
            count = int(capturepair_match.group(1))
            return CommandRequest("capture", normalized, CAPTURE_TIMEOUT_S, expected_samples=count, capture_mode="both")
        if capture_match:
            count = int(capture_match.group(1))
            channel = normalize_channel(capture_match.group(3))
            return CommandRequest("capture", normalized, CAPTURE_TIMEOUT_S, expected_samples=count, capture_mode=channel)
        if MEASURE_COMMAND_RE.match(normalized):
            return CommandRequest("measure", normalized, MEASURE_TIMEOUT_S, label="measure")
        if any(
            pattern.match(normalized)
            for pattern in (
                DDS_COMMAND_RE,
                AMP_COMMAND_RE,
                OFFSET_COMMAND_RE,
                RANGE_COMMAND_RE,
                VPGA_COMMAND_RE,
                IPGA_COMMAND_RE,
            )
        ):
            return CommandRequest("ack", normalized, ACK_TIMEOUT_S, label=normalized.split()[0])
        return CommandRequest("raw", normalized, 0.0)

    def _dds_spin_changed(self, value: int) -> None:
        snapped_frequency = dds_frequency_from_step(dds_step_from_frequency(value))
        if snapped_frequency != value:
            with QSignalBlocker(self.dds_spin):
                self.dds_spin.setValue(snapped_frequency)
        with QSignalBlocker(self.dds_slider):
            self.dds_slider.setValue(dds_step_from_frequency(snapped_frequency))
        self._source_control_changed()

    def _dds_slider_changed(self, value: int) -> None:
        frequency_hz = dds_frequency_from_step(value)
        with QSignalBlocker(self.dds_spin):
            self.dds_spin.setValue(frequency_hz)
        self._mark_source_dirty()

    def _mark_source_dirty(self) -> None:
        self.source_dirty = True

    def _source_control_changed(self, _value: object = None) -> None:
        self._mark_source_dirty()
        if self._suspend_source_autoupdate:
            return
        if self.controller.is_connected():
            self._apply_source_if_needed()

    def _apply_source_if_needed(self) -> None:
        if self._suspend_source_autoupdate:
            return
        if not self.controller.is_connected():
            return
        if not self.source_dirty:
            return
        self._queue_dirty_settings(force_source=True, force_frontend=False)
        self._update_status("Queued source settings.")

    def _mark_frontend_dirty(self) -> None:
        self.frontend_dirty = True

    def _frontend_selection_changed(self) -> None:
        self._mark_frontend_dirty()
        if self._suspend_frontend_autoupdate:
            return
        if self.controller.is_connected():
            self.apply_frontend_settings()

    def apply_frontend_settings(self) -> None:
        if not self.controller.is_connected():
            self._update_status("Connect first.")
            return
        self._queue_dirty_settings(force_source=False, force_frontend=True)
        self.controller.submit(CommandRequest("status", "status", STATUS_TIMEOUT_S, label="status"))
        self._update_status("Queued frontend settings.")

    def _queue_dirty_settings(self, *, force_source: bool = False, force_frontend: bool = False) -> None:
        if force_source or self.source_dirty:
            commands = [
                CommandRequest(
                    "ack",
                    f"dds {self.dds_spin.value()} {1 if self.dds_enabled_checkbox.isChecked() else 0}",
                    ACK_TIMEOUT_S,
                    label="dds",
                ),
                CommandRequest("ack", f"amp {self.amp_spin.value()}", ACK_TIMEOUT_S, label="amp"),
                CommandRequest("ack", f"offset {self.offset_spin.value()}", ACK_TIMEOUT_S, label="offset"),
            ]
            for request in commands:
                self.controller.submit(request)
            self.source_dirty = False

        if force_frontend or self.frontend_dirty:
            commands = [
                CommandRequest("ack", f"range {self._button_group_value(self.shunt_group)}", ACK_TIMEOUT_S, label="range"),
                CommandRequest("ack", f"vpga {self._button_group_value(self.vpga_group)}", ACK_TIMEOUT_S, label="vpga"),
                CommandRequest("ack", f"ipga {self._button_group_value(self.ipga_group)}", ACK_TIMEOUT_S, label="ipga"),
            ]
            for request in commands:
                self.controller.submit(request)
            self.frontend_dirty = False

    def _sync_auto_capture_timer(self) -> None:
        if self.auto_capture_checkbox.isChecked() and self.controller.is_connected():
            self.auto_capture_timer.start(self.request_interval_spin.value())
        else:
            self.auto_capture_timer.stop()

    def _auto_capture_tick(self) -> None:
        if not self.controller.is_connected() or self.sweep_runner is not None or self.measurement_runner is not None:
            return
        self.auto_capture_timer.setInterval(self.request_interval_spin.value())
        if not self.controller.is_idle():
            return
        self.capture_once()

    def _set_freeze(self, checked: bool) -> None:
        self.freeze_view = checked
        if not checked and self.latest_frame is not None:
            self._refresh_live_plot(self.latest_frame)

    def _poll_events(self) -> None:
        for event in self.controller.drain_events():
            kind = event["kind"]
            if kind == "line":
                self._append_terminal_line(event["direction"], event["text"])
            elif kind == "connected":
                self._set_connected_ui(True)
                self._update_status(f"Connected to {event['port']} @ {event['baud']} baud")
                self.connection_label.setText("Connected")
                self.connection_label.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: 700;")
                self.session_summary.setText(f"Connected to {event['port']} @ {event['baud']} baud")
                self.refresh_status()
                self._sync_auto_capture_timer()
            elif kind == "disconnected":
                self._set_connected_ui(False)
                self.connection_label.setText("Disconnected")
                self.connection_label.setStyleSheet(f"color: {WARNING_COLOR}; font-weight: 700;")
                if not event.get("manual") and self.auto_reconnect_checkbox.isChecked() and self.last_connected_port:
                    self.reconnect_timer.start()
                    self._update_status("Connection lost. Auto reconnect is trying...")
                else:
                    self.reconnect_timer.stop()
                    self._update_status(event["message"])
            elif kind == "error":
                self._append_note(event["message"], color=ERROR_COLOR)
                self._update_status(event["message"])
            elif kind == "ack":
                self._update_status(f"{event['label']} applied.")
            elif kind == "status":
                self._apply_status(event["status"])
            elif kind == "measurement":
                self.measurement_panel.update_measurement(event["measurement"])
                self.tabs.setCurrentWidget(self.live_tab)
                self._update_status("Measurement updated.")
            elif kind == "frame":
                self.latest_frame = event["frame"]
                if not self.freeze_view:
                    self._refresh_live_plot(event["frame"])

        while True:
            try:
                event = self.sweep_event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_sweep_event(event)

        while True:
            try:
                event = self.measurement_event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_measurement_series_event(event)

    def _attempt_reconnect(self) -> None:
        if self.controller.is_connected():
            self.reconnect_timer.stop()
            return
        if not self.last_connected_port:
            return
        self._refresh_ports()
        self.port_combo.setCurrentText(self.last_connected_port)
        self.connect_serial()

    def _set_connected_ui(self, connected: bool) -> None:
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setProperty("connectionState", "disconnect" if connected else "connect")
        self.connect_button.style().unpolish(self.connect_button)
        self.connect_button.style().polish(self.connect_button)
        self.port_combo.setEnabled(not connected)
        self.baud_spin.setEnabled(not connected)
        self.refresh_ports_button.setEnabled(not connected)
        if connected:
            self.reconnect_timer.stop()

    def _apply_status(self, status: DeviceStatus) -> None:
        self.latest_status = dict(status.values)
        self._apply_status_values(status.values)
        self._update_status("Device status updated.")

    def _apply_status_values(self, values: dict[str, str]) -> None:
        sample_count = safe_int(values, "sample_count")
        sample_rate = safe_int(values, "sample_rate_hz")
        dds_frequency_hz = safe_int(values, "dds_frequency_hz")
        dds_enabled = safe_int(values, "dds_enabled")
        amp_wiper = safe_int(values, "amp_wiper")
        offset_pwm = safe_int(values, "offset_pwm")
        shunt_range = safe_int(values, "shunt_range")
        voltage_pga = safe_int(values, "voltage_pga")
        current_pga = safe_int(values, "current_pga")

        widgets = (
            (self.samples_spin, sample_count),
            (self.dds_spin, dds_frequency_hz),
            (self.amp_spin, amp_wiper),
            (self.offset_spin, offset_pwm),
        )
        for widget, value in widgets:
            if value is None:
                continue
            with QSignalBlocker(widget):
                widget.setValue(value)

        if dds_frequency_hz is not None:
            with QSignalBlocker(self.dds_slider):
                self.dds_slider.setValue(dds_step_from_frequency(dds_frequency_hz))
        if dds_enabled is not None:
            with QSignalBlocker(self.dds_enabled_checkbox):
                self.dds_enabled_checkbox.setChecked(dds_enabled != 0)
        self._suspend_frontend_autoupdate = True
        if shunt_range is not None:
            self._set_button_group_value(self.shunt_group, max(0, min(shunt_range, 3)))
        if voltage_pga is not None:
            self._set_button_group_value(self.vpga_group, max(0, min(voltage_pga, 3)))
        if current_pga is not None:
            self._set_button_group_value(self.ipga_group, max(0, min(current_pga, 3)))
        self._suspend_frontend_autoupdate = False

        self.source_dirty = False
        self.frontend_dirty = False
        dds_text = (
            f"{format_engineering(float(dds_frequency_hz), 2)}Hz"
            if dds_frequency_hz is not None
            else "?"
        )
        summary = (
            f"DDS {dds_text} | "
            f"actual Fs {format_engineering(float(sample_rate), 2)}Hz | "
            if sample_rate is not None
            else ""
        ) + (
            f"range {shunt_range if shunt_range is not None else '?'} | "
            f"V PGA {voltage_pga if voltage_pga is not None else '?'} | "
            f"I PGA {current_pga if current_pga is not None else '?'}"
        )
        self.session_summary.setText(summary)

    def _refresh_live_plot(self, frame: FrameData) -> None:
        shunt_index = self._button_group_value(self.shunt_group)
        voltage_pga_index = self._button_group_value(self.vpga_group)
        current_pga_index = self._button_group_value(self.ipga_group)
        shunt_resistance = SHUNT_RESISTANCE_TABLE[shunt_index]
        voltage_gain = FRONTEND_GAIN_TABLE[voltage_pga_index]
        current_gain = FRONTEND_GAIN_TABLE[current_pga_index]
        self.live_plot.update_frame(
            frame,
            voltage_gain=voltage_gain,
            current_gain=current_gain,
            shunt_resistance=shunt_resistance,
            vref=self.args.vref,
            adc_max=self.args.adc_max,
        )

    def save_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if not ok or not name.strip():
            return
        self.profiles[name.strip()] = self._snapshot_ui()
        try:
            self.profile_store.save(self.profiles)
        except OSError as exc:
            QMessageBox.critical(self, "Profile Error", f"Could not save profiles:\n{exc}")
            return
        self._update_profile_combo()
        self.profile_combo.setCurrentText(name.strip())
        self._update_status(f"Saved profile '{name.strip()}'.")

    def load_profile(self) -> None:
        name = self.profile_combo.currentText().strip()
        if not name or name not in self.profiles:
            return
        self._apply_profile(self.profiles[name])
        self._update_status(f"Loaded profile '{name}'.")

    def delete_profile(self) -> None:
        name = self.profile_combo.currentText().strip()
        if not name or name not in self.profiles:
            return
        self.profiles.pop(name, None)
        try:
            self.profile_store.save(self.profiles)
        except OSError as exc:
            QMessageBox.critical(self, "Profile Error", f"Could not save profiles:\n{exc}")
            return
        self._update_profile_combo()
        self._update_status(f"Deleted profile '{name}'.")

    def _update_profile_combo(self) -> None:
        selected = self.profile_combo.currentText().strip()
        names = sorted(self.profiles.keys())
        with QSignalBlocker(self.profile_combo):
            self.profile_combo.clear()
            self.profile_combo.addItems(names)
            if selected:
                index = self.profile_combo.findText(selected)
                if index >= 0:
                    self.profile_combo.setCurrentIndex(index)

    def _snapshot_ui(self) -> dict[str, Any]:
        return profile_snapshot_from_dict(
            {
                "port": self.port_combo.currentText(),
                "baud": self.baud_spin.value(),
                "samples": self.samples_spin.value(),
                "sample_rate": self.sample_rate_spin.value(),
                "sample_rate_auto": self.sample_rate_auto_button.isChecked(),
                "request_interval_ms": self.request_interval_spin.value(),
                "auto_capture": self.auto_capture_checkbox.isChecked(),
                "dds_frequency_hz": self.dds_spin.value(),
                "dds_enabled": self.dds_enabled_checkbox.isChecked(),
                "amp_wiper": self.amp_spin.value(),
                "offset_pwm": self.offset_spin.value(),
                "shunt_range": self._button_group_value(self.shunt_group),
                "voltage_pga": self._button_group_value(self.vpga_group),
                "current_pga": self._button_group_value(self.ipga_group),
                "sweep_start_hz": self.sweep_start_spin.value(),
                "sweep_stop_hz": self.sweep_stop_spin.value(),
                "sweep_points_per_decade": self.sweep_ppd_spin.value(),
                "sweep_settle_ms": self.sweep_settle_spin.value(),
                "sweep_samples": self.sweep_samples_spin.value(),
                "sweep_sample_rate": self.sweep_sample_rate_spin.value(),
                "measurement_count": self.measurement_count_spin.value(),
                "measurement_delay_s": self.measurement_delay_spin.value(),
            }
        )

    def _apply_profile(self, profile: dict[str, Any]) -> None:
        self.port_combo.setCurrentText(str(profile["port"]))
        self.baud_spin.setValue(int(profile["baud"]))
        self.samples_spin.setValue(int(profile["samples"]))
        self.sample_rate_spin.setValue(int(profile["sample_rate"]))
        self.sample_rate_auto_button.setChecked(bool(profile["sample_rate_auto"]))
        self.request_interval_spin.setValue(int(profile["request_interval_ms"]))
        self.auto_capture_checkbox.setChecked(bool(profile["auto_capture"]))
        self._suspend_source_autoupdate = True
        self.dds_spin.setValue(int(profile["dds_frequency_hz"]))
        self.dds_enabled_checkbox.setChecked(bool(profile["dds_enabled"]))
        self.amp_spin.setValue(int(profile["amp_wiper"]))
        self.offset_spin.setValue(int(profile["offset_pwm"]))
        self._suspend_source_autoupdate = False
        self._suspend_frontend_autoupdate = True
        self._set_button_group_value(self.shunt_group, int(profile["shunt_range"]))
        self._set_button_group_value(self.vpga_group, int(profile["voltage_pga"]))
        self._set_button_group_value(self.ipga_group, int(profile["current_pga"]))
        self._suspend_frontend_autoupdate = False
        self.sweep_start_spin.setValue(float(profile["sweep_start_hz"]))
        self.sweep_stop_spin.setValue(float(profile["sweep_stop_hz"]))
        self.sweep_ppd_spin.setValue(int(profile["sweep_points_per_decade"]))
        self.sweep_settle_spin.setValue(int(profile["sweep_settle_ms"]))
        self.sweep_samples_spin.setValue(int(profile["sweep_samples"]))
        self.sweep_sample_rate_spin.setValue(int(profile["sweep_sample_rate"]))
        self.measurement_count_spin.setValue(int(profile["measurement_count"]))
        self.measurement_delay_spin.setValue(float(profile["measurement_delay_s"]))
        self.source_dirty = True
        self.frontend_dirty = True
        self._sync_auto_capture_timer()

    def start_sweep(self) -> None:
        if not self.controller.is_connected():
            self._update_status("Connect first.")
            return
        if self.sweep_runner is not None:
            self._update_status("Sweep already running.")
            return
        if not self.controller.is_idle():
            self._update_status("Wait for current activity to finish before sweeping.")
            return
        start_hz = self.sweep_start_spin.value()
        stop_hz = self.sweep_stop_spin.value()
        if stop_hz < start_hz:
            self._update_status("Sweep stop frequency must be >= start frequency.")
            return

        self.auto_capture_checkbox.setChecked(False)
        self.sweep_results = []
        self.sweep_table.setRowCount(0)
        self.sweep_mag_curve.setData([], [])
        self.sweep_phase_curve.setData([], [])
        self.measurement_panel.clear()

        settings = self._snapshot_ui()
        self.sweep_runner = SweepRunner(self.controller, self.sweep_event_queue, settings)
        self.sweep_runner.start()
        self.start_sweep_button.setEnabled(False)
        self.stop_sweep_button.setEnabled(True)
        self.tabs.setCurrentWidget(self.sweep_tab)
        self._update_status("Sweep started.")

    def stop_sweep(self) -> None:
        if self.sweep_runner is not None:
            self.sweep_runner.stop()
            self._update_status("Stopping sweep...")

    def _handle_sweep_event(self, event: dict[str, Any]) -> None:
        kind = event["kind"]
        if kind == "sweep_started":
            self.sweep_progress_label.setText("Running sweep...")
        elif kind == "sweep_point":
            point: SweepPoint = event["point"]
            self.sweep_results.append(point)
            self._append_sweep_row(point)
            self._refresh_sweep_plots()
            self.measurement_panel.update_measurement(point.measurement)
            self.sweep_progress_label.setText(
                f"Point {event['index']} / {event['total']} @ {format_engineering(point.frequency_hz, 2)}Hz"
            )
        elif kind == "sweep_complete":
            self.sweep_progress_label.setText(f"Completed {len(self.sweep_results)} points.")
            self.tabs.setCurrentWidget(self.sweep_tab)
            self._finish_sweep()
            self._update_status("Sweep completed.")
        elif kind == "sweep_stopped":
            self.sweep_progress_label.setText("Sweep stopped.")
            self._finish_sweep()
            self._update_status("Sweep stopped.")
        elif kind == "sweep_error":
            self.sweep_progress_label.setText(f"Sweep error: {event['message']}")
            self._append_note(f"sweep error: {event['message']}", color=ERROR_COLOR)
            self._finish_sweep()
            self._update_status(f"Sweep error: {event['message']}")

    def _finish_sweep(self) -> None:
        if self.sweep_runner is not None:
            self.sweep_runner = None
        self.start_sweep_button.setEnabled(True)
        self.stop_sweep_button.setEnabled(False)

    def _handle_measurement_series_event(self, event: dict[str, Any]) -> None:
        kind = event["kind"]
        if kind == "measurement_series_started":
            self.measurement_series_label.setText(f"Running 0 / {event['total']} measurements...")
        elif kind == "measurement_series_point":
            point: MeasurementPoint = event["point"]
            self.measurement_results.append(point)
            self._set_save_measurements_prompt(False)
            self._append_measurement_row(point)
            self.measurement_panel.update_measurement(point.measurement)
            self.measurement_series_label.setText(
                f"Measurement {point.index} / {event['total']} | elapsed {point.elapsed_s:.3f} s"
            )
        elif kind == "measurement_series_complete":
            self.measurement_series_label.setText(f"Completed {len(self.measurement_results)} measurements.")
            self._finish_measurement_series()
            self.data_viewer_tab.load_rows(self._measurement_csv_rows(), label="Unsaved measurement series")
            self.tabs.setCurrentWidget(self.data_viewer_tab)
            self._set_save_measurements_prompt(True)
            self._update_status("Repeated measurements completed.")
        elif kind == "measurement_series_stopped":
            self.measurement_series_label.setText(f"Stopped after {len(self.measurement_results)} measurements.")
            self._finish_measurement_series()
            self._update_status("Repeated measurements stopped.")
        elif kind == "measurement_series_error":
            self.measurement_series_label.setText(f"Measurement error: {event['message']}")
            self._append_note(f"measurement error: {event['message']}", color=ERROR_COLOR)
            self._finish_measurement_series()
            self._update_status(f"Measurement error: {event['message']}")

    def _finish_measurement_series(self) -> None:
        if self.measurement_runner is not None:
            self.measurement_runner = None
        self.run_measurements_button.setEnabled(True)
        self.stop_measurements_button.setEnabled(False)

    def _append_measurement_row(self, point: MeasurementPoint) -> None:
        row = self.measurement_table.rowCount()
        self.measurement_table.insertRow(row)
        values = point.measurement.values
        model, resistance, inductance, capacitance = derived_rlc_fields(values)
        cells = [
            str(point.index),
            f"{point.elapsed_s:.3f}",
            values.get("impedance_mag_ohm", ""),
            values.get("phase_diff_deg", ""),
            model,
            resistance,
            inductance,
            capacitance,
        ]
        for column, text in enumerate(cells):
            self.measurement_table.setItem(row, column, QTableWidgetItem(text))
        self.measurement_table.scrollToBottom()

    def _append_sweep_row(self, point: SweepPoint) -> None:
        row = self.sweep_table.rowCount()
        self.sweep_table.insertRow(row)
        values = point.measurement.values
        model, resistance, inductance, capacitance = derived_rlc_fields(values)
        cells = [
            f"{point.frequency_hz:.2f}",
            values.get("impedance_mag_ohm", ""),
            values.get("phase_diff_deg", ""),
            model,
            resistance,
            inductance,
            capacitance,
        ]
        for column, text in enumerate(cells):
            self.sweep_table.setItem(row, column, QTableWidgetItem(text))
        self.sweep_table.scrollToBottom()

    def _refresh_sweep_plots(self) -> None:
        frequencies = [point.frequency_hz for point in self.sweep_results]
        magnitudes = [safe_float(point.measurement.values, "impedance_mag_ohm") or 0.0 for point in self.sweep_results]
        phases = [safe_float(point.measurement.values, "phase_diff_deg") or 0.0 for point in self.sweep_results]
        self.sweep_mag_curve.setData(frequencies, magnitudes)
        self.sweep_phase_curve.setData(frequencies, phases)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        self.auto_capture_timer.stop()
        self.reconnect_timer.stop()
        if self.measurement_runner is not None:
            self.measurement_runner.stop()
            self.measurement_runner.join(timeout=1.0)
            self.measurement_runner = None
        if self.sweep_runner is not None:
            self.sweep_runner.stop()
            self.sweep_runner.join(timeout=1.0)
            self.sweep_runner = None
        self.controller.disconnect(manual=True)
        super().closeEvent(event)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Desktop STM32 LCR meter application.")
    parser.add_argument("--port", default=None, help="Serial port device.")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate.")
    parser.add_argument("--samples", type=int, default=100, help="Default capture sample count.")
    parser.add_argument("--sample-rate", type=int, default=0, help="Default capture sample rate.")
    parser.add_argument(
        "--channel",
        choices=("voltage", "current", "both", "v", "i"),
        default="both",
        help="Default capture channel.",
    )
    parser.add_argument(
        "--request-interval-ms",
        type=int,
        default=100,
        help="Auto capture interval in milliseconds.",
    )
    parser.add_argument("--vref", type=float, default=3.3, help="ADC reference voltage.")
    parser.add_argument("--adc-max", type=int, default=4095, help="ADC maximum code.")
    return parser


def main() -> None:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication([])
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    window = MainWindow(build_arg_parser().parse_args())
    window.showMaximized()
    app.exec()


if __name__ == "__main__":
    main()

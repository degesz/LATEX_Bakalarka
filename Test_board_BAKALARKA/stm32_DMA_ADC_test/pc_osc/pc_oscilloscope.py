#!/usr/bin/env python3
"""Simple serial oscilloscope view with an interactive serial terminal."""

from __future__ import annotations

import argparse
from collections import deque
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
UI_FONT = "Ioskeley Mono"
ADC_Y_MAX = 4095
VOLTAGE_LINE_COLOR = "#ff0000"
CURRENT_LINE_COLOR = "#4ea8de"
AXIS_FONT_SIZE = 20
TICK_FONT_SIZE = 18
TERMINAL_MAX_LINES = 300

VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
PAIR_VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")
SAMPLE_RATE_LINE_RE = re.compile(r"^\s*sample_rate_hz\s*,\s*(\d+)\s*$", re.IGNORECASE)
CAPTURE_COMMAND_RE = re.compile(
    r"^\s*capture\s+(\d+)\s+(\d+)\s+(voltage|current|v|i)\s*$", re.IGNORECASE
)
CAPTUREPAIR_COMMAND_RE = re.compile(r"^\s*capturepair\s+(\d+)\s+(\d+)\s*$", re.IGNORECASE)


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


def serial_worker(
    port: str,
    baud: int,
    frame_queue: "queue.Queue[Frame]",
    line_queue: "queue.Queue[SerialLine]",
    command_queue: "queue.Queue[str]",
    stop_event: threading.Event,
) -> None:
    primary_values_by_index = {}
    secondary_values_by_index = {}
    sample_rate_hz: Optional[int] = None
    collecting = False
    expected_count = 0
    awaiting_frame = False
    deferred_capture_command: Optional[str] = None
    expected_frame_kind = "single"
    active_channel_label = "Voltage"

    def enqueue_line(direction: str, text: str) -> None:
        try:
            line_queue.put_nowait(SerialLine(direction, text))
        except queue.Full:
            pass

    def send_command(command: str) -> bool:
        nonlocal primary_values_by_index, secondary_values_by_index, sample_rate_hz
        nonlocal collecting, expected_count, awaiting_frame, expected_frame_kind
        nonlocal active_channel_label
        payload = f"{command.rstrip()}\n".encode("ascii", errors="replace")
        ser.write(payload)
        ser.flush()
        enqueue_line("tx", command.rstrip())

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
            try:
                while True:
                    command = command_queue.get_nowait()
                    if (
                        CAPTURE_COMMAND_RE.match(command)
                        or CAPTUREPAIR_COMMAND_RE.match(command)
                    ) and awaiting_frame:
                        deferred_capture_command = command
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

            sample_rate_match = SAMPLE_RATE_LINE_RE.match(line)
            if sample_rate_match:
                sample_rate_hz = int(sample_rate_match.group(1))
                continue

            if line.lower().startswith("error,"):
                print(f"[mcu] {line}")
                collecting = False
                primary_values_by_index = {}
                secondary_values_by_index = {}
                awaiting_frame = False
                if deferred_capture_command is not None:
                    try:
                        send_command(deferred_capture_command)
                    except serial.SerialException as exc:
                        print(f"[serial] write error: {exc}")
                        break
                    deferred_capture_command = None
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
                if deferred_capture_command is not None:
                    try:
                        send_command(deferred_capture_command)
                    except serial.SerialException as exc:
                        print(f"[serial] write error: {exc}")
                        break
                    deferred_capture_command = None


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
        default=256,
        help="Samples requested per capture frame (default: 256).",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=200000,
        help="Requested capture sample rate in Hz (default: 200000).",
    )
    parser.add_argument(
        "--channel",
        choices=("voltage", "current", "both", "v", "i"),
        default="voltage",
        help="Capture mode: voltage, current, or both via capturepair (default: voltage).",
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
    def __init__(self, args: argparse.Namespace, port: str) -> None:
        self.args = args
        self.port = port
        self.frame_queue: "queue.Queue[Frame]" = queue.Queue(maxsize=8)
        self.line_queue: "queue.Queue[SerialLine]" = queue.Queue(maxsize=256)
        self.command_queue: "queue.Queue[str]" = queue.Queue(maxsize=64)
        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=serial_worker,
            args=(
                port,
                args.baud,
                self.frame_queue,
                self.line_queue,
                self.command_queue,
                self.stop_event,
            ),
            daemon=True,
        )
        self.worker.start()

        self.root = tk.Tk()
        self.root.title("STM32 Serial Oscilloscope")
        self.root.geometry("1280x820")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.configure(bg=UI_BG)

        self.mpl_font_family = resolve_matplotlib_font_family(UI_FONT)
        self.samples_var = tk.StringVar(value=str(args.samples))
        self.sample_rate_var = tk.StringVar(value=str(args.sample_rate))
        normalized_channel = {"v": "voltage", "i": "current"}.get(args.channel, args.channel)
        self.channel_var = tk.StringVar(value="both" if normalized_channel == "both" else normalized_channel)
        self.command_var = tk.StringVar()
        self.auto_capture_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(
            value=f"Connected to {port} @ {args.baud} baud | waiting for data..."
        )

        self.latest: Optional[Frame] = None
        self.frame_counter = 0
        self.flash_ticks_remaining = 0
        self.flash_duration_ticks = 4
        self.capture_job: Optional[str] = None
        self.terminal_history: deque[str] = deque(maxlen=TERMINAL_MAX_LINES)
        self.terminal_dirty = False
        self.legend: Optional[object] = None
        self.legend_labels: tuple[str, ...] = ()

        self._build_ui()
        self._queue_capture_command(reset_terminal=False)
        self._schedule_capture()
        self._poll_queues()

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

        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(1, weight=1)

        controls = ttk.Frame(self.root, padding=10)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew")
        for column in range(9):
            controls.columnconfigure(column, weight=1 if column in (1, 3, 5, 8) else 0)

        ttk.Label(controls, text=f"Port: {self.port}").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Samples").grid(row=0, column=1, sticky="e", padx=(12, 4))
        samples_entry = ttk.Entry(controls, textvariable=self.samples_var, width=10)
        samples_entry.grid(row=0, column=2, sticky="ew")
        ttk.Label(controls, text="Sample rate (Hz)").grid(
            row=0, column=3, sticky="e", padx=(12, 4)
        )
        sample_rate_entry = ttk.Entry(
            controls, textvariable=self.sample_rate_var, width=12
        )
        sample_rate_entry.grid(row=0, column=4, sticky="ew")
        ttk.Label(controls, text="Channel").grid(row=0, column=5, sticky="e", padx=(12, 4))
        channel_box = ttk.Combobox(
            controls,
            textvariable=self.channel_var,
            values=("voltage", "current", "both"),
            width=10,
            state="readonly",
        )
        channel_box.grid(row=0, column=6, sticky="ew")
        ttk.Checkbutton(
            controls, text="Auto capture", variable=self.auto_capture_var
        ).grid(row=0, column=7, sticky="w", padx=(12, 4))
        ttk.Button(controls, text="Apply / Capture", command=self.apply_capture_settings).grid(
            row=0, column=8, sticky="e"
        )

        samples_entry.bind("<Return>", lambda _event: self.apply_capture_settings())
        sample_rate_entry.bind("<Return>", lambda _event: self.apply_capture_settings())
        channel_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_capture_settings())

        plot_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        plot_frame.grid(row=1, column=0, sticky="nsew")
        plot_frame.rowconfigure(0, weight=1)
        plot_frame.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor=UI_BG)
        self.ax = self.figure.add_subplot(111)
        self.ax2 = self.ax.twinx()
        self.ax.set_facecolor(UI_BG)
        self.ax2.set_facecolor(UI_BG)
        self.primary_line, = self.ax.plot([], [], lw=1.8, label="Voltage", color=VOLTAGE_LINE_COLOR)
        self.secondary_line, = self.ax.plot([], [], lw=1.4, label="Current", color=CURRENT_LINE_COLOR)
        self.ax.set_title("STM32 Serial Oscilloscope")
        self.ax.set_xlabel("Sample Index")
        self.ax.set_ylabel("ADC Code")
        self.ax.grid(True, alpha=0.3, color=UI_MUTED)
        self.ax2.set_ylabel("Voltage (V)")
        self.ax.set_ylim(0, ADC_Y_MAX)
        self.ax.tick_params(axis="both", colors=UI_FG, labelsize=TICK_FONT_SIZE)
        self.ax2.tick_params(axis="y", colors=UI_FG, labelsize=TICK_FONT_SIZE)
        self.ax.xaxis.label.set_color(UI_FG)
        self.ax.yaxis.label.set_color(UI_FG)
        self.ax2.yaxis.label.set_color(UI_FG)
        self.ax.xaxis.label.set_fontsize(AXIS_FONT_SIZE)
        self.ax.yaxis.label.set_fontsize(AXIS_FONT_SIZE)
        self.ax2.yaxis.label.set_fontsize(AXIS_FONT_SIZE)
        self.ax.xaxis.label.set_fontfamily(self.mpl_font_family)
        self.ax.yaxis.label.set_fontfamily(self.mpl_font_family)
        self.ax2.yaxis.label.set_fontfamily(self.mpl_font_family)
        self.ax.title.set_fontfamily(self.mpl_font_family)
        self.ax.title.set_color(UI_FG)
        for spine in self.ax.spines.values():
            spine.set_color(UI_FG)
        for spine in self.ax2.spines.values():
            spine.set_color(UI_FG)
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
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        terminal_frame = ttk.LabelFrame(self.root, text="Serial Terminal", padding=10)
        terminal_frame.grid(row=1, column=1, sticky="nsew", padx=(0, 10), pady=(0, 10))
        terminal_frame.rowconfigure(0, weight=1)
        terminal_frame.columnconfigure(0, weight=1)

        self.terminal_text = scrolledtext.ScrolledText(
            terminal_frame,
            wrap="word",
            height=20,
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
        sample_rate = self._parse_positive_int(self.sample_rate_var.get(), "sample rate")
        channel = self.channel_var.get().strip().lower() or "voltage"
        if channel == "both":
            return f"capturepair {samples} {sample_rate}"
        return f"capture {samples} {sample_rate} {channel}"

    def _parse_positive_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {field_name}: {value!r}") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name.capitalize()} must be positive.")
        return parsed

    def _queue_command(self, command: str) -> None:
        try:
            self.command_queue.put_nowait(command)
        except queue.Full:
            self.status_var.set("Command queue full; try again.")

    def _queue_capture_command(self, reset_terminal: bool = True) -> bool:
        try:
            command = self._capture_command()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return False

        if reset_terminal:
            self._append_terminal_line("# ", f"auto capture -> {command}")
        self._queue_command(command)
        return True

    def apply_capture_settings(self) -> None:
        if self._queue_capture_command():
            self.status_var.set(
                f"Capture request updated: {self._capture_command()} | auto={'on' if self.auto_capture_var.get() else 'off'}"
            )
            self._schedule_capture()

    def send_terminal_command(self, _event: object = None) -> None:
        command = self.command_var.get().strip()
        if not command:
            return
        self._queue_command(command)
        self.command_var.set("")

    def _schedule_capture(self) -> None:
        if self.capture_job is not None:
            self.root.after_cancel(self.capture_job)
            self.capture_job = None

        if not self.auto_capture_var.get():
            return

        delay_ms = max(self.args.request_interval_ms, 0)
        self.capture_job = self.root.after(delay_ms, self._auto_capture_tick)

    def _auto_capture_tick(self) -> None:
        self.capture_job = None
        if self.auto_capture_var.get():
            self._queue_capture_command(reset_terminal=False)
            self._schedule_capture()

    def _poll_queues(self) -> None:
        got_new_frame = False
        redraw_needed = False

        while True:
            try:
                line = self.line_queue.get_nowait()
            except queue.Empty:
                break
            prefix = "TX> " if line.direction == "tx" else "RX> "
            self._append_terminal_line(prefix, line.text)

        self._flush_terminal()

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

        handles = [self.primary_line]
        if len(labels) > 1:
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

        self.primary_line.set_data(x_data, frame.primary_values)
        self.primary_line.set_label(frame.primary_label)
        if frame.secondary_values is not None and frame.secondary_label is not None:
            self.secondary_line.set_data(x_data, frame.secondary_values)
            self.secondary_line.set_label(frame.secondary_label)
            self.secondary_line.set_visible(True)
            self._sync_legend([frame.primary_label, frame.secondary_label])
        else:
            self.secondary_line.set_data([], [])
            self.secondary_line.set_visible(False)
            self._sync_legend([])
        x_min = min(x_data)
        x_max = max(x_data)
        if x_min == x_max:
            x_max += 1
        self.ax.set_xlim(x_min, x_max)

        self.ax.set_ylim(0, ADC_Y_MAX)

        left_ymin, left_ymax = self.ax.get_ylim()
        self.ax2.set_ylim(
            left_ymin * self.args.vref / ADC_Y_MAX,
            left_ymax * self.args.vref / ADC_Y_MAX,
        )

        status = f"Frames: {self.frame_counter} | Samples: {len(frame.primary_values)} | Mode: {self.channel_var.get()}"
        if frame.sample_rate_hz and frame.sample_rate_hz > 0:
            status = f"{status} | Fs: {frame.sample_rate_hz} Hz"
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
        self.stop_event.set()
        if self.capture_job is not None:
            self.root.after_cancel(self.capture_job)
            self.capture_job = None
        self.worker.join(timeout=1.0)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    args = build_arg_parser().parse_args()
    port = args.port or autodetect_port()
    if not port:
        raise SystemExit("No serial device found. Pass --port explicitly.")

    try:
        with serial.Serial(port, baudrate=args.baud, timeout=0.2):
            pass
    except serial.SerialException as exc:
        raise SystemExit(
            f"Could not open serial port {port}: {exc}\n"
            "Use the correct device path or omit --port to auto-detect."
        ) from exc

    app = OscilloscopeApp(args, port)
    app.run()


if __name__ == "__main__":
    main()

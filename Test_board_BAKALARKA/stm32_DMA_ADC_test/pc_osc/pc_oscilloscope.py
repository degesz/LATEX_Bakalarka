#!/usr/bin/env python3
"""
Simple serial oscilloscope view for STM32 CSV captures.

Expected frame format from MCU:
    sample_rate_hz,1000000
    index,value
    0,1234
    1,1228
    ...
    99,1190

Any non-matching lines are ignored, so status text can coexist.
"""

from __future__ import annotations

import argparse
import queue
import re
import threading
from dataclasses import dataclass
from typing import Optional

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import serial
import serial.tools.list_ports


VALUE_LINE_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
SAMPLE_RATE_LINE_RE = re.compile(r"^\s*sample_rate_hz\s*,\s*(\d+)\s*$", re.IGNORECASE)


@dataclass
class Frame:
    sample_indices: list[int]
    sample_values: list[int]
    sample_rate_hz: Optional[int] = None


def time_axis_unit(frame_duration_s: float) -> tuple[str, float]:
    """Return ('ms' or 'us', scale factor from seconds to that unit)."""
    if frame_duration_s >= 0.01:
        return ("ms", 1e3)
    return ("us", 1e6)


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


def serial_reader_worker(
    port: str,
    baud: int,
    frame_queue: "queue.Queue[Frame]",
    stop_event: threading.Event,
    expected_count: int,
) -> None:
    current = {}
    sample_rate_hz: Optional[int] = None
    collecting = False

    with serial.Serial(port, baudrate=baud, timeout=0.2) as ser:
        while not stop_event.is_set():
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

            if line.lower().startswith("index,value"):
                collecting = True
                current = {}
                continue

            sample_rate_match = SAMPLE_RATE_LINE_RE.match(line)
            if sample_rate_match:
                sample_rate_hz = int(sample_rate_match.group(1))
                continue

            match = VALUE_LINE_RE.match(line)
            if not match:
                continue

            if not collecting:
                # Also allow receiving raw "i,v" lines without header.
                collecting = True
                current = {}

            idx = int(match.group(1))
            val = int(match.group(2))
            current[idx] = val

            # Publish frame when we have enough samples.
            if len(current) >= expected_count:
                indices = sorted(current.keys())[:expected_count]
                values = [current[i] for i in indices]
                frame_queue.put(Frame(indices, values, sample_rate_hz=sample_rate_hz))
                collecting = False
                current = {}


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
        default=1000,
        help="Expected samples per frame (default: 100).",
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


def main() -> None:
    args = build_arg_parser().parse_args()
    port = args.port or autodetect_port()
    if not port:
        raise SystemExit("No serial device found. Pass --port explicitly.")

    print(f"Using serial port: {port} @ {args.baud} baud")
    print("Close the plot window to stop.")

    frame_queue: "queue.Queue[Frame]" = queue.Queue(maxsize=8)
    stop_event = threading.Event()

    worker = threading.Thread(
        target=serial_reader_worker,
        args=(port, args.baud, frame_queue, stop_event, args.samples),
        daemon=True,
    )
    worker.start()

    fig, ax = plt.subplots()
    (line,) = ax.plot([], [], lw=1.8)
    ax.set_title("STM32 Serial Oscilloscope")
    ax.set_xlabel("Sample Index")
    ax.set_ylabel("ADC Code")
    ax.grid(True, alpha=0.3)
    status_text = ax.text(
        0.01,
        0.98,
        "Waiting for data...",
        transform=ax.transAxes,
        va="top",
        fontsize=9,
    )
    rx_flash = ax.text(
        0.5,
        -0.12,
        "RX",
        transform=ax.transAxes,
        va="top",
        ha="center",
        fontsize=16,
        fontweight="bold",
        color="black",
        alpha=0.25,
        bbox={
            "boxstyle": "round,pad=0.2",
            "facecolor": "#9cff9c",
            "edgecolor": "tab:green",
            "linewidth": 1.5,
        },
        clip_on=False,
    )

    # Secondary axis for voltage view.
    ax2 = ax.twinx()
    ax2.set_ylabel("Voltage (V)")

    latest: Optional[Frame] = None
    frame_counter = 0
    flash_ticks_remaining = 0
    flash_duration_ticks = 4
    base_facecolor = ax.get_facecolor()
    flash_facecolor = (0.75, 1.0, 0.75, 1.0)

    def animate(_tick: int):
        nonlocal latest, frame_counter, flash_ticks_remaining

        got_new = False
        while True:
            try:
                latest = frame_queue.get_nowait()
                got_new = True
            except queue.Empty:
                break

        if not latest:
            return (line, status_text, rx_flash)

        if got_new:
            frame_counter += 1
            flash_ticks_remaining = flash_duration_ticks
            if latest.sample_rate_hz and latest.sample_rate_hz > 0:
                frame_duration_s = max(1, len(latest.sample_indices)) / latest.sample_rate_hz
                unit_label, unit_scale = time_axis_unit(frame_duration_s)
                x_data = [
                    (idx / latest.sample_rate_hz) * unit_scale for idx in latest.sample_indices
                ]
                ax.set_xlabel(f"Time ({unit_label})")
            else:
                x_data = latest.sample_indices
                ax.set_xlabel("Sample Index")
            line.set_data(x_data, latest.sample_values)

            x_min = min(x_data)
            x_max = max(x_data)
            if x_min == x_max:
                x_max += 1
            ax.set_xlim(x_min, x_max)

            y_min = min(latest.sample_values)
            y_max = max(latest.sample_values)
            if y_min == y_max:
                y_max += 1
            y_margin = max(8, int((y_max - y_min) * 0.1))
            ax.set_ylim(max(0, y_min - y_margin), min(args.adc_max, y_max + y_margin))

            # Keep secondary axis in sync with primary ADC-code axis.
            left_ymin, left_ymax = ax.get_ylim()
            ax2.set_ylim(
                left_ymin * args.vref / args.adc_max,
                left_ymax * args.vref / args.adc_max,
            )

            status_text.set_text(
                f"Frames: {frame_counter} | Samples: {len(latest.sample_values)}"
            )
            if latest.sample_rate_hz and latest.sample_rate_hz > 0:
                status_text.set_text(
                    f"{status_text.get_text()} | Fs: {latest.sample_rate_hz} Hz"
                )

        if flash_ticks_remaining > 0:
            rx_flash.set_alpha(1.0)
            blend = flash_ticks_remaining / flash_duration_ticks
            blended = tuple(
                base + (flash - base) * blend
                for base, flash in zip(base_facecolor, flash_facecolor)
            )
            ax.set_facecolor(blended)
            flash_ticks_remaining -= 1
        else:
            rx_flash.set_alpha(0.25)
            ax.set_facecolor(base_facecolor)

        return (line, status_text, rx_flash)

    ani = FuncAnimation(fig, animate, interval=args.refresh_ms, blit=False)
    # Keep a reference to avoid being garbage-collected.
    _ = ani

    try:
        plt.show()
    finally:
        stop_event.set()
        worker.join(timeout=1.0)


if __name__ == "__main__":
    main()

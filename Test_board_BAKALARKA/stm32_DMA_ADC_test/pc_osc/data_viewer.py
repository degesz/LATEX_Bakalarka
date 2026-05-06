#!/usr/bin/env python3
"""CSV data viewer for STM32 LCR meter measurements."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
from pathlib import Path
from typing import Optional

import pyqtgraph as pg
import importlib
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


APP_TITLE = "LCR Measurement Data Viewer"
APP_FONT_FAMILY = "Ioskeley Mono"
BASE_DPI = 96.0

BG_COLOR = "#000000"
PANEL_COLOR = "#000000"
PANEL_ALT_COLOR = "#050505"
BORDER_COLOR = "#8a4f00"
GRID_COLOR = "#3a2400"
TEXT_COLOR = "#ffd27a"
FIELD_TEXT_COLOR = "#ff9f1c"
MUTED_COLOR = "#b8882d"
ACCENT_COLOR = "#ffb000"
CURRENT_COLOR = "#3390ff"
APP_ORG = "stm32_lcr_meter"
APP_CONFIG_PATH = Path.home() / ".config" / APP_ORG
VIEWER_CONFIG_PATH = APP_CONFIG_PATH / "data_viewer.json"
FRONTEND_GAIN_TABLE = (1.0, 2.0, 5.0, 10.0)
SHUNT_RESISTANCE_TABLE = (100.0, 1000.0, 10000.0, 100000.0)

MEASURED_COLUMNS = (
    "series_r",
    "series_l",
    "series_c",
    "impedance_mag_ohm",
    "impedance_real_ohm",
    "impedance_imag_ohm",
    "phase_diff_deg",
    "voltage_amplitude_v",
    "voltage_phase_deg",
    "current_amplitude_a",
    "current_phase_deg",
)
QUICK_COLUMNS = (
    ("R", "series_r"),
    ("L", "series_l"),
    ("C", "series_c"),
)
OTHER_COLUMNS = tuple(column for column in MEASURED_COLUMNS if column not in {item[1] for item in QUICK_COLUMNS})
BASE_UNITS = {
    "series_r": "Ω",
    "series_l": "H",
    "series_c": "F",
    "impedance_mag_ohm": "Ω",
    "impedance_real_ohm": "Ω",
    "impedance_imag_ohm": "Ω",
    "phase_diff_deg": "deg",
    "voltage_amplitude_v": "V",
    "voltage_phase_deg": "deg",
    "current_amplitude_a": "A",
    "current_phase_deg": "deg",
}
DISPLAY_NAMES = {
    "series_r": "R",
    "series_l": "L",
    "series_c": "C",
    "impedance_mag_ohm": "|Z|",
    "impedance_real_ohm": "Re(Z)",
    "impedance_imag_ohm": "Im(Z)",
    "phase_diff_deg": "Phase diff",
    "voltage_amplitude_v": "Voltage amplitude",
    "voltage_phase_deg": "Voltage phase",
    "current_amplitude_a": "Current amplitude",
    "current_phase_deg": "Current phase",
}
CZECH_DISPLAY_NAMES = {
    "series_r": "Odpor R",
    "series_l": "Indukčnost L",
    "series_c": "Kapacita C",
    "impedance_mag_ohm": "|Z|",
    "impedance_real_ohm": "Re(Z)",
    "impedance_imag_ohm": "Im(Z)",
    "phase_diff_deg": "Fázový rozdíl",
    "voltage_amplitude_v": "Amplituda napětí",
    "voltage_phase_deg": "Fáze napětí",
    "current_amplitude_a": "Amplituda proudu",
    "current_phase_deg": "Fáze proudu",
}
STATIC_COLUMNS = (
    "model",
    "sample_rate_hz",
    "dds_frequency_hz",
    "shunt_range",
    "voltage_pga",
    "current_pga",
    "shunt_resistance_ohm",
    "samples_per_period",
    "captured_cycles",
)


def _persist_last_export_directory(directory: Path) -> None:
    try:
        data: dict[str, str] = {}
        if VIEWER_CONFIG_PATH.exists():
            data = json.loads(VIEWER_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        data["last_export_directory"] = str(directory)
        VIEWER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        VIEWER_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


def _patch_pyqtgraph_exporter_directory_memory() -> None:
    exporter_module = importlib.import_module("pyqtgraph.exporters.Exporter")
    if getattr(exporter_module, "_lcr_export_dir_patch_installed", False):
        return
    original_file_save_finished = exporter_module.Exporter.fileSaveFinished

    def wrapped_file_save_finished(self, fileName):
        original_file_save_finished(self, fileName)
        _persist_last_export_directory(Path(fileName).parent)

    exporter_module.Exporter.fileSaveFinished = wrapped_file_save_finished
    exporter_module._lcr_export_dir_patch_installed = True


def _patch_pyqtgraph_svg_exporter() -> None:
    svg_exporter_module = importlib.import_module("pyqtgraph.exporters.SVGExporter")
    if getattr(svg_exporter_module, "_lcr_svg_patch_installed", False):
        return
    original_correct_coordinates = getattr(svg_exporter_module, "correctCoordinates", None)
    if original_correct_coordinates is None:
        return

    def safe_correct_coordinates(node, defs, item, options):
        try:
            return original_correct_coordinates(node, defs, item, options)
        except ValueError as exc:
            # pyqtgraph 0.13.x can fail on some path tokens during SVG export
            # (expects "x,y" for every token). Keep export working by skipping
            # the coordinate-normalization pass for this problematic element.
            if "not enough values to unpack" in str(exc):
                return
            raise

    svg_exporter_module.correctCoordinates = safe_correct_coordinates
    svg_exporter_module._lcr_svg_patch_installed = True


_patch_pyqtgraph_svg_exporter()
_patch_pyqtgraph_exporter_directory_memory()


class ExportStyleDialog(QDialog):
    def __init__(self, current: dict[str, str | bool], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export style")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.use_czech_labels = QCheckBox("Use Czech axis labels")
        self.use_czech_labels.setChecked(bool(current["use_czech_labels"]))
        self.transparent_background = QCheckBox("Transparent background")
        self.transparent_background.setChecked(bool(current["transparent_background"]))
        self.show_horizontal_y_grid = QCheckBox("Horizontal Y grid")
        self.show_horizontal_y_grid.setChecked(bool(current["show_horizontal_y_grid"]))
        self.text_color_edit = QLineEdit(str(current["text_color"]))
        self.axis_grid_color_edit = QLineEdit(str(current["axis_grid_color"]))
        self.primary_color_edit = QLineEdit(str(current["primary_color"]))
        self.secondary_color_edit = QLineEdit(str(current["secondary_color"]))
        preset_row = QHBoxLayout()
        self.display_preset_button = QPushButton("Preset: Display")
        self.export_preset_button = QPushButton("Preset: Export")
        self.display_preset_button.clicked.connect(self._apply_display_preset)
        self.export_preset_button.clicked.connect(self._apply_export_preset)
        preset_row.addWidget(self.display_preset_button)
        preset_row.addWidget(self.export_preset_button)
        form.addRow("Presets", preset_row)
        form.addRow("", self.use_czech_labels)
        form.addRow("", self.transparent_background)
        form.addRow("", self.show_horizontal_y_grid)
        form.addRow("Text color", self._build_color_row(self.text_color_edit))
        form.addRow("Axis + grid color", self._build_color_row(self.axis_grid_color_edit))
        form.addRow("Primary color", self._build_color_row(self.primary_color_edit))
        form.addRow("Secondary color", self._build_color_row(self.secondary_color_edit))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def settings(self) -> dict[str, str | bool]:
        return {
            "use_czech_labels": self.use_czech_labels.isChecked(),
            "transparent_background": self.transparent_background.isChecked(),
            "show_horizontal_y_grid": self.show_horizontal_y_grid.isChecked(),
            "text_color": self._valid_color(self.text_color_edit.text(), "#000000"),
            "axis_grid_color": self._valid_color(self.axis_grid_color_edit.text(), "#000000"),
            "primary_color": self._valid_color(self.primary_color_edit.text(), ACCENT_COLOR),
            "secondary_color": self._valid_color(self.secondary_color_edit.text(), CURRENT_COLOR),
        }

    @staticmethod
    def _valid_color(value: str, fallback: str) -> str:
        color = QColor(value.strip())
        return color.name() if color.isValid() else fallback

    def _build_color_row(self, line_edit: QLineEdit) -> QWidget:
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        choose_button = QPushButton("Choose...")
        preview = QLabel()
        preview.setFixedSize(28, 18)
        preview.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Plain)
        self._update_preview(line_edit, preview)
        line_edit.textChanged.connect(lambda _text, edit=line_edit, swatch=preview: self._update_preview(edit, swatch))
        choose_button.clicked.connect(lambda _checked=False, edit=line_edit: self._choose_color(edit))
        row_layout.addWidget(line_edit)
        row_layout.addWidget(choose_button)
        row_layout.addWidget(preview)
        return row

    def _choose_color(self, target_edit: QLineEdit) -> None:
        current = QColor(target_edit.text().strip())
        picked = QColorDialog.getColor(current if current.isValid() else QColor("#000000"), self, "Select color")
        if picked.isValid():
            target_edit.setText(picked.name())

    def _update_preview(self, target_edit: QLineEdit, preview: QLabel) -> None:
        preview.setStyleSheet(
            f"background: {self._valid_color(target_edit.text(), '#000000')}; border: 1px solid #777;"
        )

    def _apply_display_preset(self) -> None:
        self.use_czech_labels.setChecked(False)
        self.transparent_background.setChecked(False)
        self.show_horizontal_y_grid.setChecked(True)
        self.text_color_edit.setText(FIELD_TEXT_COLOR)
        self.axis_grid_color_edit.setText(BORDER_COLOR)
        self.primary_color_edit.setText(ACCENT_COLOR)
        self.secondary_color_edit.setText(CURRENT_COLOR)

    def _apply_export_preset(self) -> None:
        self.use_czech_labels.setChecked(True)
        self.transparent_background.setChecked(True)
        self.show_horizontal_y_grid.setChecked(True)
        self.text_color_edit.setText("#000000")
        self.axis_grid_color_edit.setText("#000000")
        self.primary_color_edit.setText(ACCENT_COLOR)
        self.secondary_color_edit.setText(CURRENT_COLOR)


def compute_ui_scale() -> float:
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    dpi = max(screen.logicalDotsPerInch(), BASE_DPI)
    return max(1.0, min(dpi / BASE_DPI, 2.0))


def parse_float(text: str) -> Optional[float]:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def format_number(value: float) -> str:
    return f"{value:.12g}"


def format_clean_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}"


def format_rounded(value: float, unit: str = "") -> str:
    suffix = f" {unit}" if unit else ""
    return f"{format_clean_number(value)}{suffix}"


def format_frequency_hz(value: float) -> str:
    if abs(value) >= 1000.0:
        return format_rounded(value / 1000.0, "kHz")
    return format_rounded(value, "Hz")


def format_resistance_ohm(value: float) -> str:
    if abs(value) >= 1000.0:
        return format_rounded(value / 1000.0, "kΩ")
    return format_rounded(value, "Ω")


def format_static_value(column: str, value: str) -> str:
    numeric = parse_float(value)
    if numeric is None:
        return value
    if column == "sample_rate_hz":
        return format_frequency_hz(numeric)
    if column == "dds_frequency_hz":
        return format_frequency_hz(numeric)
    if column == "shunt_resistance_ohm":
        return format_resistance_ohm(numeric)
    if column == "shunt_range":
        index = int(numeric)
        if 0 <= index < len(SHUNT_RESISTANCE_TABLE):
            return format_resistance_ohm(SHUNT_RESISTANCE_TABLE[index])
    if column in {"voltage_pga", "current_pga"}:
        index = int(numeric)
        if 0 <= index < len(FRONTEND_GAIN_TABLE):
            return f"{format_clean_number(FRONTEND_GAIN_TABLE[index])}x"
    return format_rounded(numeric)


def si_scale(values: list[float]) -> tuple[float, str]:
    max_abs = max((abs(value) for value in values), default=0.0)
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
    for factor, prefix in prefixes:
        if max_abs >= factor:
            return (factor, prefix)
    return (1e-12, "p")


class DataViewerWidget(QWidget):
    def __init__(
        self,
        csv_path: Optional[Path] = None,
        *,
        load_last: bool = True,
        configure_palette: bool = False,
    ) -> None:
        super().__init__()
        self.ui_scale = compute_ui_scale()
        self.rows: list[dict[str, str]] = []
        self.numeric_columns: list[str] = []
        self.selected_column = "series_r"
        self.current_path: Optional[Path] = None
        self.last_directory = Path.home()
        self.export_use_czech_labels = False
        self.export_transparent_background = False
        self.export_show_horizontal_y_grid = True
        self.export_text_color = FIELD_TEXT_COLOR
        self.export_axis_grid_color = BORDER_COLOR
        self.export_primary_color = ACCENT_COLOR
        self.export_secondary_color = CURRENT_COLOR
        self.histogram_item: Optional[pg.BarGraphItem] = None
        self.gaussian_fill_item: Optional[pg.FillBetweenItem] = None
        self.gaussian_curve_item: Optional[pg.PlotCurveItem] = None
        self.gaussian_curve_pen = None
        self.sigma_ticks: list[tuple[float, str]] = []

        self._load_viewer_config()
        if configure_palette:
            self._configure_palette()
        self._build_ui()

        if csv_path is not None:
            self.load_csv(csv_path)
        elif load_last and self.current_path is not None:
            self.load_csv(self.current_path)

    def _configure_palette(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        font_size = max(11, int(round(11 * self.ui_scale)))
        padding_y = max(7, int(round(7 * self.ui_scale)))
        padding_x = max(12, int(round(12 * self.ui_scale)))
        field_padding = max(4, int(round(4 * self.ui_scale)))
        title_margin = max(10, int(round(10 * self.ui_scale)))
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
            QGroupBox {{
                border: 1px solid {BORDER_COLOR};
                margin-top: {title_margin}px;
                padding-top: {title_margin}px;
                background: {PANEL_COLOR};
                font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }}
            QPushButton {{
                background: {PANEL_ALT_COLOR};
                border: 1px solid {BORDER_COLOR};
                padding: {padding_y}px {padding_x}px;
            }}
            QPushButton:hover {{
                background: #2d1800;
                color: #fff1c7;
            }}
            QPushButton:disabled {{
                color: #6f5522;
                border-color: #4f3510;
                background: #050505;
            }}
            QPushButton:checked {{
                background: #3a1f00;
                color: #fff1c7;
                border: 1px solid {FIELD_TEXT_COLOR};
            }}
            QComboBox, QSpinBox {{
                background: #000000;
                color: {FIELD_TEXT_COLOR};
                border: 1px solid {BORDER_COLOR};
                padding: {field_padding}px;
            }}
            QLabel#summaryLabel {{
                color: {MUTED_COLOR};
            }}
            QFrame#valueTile {{
                background: #000000;
                border: 1px solid {BORDER_COLOR};
                padding: {field_padding}px;
            }}
            QLabel#tileLabel {{
                color: {MUTED_COLOR};
                font-size: {max(11, int(round(11 * self.ui_scale)))}px;
            }}
            QLabel#tileValue {{
                color: {FIELD_TEXT_COLOR};
                font-weight: 700;
            }}
            QLabel#tableKey {{
                color: {MUTED_COLOR};
                font-size: {max(10, int(round(10 * self.ui_scale)))}px;
            }}
            QLabel#tableValue {{
                color: {FIELD_TEXT_COLOR};
                font-size: {max(10, int(round(10 * self.ui_scale)))}px;
                font-weight: 500;
            }}
            QLabel#statsTableKey {{
                color: {MUTED_COLOR};
                font-size: {max(12, int(round(12 * self.ui_scale)))}px;
                border-bottom: 1px solid {GRID_COLOR};
                padding: {max(3, int(round(3 * self.ui_scale)))}px;
            }}
            QLabel#statsTableValue {{
                color: {FIELD_TEXT_COLOR};
                font-size: {max(12, int(round(12 * self.ui_scale)))}px;
                font-weight: 600;
                border-bottom: 1px solid {GRID_COLOR};
                padding: {max(3, int(round(3 * self.ui_scale)))}px;
            }}
            """
        )
        pg.setConfigOptions(antialias=True, foreground=TEXT_COLOR)

    def _load_viewer_config(self) -> None:
        if not VIEWER_CONFIG_PATH.exists():
            return
        try:
            data = json.loads(VIEWER_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        last_file = data.get("last_file")
        if isinstance(last_file, str) and last_file:
            path = Path(last_file)
            self.last_directory = path.parent if path.parent.exists() else Path.home()
            if path.exists():
                self.current_path = path
        last_export_directory = data.get("last_export_directory")
        if isinstance(last_export_directory, str) and last_export_directory:
            exporter_module = importlib.import_module("pyqtgraph.exporters.Exporter")
            export_path = Path(last_export_directory)
            if export_path.exists():
                exporter_module.LastExportDirectory = str(export_path)
        if isinstance(data.get("export_use_czech_labels"), bool):
            self.export_use_czech_labels = bool(data["export_use_czech_labels"])
        if isinstance(data.get("export_transparent_background"), bool):
            self.export_transparent_background = bool(data["export_transparent_background"])
        if isinstance(data.get("export_show_horizontal_y_grid"), bool):
            self.export_show_horizontal_y_grid = bool(data["export_show_horizontal_y_grid"])
        if isinstance(data.get("export_text_color"), str):
            self.export_text_color = data["export_text_color"]
        if isinstance(data.get("export_axis_grid_color"), str):
            self.export_axis_grid_color = data["export_axis_grid_color"]
        if isinstance(data.get("export_primary_color"), str):
            self.export_primary_color = data["export_primary_color"]
        if isinstance(data.get("export_secondary_color"), str):
            self.export_secondary_color = data["export_secondary_color"]

    def _save_viewer_config(self) -> None:
        data: dict[str, str] = {}
        if self.current_path is not None:
            data["last_file"] = str(self.current_path)
        data["export_use_czech_labels"] = self.export_use_czech_labels
        data["export_transparent_background"] = self.export_transparent_background
        data["export_show_horizontal_y_grid"] = self.export_show_horizontal_y_grid
        data["export_text_color"] = self.export_text_color
        data["export_axis_grid_color"] = self.export_axis_grid_color
        data["export_primary_color"] = self.export_primary_color
        data["export_secondary_color"] = self.export_secondary_color
        exporter_module = importlib.import_module("pyqtgraph.exporters.Exporter")
        export_dir = getattr(exporter_module, "LastExportDirectory", None)
        if isinstance(export_dir, str) and export_dir:
            data["last_export_directory"] = export_dir
        try:
            VIEWER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            VIEWER_CONFIG_PATH.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        spacing = max(12, int(round(12 * self.ui_scale)))
        layout.setContentsMargins(spacing, spacing, spacing, spacing)
        layout.setSpacing(spacing)

        controls = QGroupBox("Data")
        controls_layout = QVBoxLayout(controls)
        top_controls = QHBoxLayout()
        top_controls.setSpacing(max(8, int(round(8 * self.ui_scale))))
        self.open_button = QPushButton("Open CSV")
        self.open_button.clicked.connect(self.open_csv)
        self.export_style_button = QPushButton("Chart style")
        self.export_style_button.clicked.connect(self.open_export_style_dialog)
        self.quick_group = QButtonGroup(self)
        self.quick_group.setExclusive(False)
        self.quick_buttons: dict[str, QPushButton] = {}
        quick_row = QHBoxLayout()
        for index, (label, column) in enumerate(QUICK_COLUMNS):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, col=column: self.select_column(col))
            self.quick_group.addButton(button, index)
            self.quick_buttons[column] = button
            quick_row.addWidget(button)
        self.other_combo = QComboBox()
        self.other_combo.currentTextChanged.connect(self._other_column_changed)
        self.other_combo.setMinimumWidth(int(round(260 * self.ui_scale)))
        self.bin_spin = QSpinBox()
        self.bin_spin.setRange(3, 200)
        self.bin_spin.setValue(20)
        self.bin_spin.valueChanged.connect(self.refresh_analysis)
        self.bin_spin.setMaximumWidth(int(round(110 * self.ui_scale)))
        self.file_label = QLabel("No CSV loaded.")
        self.file_label.setObjectName("summaryLabel")

        top_controls.addWidget(self.open_button)
        top_controls.addWidget(self.export_style_button)
        top_controls.addLayout(quick_row)
        top_controls.addSpacing(max(12, int(round(12 * self.ui_scale))))
        top_controls.addWidget(QLabel("Other"))
        top_controls.addWidget(self.other_combo)
        top_controls.addSpacing(max(12, int(round(12 * self.ui_scale))))
        top_controls.addWidget(QLabel("Bins"))
        top_controls.addWidget(self.bin_spin)
        top_controls.addStretch(1)
        controls_layout.addLayout(top_controls)
        controls_layout.addWidget(self.file_label)

        self.plot = pg.PlotWidget()
        self.plot.setBackground(PANEL_COLOR)
        self.plot.showGrid(x=False, y=False, alpha=0.0)
        self.plot.getPlotItem().getAxis("left").setTextPen(FIELD_TEXT_COLOR)
        self.plot.getPlotItem().getAxis("bottom").setTextPen(FIELD_TEXT_COLOR)
        self.plot.getPlotItem().getAxis("left").setPen(BORDER_COLOR)
        self.plot.getPlotItem().getAxis("bottom").setPen(BORDER_COLOR)
        self.plot.getPlotItem().getAxis("bottom").setStyle(autoExpandTextSpace=True)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(True)
        self.plot.getPlotItem().hideButtons()
        self._apply_axis_style()

        metadata_group = QGroupBox("Static Metadata")
        self.metadata_layout = QGridLayout(metadata_group)
        self.metadata_layout.setHorizontalSpacing(max(8, int(round(8 * self.ui_scale))))
        self.metadata_layout.setVerticalSpacing(max(8, int(round(8 * self.ui_scale))))

        stats_group = QGroupBox("Statistics")
        self.stats_layout = QGridLayout(stats_group)
        self.stats_layout.setHorizontalSpacing(0)
        self.stats_layout.setVerticalSpacing(0)

        sidebar_content = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(max(6, int(round(6 * self.ui_scale))))
        sidebar_layout.addWidget(stats_group)
        sidebar_layout.addWidget(metadata_group)
        sidebar_layout.addStretch(1)

        content_splitter = QSplitter(Qt.Horizontal)
        content_splitter.addWidget(sidebar_content)
        content_splitter.addWidget(self.plot)
        content_splitter.setSizes([int(round(320 * self.ui_scale)), int(round(960 * self.ui_scale))])
        content_splitter.setStretchFactor(0, 0)
        content_splitter.setStretchFactor(1, 1)

        layout.addWidget(controls)
        layout.addWidget(content_splitter, 1)

    def open_csv(self) -> None:
        path_text = self._choose_csv_open_path()
        if path_text:
            self.load_csv(Path(path_text))

    def open_export_style_dialog(self) -> None:
        dialog = ExportStyleDialog(
            {
                "use_czech_labels": self.export_use_czech_labels,
                "transparent_background": self.export_transparent_background,
                "show_horizontal_y_grid": self.export_show_horizontal_y_grid,
                "text_color": self.export_text_color,
                "axis_grid_color": self.export_axis_grid_color,
                "primary_color": self.export_primary_color,
                "secondary_color": self.export_secondary_color,
            },
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        settings = dialog.settings()
        self.export_use_czech_labels = bool(settings["use_czech_labels"])
        self.export_transparent_background = bool(settings["transparent_background"])
        self.export_show_horizontal_y_grid = bool(settings["show_horizontal_y_grid"])
        self.export_text_color = str(settings["text_color"])
        self.export_axis_grid_color = str(settings["axis_grid_color"])
        self.export_primary_color = str(settings["primary_color"])
        self.export_secondary_color = str(settings["secondary_color"])
        self._apply_axis_style()
        self.refresh_analysis()

    def _apply_axis_style(self) -> None:
        plot_item = self.plot.getPlotItem()
        left_axis = plot_item.getAxis("left")
        bottom_axis = plot_item.getAxis("bottom")
        top_axis = plot_item.getAxis("top")
        right_axis = plot_item.getAxis("right")
        dashed_grid_pen = pg.mkPen(self.export_axis_grid_color, width=1, style=Qt.PenStyle.DashLine)
        dashed_grid_pen.setDashPattern([10.0, 8.0])

        plot_item.showAxis("top", True)
        plot_item.showAxis("right", True)

        self.plot.setBackground(None if self.export_transparent_background else PANEL_COLOR)
        left_axis.setTextPen(self.export_text_color)
        bottom_axis.setTextPen(self.export_text_color)
        left_axis.setPen(self.export_axis_grid_color)
        bottom_axis.setPen(self.export_axis_grid_color)
        left_axis.setTickPen(dashed_grid_pen)
        bottom_axis.setTickPen(dashed_grid_pen)
        left_axis.setGrid(130 if self.export_show_horizontal_y_grid else False)
        bottom_axis.setGrid(130)

        # Keep a boxed chart but avoid secondary right-axis grid lines/ticks.
        for border_axis in (top_axis, right_axis):
            border_axis.setPen(self.export_axis_grid_color)
            border_axis.setTextPen(self.export_axis_grid_color)
            border_axis.setStyle(showValues=False, tickLength=0)
            border_axis.setTicks([])
            border_axis.setGrid(False)

    def _choose_csv_open_path(self) -> str:
        start_dir = str(self.last_directory)
        if shutil.which("kdialog") is not None:
            result = subprocess.run(
                ["kdialog", "--getopenfilename", start_dir, "CSV files (*.csv)"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""

        path_text, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open Measurement CSV",
            start_dir,
            "CSV files (*.csv);;All files (*)",
        )
        return path_text

    def load_csv(self, path: Path) -> None:
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
                reader = csv.DictReader(csv_file)
                rows = [dict(row) for row in reader]
        except OSError as exc:
            QMessageBox.critical(self, "CSV Error", f"Could not open CSV:\n{exc}")
            return

        if not rows:
            QMessageBox.warning(self, "CSV Error", "CSV file contains no data rows.")
            return

        self.current_path = path
        self.last_directory = path.parent
        self._save_viewer_config()
        self.load_rows(rows, label=str(path))

    def load_rows(self, rows: list[dict[str, str]], *, label: str = "Unsaved measurement series") -> None:
        if not rows:
            self.rows = []
            self.numeric_columns = []
            self.current_path = None
            self.file_label.setText("No data loaded.")
            self._update_metadata()
            self._update_variable_controls()
            self.refresh_analysis()
            return

        self.rows = rows
        self.numeric_columns = self._detect_numeric_columns(rows)
        self._update_metadata()
        self.file_label.setText(f"{label} | {len(rows)} rows")

        self._update_variable_controls()

        if not self.numeric_columns:
            QMessageBox.warning(self, "CSV Error", "No numeric columns found.")
            self.refresh_analysis()
            return
        if self.selected_column not in self.numeric_columns:
            self.selected_column = self.numeric_columns[0]
        self._sync_variable_controls()
        self.refresh_analysis()

    @staticmethod
    def _detect_numeric_columns(rows: list[dict[str, str]]) -> list[str]:
        numeric_columns: list[str] = []
        for column in MEASURED_COLUMNS:
            if column not in rows[0]:
                continue
            values = [parse_float(row.get(column, "")) for row in rows]
            if any(value is not None for value in values):
                numeric_columns.append(column)
        return numeric_columns

    def _update_metadata(self) -> None:
        if not self.rows:
            self._populate_tiles(self.metadata_layout, [], columns=2)
            return
        items: list[tuple[str, str]] = [("Measurements", str(len(self.rows)))]
        average_interval = self._average_measurement_interval_s()
        if average_interval is not None:
            items.append(("Measurement time", format_rounded(average_interval, "s")))
        for column in STATIC_COLUMNS:
            if column not in self.rows[0]:
                continue
            values = [row.get(column, "") for row in self.rows]
            unique_values = sorted({value for value in values if value != ""})
            if not unique_values:
                continue
            if len(unique_values) == 1:
                items.append((self._metadata_label(column), format_static_value(column, unique_values[0])))
            else:
                preview = ", ".join(format_static_value(column, value) for value in unique_values[:8])
                suffix = " ..." if len(unique_values) > 8 else ""
                items.append((self._metadata_label(column), f"{preview}{suffix}"))
        self._populate_tiles(self.metadata_layout, items, columns=2)

    def _populate_tiles(
        self,
        target_layout: QGridLayout,
        items: list[tuple[str, str]],
        *,
        columns: int,
        highlights: Optional[dict[str, str]] = None,
    ) -> None:
        # Compact table mode: render key/value rows without color coding.
        self._clear_layout(target_layout)
        target_layout.setHorizontalSpacing(max(8, int(round(8 * self.ui_scale))))
        target_layout.setVerticalSpacing(max(3, int(round(3 * self.ui_scale))))
        target_layout.setContentsMargins(4, 4, 4, 4)
        for row, (label, value) in enumerate(items):
            key_widget = QLabel(label)
            is_stats_table = target_layout is self.stats_layout
            key_widget.setObjectName("statsTableKey" if is_stats_table else "tableKey")
            key_widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            value_widget = QLabel(value)
            value_widget.setObjectName("statsTableValue" if is_stats_table else "tableValue")
            value_widget.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            value_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_widget.setWordWrap(True)
            target_layout.addWidget(key_widget, row, 0)
            target_layout.addWidget(value_widget, row, 1)

        target_layout.setColumnStretch(0, 0)
        target_layout.setColumnStretch(1, 1)
        return

    @staticmethod
    def _clear_layout(layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                DataViewerWidget._clear_layout(child_layout)

    def _populate_tiles_legacy(
        self,
        target_layout: QGridLayout,
        items: list[tuple[str, str]],
        *,
        columns: int,
        highlights: Optional[dict[str, str]] = None,
    ) -> None:
        while target_layout.count():
            item = target_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        highlights = highlights or {}
        for index, (label, value) in enumerate(items):
            tile = QFrame()
            tile.setObjectName("valueTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(8, 6, 8, 6)
            tile_layout.setSpacing(2)
            label_widget = QLabel(label)
            label_widget.setObjectName("tileLabel")
            value_widget = QLabel(value)
            value_widget.setObjectName("tileValue")
            value_widget.setWordWrap(True)
            if label in highlights:
                value_widget.setStyleSheet(f"color: {highlights[label]}; font-weight: 800;")
            value_widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
            tile_layout.addWidget(label_widget)
            tile_layout.addWidget(value_widget)
            row = index // columns
            column = index % columns
            target_layout.addWidget(tile, row, column)
        for column in range(columns):
            target_layout.setColumnStretch(column, 1)

    def _average_measurement_interval_s(self) -> Optional[float]:
        if len(self.rows) < 2:
            return None
        elapsed_values = [
            value
            for value in (parse_float(row.get("elapsed_s", "")) for row in self.rows)
            if value is not None
        ]
        if len(elapsed_values) < 2:
            return None
        elapsed_values.sort()
        intervals = [
            elapsed_values[index] - elapsed_values[index - 1]
            for index in range(1, len(elapsed_values))
            if elapsed_values[index] >= elapsed_values[index - 1]
        ]
        if not intervals:
            return None
        return statistics.fmean(intervals)

    @staticmethod
    def _metadata_label(column: str) -> str:
        labels = {
            "model": "Detected model",
            "sample_rate_hz": "Sample rate",
            "dds_frequency_hz": "DDS frequency",
            "shunt_range": "Shunt range",
            "voltage_pga": "Voltage PGA",
            "current_pga": "Current PGA",
            "shunt_resistance_ohm": "Shunt resistance",
            "samples_per_period": "Samples per period",
            "captured_cycles": "Captured cycles",
        }
        return labels.get(column, column)

    def _update_variable_controls(self) -> None:
        for column, button in self.quick_buttons.items():
            button.setEnabled(column in self.numeric_columns)

        other_columns = [column for column in OTHER_COLUMNS if column in self.numeric_columns]
        self.other_combo.blockSignals(True)
        self.other_combo.clear()
        for column in other_columns:
            self.other_combo.addItem(DISPLAY_NAMES.get(column, column), column)
        self.other_combo.blockSignals(False)

    def _sync_variable_controls(self) -> None:
        for column, button in self.quick_buttons.items():
            button.blockSignals(True)
            button.setChecked(column == self.selected_column)
            button.blockSignals(False)

        index = self.other_combo.findData(self.selected_column)
        self.other_combo.blockSignals(True)
        self.other_combo.setCurrentIndex(index)
        self.other_combo.blockSignals(False)

    def select_column(self, column: str) -> None:
        if column not in self.numeric_columns:
            return
        self.selected_column = column
        self._sync_variable_controls()
        self.refresh_analysis()

    def _other_column_changed(self) -> None:
        column = self.other_combo.currentData()
        if isinstance(column, str):
            self.select_column(column)

    def _selected_values(self) -> list[float]:
        column = self.selected_column
        if not column:
            return []
        return [
            value
            for value in (parse_float(row.get(column, "")) for row in self.rows)
            if value is not None
        ]

    def refresh_analysis(self) -> None:
        values = self._selected_values()
        self._update_stats(values)
        self._update_plot(values)

    def _update_stats(self, values: list[float]) -> None:
        if not values:
            self._populate_tiles(self.stats_layout, [], columns=2)
            return
        scaled_values, _scale, unit_label = self._scaled_values(values, self.selected_column)
        sorted_values = sorted(scaled_values)
        mean = statistics.fmean(scaled_values)
        median = statistics.median(scaled_values)
        minimum = min(scaled_values)
        maximum = max(scaled_values)
        span = maximum - minimum
        stddev = statistics.stdev(scaled_values) if len(scaled_values) > 1 else 0.0
        variance = statistics.variance(scaled_values) if len(scaled_values) > 1 else 0.0
        stderr = stddev / math.sqrt(len(scaled_values)) if scaled_values else 0.0
        unit_suffix = "" if unit_label == "value" else f" {unit_label}"
        variance_suffix = "" if unit_label == "value" else f" {unit_label}^2"

        stats = [
            ("count", str(len(values))),
            ("mean", f"{format_number(mean)}{unit_suffix}"),
            ("std dev", f"{format_number(stddev)}{unit_suffix}"),
            ("median", f"{format_number(median)}{unit_suffix}"),
            ("min", f"{format_number(minimum)}{unit_suffix}"),
            ("max", f"{format_number(maximum)}{unit_suffix}"),
            ("range", f"{format_number(span)}{unit_suffix}"),
            ("standard error", f"{format_number(stderr)}{unit_suffix}"),
            ("variance", f"{format_number(variance)}{variance_suffix}"),
            ("q1", f"{format_number(self._percentile(sorted_values, 0.25))}{unit_suffix}"),
            ("q3", f"{format_number(self._percentile(sorted_values, 0.75))}{unit_suffix}"),
        ]
        self._populate_tiles(
            self.stats_layout,
            stats,
            columns=2,
            highlights={},
        )

    @staticmethod
    def _percentile(sorted_values: list[float], fraction: float) -> float:
        if not sorted_values:
            return 0.0
        position = fraction * (len(sorted_values) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return sorted_values[lower]
        weight = position - lower
        return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight

    def _update_plot(self, values: list[float]) -> None:
        self.plot.clear()
        self.histogram_item = None
        self.gaussian_fill_item = None
        self.gaussian_curve_item = None
        self.gaussian_curve_pen = None
        self.sigma_ticks = []
        self.plot.getPlotItem().getAxis("left").setTicks(None)
        self.plot.getPlotItem().getAxis("bottom").setTicks(None)
        column = self.selected_column or "Value"
        scaled_values, _scale, unit_label = self._scaled_values(values, column)
        display_map = CZECH_DISPLAY_NAMES if self.export_use_czech_labels else DISPLAY_NAMES
        display_name = display_map.get(column, column)
        y_label = "Počet / škálovaná hustota" if self.export_use_czech_labels else "Count / scaled density"
        self.plot.getPlotItem().setLabel("bottom", f"{display_name} [{unit_label}]", color=self.export_text_color)
        self.plot.getPlotItem().setLabel("left", y_label, color=self.export_text_color)
        self._apply_axis_style()
        if not scaled_values:
            return

        self._plot_overlay(scaled_values, unit_label)

    def _plot_overlay(self, values: list[float], unit_label: str) -> None:
        centers, heights, bin_width = self._histogram(values)
        max_count = max(heights) if heights else 1
        secondary_color = QColor(self.export_secondary_color)
        self.histogram_item = pg.BarGraphItem(
            x=centers,
            height=heights,
            width=bin_width * 0.9,
            brush=QColor(secondary_color.red(), secondary_color.green(), secondary_color.blue(), 75),
            pen=pg.mkPen(self.export_secondary_color, width=2),
        )
        self.plot.addItem(self.histogram_item)

        mean = statistics.fmean(values)
        stddev = statistics.stdev(values) if len(values) > 1 else 0.0
        minimum = min(values)
        maximum = max(values)
        if stddev <= 0.0:
            stddev = max(abs(mean) * 0.01, 1e-12)
        left = min(minimum, mean - 4.0 * stddev)
        right = max(maximum, mean + 4.0 * stddev)
        if left == right:
            left -= 1.0
            right += 1.0

        x_values = [left + (right - left) * index / 399 for index in range(400)]
        density_values = [
            (1.0 / (stddev * math.sqrt(2.0 * math.pi)))
            * math.exp(-0.5 * ((x_value - mean) / stddev) ** 2)
            for x_value in x_values
        ]
        max_density = max(density_values) if density_values else 1.0
        scale = max_count / max_density if max_density > 0.0 else 1.0
        y_values = [value * scale for value in density_values]
        baseline = [0.0 for _ in x_values]
        fill_top_pen = pg.mkPen(self.export_primary_color, width=2)
        fill_top = pg.PlotCurveItem(x_values, y_values, pen=fill_top_pen)
        fill_bottom = pg.PlotCurveItem(x_values, baseline, pen=pg.mkPen(self.export_primary_color, width=0))
        self.plot.addItem(fill_top)
        self.plot.addItem(fill_bottom)
        self.gaussian_curve_item = fill_top
        self.gaussian_curve_pen = fill_top_pen
        primary_fill_color = QColor(self.export_primary_color)
        brush = QColor(primary_fill_color.red(), primary_fill_color.green(), primary_fill_color.blue(), 85)
        self.gaussian_fill_item = pg.FillBetweenItem(fill_top, fill_bottom, brush=brush)
        self.plot.addItem(self.gaussian_fill_item)
        self._set_sigma_axis_ticks(mean, stddev, unit_label)
        self.plot.setXRange(left, right, padding=0.0)
        y_max = max(max_count, max(y_values, default=0.0)) * 1.05
        if y_max <= 0.0:
            y_max = 1.0
        self.plot.setYRange(0.0, y_max, padding=0.0)
        self._set_y_axis_integer_ticks(y_max)

    def _set_y_axis_integer_ticks(self, y_max: float) -> None:
        axis = self.plot.getPlotItem().getAxis("left")
        if not self.export_show_horizontal_y_grid:
            axis.setTicks(None)
            return
        upper = max(1, int(math.ceil(y_max)))
        # Keep labels/grid on integer values, but cap displayed labels to <= 10.
        step = max(1, int(math.ceil(upper / 9)))
        values = list(range(0, upper + 1, step))
        if values[-1] != upper:
            values.append(upper)
        ticks = [(float(value), str(value)) for value in values]
        axis.setTicks([ticks])

    def _set_sigma_axis_ticks(self, mean: float, stddev: float, unit_label: str) -> None:
        positions = [(0, mean, "mean")]
        for sigma in range(1, 4):
            positions.append((-sigma, mean - sigma * stddev, f"-{sigma}σ"))
            positions.append((sigma, mean + sigma * stddev, f"+{sigma}σ"))

        major_ticks: list[tuple[float, str]] = []
        axis_color = QColor(self.export_axis_grid_color)
        text_color = QColor(self.export_text_color)
        for sigma, x_value, label in sorted(positions, key=lambda item: item[1]):
            value_text = f"{x_value:.2f}"
            if sigma == 0:
                tick_label = f"mean\n{value_text}"
                pen = pg.mkPen(text_color, width=2, style=Qt.PenStyle.DashLine)
            else:
                tick_label = f"{label}\n{value_text}"
                pen = pg.mkPen(axis_color, width=1, style=Qt.PenStyle.DotLine)
            self.plot.addLine(x=x_value, pen=pen)
            major_ticks.append((x_value, tick_label))
        self.plot.getPlotItem().getAxis("bottom").setTicks([major_ticks])

    def _histogram(self, values: list[float]) -> tuple[list[float], list[int], float]:
        bins = max(1, self.bin_spin.value())
        minimum = min(values)
        maximum = max(values)
        if minimum == maximum:
            width = max(abs(minimum) * 0.01, 1.0)
            centers = [minimum]
            heights = [len(values)]
        else:
            width = (maximum - minimum) / bins
            counts = [0 for _ in range(bins)]
            for value in values:
                index = min(bins - 1, max(0, int((value - minimum) / width)))
                counts[index] += 1
            centers = [minimum + (index + 0.5) * width for index in range(bins)]
            heights = counts
        return (centers, heights, width)

    @staticmethod
    def _scaled_values(values: list[float], column: str) -> tuple[list[float], float, str]:
        unit = BASE_UNITS.get(column, "")
        if unit == "deg" or not unit:
            return (values, 1.0, unit or "value")
        scale, prefix = si_scale(values)
        return ([value / scale for value in values], scale, f"{prefix}{unit}")


class MainWindow(QMainWindow):
    def __init__(self, csv_path: Optional[Path] = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        ui_scale = compute_ui_scale()
        self.resize(int(round(1280 * ui_scale)), int(round(820 * ui_scale)))
        self.setCentralWidget(DataViewerWidget(csv_path, configure_palette=True))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="View LCR meter measurement CSV data.")
    parser.add_argument("csv_path", nargs="?", default=None, help="Measurement CSV to open.")
    return parser


def main() -> None:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication([])
    args = build_arg_parser().parse_args()
    csv_path = Path(args.csv_path) if args.csv_path else None
    window = MainWindow(csv_path)
    window.showMaximized()
    app.exec()


if __name__ == "__main__":
    main()

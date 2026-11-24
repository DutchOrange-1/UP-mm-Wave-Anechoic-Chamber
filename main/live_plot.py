"""
live_plot.py

Live plotting for the chamber GUI. While a scan runs, each finished
measurement (a PointN.s2p file written by the VNA module) is read and drawn,
so the operator can see the antenna pattern build up and spot a problem
(cable fault, wrong frequency range, no signal) after the first few points
instead of at the end of a two-hour scan.

Two plots:
    1. Pattern: level (dB) against pattern angle, at one chosen frequency,
       with co-pol and cross-pol as separate curves.
    2. Latest sweep: level (dB) against frequency for the most recent point.

This file contains:
    - parse_s2p(): a Touchstone (.s2p) reader. No GUI code, easy to test.
    - pattern_angle_deg(): turns the motor positions into one pattern angle.
    - LivePlotPanel: the widget that chamber_scan_ui.py embeds.

Requires pyqtgraph:   pip install pyqtgraph
(pyqtgraph draws through Qt's own painter, which is why it is used here
rather than matplotlib: it stays responsive on a Raspberry Pi 3B.)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg  # imported after PySide6 so it picks that Qt binding

# --------------------------------------------------------------------------
# Reading .s2p files
# --------------------------------------------------------------------------

# Column order of a 2-port Touchstone file is fixed by the format:
# frequency, S11, S21, S12, S22 (each as a pair of numbers).
PARAMETER_ORDER = ("S11", "S21", "S12", "S22")

_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


@dataclass
class S2PData:
    freq_hz: np.ndarray
    s: dict  # "S11" | "S21" | "S12" | "S22" -> complex ndarray, one value per frequency


def parse_s2p(text: str) -> S2PData:
    """Parse the text of a Touchstone 2-port file.

    The option line ("# HZ S RI R 50") says which frequency unit and number
    format the file uses; RI (real/imaginary), MA (magnitude/angle in degrees)
    and DB (dB/angle in degrees) are all handled. Raises ValueError on
    anything malformed, so a half-written file is never mistaken for data.
    """
    freq_scale = 1e9  # Touchstone default unit is GHz
    fmt = "MA"        # Touchstone default format
    rows = []

    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()  # "!" starts a comment
        if not line:
            continue
        if line.startswith("#"):
            for token in line[1:].upper().split():
                if token in _FREQ_UNITS:
                    freq_scale = _FREQ_UNITS[token]
                elif token in ("RI", "MA", "DB"):
                    fmt = token
            continue
        values = line.split()
        if len(values) != 9:
            raise ValueError(f"expected 9 columns, found {len(values)}: {line[:60]!r}")
        rows.append([float(v) for v in values])

    if len(rows) < 2:
        raise ValueError("no data rows found")

    data = np.array(rows)
    freq_hz = data[:, 0] * freq_scale
    pairs = data[:, 1:].reshape(len(rows), 4, 2)
    first, second = pairs[:, :, 0], pairs[:, :, 1]

    if fmt == "RI":
        z = first + 1j * second
    elif fmt == "MA":
        z = first * np.exp(1j * np.deg2rad(second))
    else:  # DB
        z = 10 ** (first / 20) * np.exp(1j * np.deg2rad(second))

    return S2PData(freq_hz, {name: z[:, i] for i, name in enumerate(PARAMETER_ORDER)})


def to_db(z: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.abs(z) + 1e-15)


# --------------------------------------------------------------------------
# Pattern angle
# --------------------------------------------------------------------------

def pattern_angle_deg(aut_rotation_deg: float, boom_deg: float) -> float:
    """One continuous angle (0 to 180 degrees) for the pattern plot.

    ASSUMPTION - please check against the physical chamber. The scan sweeps
    the boom from 0 to 90 degrees with the AUT turned to 0 (or 90 for
    cross-pol), then turns the AUT by 180 degrees (270 for cross-pol) and
    sweeps the boom back from 90 to 0 to reach the other side. Read that
    way, the first pass covers 0-90 and the second pass covers 90-180, so
    the angle is the boom angle for the first pass and 180 minus the boom
    angle for the second. If the real geometry differs, this is the only
    function that needs to change.
    """
    return float(boom_deg) if aut_rotation_deg < 180 else 180.0 - float(boom_deg)


# --------------------------------------------------------------------------
# The widget
# --------------------------------------------------------------------------

_CO_COLOUR = (30, 90, 200)
_CROSS_COLOUR = (210, 50, 50)


class LivePlotPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        pg.setConfigOption("background", "w")
        pg.setConfigOption("foreground", "k")

        # Every received point is kept, so changing the frequency, the
        # parameter or normalisation redraws the whole pattern instantly.
        self._records: list[dict] = []

        self._param_box = QComboBox()
        self._param_box.addItems(["S21", "S11", "S12", "S22"])
        self._param_box.setToolTip("Which S-parameter to plot. S21 is the transmission between the two antennas.")

        self._freq_spin = QDoubleSpinBox()
        self._freq_spin.setDecimals(3)
        self._freq_spin.setSuffix(" GHz")
        self._freq_spin.setRange(0.0, 220.0)
        self._freq_spin.setToolTip("Frequency used for the pattern plot. The nearest measured point is used.")

        self._norm_box = QCheckBox("Normalise to co-pol peak")
        self._norm_box.setToolTip(
            "Shift both curves so the co-pol peak is 0 dB. The cross-pol "
            "level then reads directly as dB below the co-pol peak."
        )

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Parameter:"))
        controls.addWidget(self._param_box)
        controls.addWidget(QLabel("Frequency:"))
        controls.addWidget(self._freq_spin)
        controls.addWidget(self._norm_box)
        controls.addStretch(1)

        # Pattern plot
        self._pattern_plot = pg.PlotWidget()
        self._pattern_plot.showGrid(x=True, y=True, alpha=0.3)
        self._pattern_plot.setLabel("bottom", "Pattern angle", units="deg")
        self._pattern_plot.addLegend(offset=(10, 10))
        self._co_curve = self._pattern_plot.plot(
            pen=pg.mkPen(_CO_COLOUR, width=2), symbol="o", symbolSize=7,
            symbolBrush=_CO_COLOUR, symbolPen=None, name="Co-pol",
        )
        self._cross_curve = self._pattern_plot.plot(
            pen=pg.mkPen(_CROSS_COLOUR, width=2), symbol="s", symbolSize=7,
            symbolBrush=_CROSS_COLOUR, symbolPen=None, name="Cross-pol",
        )

        # Latest-sweep plot
        self._sweep_plot = pg.PlotWidget()
        self._sweep_plot.showGrid(x=True, y=True, alpha=0.3)
        self._sweep_plot.setLabel("bottom", "Frequency", units="GHz")
        self._sweep_curve = self._sweep_plot.plot(pen=pg.mkPen((60, 60, 60), width=2))
        self._freq_marker = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen((210, 140, 0), width=1.5, style=Qt.PenStyle.DashLine)
        )
        self._sweep_plot.addItem(self._freq_marker)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._pattern_plot, stretch=3)
        layout.addWidget(self._sweep_plot, stretch=2)

        self._param_box.currentTextChanged.connect(self._refresh)
        self._freq_spin.valueChanged.connect(self._refresh)
        self._norm_box.toggled.connect(self._refresh)

        self.reset(50e9, 60e9)

    # ---- called by the GUI ----------------------------------------------

    def reset(self, f_start_hz: float, f_stop_hz: float) -> None:
        """Clear everything for a new scan and centre the frequency picker
        in the sweep range."""
        self._records.clear()
        start_ghz, stop_ghz = f_start_hz / 1e9, f_stop_hz / 1e9
        self._freq_spin.blockSignals(True)
        self._freq_spin.setRange(start_ghz, stop_ghz)
        self._freq_spin.setSingleStep(max((stop_ghz - start_ghz) / 100.0, 0.001))
        self._freq_spin.setValue((start_ghz + stop_ghz) / 2.0)
        self._freq_spin.blockSignals(False)
        self._refresh()

    def add_point(self, row: dict, data: S2PData) -> None:
        """Add one finished measurement. `row` is the manifest row for the
        point (polarisation, aut_rotation_deg, boom_deg, point_index)."""
        self._records.append(
            {
                "pol": row["polarisation"],
                "angle": pattern_angle_deg(float(row["aut_rotation_deg"]), float(row["boom_deg"])),
                "index": row["point_index"],
                "freq_hz": data.freq_hz,
                "s": data.s,
            }
        )
        self._refresh()

    # ---- drawing ----------------------------------------------------------

    def _pattern_curve(self, pol: str) -> tuple[np.ndarray, np.ndarray]:
        param = self._param_box.currentText()
        target_hz = self._freq_spin.value() * 1e9
        angles, levels = [], []
        for rec in self._records:
            if rec["pol"] != pol:
                continue
            nearest = int(np.argmin(np.abs(rec["freq_hz"] - target_hz)))
            angles.append(rec["angle"])
            levels.append(float(to_db(rec["s"][param][nearest])))
        if not angles:
            return np.array([]), np.array([])
        order = np.argsort(angles)
        return np.array(angles)[order], np.array(levels)[order]

    def _refresh(self, *_args) -> None:
        param = self._param_box.currentText()
        co_x, co_y = self._pattern_curve("co")
        cross_x, cross_y = self._pattern_curve("cross")

        unit = "dB"
        if self._norm_box.isChecked() and co_y.size:
            peak = co_y.max()
            co_y, cross_y = co_y - peak, cross_y - peak
            unit = "dB rel. co-pol peak"

        self._co_curve.setData(co_x, co_y)
        self._cross_curve.setData(cross_x, cross_y)
        self._pattern_plot.setLabel("left", f"|{param}| ({unit})")

        if self._records:
            latest = self._records[-1]
            f_used = latest["freq_hz"][int(np.argmin(np.abs(latest["freq_hz"] - self._freq_spin.value() * 1e9)))]
            self._pattern_plot.setTitle(f"{param} pattern at {f_used / 1e9:.3f} GHz  ({len(self._records)} points)")
            self._sweep_curve.setData(latest["freq_hz"] / 1e9, to_db(latest["s"][param]))
            self._sweep_plot.setTitle(
                f"Latest sweep: point {latest['index']} ({latest['pol']}-pol, angle {latest['angle']:.1f} deg)"
            )
        else:
            self._pattern_plot.setTitle("Pattern: waiting for the first measurement")
            self._sweep_curve.setData([], [])
            self._sweep_plot.setTitle("Latest sweep")

        self._sweep_plot.setLabel("left", f"|{param}| (dB)")
        self._freq_marker.setValue(self._freq_spin.value())

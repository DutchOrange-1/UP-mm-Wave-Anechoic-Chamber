"""
Chamber control screen connected directly to the real hardware modules:
    - anritsu_vectorstar_vna_interface.py: setup_vna(), sweep_and_save()
    - Motor_scan_A_plane.py:                planer_scan()

    Scan runs in a subprocess and not a QThread because as soon as the hardware modules
    are imported, they connect to the real equipment (VNA and motors)

    Limitations in the hardware module that this file works around:
    1. The Bug: In Motor_scan_A_plane.py, scan_AUT_CO_Only() incorrectly sets its total point count to azimuth_points * 4 instead of * 2.
    Because an ECO/HCO scan only takes two passes, the motor script's internal log caps out at 50% even when finished. 
    
    The GUI Solution: chamber_ui.py ignores that calculation completely and tracks true progress with build_expected_manifest().
    The GUI progress bar is always accurate, but anyone reading the raw terminal log directly will still see the misleading 50% cap.

    2. The Issue: take_sample() calls sweep_and_save() with no arguments, relying on its default "s2p_data" folder.
    Because Python evaluates default arguments only once at import time, the destination folder cannot be redirected
    or passed in via the motor module. 

    The Fix: The worker subprocess uses os.chdir(config.output_dir) right before importing the hardware scripts.
    Because "s2p_data" is a relative path, it is created directly inside whichever folder the operator selected in the GUI.

    3. The Issue: Files are saved sequentially as Point1.s2p, Point2.s2p, etc., with zero angular position metadata.
    The Fragile Fix: chamber_ui.py replicates the exact motor loop in build_expected_manifest().
    Whenever the log prints "Taking Sample...", it assumes the scan followed the planned sequence and writes the paired angles into manifest.csv.
    The Risk: If someone changes the motor movement order in Motor_scan_A_plane.py, the GUI will silently write wrong angles to the CSV.
     Ideal Solution: The motor script should provide a callback function (e.g., on_sample(aut_deg, boom_deg)) to
     send real-time coordinates directly from the motor controllers on every capture.
    
    4. Stage 1 (Graceful Stop): Clicking Abort sends SIGTERM, which is converted to a KeyboardInterrupt.
    This triggers the except KeyboardInterrupt: blocks in the motor code to stop active motion.
    Stage 2 (Homing & Cleanup): The hardware script then proceeds to run homing() and close_device() on both axes.
    The motors will still move physically for a few seconds to return home.
    Stage 3 (Hard Kill): If the homing sequence hangs and exceeds the 20-second timeout (abort_grace_s),
    the GUI forces a hard kill (process.kill()).
    Operator Takeaway: After aborting, wait for physical motion to stop.
    If a hard kill occurs, inspect the chamber manually before running another scan.

    5. RESOLVED: planer_scan() now accepts com1, com2 and project_name as parameters. Confirmed from the
    motor code itself that com1 controls Elevation and com2 controls Azimuth (elev = "...COM"+com1,
    azi = "...COM"+com2) — the opposite pairing from this file's own "azimuth"/"elevation" field names,
    so the swap is handled once, at the _run_scan_process() call site, rather than in the GUI fields
    themselves. project_name is passed straight through to vna_interface.sweep_and_save() as its out_dir
    argument — this is INFERRED from reading the call site (sweep_and_save()'s only parameter is out_dir),
    not explicitly confirmed by whoever owns the motor file, so it's worth a quick confirmation message
    before relying on it in a real run. It must never be an empty string: sweep_and_save() calls
    os.makedirs(out_dir), which raises FileNotFoundError immediately on an empty path — every sample in
    the run would crash. ScanPanel refuses to start a scan with a blank project name for this reason.
"""
from __future__ import annotations

import csv
import datetime
import logging
import logging.handlers
import multiprocessing as mp
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------------
# Defaults for things the GUI can't query or validate automatically — motor
# COM ports, mainly. Kept here, at the top, specifically so they're easy to
# find and edit without digging through the rest of the file once the real
# port numbers are confirmed. See item 5 in the docstring above for the
# important caveat that these are NOT yet wired into the hardware call.
# --------------------------------------------------------------------------

DEFAULT_AZIMUTH_COM_PORT = "13"
DEFAULT_ELEVATION_COM_PORT = "12"
DEFAULT_RUN_NOTES_FILENAME = "project1.txt"
DEFAULT_PROJECT_NAME = "project1"

# --------------------------------------------------------------------------
# Type of scan: 
# User sets the E plane or H plane manually by positioning the antennas
# according to desired measurement. Motors do not encode that.
# --------------------------------------------------------------------------

SCAN_TYPES = {
    "E-plane Co & Cross": "E",
    "E-plane Co only": "ECO",
    "H-plane Co & Cross": "H",
    "H-plane Co only": "HCO",
}


@dataclass
class ScanConfig:
    f_start_hz: float      #starting frequency
    f_stop_hz: float       #stopping frequency
    num_points: int        #number of points
    #VNA downconverts the received signal before measuring it (either slower measurements with less noise or faster sweep with more noise on the trace)
    ifbw_hz: float          #vna's intermediate frequency bandwidth measured in hertz (from the vna interface file)
    scan_type: str          # 'E' | 'ECO' | 'H' | 'HCO'
    azimuth_points: int     # number of angular positions per 90 degree boom sweep pass
    output_dir: Path        #output directory where .s2p and manifest.csv are saved
    azimuth_com_port: str = DEFAULT_AZIMUTH_COM_PORT
    elevation_com_port: str = DEFAULT_ELEVATION_COM_PORT
    run_notes_filename: str = DEFAULT_RUN_NOTES_FILENAME
    # Passed straight through to planer_scan(project_name=...), which
    # passes it straight through to vna_interface.sweep_and_save(out_dir=...)
    # — see item 5 in the module docstring. Must never be empty: an empty
    # string makes sweep_and_save()'s os.makedirs(out_dir) raise
    # FileNotFoundError on the very first sample.
    project_name: str = DEFAULT_PROJECT_NAME


# --------------------------------------------------------------------------
# Manifest replicates scan_AUT_CO_CROSS / scan_AUT_CO_Only's positioning
# sequence exactly, so each PointN.s2p can be matched to the angles it was
# taken at. See item 3 at the top of this file for the caveat on this.
# --------------------------------------------------------------------------

def build_expected_manifest(scan_type: str, azimuth_points: int) -> list[dict]:
    #start at 0 degrees and go to 90 degrees in steps of azimuth_points
    elev_positions = np.linspace(0, 90, azimuth_points)
    plane = "E-plane" if scan_type in ("E", "ECO") else "H-plane"

    if scan_type in ("E", "H"):
        passes = [
            (0, "co", elev_positions, "up"),
            (180, "co", elev_positions[::-1], "down"),
            (90, "cross", elev_positions, "up"),
            (270, "cross", elev_positions[::-1], "down"),
        ]
    elif scan_type in ("ECO", "HCO"):
        passes = [
            (0, "co", elev_positions, "up"),
            (180, "co", elev_positions[::-1], "down"),
        ]
    else:
        raise ValueError(f"Unknown scan type: {scan_type!r}")

    manifest: list[dict] = [] #create empty and append in loop
    for aut_deg, pol, positions, direction in passes:
        for boom_deg in positions:
            manifest.append(
                {
                    "plane": plane,
                    "polarisation": pol,
                    "aut_rotation_deg": aut_deg,
                    "boom_deg": round(float(boom_deg), 4),
                    "sweep_direction": direction,
                }
            )
    return manifest


# --------------------------------------------------------------------------
# Entry point of the subprocess:
# Opening the GUI should not affect the real equipment (motors and vna)
# Thus the hardware modules are only imported HERE, not at the top of file
# --------------------------------------------------------------------------

def _run_scan_process(config: ScanConfig, log_queue: mp.Queue) -> None:
    queue_handler = logging.handlers.QueueHandler(log_queue)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(queue_handler)

    #ScanController.abort() calls process.terminate(), which sends SIGTERM.
    #if SIGTERM kills this process immediately if it is left alone
    # motors coast to position they were last commanded to  
    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # sweep_and_save()'s out_dir cannot be overridden through planer_scan's
    # public API, so change the process's own cwd instead, see item 2 in
    # the module docstring.
    config.output_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(config.output_dir)

    # planer_scan() now accepts com1/com2/project_name directly (see item 5
    # in the module docstring for the history). Confirmed by reading the
    # motor code itself:
    #   elev = ...COM + com1   -> com1 is the ELEVATION port
    #   azi  = ...COM + com2   -> com2 is the AZIMUTH port
    # This is the opposite pairing from the field names in our own GUI, so
    # the swap happens right here at the call site, not in the GUI layer.
    logging.info(
        "Motor ports this run: elevation=COM%s (com1), azimuth=COM%s (com2).",
        config.elevation_com_port, config.azimuth_com_port,
    )

    try:
        import anritsu_vectorstar_vna_interface as vna_interface
        import Motor_scan_A_plane as motor_scan
    except Exception as exc:
        # does this apply to both motors and vna or is it a "if one fails, exception thrown"
        logging.error("Failed to import/connect to hardware: %s", exc)
        log_queue.put(None)
        return

    try:
        # give vna its data
        vna_interface.setup_vna(
            f_start_hz=config.f_start_hz,
            f_stop_hz=config.f_stop_hz,
            num_points=config.num_points,
            ifbw_hz=config.ifbw_hz,
        )
        # give motors their data
        motor_scan.planer_scan(
            azimuth_points=config.azimuth_points,
            type=config.scan_type,
            com1=config.elevation_com_port,
            com2=config.azimuth_com_port,
            project_name=config.project_name,
        )
        logging.info("Scan complete.")
    except Exception as exc:
        logging.error("Scan failed: %s", exc)
    finally:
        try:
            vna_interface.shutdown()
        except Exception as exc:
            logging.warning("VNA shutdown reported an error: %s", exc)
        log_queue.put(None)  # sentinel: tells the GUI side that work is done


# --------------------------------------------------------------------------
# GUI-side controller. Owns the subprocess, drains the log queue on a
# timer, matches "Taking Sample..." lines against the expected manifest,
# and writes manifest.csv incrementally so a crash mid-run loses at most
# the point in flight.
# --------------------------------------------------------------------------

class ScanController(QObject):
    log_line = Signal(str)
    progress = Signal(int, int)   # done, total
    point_logged = Signal(dict)
    finished = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._process: mp.Process | None = None
        self._log_queue: mp.Queue | None = None
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._drain_queue)
        self._manifest: list[dict] = []
        self._manifest_index = 0
        self._manifest_file = None
        self._manifest_writer: csv.DictWriter | None = None
        # there is should be a waiting period for there to be a graceful
        # stop before escalating to a hard kill
        # planer_scan() has a KeyboardInterrupt handling which stops the motors quickly
        # there is homing() and close_device() on both axes
        # tests can shrink it instead of waiting on real timing
        self.abort_grace_s = 20 

    def start(self, config: ScanConfig) -> None:
        self._manifest = build_expected_manifest(config.scan_type, config.azimuth_points)
        self._manifest_index = 0

        config.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_run_notes(config)

        manifest_path = config.output_dir / "manifest.csv"
        is_new = not manifest_path.exists()
        self._manifest_file = open(manifest_path, "a", newline="")
        fieldnames = [
            "point_index", "s2p_filename", "plane", "polarisation",
            "aut_rotation_deg", "boom_deg", "sweep_direction",
        ]
        self._manifest_writer = csv.DictWriter(self._manifest_file, fieldnames=fieldnames)
        if is_new:
            self._manifest_writer.writeheader()

        self._log_queue = mp.Queue()
        self._process = mp.Process(
            target=_run_scan_process, args=(config, self._log_queue), daemon=True
        )
        self._process.start()
        self._poll_timer.start()

    def _write_run_notes(self, config: ScanConfig) -> None:
        """A small human-readable summary of this run's settings, written
        alongside the .s2p files and manifest.csv so a run is still
        identifiable later without having to reopen the GUI or dig through
        the raw log. Named by the operator (default: project1.txt)."""
        if not config.run_notes_filename:
            return
        lines = [
            f"Run started: {datetime.datetime.now().isoformat(timespec='seconds')}",
            f"Scan type: {config.scan_type}",
            f"Frequency: {config.f_start_hz/1e9:.3f}-{config.f_stop_hz/1e9:.3f} GHz, "
            f"{config.num_points} points, IF bandwidth {config.ifbw_hz:.0f} Hz",
            f"Azimuth points per 90-degree pass: {config.azimuth_points}",
            f"Expected total samples: {len(self._manifest)}",
            f"Azimuth COM port: COM{config.azimuth_com_port} (sent as com2)",
            f"Elevation COM port: COM{config.elevation_com_port} (sent as com1)",
            f"Project name: {config.project_name} "
            "(becomes the .s2p output folder name, per planer_scan())",
            f"Output folder: {config.output_dir}",
        ]
        notes_path = config.output_dir / config.run_notes_filename
        notes_path.write_text("\n".join(lines) + "\n")

    def abort(self) -> None:
        if self._process is not None and self._process.is_alive():
            self.log_line.emit("Operator requested abort which terminates scan process.")
            self._process.terminate()
            self._process.join(timeout=self.abort_grace_s)
            if self._process.is_alive():
                self.log_line.emit("Process did not exit after the stop/home sequence; forcing a hard kill."
                "Check the chamber physically before starting another scan.")
                self._process.kill()
                self._process.join(timeout=5)
        self._poll_timer.stop()
        self._close_manifest()
        self.failed.emit("Aborted by operator")

    def _drain_queue(self) -> None:
        if self._log_queue is None:
            return
        while not self._log_queue.empty():
            record = self._log_queue.get()

            if record is None:
                self._poll_timer.stop()
                self._close_manifest()
                exit_ok = self._process is None or self._process.exitcode == 0
                if self._process is not None:
                    self._process.join(timeout=5)
                if exit_ok:
                    self.finished.emit()
                else:
                    self.failed.emit("Scan process exited with an error — see log")
                return

            message = record.getMessage()
            self.log_line.emit(message)

            if "Taking Sample" in message:
                self._record_point()

    def _record_point(self) -> None:
        if self._manifest_index >= len(self._manifest):
            self.log_line.emit(
                "WARNING: more samples taken than the expected manifest length. "
                "Double check azimuth_points/type match what actually ran."
            )
            return
        entry = self._manifest[self._manifest_index]
        self._manifest_index += 1
        row = {
            "point_index": self._manifest_index,
            "s2p_filename": f"Point{self._manifest_index}.s2p",
            **entry,
        }
        if self._manifest_writer is not None:
            self._manifest_writer.writerow(row)
            self._manifest_file.flush()
        self.point_logged.emit(row)
        self.progress.emit(self._manifest_index, len(self._manifest))

    def _close_manifest(self) -> None:
        if self._manifest_file is not None:
            self._manifest_file.close()
            self._manifest_file = None
            self._manifest_writer = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.is_alive()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class ScanPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CEFIM mmWave Chamber: Scan Control")
        self.resize(560, 700)

        self._controller = ScanController()
        self._controller.log_line.connect(self._append_log)
        self._controller.progress.connect(self._on_progress)
        self._controller.finished.connect(self._on_finished)
        self._controller.failed.connect(self._on_failed)

        self._build_ui()

    # ---- layout ---------------------------------------------------

    def _build_ui(self) -> None:
        title = QLabel("mmWave Antenna Measurement System")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")

        # Frequency sweep -> setup_vna()
        freq_group = QGroupBox("Frequency sweep")
        self._start_ghz = QDoubleSpinBox()
        self._start_ghz.setRange(0.07, 220.0)
        self._start_ghz.setDecimals(3)
        self._start_ghz.setValue(50.0)
        self._start_ghz.setSuffix(" GHz")

        self._stop_ghz = QDoubleSpinBox()
        self._stop_ghz.setRange(0.07, 220.0)
        self._stop_ghz.setDecimals(3)
        self._stop_ghz.setValue(60.0)
        self._stop_ghz.setSuffix(" GHz")

        self._points = QSpinBox()
        self._points.setRange(2, 20001)
        self._points.setValue(201)

        self._ifbw = QDoubleSpinBox()
        self._ifbw.setRange(1.0, 1_000_000.0)
        self._ifbw.setDecimals(0)
        self._ifbw.setValue(1000.0)
        self._ifbw.setSuffix(" Hz")

        freq_form = QFormLayout()
        freq_form.addRow("Start:", self._start_ghz)
        freq_form.addRow("Stop:", self._stop_ghz)
        freq_form.addRow("Points:", self._points)
        freq_form.addRow("IF bandwidth:", self._ifbw)
        freq_group.setLayout(freq_form)

        # Scan type + resolution -> planer_scan()
        scan_group = QGroupBox("Scan")
        self._type_box = QComboBox()
        self._type_box.addItems(list(SCAN_TYPES.keys()))
        self._type_box.setToolTip(
            "E vs H is a manual antenna-orientation choice. The motor "
            "sequence is identical either way."
        )
        self._az_points = QSpinBox()
        self._az_points.setRange(2, 500)
        self._az_points.setValue(9)
        self._az_points.setToolTip("Number of boom positions per 0-90 degree pass")

        scan_form = QFormLayout()
        scan_form.addRow("Measurement:", self._type_box)
        scan_form.addRow("Points per 90 degree pass:", self._az_points)
        scan_group.setLayout(scan_form)

        

        # Motor COM ports -> planer_scan(com1=elevation, com2=azimuth). The
        # field names here follow the axis, not his parameter names — the
        # com1/com2 swap is handled once at the subprocess call site.
        port_group = QGroupBox("Motor COM ports")
        port_validator = QIntValidator(1, 999, self)

        self._azimuth_com_edit = QLineEdit(DEFAULT_AZIMUTH_COM_PORT)
        self._azimuth_com_edit.setValidator(port_validator)
        self._azimuth_com_edit.setToolTip("Sent to planer_scan() as com2.")

        self._elevation_com_edit = QLineEdit(DEFAULT_ELEVATION_COM_PORT)
        self._elevation_com_edit.setValidator(port_validator)
        self._elevation_com_edit.setToolTip("Sent to planer_scan() as com1.")

        self._project_name_edit = QLineEdit(DEFAULT_PROJECT_NAME)
        self._project_name_edit.setToolTip(
            "Sent to planer_scan() as project_name, which becomes the .s2p "
            "output folder name. Cannot be blank — an empty value crashes "
            "the VNA save step on the very first sample."
        )

        port_form = QFormLayout()
        port_form.addRow("Azimuth (COM):", self._azimuth_com_edit)
        port_form.addRow("Elevation (COM):", self._elevation_com_edit)
        port_form.addRow("Project name:", self._project_name_edit)
        port_group.setLayout(port_form)

        # Output folder for s2p_data/ and manifest.csv, plus a human-readable
        # run notes file written alongside them.
        out_group = QGroupBox("Output")
        default_dir = Path.cwd() / f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}"
        self._out_dir_edit = QLineEdit(str(default_dir))
        browse_button = QPushButton("Choose...")
        browse_button.clicked.connect(self._on_browse)
        out_row = QHBoxLayout()
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(browse_button)

        self._run_notes_edit = QLineEdit(DEFAULT_RUN_NOTES_FILENAME)
        self._run_notes_edit.setToolTip(
            "A plain-text summary of this run's settings, written into the "
            "output folder alongside the .s2p files and manifest.csv."
        )

        out_form = QFormLayout()
        out_form.addRow("Folder:", out_row)
        out_form.addRow("Run notes file:", self._run_notes_edit)
        out_group.setLayout(out_form)

        # Controls
        self._start_button = QPushButton("Start Scan")
        self._start_button.clicked.connect(self._on_start)
        self._abort_button = QPushButton("Abort")
        self._abort_button.setEnabled(False)
        self._abort_button.clicked.connect(self._on_abort)
        button_row = QHBoxLayout()
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._abort_button)

        self._progress_bar = QProgressBar()

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(freq_group)
        layout.addWidget(scan_group)
        layout.addWidget(port_group)
        layout.addWidget(out_group)
        layout.addLayout(button_row)
        layout.addWidget(self._progress_bar)
        layout.addWidget(QLabel("Live log:"))
        layout.addWidget(self._log_view, stretch=1)

    # ---- actions ---------------------------------------------------

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self._out_dir_edit.text()
        )
        if chosen:
            self._out_dir_edit.setText(chosen)

    def _on_start(self) -> None:
        start_hz = self._start_ghz.value() * 1e9
        stop_hz = self._stop_ghz.value() * 1e9
        if stop_hz <= start_hz:
            self._append_log("Stop frequency must exceed start frequency.")
            return

        project_name = self._project_name_edit.text().strip()
        if not project_name:
            self._append_log(
                "Project name cannot be blank — it becomes the .s2p output "
                "folder name, and an empty value crashes the VNA save step."
            )
            return

        scan_type = SCAN_TYPES[self._type_box.currentText()]
        config = ScanConfig(
            f_start_hz=start_hz,
            f_stop_hz=stop_hz,
            num_points=self._points.value(),
            ifbw_hz=self._ifbw.value(),
            scan_type=scan_type,
            azimuth_points=self._az_points.value(),
            output_dir=Path(self._out_dir_edit.text()),
            azimuth_com_port=self._azimuth_com_edit.text(),
            elevation_com_port=self._elevation_com_edit.text(),
            run_notes_filename=self._run_notes_edit.text(),
            project_name=project_name,
        )

        total = len(build_expected_manifest(scan_type, config.azimuth_points))
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(total)
        self._log_view.clear()
        self._set_controls_enabled(False)
        self._controller.start(config)

    def _on_abort(self) -> None:
        self._controller.abort()

    def _append_log(self, message: str) -> None:
        self._log_view.appendPlainText(message)

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _on_finished(self) -> None:
        self._append_log("=== Scan finished ===")
        self._set_controls_enabled(True)

    def _on_failed(self, message: str) -> None:
        self._append_log(f"=== Scan ended: {message} ===")
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._start_button.setEnabled(enabled)
        self._abort_button.setEnabled(not enabled)
        for widget in (
            self._start_ghz, self._stop_ghz, self._points, self._ifbw,
            self._type_box, self._az_points, self._out_dir_edit,
            self._azimuth_com_edit, self._elevation_com_edit, self._run_notes_edit,
            self._project_name_edit,
        ):
            widget.setEnabled(enabled)

    def closeEvent(self, event) -> None:
        if self._controller.is_running():
            self._controller.abort()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    panel = ScanPanel()
    panel.show()
    return app.exec()


if __name__ == "__main__":
    mp.freeze_support()  # for multiprocessing on Windows or macOS spawn
    sys.exit(main())

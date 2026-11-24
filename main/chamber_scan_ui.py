"""
Chamber control screen connected directly to the real hardware modules:
    - anritsu_vectorstar_vna_interface.py: setup_vna(), sweep_and_save(), shutdown()
    - Motor_scan_A_plane.py:                planer_scan()

    Scan runs in a subprocess and not a QThread because as soon as the hardware modules
    are imported, they connect to the real equipment (VNA and motors).

    How this file lines up with the hardware modules
    ------------------------------------------------
    1. Progress. scan_AUT_CO_Only() sets its internal point count to azimuth_points * 4 instead
       of * 2, so the motor script's own log caps at 50% for ECO/HCO scans. The GUI ignores that
       and tracks true progress with build_expected_manifest().

    2. Output folder. take_sample() calls sweep_and_save() without an out_dir, so files go to the
       default relative folder "s2p_data". The worker subprocess does os.chdir(output_dir) just
       before importing the hardware modules, so the files land in <output_dir>/s2p_data/.

    3. File names. The VNA module saves  <project>_ELV_<x>_AZI_<y>_<N>.s2p  where N is a
       per-run counter (1, 2, 3 ...). The motor code labels ELV with the boom angle on the up
       passes and with (180 - boom) on the down passes, and AZI with the AUT rotation.
       build_expected_manifest() reproduces that label, so manifest.csv holds the real file
       name, and the physical boom angle in boom_deg. The live plot finds each file by its
       counter N, and a warning is logged if the file found differs from the expected name
       (which means the motor sequence no longer matches the manifest).
       Ideal future fix: a callback from the motor script on every capture.

    4. Abort. process.terminate() is a hard kill on Windows (no signal handler ever runs), so
       Abort sets a multiprocessing.Event instead. A watcher thread in the worker turns it into
       KeyboardInterrupt in the main thread, which triggers the except KeyboardInterrupt: blocks
       in the motor code (stop motion), after which planer_scan() homes and closes the axes.
       Stage 3 (hard kill) still applies if that takes longer than abort_grace_s (20 s).
       Operator takeaway: after aborting, wait for physical motion to stop. After a hard kill,
       inspect the chamber manually before running another scan.

    5. Ports and project name. planer_scan(com1=ELEVATION port, com2=AZIMUTH port,
       project_name_global=...). That is the opposite pairing from this file's field names, so
       the swap is done once at the call site in _run_scan_process(). project_name becomes part
       of every .s2p file name, so it is sanitised for Windows and may not be blank.

    6. Finish sound. finish_scanning() plays "./supplementary/finish_audio.wav", a relative
       path that breaks after the os.chdir() in item 2. The worker wraps winsound.PlaySound so
       the path is resolved against the folder the GUI was launched from, and a missing sound
       file only logs a warning instead of skipping the final homing.

    7. Result reporting. The worker sends ("done", ok) when it finishes, so import/connect
       failures and scan exceptions are shown as failures rather than "Scan finished".
"""
from __future__ import annotations

import _thread
import csv
import datetime
import glob
import logging
import logging.handlers
import multiprocessing as mp
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
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

# Live plotting needs pyqtgraph (pip install pyqtgraph). If it is missing the
# scan still works; the plot area just shows an install hint instead.
_LIVE_PLOT_ERROR = ""
try:
    from live_plot import LivePlotPanel, parse_s2p
except ImportError as _exc:
    LivePlotPanel = None
    parse_s2p = None
    _LIVE_PLOT_ERROR = str(_exc)

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
    "Co & Cross Pol": "E",
    "Co Pol": "ECO",
    # "E-plane Co & Cross": "E",
    # "E-plane Co only": "ECO",
    # "H-plane Co & Cross": "H",
    # "H-plane Co only": "HCO",
}


def safe_name(text: str) -> str:
    """Make text safe to use inside a Windows file or folder name
    (Windows forbids \\ / : * ? " < > | in file names)."""
    cleaned = "".join("_" if c in '\\/:*?"<>|' else c for c in text.strip())
    return cleaned or "project"


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
    # Passed to planer_scan(project_name_global=...), which hands it to
    # vna_interface.sweep_and_save(project_name=...). It becomes the start of
    # every .s2p file name, so it must be non-empty and filename-safe
    # (see items 3 and 5 in the module docstring).
    project_name: str = DEFAULT_PROJECT_NAME


# --------------------------------------------------------------------------
# Manifest replicates scan_AUT_CO_CROSS / scan_AUT_CO_Only's positioning
# sequence exactly, so each s2p file can be matched to the angles it was
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
            # Motor_scan_A_plane labels the file with pos on the way up and
            # (180 - pos) on the way down (take_sample(180-pos, azi_pos)).
            elv_label = float(boom_deg) if direction == "up" else 180 - float(boom_deg)
            manifest.append(
                {
                    "plane": plane,
                    "polarisation": pol,
                    "aut_rotation_deg": aut_deg,
                    "boom_deg": round(float(boom_deg), 4),
                    "motor_elv_label_deg": round(elv_label, 4),
                    "sweep_direction": direction,
                    # not written to the CSV: the exact "ELV_x_AZI_y" text the
                    # motor passes to sweep_and_save(file_name=...)
                    "_file_label": "ELV_{:.2f}_AZI_{:.2f}".format(elv_label, aut_deg),
                }
            )
    return manifest


# --------------------------------------------------------------------------
# Entry point of the subprocess:
# Opening the GUI should not affect the real equipment (motors and vna)
# Thus the hardware modules are only imported HERE, not at the top of file
# --------------------------------------------------------------------------

def _make_finish_sound_safe(launch_cwd: str) -> None:
    """finish_scanning() plays a RELATIVE path, which stops working after the
    os.chdir() into the output folder. Resolve it against the launch folder and
    never let a sound problem skip the motor code's final homing (item 6)."""
    try:
        import winsound
    except ImportError:
        return
    original = winsound.PlaySound

    def play(sound, flags):
        try:
            if isinstance(sound, str) and not os.path.isabs(sound):
                for base in (launch_cwd, os.path.dirname(os.path.abspath(__file__))):
                    candidate = os.path.join(base, sound)
                    if os.path.exists(candidate):
                        sound = candidate
                        break
                else:
                    logging.warning("Finish sound %s not found; skipping it.", sound)
                    return
            original(sound, flags)
        except Exception as exc:
            logging.warning("Could not play finish sound: %s", exc)

    winsound.PlaySound = play


def _run_scan_process(config: ScanConfig, log_queue: mp.Queue, stop_event) -> None:
    queue_handler = logging.handlers.QueueHandler(log_queue)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(queue_handler)

    # Abort path that also works on Windows, where process.terminate() is a hard
    # kill and no signal handler ever runs: the GUI sets stop_event, and this
    # watcher raises KeyboardInterrupt in the main thread, which the motor code's
    # "except KeyboardInterrupt" blocks turn into a motion stop + homing.
    def _watch_stop() -> None:
        stop_event.wait()
        _thread.interrupt_main()

    threading.Thread(target=_watch_stop, daemon=True).start()

    # On Linux/macOS terminate() sends SIGTERM; treat it the same way.
    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError):
        pass

    launch_cwd = os.getcwd()
    ok = False
    vna_interface = None
    try:
        # sweep_and_save()'s out_dir cannot be set through planer_scan's public
        # API, so change the process's own cwd instead (item 2).
        config.output_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(config.output_dir)
        _make_finish_sound_safe(launch_cwd)

        # com1 is the ELEVATION port and com2 the AZIMUTH port in the motor
        # code: the opposite of this GUI's field names, so swap here (item 5).
        logging.info(
            "Motor ports this run: elevation=COM%s (com1), azimuth=COM%s (com2).",
            config.elevation_com_port, config.azimuth_com_port,
        )

        try:
            import anritsu_vectorstar_vna_interface as vna_interface
            import Motor_scan_A_plane as motor_scan
        except Exception as exc:
            # Either module connects to its equipment on import; if one fails
            # the whole scan is abandoned.
            logging.error("Failed to import/connect to hardware: %s", exc)
            return

        vna_interface.setup_vna(
            f_start_hz=config.f_start_hz,
            f_stop_hz=config.f_stop_hz,
            num_points=config.num_points,
            ifbw_hz=config.ifbw_hz,
        )
        motor_scan.planer_scan(
            azimuth_points=config.azimuth_points,
            type=config.scan_type,
            com1=config.elevation_com_port,
            com2=config.azimuth_com_port,
            project_name_global=config.project_name,
        )
        if stop_event.is_set():
            logging.info("Scan aborted: motors were stopped and homed by the motor script.")
        else:
            logging.info("Scan complete.")
            ok = True
    except KeyboardInterrupt:
        logging.error(
            "Interrupted outside the motor script's stop handlers. "
            "Check the chamber physically before starting another scan."
        )
    except Exception as exc:
        logging.error("Scan failed: %s", exc)
        logging.error(
            "The motor script does not home after an error: check the chamber "
            "physically before starting another scan."
        )
    finally:
        if vna_interface is not None:
            try:
                vna_interface.shutdown()
            except (Exception, KeyboardInterrupt) as exc:
                logging.warning("VNA shutdown reported an error: %s", exc)
        log_queue.put(("done", ok))   # tells the GUI side that work is done


# --------------------------------------------------------------------------
# GUI-side controller. Owns the subprocess, drains the log queue on a
# timer, matches "Taking Sample..." lines against the expected manifest,
# and writes manifest.csv incrementally so a crash mid-run loses at most
# the point in flight.
# --------------------------------------------------------------------------

@dataclass
class _PendingPoint:
    """A measurement that has been announced in the log ("Taking Sample...")
    but whose .s2p file has not been read yet. The log line comes BEFORE the
    VNA sweep, so the file appears a little later."""
    row: dict
    last_size: int = -1      # file size at the previous check
    parse_failures: int = 0


class ScanController(QObject):
    log_line = Signal(str)
    progress = Signal(int, int)   # done, total
    point_logged = Signal(dict)
    point_data = Signal(dict, object)   # manifest row, S2PData read from its .s2p file
    finished = Signal()
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._process: mp.Process | None = None
        self._log_queue: mp.Queue | None = None
        self._stop_event = None
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._drain_queue)
        self._manifest: list[dict] = []
        self._manifest_index = 0
        self._manifest_file = None
        self._manifest_writer: csv.DictWriter | None = None
        # Graceful stop window before escalating to a hard kill. The motor
        # script stops the motors, then runs homing() and close_device() on
        # both axes. Tests can shrink it instead of waiting on real timing.
        self.abort_grace_s = 20
        self._aborting = False
        self._abort_deadline = 0.0
        self._dead_ticks = 0
        # live plotting: points waiting for their .s2p file, and where to look
        self._pending: list[_PendingPoint] = []
        self._s2p_dirs: list[Path] = []
        self._project_name = ""
        self._announced_s2p_dir = False
        self._warned_name_mismatch = False
        self._expected_points = 0   # sweep points per file; 0 = do not check

    def start(self, config: ScanConfig) -> None:
        self._prepare_run(config)

        self._log_queue = mp.Queue()
        self._stop_event = mp.Event()
        self._aborting = False
        self._dead_ticks = 0
        self._process = mp.Process(
            target=_run_scan_process,
            args=(config, self._log_queue, self._stop_event),
            daemon=True,
        )
        self._process.start()
        self._poll_timer.start()

    def _prepare_run(self, config: ScanConfig) -> None:
        """Everything start() does before launching the subprocess."""
        self._manifest = build_expected_manifest(config.scan_type, config.azimuth_points)
        self._manifest_index = 0

        config.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_run_notes(config)

        manifest_path = config.output_dir / "manifest.csv"
        is_new = not manifest_path.exists()
        self._manifest_file = open(manifest_path, "a", newline="")
        fieldnames = [
            "point_index", "s2p_filename", "plane", "polarisation",
            "aut_rotation_deg", "boom_deg", "motor_elv_label_deg", "sweep_direction",
        ]
        self._manifest_writer = csv.DictWriter(
            self._manifest_file, fieldnames=fieldnames, extrasaction="ignore"
        )
        if is_new:
            self._manifest_writer.writeheader()

        # The subprocess runs inside output_dir and the VNA module saves into
        # its default relative folder "s2p_data" (item 2).
        self._s2p_dirs = [config.output_dir / "s2p_data"]
        self._project_name = config.project_name
        self._pending = []
        self._announced_s2p_dir = False
        self._warned_name_mismatch = False
        self._expected_points = config.num_points

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
            "(start of every .s2p file name)",
            f"Output folder: {config.output_dir} (.s2p files are in the s2p_data subfolder)",
        ]
        notes_path = config.output_dir / config.run_notes_filename
        notes_path.write_text("\n".join(lines) + "\n")

    # ---- abort ---------------------------------------------------

    def abort(self) -> None:
        """Ask the worker to stop. Non-blocking: the poll timer watches for the
        process to finish and escalates to a hard kill after abort_grace_s."""
        if self._aborting or not self.is_running():
            return
        self._aborting = True
        self._abort_deadline = time.monotonic() + self.abort_grace_s
        self.log_line.emit(
            "Operator requested abort: stopping motors and homing. "
            "Wait for physical motion to stop."
        )
        self._stop_event.set()

    def abort_and_wait(self) -> None:
        """Blocking version, used when the window is closed mid-scan."""
        if not self.is_running():
            return
        self._aborting = True
        self._stop_event.set()
        self._process.join(timeout=self.abort_grace_s)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=5)
        self._poll_timer.stop()
        self._pending.clear()
        self._close_manifest()

    # ---- queue / progress ------------------------------------------

    def _drain_queue(self) -> None:
        if self._log_queue is None:
            return
        self._poll_pending()
        done_ok = None
        while True:
            try:
                record = self._log_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(record, tuple):          # ("done", ok)
                done_ok = bool(record[1])
                break
            message = record.getMessage()
            self.log_line.emit(message)
            if "Taking Sample" in message:
                self._record_point()

        if done_ok is not None:
            self._finish(done_ok)
            return

        if self._process is not None and not self._process.is_alive():
            # Died without sending "done" (crash, or hard kill). Wait one more
            # tick so a "done" still in flight is read first.
            self._dead_ticks += 1
            if self._dead_ticks >= 2:
                self._finish(False, "Scan process ended unexpectedly: see log")
            return
        self._dead_ticks = 0

        if self._aborting and time.monotonic() > self._abort_deadline:
            self.log_line.emit(
                "Process did not exit after the stop/home sequence; forcing a hard "
                "kill. Check the chamber physically before starting another scan."
            )
            self._process.kill()
            self._process.join(timeout=5)

    def _finish(self, ok: bool, message: str = "") -> None:
        self._poll_pending(final=True)
        self._pending.clear()
        self._poll_timer.stop()
        self._close_manifest()
        if self._process is not None:
            self._process.join(timeout=5)
        aborted = self._aborting
        self._aborting = False
        if aborted:
            self.failed.emit("Aborted by operator")
        elif ok:
            self.finished.emit()
        else:
            self.failed.emit(message or "Scan failed: see log")

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
            # same text the VNA module builds: <project>_<file_name>_<counter>.s2p
            "s2p_filename": f"{self._project_name}_{entry['_file_label']}_{self._manifest_index}.s2p",
            **entry,
        }
        if self._manifest_writer is not None:
            self._manifest_writer.writerow(row)
            self._manifest_file.flush()
        self.point_logged.emit(row)
        self.progress.emit(self._manifest_index, len(self._manifest))
        if parse_s2p is not None:
            self._pending.append(_PendingPoint(row))

    def _find_s2p(self, row: dict) -> Path | None:
        """Find a point's file: first by its exact expected name, then by its
        counter, so plotting still works if the angle labels differ."""
        for directory in self._s2p_dirs:
            exact = directory / row["s2p_filename"]
            if exact.exists():
                return exact
            if directory.is_dir():
                pattern = f"{glob.escape(self._project_name)}_*_{row['point_index']}.s2p"
                matches = sorted(directory.glob(pattern))
                if matches:
                    if not self._warned_name_mismatch:
                        self._warned_name_mismatch = True
                        self.log_line.emit(
                            f"WARNING: expected {row['s2p_filename']} but found "
                            f"{matches[0].name}. The motor sequence no longer matches the "
                            "manifest angles: check manifest.csv before using it."
                        )
                    return matches[0]
        return None

    def _poll_pending(self, final: bool = False) -> None:
        """Read the .s2p file of every announced point that has appeared, and
        hand the data to the live plot.

        A file is only read once its size has stopped changing between two
        checks, so a file that is still being written is never parsed half
        way. `final=True` (scan over) reads whatever exists without waiting,
        then reports any points that never produced a readable file."""
        if parse_s2p is None or not self._pending:
            return
        waiting: list[_PendingPoint] = []
        for item in self._pending:
            path = self._find_s2p(item.row)
            if path is None:
                waiting.append(item)
                continue
            try:
                size = path.stat().st_size
            except OSError:
                waiting.append(item)
                continue
            if size == 0 or (size != item.last_size and not final):
                item.last_size = size
                waiting.append(item)
                continue
            try:
                data = parse_s2p(path.read_text())
                if self._expected_points and data.freq_hz.size != self._expected_points:
                    raise ValueError(
                        f"has {data.freq_hz.size} points, expected {self._expected_points}"
                    )
            except (ValueError, OSError) as exc:
                item.parse_failures += 1
                if item.parse_failures >= 5 or final:
                    self.log_line.emit(f"Live plot: could not read {path.name}: {exc}")
                else:
                    item.last_size = -1   # wait for a fresh stable read
                    waiting.append(item)
                continue
            if not self._announced_s2p_dir:
                self.log_line.emit(f"Live plot: reading .s2p files from {path.parent}")
                self._announced_s2p_dir = True
            self.point_data.emit(item.row, data)
        self._pending = waiting
        if final and self._pending:
            names = ", ".join(p.row["s2p_filename"] for p in self._pending[:5])
            self.log_line.emit(
                f"Live plot: {len(self._pending)} point(s) had no .s2p file to plot "
                f"(first: {names}). Check where the VNA module saved them."
            )
            self._pending = []

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
        self.resize(1240, 740)

        self._controller = ScanController()
        self._controller.log_line.connect(self._append_log)
        self._controller.progress.connect(self._on_progress)
        self._controller.finished.connect(self._on_finished)
        self._controller.failed.connect(self._on_failed)

        if LivePlotPanel is not None:
            self._plot_panel = LivePlotPanel()
            self._controller.point_data.connect(self._plot_panel.add_point)
        else:
            self._plot_panel = QLabel(
                "Live plots are switched off because live_plot.py (or pyqtgraph) "
                "could not be imported:\n"
                f"{_LIVE_PLOT_ERROR}\n\n"
                "Keep live_plot.py next to this file and run:  pip install pyqtgraph"
            )
            self._plot_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
        # The default folder name starts with the project name and is rebuilt
        # whenever the project name changes, and again at Start, so every scan
        # gets a fresh folder. Once the operator types a folder or uses
        # "Choose...", that choice is left alone (_out_dir_is_auto = False).
        self._out_dir_is_auto = True
        self._out_dir_edit = QLineEdit(str(self._default_out_dir()))
        self._out_dir_edit.textEdited.connect(self._on_out_dir_edited)
        self._project_name_edit.textChanged.connect(self._refresh_auto_out_dir)
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

        # Controls and log on the left, live plots on the right
        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(freq_group)
        layout.addWidget(scan_group)
        layout.addWidget(port_group)
        layout.addWidget(out_group)
        layout.addLayout(button_row)
        layout.addWidget(self._progress_bar)
        layout.addWidget(QLabel("Live log:"))
        layout.addWidget(self._log_view, stretch=1)

        root = QHBoxLayout(self)
        root.addLayout(layout, stretch=2)
        root.addWidget(self._plot_panel, stretch=3)

    # ---- actions ---------------------------------------------------

    _safe_name = staticmethod(safe_name)

    def _default_out_dir(self) -> Path:
        name = self._safe_name(self._project_name_edit.text())
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path.cwd() / f"{name}_{stamp}"

    def _refresh_auto_out_dir(self) -> None:
        if self._out_dir_is_auto:
            self._out_dir_edit.setText(str(self._default_out_dir()))

    def _on_out_dir_edited(self, _text: str) -> None:
        # textEdited only fires for typing by the operator, not for setText()
        self._out_dir_is_auto = False

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self._out_dir_edit.text()
        )
        if chosen:
            self._out_dir_edit.setText(chosen)
            self._out_dir_is_auto = False

    def _on_start(self) -> None:
        start_hz = self._start_ghz.value() * 1e9
        stop_hz = self._stop_ghz.value() * 1e9
        if stop_hz <= start_hz:
            self._append_log("Stop frequency must exceed start frequency.")
            return

        project_name = self._project_name_edit.text().strip()
        if not project_name:
            self._append_log(
                "Project name cannot be blank: it is the start of every .s2p file name."
            )
            return
        safe_project = safe_name(project_name)
        if safe_project != project_name:
            self._append_log(
                f"Project name contains characters Windows does not allow in file names; "
                f"using '{safe_project}' instead."
            )
            project_name = safe_project

        self._refresh_auto_out_dir()  # new timestamp, so each scan gets its own folder

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
        if LivePlotPanel is not None:
            self._plot_panel.reset(start_hz, stop_hz)
        self._set_controls_enabled(False)
        self._controller.start(config)

    def _on_abort(self) -> None:
        self._abort_button.setEnabled(False)
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
            self._controller.abort_and_wait()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    panel = ScanPanel()
    panel.show()
    return app.exec()


if __name__ == "__main__":
    mp.freeze_support()  # for multiprocessing on Windows or macOS spawn
    sys.exit(main())

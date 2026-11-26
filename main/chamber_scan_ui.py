"""
Chamber control screen connected directly to the real hardware modules:
    - anritsu_vectorstar_vna_interface_2.py (or anritsu_vectorstar_vna_interface.py):
        setup_vna(), set_averaging(), preset_func(), sweep_and_save(), shutdown()
    - Motor_scan_A_plane_3.py (or Motor_scan_A_plane.py):
        planer_scan()

    Scan, homing, and VNA preset run in subprocesses rather than QThreads because
    importing the hardware modules immediately connects to the real equipment.

    How this file lines up with the hardware modules
    ------------------------------------------------
    1. Progress. scan_AUT_POL_Only() sets its internal point count to azimuth_points * 4
       instead of * 2. The GUI ignores that and tracks true progress with
       build_expected_manifest().

    2. Output folder. take_sample() calls sweep_and_save() without an out_dir, so files go to the
       default relative folder "s2p_data". The worker subprocess adds the script folder to
       sys.path and calls os.chdir(output_dir) before importing the hardware modules, so the
       files land in <output_dir>/s2p_data/.

    3. File names & angles. The VNA module saves  <project>_ELV_<x>_AZI_<y>_<N>.s2p  where N is
       a per-run counter (1, 2, 3 ...). Motor_scan_A_plane_3.py uses:
           CO      -> AUT 90 deg (up), 270 deg (down)
           CROSS   -> AUT 0 deg (up),  180 deg (down)
           COCROSS -> CO passes (90, 270) then CROSS passes (0, 180)
       build_expected_manifest() reproduces that sequence so manifest.csv and LivePlotPanel
       stay aligned with the hardware files.

    4. Abort (stop in place, NO homing). Abort sets a multiprocessing.Event. In the worker,
       _install_safe_abort_on_ximc() replaces libximc's blocking command_wait_for_stop() with a
       50 ms status poll and blocks any post-abort command_home() / command_move() /
       command_zero() call. Pressing Abort therefore halts the boom immediately mid-motion,
       skips the final homing() at the end of planer_scan(), and closes both COM ports cleanly.
       Stage 3 (hard kill) still applies if exit takes longer than abort_grace_s (20 s).

    5. Ports and project name. planer_scan(com1=ELEVATION port, com2=AZIMUTH port,
       project_name_global=...). That is the opposite pairing from this file's field names, so
       the swap is done once at the call site in _run_scan_process().

    6. Finish sound. finish_scanning() plays a relative path that breaks after os.chdir().
       The worker wraps winsound.PlaySound so the path is resolved against the launch/script
       folder and any playback error only logs a warning instead of interrupting cleanup.

    7. Post-scan compression. When enabled, ScanController._finish() zips <output_dir>/s2p_data/
       into <output_dir>/<project_name>_s2p_data.zip strictly AFTER _poll_pending(final=True)
       has read every point for the live plot, verifies the archive, and then removes the raw
       .s2p files.
"""
from __future__ import annotations

import _thread
import csv
import datetime
import glob
import importlib
import logging
import logging.handlers
import multiprocessing as mp
import os
import queue
import signal
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Live plotting needs pyqtgraph (pip install pyqtgraph). Try live_plot_2.py
# first, falling back to live_plot.py if renamed.
_LIVE_PLOT_ERROR = ""
LivePlotPanel = None
parse_s2p = None
for _plot_mod_name in ("live_plot_2", "live_plot"):
    try:
        _plot_mod = importlib.import_module(_plot_mod_name)
        LivePlotPanel = _plot_mod.LivePlotPanel
        parse_s2p = _plot_mod.parse_s2p
        _LIVE_PLOT_ERROR = ""
        break
    except ImportError as _exc:
        _LIVE_PLOT_ERROR = str(_exc)

# --------------------------------------------------------------------------
# Defaults for things the GUI can't query or validate automatically — motor
# COM ports, project name, frequency band, and motor settling sleep time.
# --------------------------------------------------------------------------

DEFAULT_START_GHZ = 70.0
DEFAULT_STOP_GHZ = 110.0
DEFAULT_AZIMUTH_COM_PORT = "13"
DEFAULT_ELEVATION_COM_PORT = "12"
DEFAULT_PROJECT_NAME = "project1"
DEFAULT_SLEEP_TIME_S = 1.0   # wobble_time_azimuth default (seconds)
DEFAULT_SLEEP_STEP_S = 0.2   # default up/down increment for the sleep time spinbox
DEFAULT_AVG_COUNT = 4        # default count shown when the user enables VNA averaging

# --------------------------------------------------------------------------
# Type of scan:
# User sets the E plane or H plane manually by positioning the antennas
# according to desired measurement. Motors do not encode that.
# --------------------------------------------------------------------------

SCAN_TYPES = {
    "Co pol only": "CO",
    "Cross Pol only": "CROSS",
    "CO and Cross": "COCROSS",
}

AVG_TYPES = {
    "Point": "point",
    "Sweep": "sweep",
}


def safe_name(text: str) -> str:
    """Make text safe to use inside a Windows file or folder name
    (Windows forbids \\ / : * ? " < > | in file names)."""
    cleaned = "".join("_" if c in '\\/:*?"<>|' else c for c in text.strip())
    return cleaned or "project"


@dataclass
class ScanConfig:
    f_start_hz: float      # starting frequency
    f_stop_hz: float       # stopping frequency
    num_points: int        # number of points
    ifbw_hz: float         # VNA intermediate frequency bandwidth in Hz
    scan_type: str         # 'CO' | 'CROSS' | 'COCROSS'
    azimuth_points: int    # number of angular positions per 90 degree boom sweep pass
    output_dir: Path       # output directory where .s2p and manifest.csv are saved
    wobble_time_azimuth: float = DEFAULT_SLEEP_TIME_S
    vna_averages: int = 1  # 1 = averaging OFF; 2..1024 = averaging ON
    vna_avg_type: str = "point"  # "point" | "sweep"
    compress_s2p: bool = True
    azimuth_com_port: str = DEFAULT_AZIMUTH_COM_PORT
    elevation_com_port: str = DEFAULT_ELEVATION_COM_PORT
    run_notes_filename: str = f"{DEFAULT_PROJECT_NAME}.txt"
    project_name: str = DEFAULT_PROJECT_NAME


# --------------------------------------------------------------------------
# Manifest replicates Motor_scan_A_plane_3.py's positioning sequence exactly,
# so each s2p file can be matched to the angles it was taken at.
# --------------------------------------------------------------------------

def build_expected_manifest(scan_type: str, azimuth_points: int) -> list[dict]:
    elev_positions = np.linspace(0, 90, azimuth_points)

    if scan_type in ("E", "ECO", "ECROSS"):
        plane = "E-plane"
    elif scan_type in ("H", "HCO", "HCROSS"):
        plane = "H-plane"
    else:
        plane = "Manual"

    # Matches Motor_scan_A_plane_3.py:
    #   CO      -> pos1=90, pos2=270
    #   CROSS   -> pos1=0,  pos2=180
    #   COCROSS -> pos1=90, pos2=270, pos3=0, pos4=180
    if scan_type == "COCROSS":
        passes = [
            (90, "co", elev_positions, "up"),
            (270, "co", elev_positions[::-1], "down"),
            (0, "cross", elev_positions, "up"),
            (180, "cross", elev_positions[::-1], "down"),
        ]
    elif scan_type == "CO":
        passes = [
            (90, "co", elev_positions, "up"),
            (270, "co", elev_positions[::-1], "down"),
        ]
    elif scan_type == "CROSS":
        passes = [
            (0, "cross", elev_positions, "up"),
            (180, "cross", elev_positions[::-1], "down"),
        ]
    elif scan_type in ("E", "H"):
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
    elif scan_type in ("ECROSS", "HCROSS"):
        passes = [
            (90, "cross", elev_positions, "up"),
            (270, "cross", elev_positions[::-1], "down"),
        ]
    else:
        raise ValueError(f"Unknown scan type: {scan_type!r}")

    manifest: list[dict] = []
    for aut_deg, pol, positions, direction in passes:
        for boom_deg in positions:
            elv_label = float(boom_deg) if direction == "up" else 180 - float(boom_deg)
            manifest.append(
                {
                    "plane": plane,
                    "polarisation": pol,
                    "aut_rotation_deg": aut_deg,
                    "boom_deg": round(float(boom_deg), 4),
                    "motor_elv_label_deg": round(elv_label, 4),
                    "sweep_direction": direction,
                    "_file_label": "ELV_{:.2f}_AZI_{:.2f}".format(elv_label, aut_deg),
                }
            )
    return manifest


# --------------------------------------------------------------------------
# Subprocess helpers: hardware imports, safe finish sound, and stop-in-place
# abort enforcement on libximc.
# --------------------------------------------------------------------------

def _ensure_script_dir_on_path() -> None:
    """Keep the folder holding chamber_ui_2.py on sys.path after os.chdir()."""
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)


def _import_vna_interface():
    """Import anritsu_vectorstar_vna_interface_2 (or the base name) and alias
    it under 'anritsu_vectorstar_vna_interface' so Motor_scan_A_plane_3.py
    reuses the exact same VNA session instead of opening a second connection."""
    _ensure_script_dir_on_path()
    last_exc = None
    for mod_name in ("anritsu_vectorstar_vna_interface_2", "anritsu_vectorstar_vna_interface"):
        try:
            mod = importlib.import_module(mod_name)
            sys.modules["anritsu_vectorstar_vna_interface"] = mod
            logging.info("Loaded VNA interface module: %s", mod_name)
            return mod
        except ModuleNotFoundError as exc:
            if exc.name == mod_name:
                last_exc = exc
                continue
            raise
    raise last_exc or ModuleNotFoundError("anritsu_vectorstar_vna_interface_2")


def _import_motor_scan():
    """Import Motor_scan_A_plane_3 (falling back to _2 or the base name)."""
    _ensure_script_dir_on_path()
    last_exc = None
    for mod_name in ("Motor_scan_A_plane_3", "Motor_scan_A_plane_2", "Motor_scan_A_plane"):
        try:
            mod = importlib.import_module(mod_name)
            logging.info("Loaded motor scan module: %s", mod_name)
            return mod
        except ModuleNotFoundError as exc:
            if exc.name == mod_name:
                last_exc = exc
                continue
            raise
    raise last_exc or ModuleNotFoundError("Motor_scan_A_plane_3")


def _setup_worker_logging_and_abort(log_queue: mp.Queue, stop_event) -> None:
    queue_handler = logging.handlers.QueueHandler(log_queue)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(queue_handler)

    def _watch_stop() -> None:
        stop_event.wait()
        _thread.interrupt_main()

    threading.Thread(target=_watch_stop, daemon=True).start()

    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError):
        pass


def _make_finish_sound_safe(launch_cwd: str) -> None:
    """finish_scanning() plays a relative path, which stops working after the
    os.chdir() into the output folder. Resolve it against the launch/script
    folder and never let a sound problem interrupt final homing (item 6)."""
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


def _stop_all_axes(tracked_axes: list) -> None:
    for axis in tracked_axes:
        if getattr(axis, "_is_open", False):
            try:
                axis.command_stop()
            except Exception as exc:
                logging.error("Could not stop axis %s: %s", getattr(axis, "_uri", ""), exc)


def _close_all_axes(tracked_axes: list) -> None:
    for axis in tracked_axes:
        if getattr(axis, "_is_open", False):
            try:
                axis.close_device()
            except Exception as exc:
                logging.warning(
                    "Error closing axis %s: %s", getattr(axis, "_uri", ""), exc
                )


def _install_safe_abort_on_ximc(stop_event) -> list:
    """Wrap libximc.highlevel.Axis inside this worker subprocess so Abort:
      1. Stops motors immediately mid-motion by polling get_status() every 50 ms
         instead of blocking inside C's command_wait_for_stop(), and
      2. Blocks any post-abort command_home(), command_zero(), or command_move()
         so the unconditional homing() at the end of planer_scan() never moves
         the arm after an abort.
    """
    import libximc.highlevel as ximc

    original_axis_cls = ximc.Axis
    mvcmd_running_mask = getattr(
        getattr(ximc, "MvcmdStatus", None), "MVCMD_RUNNING", 0x80
    )
    tracked_axes: list = []
    logged_skip_home = {"done": False}

    class SafeAbortAxis(original_axis_cls):
        def __init__(self, uri, *args, **kwargs):
            super().__init__(uri, *args, **kwargs)
            self._uri = uri
            self._is_open = False
            self._skipped_motion = False
            tracked_axes.append(self)

        def open_device(self, *args, **kwargs):
            res = super().open_device(*args, **kwargs)
            self._is_open = True
            return res

        def close_device(self, *args, **kwargs):
            if not self._is_open:
                return None
            try:
                return super().close_device(*args, **kwargs)
            finally:
                self._is_open = False

        def command_move(self, *args, **kwargs):
            if stop_event.is_set():
                _stop_all_axes(tracked_axes)
                raise KeyboardInterrupt
            self._skipped_motion = False
            return super().command_move(*args, **kwargs)

        def command_home(self, *args, **kwargs):
            if stop_event.is_set():
                self._skipped_motion = True
                if not logged_skip_home["done"]:
                    logged_skip_home["done"] = True
                    logging.warning(
                        "Abort active: skipping homing so the chamber arm stays "
                        "stopped in place."
                    )
                return None
            self._skipped_motion = False
            return super().command_home(*args, **kwargs)

        def command_zero(self, *args, **kwargs):
            if stop_event.is_set():
                return None
            return super().command_zero(*args, **kwargs)

        def command_wait_for_stop(self, refresh_interval_ms=100):
            if self._skipped_motion:
                return None
            poll_s = max(0.02, min(float(refresh_interval_ms) / 1000.0, 0.05))
            try:
                while True:
                    if stop_event.is_set():
                        _stop_all_axes(tracked_axes)
                        raise KeyboardInterrupt
                    status = self.get_status()
                    if (status.MvCmdSts & mvcmd_running_mask) == 0:
                        return None
                    time.sleep(poll_s)
            except KeyboardInterrupt:
                _stop_all_axes(tracked_axes)
                raise

    ximc.Axis = SafeAbortAxis
    return tracked_axes


# --------------------------------------------------------------------------
# Entry points of the subprocesses:
# Opening the GUI should not affect the real equipment (motors and VNA).
# Thus the hardware modules are only imported HERE, not at the top of file.
# --------------------------------------------------------------------------

def _run_scan_process(config: ScanConfig, log_queue: mp.Queue, stop_event) -> None:
    _setup_worker_logging_and_abort(log_queue, stop_event)

    launch_cwd = os.getcwd()
    ok = False
    vna_interface = None
    tracked_axes: list = []
    try:
        _ensure_script_dir_on_path()
        config.output_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(config.output_dir)
        _make_finish_sound_safe(launch_cwd)

        logging.info(
            "Motor ports this run: elevation=COM%s (com1), azimuth=COM%s (com2), "
            "sleep time=%.2f s.",
            config.elevation_com_port,
            config.azimuth_com_port,
            config.wobble_time_azimuth,
        )

        try:
            tracked_axes = _install_safe_abort_on_ximc(stop_event)
            vna_interface = _import_vna_interface()
            motor_scan = _import_motor_scan()
        except Exception as exc:
            logging.error("Failed to import/connect to hardware: %s", exc)
            return

        vna_interface.setup_vna(
            f_start_hz=config.f_start_hz,
            f_stop_hz=config.f_stop_hz,
            num_points=config.num_points,
            ifbw_hz=config.ifbw_hz,
        )

        if hasattr(vna_interface, "set_averaging"):
            vna_interface.set_averaging(
                num_averages=config.vna_averages,
                avg_type=config.vna_avg_type,
            )
            if config.vna_averages > 1:
                logging.info(
                    "VNA averaging enabled: %d (%s-by-%s).",
                    config.vna_averages,
                    config.vna_avg_type,
                    config.vna_avg_type,
                )
            else:
                logging.info("VNA averaging OFF (1 measurement per point).")
        elif config.vna_averages > 1:
            logging.error(
                "Averaging was requested (%d), but the VNA module "
                "does not define set_averaging().",
                config.vna_averages,
            )
            return

        motor_scan.planer_scan(
            azimuth_points=config.azimuth_points,
            type=config.scan_type,
            wobble_time_azimuth=config.wobble_time_azimuth,
            com1=config.elevation_com_port,
            com2=config.azimuth_com_port,
            project_name_global=config.project_name,
        )
        if stop_event.is_set():
            logging.info(
                "Scan aborted: motors stopped in place (homing skipped). "
                "Inspect the chamber physically before moving or homing."
            )
        else:
            logging.info("Scan complete.")
            ok = True
    except KeyboardInterrupt:
        _stop_all_axes(tracked_axes)
        if stop_event.is_set():
            logging.info(
                "Scan aborted: motors stopped in place (homing skipped). "
                "Inspect the chamber physically before moving or homing."
            )
        else:
            logging.error(
                "Interrupted unexpectedly. Motors were commanded to stop; "
                "inspect the chamber physically before starting another scan."
            )
    except Exception as exc:
        _stop_all_axes(tracked_axes)
        logging.error("Scan failed: %s", exc)
        logging.error(
            "Motors were not homed after the error: inspect the chamber "
            "physically before starting another scan."
        )
    finally:
        _close_all_axes(tracked_axes)
        if vna_interface is not None:
            try:
                vna_interface.shutdown()
            except (Exception, KeyboardInterrupt) as exc:
                logging.warning("VNA shutdown reported an error: %s", exc)
        log_queue.put(("done", ok))


def _run_home_process(
    elevation_com_port: str,
    azimuth_com_port: str,
    log_queue: mp.Queue,
    stop_event,
) -> None:
    """Home both axes directly via libximc without importing the VNA module,
    with immediate mid-motion abort support."""
    _setup_worker_logging_and_abort(log_queue, stop_event)

    ok = False
    tracked_axes: list = []
    try:
        logging.info(
            "Starting homing: elevation=COM%s (com1), azimuth=COM%s (com2).",
            elevation_com_port,
            azimuth_com_port,
        )
        try:
            tracked_axes = _install_safe_abort_on_ximc(stop_event)
            import libximc.highlevel as ximc
        except Exception as exc:
            logging.error("Failed to import libximc: %s", exc)
            return

        elev_uri = r"xi-com:\\.\COM" + elevation_com_port
        azi_uri = r"xi-com:\\.\COM" + azimuth_com_port

        azi_axis = ximc.Axis(azi_uri)
        elev_axis = ximc.Axis(elev_uri)
        azi_axis.open_device()
        elev_axis.open_device()

        logging.info("Homing Azimuth...")
        azi_axis.command_home()
        azi_axis.command_wait_for_stop(100)
        azi_axis.command_zero()
        if not stop_event.is_set():
            logging.info("Done Homing Azimuth.")

        logging.info("Homing Elevation...")
        elev_axis.command_home()
        elev_axis.command_wait_for_stop(100)
        elev_axis.command_zero()
        if not stop_event.is_set():
            logging.info("Done Homing Elevation.")
            ok = True
    except KeyboardInterrupt:
        _stop_all_axes(tracked_axes)
        logging.error("Homing aborted by operator. Motors stopped in place.")
    except Exception as exc:
        _stop_all_axes(tracked_axes)
        logging.error("Homing failed: %s", exc)
    finally:
        _close_all_axes(tracked_axes)
        log_queue.put(("done", ok))


def _run_preset_vna_process(log_queue: mp.Queue, stop_event) -> None:
    """Connect to the VNA in a subprocess and run preset_func()."""
    _setup_worker_logging_and_abort(log_queue, stop_event)

    ok = False
    vna_interface = None
    try:
        logging.info(
            "Connecting to VNA for preset (this may take up to 2 minutes)..."
        )
        try:
            vna_interface = _import_vna_interface()
        except Exception as exc:
            logging.error("Failed to import/connect to VNA: %s", exc)
            return

        if hasattr(vna_interface, "inst"):
            vna_interface.inst.write("LANG NATIVE")
        vna_interface.preset_func()
        logging.info("VNA preset complete.")
        ok = True
    except KeyboardInterrupt:
        logging.error("VNA preset aborted by operator.")
    except Exception as exc:
        logging.error("VNA preset failed: %s", exc)
    finally:
        if vna_interface is not None:
            try:
                vna_interface.shutdown()
            except (Exception, KeyboardInterrupt) as exc:
                logging.warning("VNA shutdown reported an error: %s", exc)
        log_queue.put(("done", ok))


# --------------------------------------------------------------------------
# GUI-side controller.
# --------------------------------------------------------------------------

@dataclass
class _PendingPoint:
    row: dict
    last_size: int = -1
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
        self.abort_grace_s = 20
        self._aborting = False
        self._abort_deadline = 0.0
        self._dead_ticks = 0
        # Live plotting & post-scan compression state
        self._pending: list[_PendingPoint] = []
        self._s2p_dirs: list[Path] = []
        self._output_dir: Path | None = None
        self._project_name = ""
        self._compress_s2p = False
        self._announced_s2p_dir = False
        self._warned_name_mismatch = False
        self._expected_points = 0

    def start(self, config: ScanConfig) -> None:
        self._prepare_run(config)
        self._launch_worker(_run_scan_process, (config,))

    def start_homing(self, elevation_com_port: str, azimuth_com_port: str) -> None:
        self._reset_non_scan_state()
        self._launch_worker(_run_home_process, (elevation_com_port, azimuth_com_port))

    def start_vna_preset(self) -> None:
        self._reset_non_scan_state()
        self._launch_worker(_run_preset_vna_process, ())

    def _reset_non_scan_state(self) -> None:
        self._manifest = []
        self._manifest_index = 0
        self._pending = []
        self._s2p_dirs = []
        self._output_dir = None
        self._compress_s2p = False

    def _launch_worker(self, target, extra_args: tuple) -> None:
        self._log_queue = mp.Queue()
        self._stop_event = mp.Event()
        self._aborting = False
        self._dead_ticks = 0
        self._process = mp.Process(
            target=target,
            args=(*extra_args, self._log_queue, self._stop_event),
            daemon=True,
        )
        self._process.start()
        self._poll_timer.start()

    def _prepare_run(self, config: ScanConfig) -> None:
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

        self._output_dir = config.output_dir
        self._s2p_dirs = [config.output_dir / "s2p_data"]
        self._project_name = config.project_name
        self._compress_s2p = config.compress_s2p
        self._pending = []
        self._announced_s2p_dir = False
        self._warned_name_mismatch = False
        self._expected_points = config.num_points

    def _write_run_notes(self, config: ScanConfig) -> None:
        if not config.run_notes_filename:
            return
        avg_desc = (
            f"ON ({config.vna_averages}, {config.vna_avg_type}-by-{config.vna_avg_type})"
            if config.vna_averages > 1
            else "OFF (1)"
        )
        s2p_storage = (
            f"Compressed to {config.project_name}_s2p_data.zip after scan"
            if config.compress_s2p
            else "Uncompressed in s2p_data subfolder"
        )
        lines = [
            f"Run started: {datetime.datetime.now().isoformat(timespec='seconds')}",
            f"Scan type: {config.scan_type}",
            f"Frequency: {config.f_start_hz/1e9:.3f}-{config.f_stop_hz/1e9:.3f} GHz, "
            f"{config.num_points} points, IF bandwidth {config.ifbw_hz:.0f} Hz",
            f"VNA averaging: {avg_desc}",
            f"Azimuth points per 90-degree pass: {config.azimuth_points}",
            f"Sleep time (wobble_time_azimuth): {config.wobble_time_azimuth:.2f} s",
            f"Expected total samples: {len(self._manifest)}",
            f"Azimuth COM port: COM{config.azimuth_com_port} (sent as com2)",
            f"Elevation COM port: COM{config.elevation_com_port} (sent as com1)",
            f"Project name: {config.project_name} (start of every .s2p file name)",
            f"Output folder: {config.output_dir} ({s2p_storage})",
        ]
        notes_path = config.output_dir / config.run_notes_filename
        notes_path.write_text("\n".join(lines) + "\n")

    def _compress_s2p_files(self) -> None:
        """Compress all .s2p files in <output_dir>/s2p_data/ into a verified
        .zip archive and delete the raw .s2p files once verification succeeds."""
        if self._output_dir is None:
            return
        s2p_dir = self._output_dir / "s2p_data"
        if not s2p_dir.is_dir():
            return
        files = sorted(s2p_dir.glob("*.s2p"))
        if not files:
            return

        zip_path = self._output_dir / f"{self._project_name}_s2p_data.zip"
        if zip_path.exists():
            stamp = datetime.datetime.now().strftime("%H%M%S")
            zip_path = self._output_dir / f"{self._project_name}_s2p_data_{stamp}.zip"
        tmp_path = zip_path.with_suffix(".zip.tmp")

        self.log_line.emit(f"Compressing {len(files)} .s2p file(s) to {zip_path.name}...")
        try:
            raw_bytes = sum(f.stat().st_size for f in files)
            with zipfile.ZipFile(
                tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as zf:
                for path in files:
                    zf.write(path, arcname=path.name)

            with zipfile.ZipFile(tmp_path, "r") as zf:
                bad_member = zf.testzip()
                if bad_member is not None:
                    raise zipfile.BadZipFile(f"CRC check failed for {bad_member}")
                if len(zf.namelist()) != len(files):
                    raise RuntimeError("Archive file count did not match source .s2p count")

            tmp_path.replace(zip_path)
            zip_bytes = zip_path.stat().st_size

            for path in files:
                path.unlink()
            try:
                s2p_dir.rmdir()
            except OSError:
                pass

            saved_pct = (1.0 - (zip_bytes / raw_bytes)) * 100.0 if raw_bytes else 0.0
            self.log_line.emit(
                f"Compressed {len(files)} .s2p file(s) -> {zip_path.name} "
                f"({raw_bytes / 1e6:.2f} MB -> {zip_bytes / 1e6:.2f} MB, "
                f"{saved_pct:.1f}% saved)."
            )
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            self.log_line.emit(
                f"WARNING: Could not compress .s2p files ({exc}). "
                f"Original files were left untouched in {s2p_dir}."
            )

    # ---- abort ---------------------------------------------------

    def abort(self) -> None:
        if self._aborting or not self.is_running():
            return
        self._aborting = True
        self._abort_deadline = time.monotonic() + self.abort_grace_s
        self.log_line.emit(
            "Operator requested abort: stopping motors in place (homing skipped). "
            "Wait for physical motion to stop and inspect the chamber."
        )
        self._stop_event.set()

    def abort_and_wait(self) -> None:
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
            self._dead_ticks += 1
            if self._dead_ticks >= 2:
                self._finish(False, "Process ended unexpectedly: see log")
            return
        self._dead_ticks = 0

        if self._aborting and time.monotonic() > self._abort_deadline:
            self.log_line.emit(
                "Process did not exit after the stop sequence; forcing a hard "
                "kill. Check the chamber physically before starting another run."
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

        if ok and not aborted and self._compress_s2p:
            self._compress_s2p_files()

        if aborted:
            self.failed.emit("Aborted by operator (motors stopped in place)")
        elif ok:
            self.finished.emit()
        else:
            self.failed.emit(message or "Failed: see log")

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
                    item.last_size = -1
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
        self.resize(1240, 780)

        self._current_action = "Scan"
        self._controller = ScanController()
        self._controller.log_line.connect(self._append_log)
        self._controller.progress.connect(self._on_progress)
        self._controller.finished.connect(self._on_finished)
        self._controller.failed.connect(self._on_failed)

        if LivePlotPanel is not None:
            self._plot_panel = LivePlotPanel()
            self._plot_panel.reset(DEFAULT_START_GHZ * 1e9, DEFAULT_STOP_GHZ * 1e9)
            self._controller.point_data.connect(self._plot_panel.add_point)
        else:
            self._plot_panel = QLabel(
                "Live plots are switched off because live_plot_2.py / live_plot.py "
                "(or pyqtgraph) could not be imported:\n"
                f"{_LIVE_PLOT_ERROR}\n\n"
                "Keep live_plot_2.py next to this file and run:  pip install pyqtgraph"
            )
            self._plot_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._build_ui()

    # ---- layout ---------------------------------------------------

    def _build_ui(self) -> None:
        title = QLabel("mmWave Antenna Measurement System")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")

        # Frequency sweep + VNA averaging -> setup_vna() & set_averaging()
        freq_group = QGroupBox("Frequency sweep & VNA")
        self._start_ghz = QDoubleSpinBox()
        self._start_ghz.setRange(0.07, 220.0)
        self._start_ghz.setDecimals(3)
        self._start_ghz.setValue(DEFAULT_START_GHZ)
        self._start_ghz.setSuffix(" GHz")

        self._stop_ghz = QDoubleSpinBox()
        self._stop_ghz.setRange(0.07, 220.0)
        self._stop_ghz.setDecimals(3)
        self._stop_ghz.setValue(DEFAULT_STOP_GHZ)
        self._stop_ghz.setSuffix(" GHz")

        self._points = QSpinBox()
        self._points.setRange(2, 20001)
        self._points.setValue(201)

        self._ifbw = QDoubleSpinBox()
        self._ifbw.setRange(1.0, 1_000_000.0)
        self._ifbw.setDecimals(0)
        self._ifbw.setValue(1000.0)
        self._ifbw.setSuffix(" Hz")

        self._avg_enable = QCheckBox("Enable")
        self._avg_enable.setToolTip(
            "Turn on VNA averaging (2-1024 measurements). "
            "When unchecked, averaging is explicitly turned OFF."
        )
        self._avg_count = QSpinBox()
        self._avg_count.setRange(2, 1024)
        self._avg_count.setValue(DEFAULT_AVG_COUNT)
        self._avg_count.setEnabled(False)
        self._avg_count.setToolTip("Number of measurements to average (2-1024).")

        self._avg_type = QComboBox()
        self._avg_type.addItems(list(AVG_TYPES.keys()))
        self._avg_type.setEnabled(False)
        self._avg_type.setToolTip(
            "Point (recommended): averages N samples at each frequency point in one trigger.\n"
            "Sweep: averages across N full sweeps (requires sweep_and_save to trigger N times)."
        )
        self._avg_enable.toggled.connect(self._on_avg_toggled)

        avg_row = QHBoxLayout()
        avg_row.addWidget(self._avg_enable)
        avg_row.addWidget(self._avg_count)
        avg_row.addWidget(self._avg_type)

        freq_form = QFormLayout()
        freq_form.addRow("Start:", self._start_ghz)
        freq_form.addRow("Stop:", self._stop_ghz)
        freq_form.addRow("Points:", self._points)
        freq_form.addRow("IF bandwidth:", self._ifbw)
        freq_form.addRow("Averaging:", avg_row)
        freq_group.setLayout(freq_form)

        # Scan type + resolution + sleep time -> planer_scan()
        scan_group = QGroupBox("Scan")
        self._type_box = QComboBox()
        self._type_box.addItems(list(SCAN_TYPES.keys()))
        self._type_box.setToolTip(
            "E vs H is a manual antenna-orientation choice. "
            "Co pol only sweeps AUT 90/270 deg, Cross Pol only sweeps 0/180 deg, "
            "and CO and Cross runs both."
        )
        self._az_points = QSpinBox()
        self._az_points.setRange(2, 500)
        self._az_points.setValue(9)
        self._az_points.setToolTip("Number of boom positions per 0-90 degree pass")

        self._sleep_time = QDoubleSpinBox()
        self._sleep_time.setRange(0.0, 300.0)
        self._sleep_time.setDecimals(2)
        self._sleep_time.setSingleStep(DEFAULT_SLEEP_STEP_S)
        self._sleep_time.setValue(DEFAULT_SLEEP_TIME_S)
        self._sleep_time.setSuffix(" s")
        self._sleep_time.setToolTip(
            "Settling delay after each boom movement before taking a VNA sample "
            "(passed to planer_scan() as wobble_time_azimuth)."
        )

        self._sleep_step = QDoubleSpinBox()
        self._sleep_step.setRange(0.01, 60.0)
        self._sleep_step.setDecimals(2)
        self._sleep_step.setSingleStep(0.1)
        self._sleep_step.setValue(DEFAULT_SLEEP_STEP_S)
        self._sleep_step.setSuffix(" s")
        self._sleep_step.setToolTip(
            "Increment/decrement step size used when adjusting Sleep time."
        )
        self._sleep_step.valueChanged.connect(self._sleep_time.setSingleStep)

        sleep_row = QHBoxLayout()
        sleep_row.addWidget(self._sleep_time, stretch=1)
        sleep_row.addWidget(QLabel("Step:"))
        sleep_row.addWidget(self._sleep_step)

        scan_form = QFormLayout()
        scan_form.addRow("Measurement:", self._type_box)
        scan_form.addRow("Points per 90 degree pass:", self._az_points)
        scan_form.addRow("Sleep time:", sleep_row)
        scan_group.setLayout(scan_form)

        # Motor COM ports -> planer_scan(com1=elevation, com2=azimuth)
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
            "Sent to planer_scan() as project_name_global, which becomes the "
            "prefix of every .s2p file and the run notes filename. Cannot be blank."
        )

        port_form = QFormLayout()
        port_form.addRow("Azimuth (COM):", self._azimuth_com_edit)
        port_form.addRow("Elevation (COM):", self._elevation_com_edit)
        port_form.addRow("Project name:", self._project_name_edit)
        port_group.setLayout(port_form)

        # Output folder, run notes, and post-scan .s2p compression
        out_group = QGroupBox("Output")
        self._out_dir_is_auto = True
        self._out_dir_edit = QLineEdit(str(self._default_out_dir()))
        self._out_dir_edit.textEdited.connect(self._on_out_dir_edited)
        self._browse_button = QPushButton("Choose...")
        self._browse_button.clicked.connect(self._on_browse)
        out_row = QHBoxLayout()
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(self._browse_button)

        self._run_notes_edit = QLineEdit(self._default_run_notes_filename())
        self._run_notes_edit.setReadOnly(True)
        self._run_notes_edit.setToolTip(
            "Automatically matches the project name (<project_name>.txt) and is "
            "saved in the output folder alongside manifest.csv and the .s2p data."
        )
        self._project_name_edit.textChanged.connect(self._on_project_name_changed)

        self._compress_s2p_box = QCheckBox("Compress .s2p files to .zip after scan")
        self._compress_s2p_box.setChecked(True)
        self._compress_s2p_box.setToolTip(
            "After all measurements finish and are plotted, compress s2p_data/*.s2p "
            "into <project_name>_s2p_data.zip, verify the archive, and remove the "
            "uncompressed .s2p files."
        )

        out_form = QFormLayout()
        out_form.addRow("Folder:", out_row)
        out_form.addRow("Run notes file:", self._run_notes_edit)
        out_form.addRow("", self._compress_s2p_box)
        out_group.setLayout(out_form)

        # Controls: primary scan row + utility row (homing & VNA preset)
        self._start_button = QPushButton("Start Scan")
        self._start_button.clicked.connect(self._on_start)
        self._abort_button = QPushButton("Abort")
        self._abort_button.setEnabled(False)
        self._abort_button.setToolTip(
            "Stop motors immediately in place without homing the boom."
        )
        self._abort_button.clicked.connect(self._on_abort)
        primary_buttons = QHBoxLayout()
        primary_buttons.addWidget(self._start_button)
        primary_buttons.addWidget(self._abort_button)

        self._home_button = QPushButton("Home Motors")
        self._home_button.setToolTip(
            "Home and zero both the azimuth and elevation motors using the COM ports above."
        )
        self._home_button.clicked.connect(self._on_home)
        self._preset_vna_button = QPushButton("Preset VNA")
        self._preset_vna_button.setToolTip(
            "Send :SYSTem:PRESet to the VectorStar VNA to return it to its preset state."
        )
        self._preset_vna_button.clicked.connect(self._on_preset_vna)
        utility_buttons = QHBoxLayout()
        utility_buttons.addWidget(self._home_button)
        utility_buttons.addWidget(self._preset_vna_button)

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
        layout.addLayout(primary_buttons)
        layout.addLayout(utility_buttons)
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

    def _default_run_notes_filename(self) -> str:
        return f"{self._safe_name(self._project_name_edit.text())}.txt"

    def _refresh_auto_out_dir(self) -> None:
        if self._out_dir_is_auto:
            self._out_dir_edit.setText(str(self._default_out_dir()))

    def _on_project_name_changed(self) -> None:
        self._refresh_auto_out_dir()
        self._run_notes_edit.setText(self._default_run_notes_filename())

    def _on_avg_toggled(self, checked: bool) -> None:
        self._avg_count.setEnabled(checked)
        self._avg_type.setEnabled(checked)

    def _on_out_dir_edited(self, _text: str) -> None:
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

        self._refresh_auto_out_dir()
        run_notes_filename = f"{project_name}.txt"
        self._run_notes_edit.setText(run_notes_filename)

        scan_type = SCAN_TYPES[self._type_box.currentText()]
        vna_averages = self._avg_count.value() if self._avg_enable.isChecked() else 1
        vna_avg_type = AVG_TYPES[self._avg_type.currentText()]

        config = ScanConfig(
            f_start_hz=start_hz,
            f_stop_hz=stop_hz,
            num_points=self._points.value(),
            ifbw_hz=self._ifbw.value(),
            scan_type=scan_type,
            azimuth_points=self._az_points.value(),
            wobble_time_azimuth=self._sleep_time.value(),
            vna_averages=vna_averages,
            vna_avg_type=vna_avg_type,
            compress_s2p=self._compress_s2p_box.isChecked(),
            output_dir=Path(self._out_dir_edit.text()),
            azimuth_com_port=self._azimuth_com_edit.text(),
            elevation_com_port=self._elevation_com_edit.text(),
            run_notes_filename=run_notes_filename,
            project_name=project_name,
        )

        self._current_action = "Scan"
        total = len(build_expected_manifest(scan_type, config.azimuth_points))
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(0)
        self._log_view.clear()
        if LivePlotPanel is not None:
            self._plot_panel.reset(start_hz, stop_hz)
        self._set_controls_enabled(False)
        self._controller.start(config)

    def _on_home(self) -> None:
        azimuth_com = self._azimuth_com_edit.text().strip()
        elevation_com = self._elevation_com_edit.text().strip()
        if not azimuth_com or not elevation_com:
            self._append_log(
                "Both Azimuth and Elevation COM ports must be set before homing."
            )
            return

        self._current_action = "Homing"
        self._progress_bar.setRange(0, 0)
        self._log_view.clear()
        self._set_controls_enabled(False)
        self._controller.start_homing(elevation_com, azimuth_com)

    def _on_preset_vna(self) -> None:
        reply = QMessageBox.question(
            self,
            "Preset VNA",
            "Reset the VNA to its preset state?\n\n"
            "This clears current trace settings and may disable an active calibration.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._current_action = "VNA Preset"
        self._progress_bar.setRange(0, 0)
        self._log_view.clear()
        self._set_controls_enabled(False)
        self._controller.start_vna_preset()

    def _on_abort(self) -> None:
        self._abort_button.setEnabled(False)
        self._controller.abort()

    def _append_log(self, message: str) -> None:
        self._log_view.appendPlainText(message)

    def _on_progress(self, done: int, total: int) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(done)

    def _on_finished(self) -> None:
        if self._current_action != "Scan":
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(1)
        self._append_log(f"=== {self._current_action} finished ===")
        self._set_controls_enabled(True)

    def _on_failed(self, message: str) -> None:
        if self._current_action != "Scan":
            self._progress_bar.setRange(0, 1)
            self._progress_bar.setValue(0)
        self._append_log(f"=== {self._current_action} ended: {message} ===")
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._start_button.setEnabled(enabled)
        self._home_button.setEnabled(enabled)
        self._preset_vna_button.setEnabled(enabled)
        self._abort_button.setEnabled(not enabled)
        for widget in (
            self._start_ghz, self._stop_ghz, self._points, self._ifbw,
            self._avg_enable,
            self._type_box, self._az_points, self._sleep_time, self._sleep_step,
            self._out_dir_edit, self._browse_button, self._compress_s2p_box,
            self._azimuth_com_edit, self._elevation_com_edit,
            self._project_name_edit,
        ):
            widget.setEnabled(enabled)
        avg_active = enabled and self._avg_enable.isChecked()
        self._avg_count.setEnabled(avg_active)
        self._avg_type.setEnabled(avg_active)

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
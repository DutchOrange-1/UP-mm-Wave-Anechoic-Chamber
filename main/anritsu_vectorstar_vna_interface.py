
# Code written by Thelma Kwedza , 19/09/2026
# Code  covers the connection between the VNA(VectorStar MS46470A Series) and the PC .
# Code supports a sinfle frequency sweep at a time and time, averaging, and instrument preset.
import os
import pyvisa

_BASE_TIMEOUT_MS = 30000

# Connection

# rm = pyvisa.ResourceManager()
rm = pyvisa.ResourceManager("@py")
print(rm.list_resources())
inst = rm.open_resource('TCPIP::10.0.0.2::INSTR')
inst.read_termination = '\n'
inst.write_termination = '\n'
inst.timeout = _BASE_TIMEOUT_MS
print(inst.query("*IDN?"))

CHANNEL = 1
SPARAM_LIST = ["S11", "S21", "S12", "S22"]
OUTPUT_DIR = "s2p_data"

_point_counter = 0
_num_averages = 1
_avg_type = "point"

# check_and_report_errors

def check_and_report_errors(label=""):
    errors = []
    for _ in range(20):
        err = inst.query("SYST:ERR?").strip()
        if err.startswith("0,") or err.startswith("+0,"):
            break
        errors.append(err)
    tag = f" [{label}]" if label else ""
    if errors:
        print(f"*** VNA ERROR QUEUE{tag}: {errors} ***")
    else:
        print(f"  Error queue clean{tag}.")
    return errors


def _ask_frequency_sweep_parameters_for_manual_test():
    def ask_float(prompt, default):
        raw = input(f"{prompt} [{default}]: ").strip()
        return float(raw) if raw else default

    def ask_int(prompt, default):
        raw = input(f"{prompt} [{default}]: ").strip()
        return int(raw) if raw else default

    print("--- Frequency sweep setup (manual test only) ---")
    f_start = ask_float("Start frequency (Hz)", 50.0e9)
    f_stop = ask_float("Stop frequency (Hz)", 60.0e9)
    if f_stop <= f_start:
        raise ValueError(
            "Stop frequency must be greater than start frequency.")

    mode = input(
        "Define sweep by (p)oints or (s)tep? [p]: ").strip().lower() or "p"
    if mode == "s":
        f_step = ask_float("Frequency step (Hz)", 50.0e6)
        num_points = int(round((f_stop - f_start) / f_step)) + 1
    else:
        num_points = ask_int("Number of sweep points", 201)

    ifbw = ask_float(
        "IF bandwidth (Hz, leave as instrument default if unsure)", 1000.0)

    return {"f_start": f_start, "f_stop": f_stop, "num_points": num_points, "ifbw": ifbw}

#Preset VNA: returns the VectorStar to its preset state
def preset_func():
    old_timeout = inst.timeout
    inst.timeout = 120000            # 2 minutes, a preset is slow
    try:
        inst.write(":SYSTem:PRESet")
        inst.query("*OPC?")          # blocks until the preset is finished
    finally:
        inst.timeout = old_timeout   # restore even if something fails
    check_and_report_errors("after preset")

# VNA Setup - Call the funtion once before the motion is called

def setup_vna(f_start_hz, f_stop_hz, num_points, ifbw_hz=1000.0):
    inst.clear()
    inst.write("LANG NATIVE")
    inst.write(":SYSTem:ERRor:CLEar")
    inst.write(f":SENSe{CHANNEL}:FREQuency:STARt {f_start_hz:.6f}")
    inst.write(f":SENSe{CHANNEL}:FREQuency:STOP {f_stop_hz:.6f}")
    inst.write(f":SENSe{CHANNEL}:SWEep:POINt {num_points}")
    inst.write(f":SENSe{CHANNEL}:BWIDth {ifbw_hz:.1f}")
    inst.write(":FORMat:DATA REAL")
    inst.write(":FORMat:BORDer SWAPped")
    check_and_report_errors("after frequency/points/IFBW/format setup")

    inst.write(f":CALCulate{CHANNEL}:PARameter:COUNt {len(SPARAM_LIST)}")
    for n, sparam in enumerate(SPARAM_LIST, start=1):
        inst.write(f":CALCulate{CHANNEL}:PARameter{n}:DEFine {sparam}")
        inst.write(f":CALCulate{CHANNEL}:PARameter{n}:FORMat REIMaginary")
    check_and_report_errors("after defining S11/S21/S12/S22 traces")

  # format of the .s2p file
    inst.write(":FORM:SNP:FREQ HZ")
    inst.write(":FORM:SNP:PAR REIM")
    check_and_report_errors("after setting S2P output format (Hz, Real/Imag)")

    print(f"VNA configured: {f_start_hz/1e9:.3f}-{f_stop_hz/1e9:.3f} GHz, "
          f"{num_points} points, S11/S21/S12/S22, ready for sweeps.")

# Averaging - call once after setup_vna() and before the motion is called.
#   num_averages : 1 turns averaging off; 2-1024 turns it on with that count.
#   avg_type     : "point" (recommended: one trigger per position still gives
#                  the finished average) or "sweep" (N full sweeps are run
#                  and averaged, so sweep_and_save() triggers N times).

# Averaging - call once after setup_vna() and before the motion is called.
# This is the function a UI calls with whatever number the user picked.
#   num_averages : 1 turns averaging off; 2-1024 turns it on with that count.
#   avg_type     : "point" (recommended: one trigger per position still gives
#                  the finished average) or "sweep" (N full sweeps are run
#                  and averaged, so sweep_and_save() triggers N times).

def set_averaging(num_averages=1, avg_type="point"):
    global _num_averages, _avg_type

    try:
        n = int(float(num_averages))
        if n != float(num_averages):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(
            f"num_averages must be a whole number between 1 and 1024, "
            f"got {num_averages!r} instead.")
    if not 1 <= n <= 1024:
        raise ValueError(
            f"num_averages must be between 1 and 1024 (the VNA's limit), "
            f"got {n}.")

    avg_type_norm = str(avg_type).strip().lower()
    if avg_type_norm not in ("point", "sweep"):
        raise ValueError(
            f"avg_type must be 'point' or 'sweep', got {avg_type!r} instead.")

    if n == 1:
        inst.write(f":SENSe{CHANNEL}:AVERage:STATe OFF")
    else:
        scpi_type = "POINt" if avg_type_norm == "point" else "SWEep"
        inst.write(f":SENSe{CHANNEL}:AVERage:TYPe {scpi_type}")
        inst.write(f":SENSe{CHANNEL}:AVERage:COUNt {n}")
        inst.write(f":SENSe{CHANNEL}:AVERage:STATe ON")
    check_and_report_errors("after averaging setup")

    # Read back what the instrument actually accepted, not what was sent.
    state = inst.query(f":SENSe{CHANNEL}:AVERage:STATe?").strip()
    state_on = state in ("1", "+1", "ON")
    if n == 1:
        if state_on:
            print("*** WARNING: averaging was asked to turn OFF but the "
                  "VNA reports it is still ON. ***")
    else:
        count_back = int(float(
            inst.query(f":SENSe{CHANNEL}:AVERage:COUNt?").strip()))
        if not state_on or count_back != n:
            print(f"*** WARNING: asked for {n} averages ON, VNA reports "
                  f"state={state!r}, count={count_back}. ***")

    _num_averages = n
    _avg_type = avg_type_norm

    # Point averaging makes ONE trigger take about N times as long, so the
    # VISA timeout has to grow with it. Sweep averaging triggers one sweep
    # at a time, so the base timeout is enough.
    if n > 1 and avg_type_norm == "point":
        inst.timeout = min(_BASE_TIMEOUT_MS * n, 600000)
    else:
        inst.timeout = _BASE_TIMEOUT_MS

    if n == 1:
        print("Averaging OFF (single measurement per point).")
    else:
        print(f"Averaging ON: {n} measurements, {avg_type_norm}-by-"
              f"{avg_type_norm}.")

def _read_arbitrary_block_text():
    first = inst.read_bytes(1)
    if first != b'#':
        raise ValueError(
            f"Expected a '#' block header from OS2P, got {first!r} instead - "
            f"the response format was not what was expected."
        )
    ndigits = int(inst.read_bytes(1))
    count_str = inst.read_bytes(ndigits).decode("ascii")
    nbytes = int(count_str)
    payload = inst.read_bytes(nbytes)
    try:
        inst.read_bytes(1)  # consume the trailing terminator, if present
    except Exception:
        pass
    return payload.decode("ascii", errors="replace")


# Function needed by Brandon's code

def sweep_and_save(project_name="prj_1", out_dir=OUTPUT_DIR, file_name="AZI_X_ELEV_X_CO_POL"):
    global _point_counter
    inst.write(":SENSe:HOLD:FUNCtion HOLD")
    inst.write(":TRIG:SING")   # blocks until the sweep completes

    inst.write("OS2P")
    s2p_text = _read_arbitrary_block_text()

    errs = check_and_report_errors("after OS2P")

    # --- Save as the incrementing PointX.s2p filename ---
    os.makedirs(out_dir, exist_ok=True)
    _point_counter += 1
    # filename = f"Point{_point_counter}.s2p"
    # name the file to be saved
    filename = f"{project_name}_{file_name}_{_point_counter}.s2p"
    path = os.path.join(out_dir, filename)
    with open(path, "w") as f:
        f.write(s2p_text)

    print(f"Saved {path}  ({len(s2p_text)} chars, "
          f"{s2p_text.count(chr(10))} lines)")
    if errs:
        print(f"  (note: {len(errs)} VNA error(s) reported during this sweep)")

    return path


# Cleanup - call once when the entire chamber run is completely finished

def shutdown():
    inst.write(":SENSe:HOLD:FUNCtion CONTinuous")
    inst.write("RTL")
    inst.close()


"""
if __name__ == "__main__":
    setup_vna(f_start_hz=71.0e9, f_stop_hz=76.0e9, num_points=11)

    for _ in range(3):
        # In the real system: motor moves, motor confirms settled, THEN:
        sweep_and_save()

    shutdown()
    """


# Code written by Thelma Kwedza , 19/09/2026
# Code  covers the connection between the VNA(VectorStar MS46470A Series) and the PC .
# Code supports a sinfle frequency sweep at a time and
import os
import pyvisa


# Connection

rm = pyvisa.ResourceManager()
print(rm.list_resources())
inst = rm.open_resource('TCPIP::10.0.0.2::INSTR')
inst.read_termination = '\n'
inst.write_termination = '\n'
inst.timeout = 30000
print(inst.query("*IDN?"))

CHANNEL = 1
SPARAM_LIST = ["S11", "S21", "S12", "S22"]
OUTPUT_DIR = "s2p_data"

_point_counter = 0


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

def sweep_and_save(out_dir=OUTPUT_DIR):
    global _point_counter
    inst.write(":SENSe:HOLD:FUNCtion HOLD")
    inst.write(":TRIG:SING")   # blocks until the sweep completes

    inst.write("OS2P")
    s2p_text = _read_arbitrary_block_text()

    errs = check_and_report_errors("after OS2P")

    # --- Save as the incrementing PointX.s2p filename ---
    os.makedirs(out_dir, exist_ok=True)
    _point_counter += 1
    filename = f"Point{_point_counter}.s2p"
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

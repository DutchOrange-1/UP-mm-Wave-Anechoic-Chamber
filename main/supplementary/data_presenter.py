import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


def plot_CO_Pol_mag(data_path):
    """_summary_
        Assumes that the processed_XXX.txt exists in the data path.

    Args:
        data_path (_type_): _description_
    """
    txt_files = list(data_path.glob("processed_*.txt"))
    file = txt_files[0]

    print(f"Processing: \n--- {file.name} ---")

    df = pd.read_csv(file, sep="\t")

    freq = df["FREQ.HZ"].to_numpy()
    start_freq = df.iat[0, 0]
    # end_freq = df.iat[-1, 0]
    # print(f"Start freq: {start_freq}, and end freq: {end_freq}")

    df = df.loc[df['FREQ.HZ'] == start_freq]

    azi = df["AZI.deg"].to_numpy()
    S12 = (df["S12_RE"].to_numpy()+df["S12_IM"].to_numpy()*1j)
    # S21 = (df["S21_RE"].to_numpy()+df["S21_IM"].to_numpy()*1j)
    S12_mag = abs(S12)
    S12_phase = np.angle(S12)

    fig, (ax_mag, ax_phase) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))

    # Plot Magnitude
    ax_mag.plot(azi, S12_mag, color="C0", lw=2, label="Magnitude")
    ax_mag.set_ylabel("Magnitude (dB)")
    ax_mag.set_title("Magnitude and Phase vs. Azimuth")
    ax_mag.grid(True)
    ax_mag.legend(loc="upper right")

    # Plot Phase
    ax_phase.plot(azi, S12_phase, color="C1", lw=2, label="Phase")
    ax_phase.set_ylabel("Phase (degrees)")
    ax_phase.set_xlabel("Azimuth (degrees)")
    ax_phase.set_ylim(-180, 180)  # Standard phase wrap range
    ax_phase.grid(True)
    ax_phase.legend(loc="upper right")

    plt.tight_layout()
    plt.show()

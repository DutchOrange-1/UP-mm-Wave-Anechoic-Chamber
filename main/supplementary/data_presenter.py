import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


def plot_CO_Pol_mag(data_path, target_freq):
    """_summary_
        Assumes that the processed_XXX.txt exists in the data path.

    Args:
        data_path (_type_): _description_
    """
    txt_files = list(data_path.glob("processed_*.txt"))
    file = txt_files[0]

    print(f"Processing Co Pol mag : --- {file.name} ---")

    df = pd.read_csv(file, sep="\t")
    df = df[df["ELV.deg"].isin([0, 180])]

    # end_freq = df.iat[-1, 0]
    # print(f"Start freq: {start_freq}, and end freq: {end_freq}")

    df = df.loc[df['FREQ.HZ'] == target_freq]

    azi = df["AZI.deg"].to_numpy()
    S12 = (df["S12_RE"].to_numpy()+df["S12_IM"].to_numpy()*1j)
    S21 = (df["S21_RE"].to_numpy()+df["S21_IM"].to_numpy()*1j)
    S12_mag = 20*np.log10(abs(S12))
    S21_mag = 20*np.log10(abs(S21))

    S12_phase = np.angle(S12)
    S21_phase = np.angle(S21)

    fig, (ax_mag, ax_phase) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))

    # Plot Magnitude
    ax_mag.plot(azi, S12_mag, color="C0", lw=2, label="Magnitude S12")
    ax_mag.plot(azi, S21_mag, color="C2", lw=2, label="Magnitude S21")
    ax_mag.set_ylabel(" Magnitude (dB)")
    ax_mag.set_title(
        f"Magnitude and Phase vs. Azimuth - Data points: {len(S21_phase)} ")
    ax_mag.grid(True)
    ax_mag.legend(loc="upper right")

    # Plot Phase
    ax_phase.plot(azi, S12_phase, color="C1", lw=2, label="Phase S12")
    ax_phase.plot(azi, S21_phase, color="C3", lw=2, label="Phase S21")
    ax_phase.set_ylabel("Phase (degrees)")
    ax_phase.set_xlabel("Azimuth (degrees)")
    # ax_phase.set_ylim(-180, 180)  # Standard phase wrap range
    ax_phase.grid(True)
    ax_phase.legend(loc="upper right")

    # plt.title(f"Data points:{len(S21_phase)} ")
    plt.tight_layout()
    plt.show()


def plot_Cross_Pol_mag(data_path):
    """_summary_
        Assumes that the processed_XXX.txt exists in the data path.

    Args:
        data_path (_type_): _description_
    """
    txt_files = list(data_path.glob("processed_*.txt"))
    file = txt_files[0]

    print(f"Processing Cross Pol mag : --- {file.name} ---")

    df = pd.read_csv(file, sep="\t")
    df = df[df["ELV.deg"].isin([90, 270])]

    start_freq = df.iat[0, 0]
    # end_freq = df.iat[-1, 0]
    # print(f"Start freq: {start_freq}, and end freq: {end_freq}")

    df = df.loc[df['FREQ.HZ'] == start_freq]

    azi = df["AZI.deg"].to_numpy()
    S12 = (df["S12_RE"].to_numpy()+df["S12_IM"].to_numpy()*1j)
    S21 = (df["S21_RE"].to_numpy()+df["S21_IM"].to_numpy()*1j)
    S12_mag = 20*np.log10(abs(S12))
    S21_mag = 20*np.log10(abs(S21))

    S12_phase = np.angle(S12)
    S21_phase = np.angle(S21)

    fig, (ax_mag, ax_phase) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))

    # Plot Magnitude
    ax_mag.plot(azi, S12_mag, color="C0", lw=2, label="Magnitude S12")
    ax_mag.plot(azi, S21_mag, color="C2", lw=2, label="Magnitude S21")
    ax_mag.set_ylabel(" Magnitude (dB)")
    ax_mag.set_title(
        f"Magnitude and Phase vs. Azimuth - Data points: {len(S21_phase)} ")
    ax_mag.grid(True)
    ax_mag.legend(loc="upper right")

    # Plot Phase
    ax_phase.plot(azi, S12_phase, color="C1", lw=2, label="Phase S12")
    ax_phase.plot(azi, S21_phase, color="C3", lw=2, label="Phase S21")
    ax_phase.set_ylabel("Phase (degrees)")
    ax_phase.set_xlabel("Azimuth (degrees)")
    # ax_phase.set_ylim(-180, 180)  # Standard phase wrap range
    ax_phase.grid(True)
    ax_phase.legend(loc="upper right")

    # plt.title(f"Data points:{len(S21_phase)} ")
    plt.tight_layout()
    plt.show()


def plot_CO_Pol_Heat_map(data_path):
    """_summary_
    Generates the heat map of the data. Frequncy y , azimuth x and the heat map is the gain.  
    """
    txt_files = list(data_path.glob("processed_*.txt"))
    file = txt_files[0]

    print(f"Processing Co Pol heat map: --- {file.name} ---")
    try:
        print("Running Plot")
        df = pd.read_csv(file, sep="\t")
        #  Extract CO Pole
        df = df[df["ELV.deg"].isin([0, 180])]

        # Calculate gain from S21
        S21 = df["S21_RE"] + 1j * df["S21_IM"]
        # S21 = 50 * (1+S21) / (1-S21)
        df["Gain_dB"] = 20 * np.log10(np.abs(S21))

        # Convert frequency from Hz to GHz
        df["Frequency_GHz"] = df["FREQ.HZ"] / 1e9

        # Create 2D grid: frequency rows, azimuth columns
        heatmap_data = df.pivot_table(
            index="Frequency_GHz",
            columns="AZI.deg",
            values="Gain_dB"
        )

        # Sort axes
        heatmap_data = heatmap_data.sort_index().sort_index(axis=1)

        # Plot
        plt.figure(figsize=(12, 7))

        plt.pcolormesh(
            heatmap_data.columns,
            heatmap_data.index,
            heatmap_data.values,
            shading="auto",
            cmap="jet"
        )

        plt.colorbar(label="Gain (dB)")

        plt.xlabel("Azimuth (degrees)")
        plt.ylabel("Frequency (GHz)")
        plt.title(
            f"CO-POL Antenna Gain Radiation Pattern - Data points: {len(S21)} ")

        plt.tight_layout()
        plt.show()
    except ZeroDivisionError:
        print("ERRORR In PLOT")


def plot_Cross_Pol_Heat_map(data_path):
    """_summary_
    Generates the heat map of the data. Frequncy y , azimuth x and the heat map is the gain.  
    """
    txt_files = list(data_path.glob("processed_*.txt"))
    file = txt_files[0]

    print(f"Processing Cross Pol Heat Map: --- {file.name} ---")

    df = pd.read_csv(file, sep="\t")
    #  Extract CROSS Pole
    df = df[df["ELV.deg"].isin([0, 180])]

    # Calculate gain from S21
    S21 = df["S21_RE"] + 1j * df["S21_IM"]
    df["Gain_dB"] = 20 * np.log10(np.abs(S21))

    # Convert frequency from Hz to GHz
    df["Frequency_GHz"] = df["FREQ.HZ"] / 1e9

    # Create 2D grid: frequency rows, azimuth columns
    heatmap_data = df.pivot_table(
        index="Frequency_GHz",
        columns="AZI.deg",
        values="Gain_dB"
    )

    # Sort axes
    heatmap_data = heatmap_data.sort_index().sort_index(axis=1)

    # Plot
    plt.figure(figsize=(12, 7))

    plt.pcolormesh(
        heatmap_data.columns,
        heatmap_data.index,
        heatmap_data.values,
        shading="auto",
        cmap="jet"
    )

    plt.colorbar(label="Gain (dB)")

    plt.xlabel("Azimuth (degrees)")
    plt.ylabel("Frequency (GHz)")
    plt.title(
        f"Cross-POL Antenna Gain Radiation Pattern - Data points: {len(S21)} ")

    plt.tight_layout()
    plt.show()


def plot_S_phase(data_path, elevation_pos=[0, 180], freq=70e9):
    """_summary_
    Args:
        data_path (_type_): _description_
        port (str, optional): _description_. Defaults to 'S11'.
        position (int, optional): _description_. Defaults to 180.
    """

    txt_files = list(data_path.glob("processed_*.txt"))
    file = txt_files[0]

    print(f"Processing Phase Plot: --- {file.name} ---")
    try:
        print("Running Plot")
        df = pd.read_csv(file, sep="\t")

        if freq in df["FREQ.HZ"].values:
            print("Frequency found:", freq/10e9, "GHz")
        else:
            print("Frequency not found:", freq/10e9, "GHz")

        print("Checking for these positions on turntable/Elev: ", elevation_pos)
        #  Extract Data for one freq, and for one elevation
        df = df[df["ELV.deg"].isin(elevation_pos)]
        df = df[df["FREQ.HZ"].isin([freq])]

        S11 = df["S11_RE"] + 1j * df["S11_IM"]
        S22 = df["S22_RE"] + 1j * df["S22_IM"]

        # Convert frequency from Hz to GHz
        # df["Frequency_GHz"] = df["FREQ.HZ"] / 1e9
        azimuth = df["AZI.deg"]

        fig, ax1 = plt.subplots()

        ax2 = ax1.twinx()

        ax1.plot(df["AZI.deg"], np.unwrap(np.angle(S11, deg=True)),
                 'b-', label="S11 Phase")

        ax2.plot(df["AZI.deg"], np.unwrap(np.angle(S22, deg=True)),
                 'r-', label="S22 Phase")

        ax1.set_xlabel("Azimuth (degrees)")
        ax1.set_ylabel("S11 Phase (degrees)", color='b')
        ax2.set_ylabel("S22 Phase (degrees)", color='r')

        ax1.grid()

        plt.title("S11 and S22 Phase vs Azimuth")

        plt.show()

    except Exception as e:
        print(f"An error occurred: {e}")

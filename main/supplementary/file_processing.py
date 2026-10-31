import numpy as np
import re
import pandas as pd
from pathlib import Path


def extract_CO_POL(project_name, azimuth_points):
    """_summary_
    project_name: What project data to extract
    azimuth_points: What azimuth points to extract - as this will be used for the file compression. 
    """
    file_list, data_path = _get_files_path(project_name)
    print(data_path)

    # Extract the data needed from the manifest.
    # points = _extract_txt_info(data_path)
    # expected_data_points = np.linspace(0, 90, points)
    # expected_data_points = np.concatenate(
    #     (expected_data_points, (180-expected_data_points[::-1])))
    # expected_data_points = np.round(expected_data_points, 3)
    # print(expected_data_points)
    points, manifest_data = _extract_txt_manifest_info(data_path)

    filenames = manifest_data["s2p_filename"].to_numpy()
    # point_index = manifest_data["point_index"].to_numpy()
    aut_rotation = manifest_data["aut_rotation_deg"].to_numpy()
    boom_angle = manifest_data["boom_deg"].to_numpy()
    # sweep_direction = manifest_data["sweep_direction"].to_numpy()

    # print(manifest_data["s2p_filename"].to_numpy())
    # print(manifest_data["aut_rotation_deg"].to_numpy())
    # print(manifest_data["boom_deg"].to_numpy())

    master_data = _extract_measurements(file_list, data_path)
    print(master_data)


def _extract_measurements(file_list, data_path):
    data_path = Path(data_path)

    def extract_angles(file):
        filename = Path(file).name

        elv = float(re.search(
            r"ELV_(-?\d+(?:\.\d+)?)", filename
        ).group(1))

        azi = float(re.search(
            r"AZI_(-?\d+(?:\.\d+)?)", filename
        ).group(1))

        return round(elv, 3), azi

    # Sort by elevation, then azimuth
    sorted_files = sorted(file_list, key=extract_angles)

    master_data = []

    for file in sorted_files:
        filename = Path(file).name
        file_path = data_path / filename

        if not file_path.exists():
            raise FileNotFoundError(f"{file_path} not found.")

        elv, azi = extract_angles(filename)

        data = np.loadtxt(file_path, skiprows=6, comments=('!', '#'))

        freq = data[:, 0]

        S11 = data[:, 1] + 1j * data[:, 2]
        S21 = data[:, 3] + 1j * data[:, 4]
        S12 = data[:, 5] + 1j * data[:, 6]
        S22 = data[:, 7] + 1j * data[:, 8]

        output_data = np.column_stack((
            freq,
            np.full(len(freq), azi),
            np.full(len(freq), elv),
            S11.real, S11.imag,
            S12.real, S12.imag,
            S21.real, S21.imag,
            S22.real, S22.imag
        ))

        master_data.append(output_data)
    return np.vstack(master_data)


def _sort_measurement_files(file_list):

    def extract_angles(file):
        filename = Path(file).name

        elv = float(re.search(r"ELV_(-?\d+(?:\.\d+)?)", filename).group(1))
        azi = float(re.search(r"AZI_(-?\d+(?:\.\d+)?)", filename).group(1))

        return round(elv, 3), azi

    return sorted(file_list, key=extract_angles)


def _extract_txt_manifest_info(data_path):
    """Extracts the number of azimuth points from the txt file
    Args:
        data_path (_type_): Extracts the number of azimuth points from the txt file

    Raises:
        FileNotFoundError: _description_

    Returns:
        _type_: Number of Azimuth points
    """
    # Go back to the project directory
    project_path = data_path.parent

    # Find the only TXT file
    txt_files = list(project_path.glob("*.txt"))

    if len(txt_files) == 1:
        log_file = txt_files[0]
        # print(f"Log file: {log_file}")

        azimuth_points = _extract_azimuth_points(log_file)
        # print(f"Azimuth points per 90-degree pass: {azimuth_points}")

        # Manifest data:
        # Load manifest
        manifest = pd.read_csv(project_path / "manifest.csv")
        # Sort by acquisition index
        manifest = manifest.sort_values("point_index")
        # Extract filenames
        # filenames = manifest["s2p_filename"].to_numpy()

        return azimuth_points, manifest

    else:
        raise FileNotFoundError(
            f"Expected exactly one TXT file, found {len(txt_files)}"
        )


def _extract_azimuth_points(file_path):
    with open(file_path, "r") as file:
        content = file.read()

    match = re.search(
        r"Azimuth points per 90-degree pass:\s*(\d+)",
        content
    )

    return int(match.group(1)) if match else None


def _get_files_path(project_name):
    from pathlib import Path
    file_list = []

    search_path = Path(__file__).parent.parent

    data_paths = list(search_path.glob(f"*{project_name}*/s2p_data"))

    if data_paths:
        # Sort by directory name and select the most recent
        data_paths.sort()
        data_path = data_paths[-1]

        print(data_path)

        for file in data_path.iterdir():
            file_name = str(file).split("\\")[-1]
            # print(file_name)
            file_list.append(file_name)
        return file_list, data_path

    else:
        print("No matching project directories found.")


# # Create azimuth and elevation columns
# azi_array = np.full(len(freq), azi)
# elv_array = np.full(len(freq), elv)

# # Convert S-parameters to magnitude in dB
# S11_dB = 20 * np.log10(np.abs(S11))
# S12_dB = 20 * np.log10(np.abs(S12))
# S21_dB = 20 * np.log10(np.abs(S21))
# S22_dB = 20 * np.log10(np.abs(S22))

# # Combine into a single array
# output_data = np.column_stack((
#     freq,
#     azi_array,
#     elv_array,
#     S11_dB,
#     S12_dB,
#     S21_dB,
#     S22_dB
# ))

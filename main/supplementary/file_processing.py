import numpy as np
import re


def extract_CO_POL(project_name, azimuth_points):
    """_summary_
    project_name: What project data to extract
    azimuth_points: What azimuth points to extract - as this will be used for the file compression. 
    """
    print(_get_files_path(project_name))


def extract_elevation(files, elev):

    sorted_files = get_sorted_files(files, elev)
    master_data = []

    for file in sorted_files:
        data = np.loadtxt(file, skiprows=6, comments='!')

        freq = data[:, 0]
        azi = float(re.search(r"AZI_(-?\d+(?:\.\d+)?)", file.name).group(1))

        S11 = data[:, 1] + 1j * data[:, 2]
        S21 = data[:, 3] + 1j * data[:, 4]
        S12 = data[:, 5] + 1j * data[:, 6]
        S22 = data[:, 7] + 1j * data[:, 8]

        output_data = np.column_stack((
            freq,
            np.full(len(freq), azi),
            np.full(len(freq), elev),
            20*np.log10(np.abs(S11)),
            20*np.log10(np.abs(S12)),
            20*np.log10(np.abs(S21)),
            20*np.log10(np.abs(S22))
        ))

        master_data.append(output_data)

    return np.vstack(master_data)


def get_sorted_files(files, elev):
    filtered_files = [
        file for file in files
        if re.search(
            rf"ELEV_{elev}(?:_|\.|$).*AZI_",
            file
        )
    ]

    filtered_files.sort(
        key=lambda f: float(
            re.search(r"AZI_(-?\d+(?:\.\d+)?)", f).group(1)
        )
    )

    return filtered_files


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
            print(file_name)
            file_list.append(file_name)

    else:
        print("No matching project directories found.")

    return file_list

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

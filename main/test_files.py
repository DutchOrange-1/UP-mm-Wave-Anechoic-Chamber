import supplementary.file_processing as fp
import supplementary.data_presenter as dp
from pathlib import Path

# import Motor_scan_A_plane as mscan

data_path = fp.compress_data(
    "E_Plane_Test_V3_20261126_154357", "./")
dp.plot_CO_Pol_Heat_map(data_path)
dp.plot_Cross_Pol_Heat_map(data_path)
# dp.plot_CO_Pol_mag(data_path, 75e9)
# dp.plot_mag_at_freq(data_path, [90, 270])
# dp.plot_Cross_Pol_mag(data_path, 80e9)
# mscan.homing()
# mscan.planer_scan(type="CROSS", project_name_global="ManualTest")

#  Plot Phase
# data_path = Path("../main/old_data/FirstRun3_20261124_151302/")
# dp.plot_S_phase(data_path)

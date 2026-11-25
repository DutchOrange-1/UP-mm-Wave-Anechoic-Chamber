import supplementary.file_processing as fp
import supplementary.data_presenter as dp
import Motor_scan_A_plane as mscan

# This file, the wave guides where pressed together the whole time, and untouched.
# data_path = fp.compress_data("FirstRun3_20261124_151302")
# dp.plot_CO_Pol_mag(data_path)
# dp.plot_CO_Pol_Heat_map(data_path)

# data_path = fp.compress_data("FirstRun3_20261124_151302")
# dp.plot_CO_Pol_mag(data_path, 85e9)
# dp.plot_CO_Pol_Heat_map(data_path)
# dp.plot_Cross_Pol_Heat_map(data_path)
# mscan.homing()
mscan.planer_scan(type="CROSS", project_name_global="ManualTest")

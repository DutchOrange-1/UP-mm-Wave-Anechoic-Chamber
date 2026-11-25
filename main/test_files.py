import supplementary.file_processing as fp
import supplementary.data_presenter as dp
# import Motor_scan_A_plane as mscan

data_path = fp.compress_data("Cross_Plot_TEst_V1_20261125_130216")
# dp.plot_CO_Pol_Heat_map(data_path)
dp.plot_Cross_Pol_Heat_map(data_path)
# mscan.homing()
# mscan.planer_scan(type="CROSS", project_name_global="ManualTest")

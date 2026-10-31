import supplementary.file_processing as fp
import supplementary.data_presenter as dp

# This file, the wave guides where pressed together the whole time, and untouched.
# data_path = fp.compress_data("consitant")
# # dp.plot_CO_Pol_mag(data_path)
# dp.plot_CO_Pol_Heat_map(data_path)

data_path = fp.compress_data("EPlane_Only")
# dp.plot_CO_Pol_mag(data_path)
dp.plot_CO_Pol_Heat_map(data_path)
dp.plot_Cross_Pol_Heat_map(data_path)

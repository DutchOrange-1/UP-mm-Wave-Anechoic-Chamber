import supplementary.file_processing as fp
import supplementary.data_presenter as dp

data_path = fp.extract_CO_POL("consitant")
dp.plot_CO_Pol_mag(data_path)

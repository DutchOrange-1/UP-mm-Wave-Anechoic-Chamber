import skrf as rf
import matplotlib.pyplot as plt

file = "../main/old_data/FirstRun3_20261124_151302/s2p_data/FirstRun3_ELV_90.00_AZI_0.00_20.s2p"

ntwk = rf.Network(file)

# All S Parmaters
ntwk.plot_s_db()
plt.grid(True)
plt.show()

# VSWR
ntwk.plot_s_vswr(m=0, n=0, label="S11")
ntwk.plot_s_vswr(m=1, n=1, label="S22")
plt.grid(True)
plt.legend()
plt.show()

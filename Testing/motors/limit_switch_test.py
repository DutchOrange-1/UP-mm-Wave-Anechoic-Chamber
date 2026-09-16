# https://colab.research.google.com/drive/1xJawpc-0CIZLDlwkefzSgWrAyBVSlaMl#scrollTo=44139bb1
# This code is to collect data for a full hemisphere. Therefore the Elevation must move x amounts
# and each time it moves, the azi must do a 180 degree sweep, maybe 360 if the cable can tolerate.

# One issue will be the initial homing, as this must be repeatable. Different antennas will make it weigh different
# amounts. Hence before every test, a calibration should be done.

import logging
import pathlib
import os
import time
import libximc.highlevel as ximc

#######################
# Setup motors:
# Define ports of the motors being used.
elev = r"xi-com:\\.\COM12"  # Elevation
azi = r"xi-com:\\.\COM13"  # azimuth

azi_axis = ximc.Axis(azi)
elev_axis = ximc.Axis(elev)

azi_axis.open_device()
elev_axis.open_device()


while True:
    # print(azi_axis.get_status())
    print(elev_axis.get_status())
    # user_input = input("Enter text (or just press Enter to exit): ")
    # if user_input == "":
    #     break
    time.sleep(0.1)


# Close devices - now done
azi_axis.close_device()
elev_axis.close_device()

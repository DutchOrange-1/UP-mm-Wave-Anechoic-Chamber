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


# Logging:
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)


# For now, due to unknown limits of the cables, they will be limited to 0-90 (Elevation) and
# 0-180 for azimuth.
# Define number of data points:
elev_points = 10
azi_points = 30

elev_start = 0
elev_end = 90
azi_start = 0
azi_end = 180

########################
# Sanity check of inputs.
if (elev_start < 0 or elev_start > 90) and elev_start > elev_end:
    print("Error with elevation settings. < 0 or > 90")
    exit()
elif (azi_start < 0 or azi_start > 180) and azi_start > azi_end:
    print("Error with azimuth settings. < 0 or > 180")
    exit()
elif elev_points % (elev_start - elev_end) == 1:
    print("Give number of points of for Elevation wrong - must be a multiple")
elif azi_points % (azi_start - azi_end) == 1:
    print("Give number of points of for Elevation wrong - must be a multiple")

#######################
# Setup motors:
# Define ports of the motors being used.
elev = r"xi-com:\\.\COM12"  # Elevation
azi = r"xi-com:\\.\COM13"  # azimuth

azi_axis = ximc.Axis(azi)
elev_axis = ximc.Axis(elev)

#####################
# Start Data Collection
azi_array = list(range(azi_start, azi_end+azi_points, azi_points))
print("Azimuth points: " + str(azi_array))

elev_array = list(range(elev_start, elev_end+elev_points, elev_points))
print("Elevation points: " + str(elev_array))

data_points = len(azi_array) * len(elev_array)
print("Data Points: " + str(data_points))


###############
# Positioning:
azi_axis.open_device()
elev_axis.open_device()
logging.info("Initial position - azimuth:", azi_axis.get_position().Position)
# print("Initial position - Elevation:", elev_axis.get_position().Position)


# Homing
# What is not home ?
print("Homing....")
azi_axis.command_home
azi_axis.command_wait_for_stop(100)
print("Done with Azimuth")


for elv_pos in elev_array:
    # print(elv_pos)
    for azi_pos in azi_array:
        print(azi_pos)


# Close devices
azi_axis.close_device()
elev_axis.close_device()

# Save data ?

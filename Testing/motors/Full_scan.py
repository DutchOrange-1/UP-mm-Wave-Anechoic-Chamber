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

# The azimuth has a resolution of 0.01 degrees per step.
# The elevation has a resolution of 0.0607 deg / step.
# Note the ^-1 to change the units to steps / deg to make later math easier.

elev_angle_step_res = (0.060714) ^ -1
azi_angle_step_res = (0.01) ^ -1

# Shake time wait (Seconds):
wobble_tim_elev = 0.1
wobble_tim_azi = 0.01


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
if elev_start < 0 or elev_start > 90 or elev_start < elev_end:
    logging.error("Error with elevation settings. < 0 or > 90")
    exit()
elif azi_start < 0 or azi_start > 180 or azi_start < azi_end:
    logging.error("Error with azimuth settings. < 0 or > 180")
    exit()
elif elev_points % (elev_start - elev_end) == 1:
    logging.error(
        "Give number of points of for Elevation wrong - must be a multiple")
elif azi_points % (azi_start - azi_end) == 1:
    logging.error(
        "Give number of points of for Azimuth wrong - must be a multiple")

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
logging.info("Azimuth points: " + str(azi_array))

elev_array = list(range(elev_start, elev_end+elev_points, elev_points))
logging.info("Elevation points: " + str(elev_array))

data_points = len(azi_array) * len(elev_array)
logging.info("Data Points: " + str(data_points))

###############
# Positioning:
azi_axis.open_device()
elev_axis.open_device()
logging.info("Initial position - azimuth:", azi_axis.get_position().Position)
logging.info("Initial position - Elevation:",
             elev_axis.get_position().Position)


# Homing
logging.info("Homing....")
azi_axis.command_home
azi_axis.command_wait_for_stop(100)
azi_axis.command_zero
logging.info("Done Homing Azimuth")

elev_axis.command_home
elev_axis.command_wait_for_stop(100)
elev_axis.command_zero
logging.info("Done Homing Elevation")
progress = 0


for elv_pos in elev_array:
    # Move elevation.
    elev_axis.command_move(elv_pos * elev_angle_step_res)
    elev_axis.command_wait_for_stop(100)
    # This is to allow the long boom arm to stop wobbling, as this is one of the biggest things
    # that would result in imprecise data.
    time.sleep(wobble_tim_elev)

    for azi_pos in azi_array:
        progress += 1
        # Move Azimuth
        azi_axis.command_move(azi_pos * azi_angle_step_res)
        azi_axis.command_wait_for_stop(100)
        time.sleep(wobble_tim_azi)
        #####
        # TAKE SAMPLE HERE !!!! - This will take time for the VN to Sweep - Func would be called here.
        #####
        logging.info("Progress: "+str(100*progress/data_points))


# Close devices - now done
azi_axis.close_device()
elev_axis.close_device()

# Save data ?
# Need to decide on export type - Simple webserver to access files ?

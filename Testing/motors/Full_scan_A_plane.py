# https://colab.research.google.com/drive/1xJawpc-0CIZLDlwkefzSgWrAyBVSlaMl#scrollTo=44139bb1
# This code is to collect the CO and Cross pol - This is to get the E-field co and cross. Then the Feed antenna
# Must be rotated to get the H Co and Cross.

import logging
import pathlib
import os
import numpy as np
import time
import libximc.highlevel as ximc

# The azimuth has a resolution of 0.01 degrees per step.
# The elevation has a resolution of 0.0607 deg / step.
# Note the ^-1 to change the units to steps / deg to make later math easier.

elev_angle_step_res = 1/(0.060714)
azi_angle_step_res = 1/(0.01)

# Shake time wait (Seconds):
wobble_tim_elev = 0.1
wobble_tim_azi = 0.01
elevation_speed = 4  # 3 Steps per second
azimuth_speed = 2000  # 2000/s


# Logging:
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)


# For now, due to unknown limits of the cables, they will be limited to 0-90 (Elevation) and
# 0-180 for azimuth.
# Define number of data points:
elev_points = 3
# Define the type of measurement:
type = 'E'  # E for E plane - Gets both Co and Cross pol
# type = 'ECO' # E plane - Cross only
# type = 'H'
# type = 'HCO'

#######################
# Setup motors:
# Define ports of the motors being used.
elev = r"xi-com:\\.\COM12"  # Elevation
azi = r"xi-com:\\.\COM13"  # azimuth

azi_axis = ximc.Axis(azi)
elev_axis = ximc.Axis(elev)

###########################
#######
###########################
progress = 0
data_points = elev_points


def homing():
    logging.info("Homing....")
    azi_axis.command_home()
    azi_axis.command_wait_for_stop(100)
    azi_axis.command_zero()
    logging.info("Done Homing Azimuth")

    logging.info("Homing....")
    elev_axis.command_home()
    elev_axis.command_wait_for_stop(100)
    elev_axis.command_zero()
    logging.info("Done Homing Elevation")


def elv_mov_up(positions):
    # Assuming starting from 0
    elev_axis.command_move(0, 0)
    for pos in positions:
        logging.info("Moving to: %.2f ", pos)
        position = int(pos * elev_angle_step_res * -1)
        elev_axis.command_move(position, 0)
        elev_axis.command_wait_for_stop(100)
        time.sleep(wobble_tim_elev)
        take_sample()


def elv_mov_down(positions):
    # Rotate the array to go from the other direction.
    positions = positions[::-1]
    # Assuming starting from 90, or close to it, will start moving down from last spot
    for pos in positions:
        logging.info("Moving to: %.2f ", pos)
        position = int(pos * elev_angle_step_res * -1)
        elev_axis.command_move(position, 0)
        elev_axis.command_wait_for_stop(100)
        time.sleep(wobble_tim_elev)
        take_sample()


def mov_azi(pos):
    position = pos * azi_angle_step_res
    azi_axis.command_move(int(position), 0)
    azi_axis.command_wait_for_stop(100)
    time.sleep(wobble_tim_azi)


def take_sample():
    global progress
    progress += 1
    # print("data_points (take sample): " + str(data_points))
    logging.info("Progress: "+str(100*progress/data_points) + " %")
    logging.info("Taking Sample...")


def time_estimate(elev_points, azi_rotations=1):
    time_sum = 0
    # Get estimated time.
    time_sum += data_points * wobble_tim_elev
    time_sum += azi_rotations * wobble_tim_azi
    # For calibration
    time_sum += abs(elev_axis.get_position().Position) / elevation_speed
    # Then add time for the sweep:
    time_sum += 180 * azi_rotations / azimuth_speed

    logging.info("Estimated time: %.2f Minutes", time_sum/60)


def scan_AUT_CO_CROSS(elev_points=9, pos1=0, pos2=180, pos3=0, pos4=0):
    """
    Does co and cross pol

    Parameters:
    elev_points=9,
    pos1=0,
    pos2=180,
    pos3=0,
    pos4=0.
    """
    global data_points
    data_points = elev_points*4

    try:

        ###############
        # Positioning:
        azi_axis.open_device()
        elev_axis.open_device()

        # Get current position
        logging.info("Initial position - azimuth: %s",
                     str(azi_axis.get_position().Position))
        logging.info("Initial position - Elevation: %s",
                     str(elev_axis.get_position().Position))
        time_estimate(elev_points, azi_rotations=4)

        time.sleep(5)
        # Homing
        homing()

        elev_positions = np.linspace(0, 90, elev_points)
        print("Positions : " + str(elev_positions))

        #####################################
        # Start Taking Measurements
        # For Pos 1:
        mov_azi(pos1)
        elv_mov_up(elev_positions)

        # For Pos2
        mov_azi(pos2)
        elv_mov_down(elev_positions)

        # For Pos3
        mov_azi(pos3)
        elv_mov_up(elev_positions)

        # For Pos4
        mov_azi(pos4)
        elv_mov_down(elev_positions)

    except KeyboardInterrupt:
        logging.error("\nEMERGENCY STOP!")

        try:
            azi_axis.command_stop()
        except Exception as e:
            logging.error(f"Could not stop azimuth: {e}")

        try:
            elev_axis.command_stop()
        except Exception as e:
            logging.error(f"Could not stop elevation: {e}")


#########
# Program


if type == 'E':
    logging.info("Doing E-Plane Co and Cross")
    scan_AUT_CO_CROSS(elev_points=elev_points)

elif type == 'ECO':
    logging.info("Doing E-Plane Co only")

elif type == 'H':
    logging.info("Doing H-Plane Co and Cross")
    scan_AUT_CO_CROSS()

elif type == 'HCO':
    logging.info("Doing H-Plane Co only")

else:
    logging.warning("%s Is not a valid selection", type)


# Close devices - now done
azi_axis.close_device()
elev_axis.close_device()

#####################

# Save data ?
# Need to decide on export type - Simple webserver to access files ?

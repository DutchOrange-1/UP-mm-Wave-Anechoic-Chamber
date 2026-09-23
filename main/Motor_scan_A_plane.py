# https://colab.research.google.com/drive/1xJawpc-0CIZLDlwkefzSgWrAyBVSlaMl#scrollTo=44139bb1

import logging
import pathlib
import os
import numpy as np
import time
import libximc.highlevel as ximc
import anritsu_vectorstar_vna_interface as vna_interface


progress = 0


def planer_scan(azimuth_points=3, type='E', wobble_time_azimuth=0.1, wobble_pol=0.01,
                main_boom_speed=4, polarization_speed=2000):
    """_summary_
    Quickest test is the ECO or HCO. 
    Please note that the sampling / data collection code can be found in take_sample(). 

    This code is only for co and cross polarization. For E/H Plane, CO and CROSS. 
    How the code works, is it will put the polarization of the AUT (Antenna Under Test) at 0 deg. Then using the boom arm
    will act as the azimuth. The Azimuth will be swept 90 deg, then the AUT will rotate 180 deg to get the other side. 
    The Azimuth will sweep back down to the bottom. This is the Co-pol. 

    If the Co and cross pol mode is selected, the AUT will then turn to 90 deg, and then 270 deg.
    type = 'E'   - Co and Cross
    type = 'ECO'  - plane - Co only
    type = 'H'   - H Co and Cross
    type = 'HCO' - Co pole only

    Note - E/H and ECO/HCO are the exact same code. H and E are dependant on the Feed antenna position. 

    main_boom_speed and polarization_speed should not be touched, unless different motors are used. 

    Args:
        azimuth_points (int, optional): _description_. Defaults to 3.
        type (str, optional): _description_. Defaults to 'E'.
        wobble_time_azimuth (float, optional): _description_. Defaults to 0.1.
        wobble_pol (float, optional): _description_. Defaults to 0.01.
        main_boom_speed (int, optional): _description_. Defaults to 4.
        polarization_speed (int, optional): _description_. Defaults to 2000.
    """

    # The azimuth has a resolution of 0.01 degrees per step.
    # The elevation has a resolution of 0.0607 deg / step.
    # Note the ^-1 to change the units to steps / deg to make later math easier.
    elev_angle_step_res = 1/(0.060714)
    azi_angle_step_res = 1/(0.01)

    global progress
    progress = 0

    # Logging:
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    # For now, due to unknown limits of the cables, they will be limited to 0-90 (Elevation) and
    # 0-180 for azimuth.
    # Define number of data points:
    # Define the type of measurement:

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
    data_points = azimuth_points

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
            time.sleep(wobble_time_azimuth)
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
            time.sleep(wobble_time_azimuth)
            take_sample()

    def mov_azi(pos):
        position = pos * azi_angle_step_res
        azi_axis.command_move(int(position), 0)
        azi_axis.command_wait_for_stop(100)
        time.sleep(wobble_pol)

    def take_sample():
        global progress
        progress += 1
        # print("data_points (take sample): " + str(data_points))
        logging.info("Progress: "+str(100*progress/data_points) + " %")
        logging.info("Taking Sample...")
        vna_interface.sweep_and_save()

    def time_estimate(azi_rotations):
        time_sum = 0
        # Get estimated time.
        time_sum += data_points * wobble_time_azimuth
        time_sum += azi_rotations * wobble_pol
        # For calibration
        time_sum += abs(elev_axis.get_position().Position) / main_boom_speed
        # Then add time for the sweep:
        time_sum += 180 * azi_rotations * azi_angle_step_res / polarization_speed
        # Moving time of the main beam:
        time_sum += 180 * azi_rotations * elev_angle_step_res / main_boom_speed

        logging.info("Estimated time: %.2f Minutes", time_sum/60)

    def scan_AUT_CO_CROSS(azimuth_points=9, pos1=0, pos2=180, pos3=0, pos4=0):
        """
        Does co and cross pol

        Parameters:
        azimuth_points=9,
        pos1=0,
        pos2=180,
        pos3=90,
        pos4=270.
        """
        global data_points
        data_points = azimuth_points*4

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
            time_estimate(azi_rotations=4)

            time.sleep(5)
            # Homing
            homing()

            elev_positions = np.linspace(0, 90, azimuth_points)
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

    def scan_AUT_CO_Only(azimuth_points=9, pos1=0, pos2=180):
        """
        Does co pol only

        Parameters:
        azimuth_points=9,
        pos1=0,
        pos2=180,
        """
        global data_points
        data_points = azimuth_points*4

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
            time_estimate(azi_rotations=4)

            time.sleep(5)
            # Homing
            homing()

            elev_positions = np.linspace(0, 90, azimuth_points)
            print("Positions : " + str(elev_positions))

            #####################################
            # Start Taking Measurements
            # For Pos 1:
            mov_azi(pos1)
            elv_mov_up(elev_positions)

            # For Pos2
            mov_azi(pos2)
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
    # Program To Be Selected
    if type == 'E':
        logging.info("Doing E-Plane Co and Cross")
        scan_AUT_CO_CROSS(azimuth_points=azimuth_points, pos1=0,
                          pos2=180, pos3=90, pos4=270)

    elif type == 'ECO':
        logging.info("Doing E-Plane Co only")
        scan_AUT_CO_Only(azimuth_points=azimuth_points, pos1=0, pos2=180)

    elif type == 'H':
        logging.info("Doing H-Plane Co and Cross")
        scan_AUT_CO_CROSS(azimuth_points=azimuth_points, pos1=0,
                          pos2=180, pos3=90, pos4=270)

    elif type == 'HCO':
        logging.info("Doing H-Plane Co only")
        scan_AUT_CO_Only(azimuth_points=azimuth_points, pos1=0, pos2=180)

    else:
        logging.warning("%s Is not a valid selection", type)

    # Go Back
    homing()
    # Close devices - now done
    azi_axis.close_device()
    elev_axis.close_device()

    #####################

    # Save data ?
    # Need to decide on export type - Simple webserver to access files ?

import logging
import pathlib
import os
import numpy as np
import time
import libximc.highlevel as ximc

elev = r"xi-com:\\.\COM12"  # Elevation
azi = r"xi-com:\\.\COM13"  # azimuth

azi_axis = ximc.Axis(azi)
elev_axis = ximc.Axis(elev)
azi_axis.open_device()
elev_axis.open_device()


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


# Close devices - now done
azi_axis.close_device()
elev_axis.close_device()

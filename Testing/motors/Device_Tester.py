# https://colab.research.google.com/drive/1xJawpc-0CIZLDlwkefzSgWrAyBVSlaMl#scrollTo=44139bb1
import pathlib
import os
import time
import libximc.highlevel as ximc

# Define ports of the motors being used.
elevation = r"xi-com:\\.\COM12"
azimuth = r"xi-com:\\.\COM13"

# elevation_axis = ximc.Axis(elevation)

azimuth_axis = ximc.Axis(azimuth)
azimuth_axis.open_device()

position = azimuth_axis.get_position()
print("Initial position:", position.Position)

print("Start moving")
azimuth_axis.command_right()
for i in range(3):
    time.sleep(1)
    print("Moving...")

print("Stop moving")
azimuth_axis.command_stop()

position = azimuth_axis.get_position()
print("Final position:", position.Position)
azimuth_axis.close_device()


# exit()
time.sleep(5)
axis = azimuth_axis
# Now to move by a set amount - in mm
#  Look into stepping / shifting it by using angles insted !
#  Would most likely need to measure the distance for 1 rev / 1 step and see how far it moves !
#  This would require some special equipment.
axis.open_device()

# ==== User unit setup ====
# We will use mm as user units
# In our example conversion coefficient will be 0.0025 mm / step.
# Set conversion coefficient for your stage here if needed
step_to_mm_conversion_coeff = 0.0025  # mm / step

# Get information about microstep mode
engine_settings = axis.get_engine_settings()

# Now we can set calibration settings for our axis
axis.set_calb(step_to_mm_conversion_coeff, engine_settings.MicrostepMode)

# ==== Perform a shift by using user units (mm in our case) ====
position_calb = axis.get_position_calb()
print("Current position:", position_calb.Position, "mm")

next_position_in_mm = 5.21
print("Move to position:", next_position_in_mm, "mm")
axis.command_move_calb(next_position_in_mm)

print("Moving...")
axis.command_wait_for_stop(100)

position_calb = axis.get_position_calb()
print("Current position:", position_calb.Position, "mm")

next_position_in_mm = 0
print("Move to position:", next_position_in_mm, "mm")
axis.command_move_calb(next_position_in_mm)

print("Moving...")
axis.command_wait_for_stop(100)

position_calb = axis.get_position_calb()
print("Current position:", position_calb.Position, "mm")

axis.close_device()
print("Done")

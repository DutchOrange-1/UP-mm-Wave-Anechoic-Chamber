# https://colab.research.google.com/drive/1xJawpc-0CIZLDlwkefzSgWrAyBVSlaMl#scrollTo=44139bb1
import pathlib
import os
import time
import libximc.highlevel as ximc


# Devices search
devices = ximc.enumerate_devices(
    ximc.EnumerateFlags.ENUMERATE_NETWORK |
    ximc.EnumerateFlags.ENUMERATE_PROBE
)

if len(devices) == 0:
    print("The real devices were not found. A virtual device will be used.")
else:
    # Print real devices list
    print("Found {} real device(s):".format(len(devices)))
    for device in devices:
        print("  {}".format(device))

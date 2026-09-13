# mm Wave Chamber at [CEFIM](https://www.up.ac.za/carl-emily-fuchs-institute-for-microelectronics-cefim). 
Where: Carl Emily Fuchs Institute for Microelectronics ([CEFIM](https://www.up.ac.za/carl-emily-fuchs-institute-for-microelectronics-cefim))
What: mm Wave Chamber
Why: To Automatize data collection through the azimuth and vertical planes to generate 3D field patterns. 
<br>
This program made use of code inspired by [SitwalaM](https://github.com/SitwalaM/cefim_vna_beagle). 

# Installation of Program:
Can't yet. 

# Brief workings of program
Nothing yet. 

# Possible features to add:
- Phase compensation during calibration. 
    * During calibration, with a standard antenna, the azimuth plane could be turned, resulting in the receiving antenna having a changing phase. This can be measured during calibration, so that the following tests are compensated. 
- Library of Pre calibrated horn antennas. 
- Simple exporting of all data files as one large ZIP. 

# Motor controls
Current the motors can be controlled / tested using [XILab](https://files.xisupport.com/Software.en.html). This is the GUI of [libximc](https://doc.xisupport.com/en/8smc5-usb/8SMCn-USB/Programming.html). This should be easily implemented with their [python section](https://doc.xisupport.com/en/8smc5-usb/8SMCn-USB/Programming/Programming_guide/Working_in_Python.html), which is made for the **8SMC5**, but we have the **8SMC4**, the [docs](https://doc.xisupport.com/en/8smc5-usb/8SMCn-USB/Programming/8SMC1-USBhF_software_compatibility.html) do discuss that they should be compatible. Follow the [installation](https://doc.xisupport.com/en/8smc5-usb/8SMCn-USB/Programming/Programming_guide/Working_in_Python.html) of it. 
<br>

Example scripts can be found [here](https://doc.xisupport.com/en/8smc5-usb/8SMCn-USB/Programming/XILab_scripts.html). Several examples can be found [here](https://github.com/Standa-Optomechanics/libximc/blob/dev-3.0/examples/test_Python/standardtest/testpython.py). <br>
In the end, this [documentation](https://colab.research.google.com/drive/1xJawpc-0CIZLDlwkefzSgWrAyBVSlaMl#scrollTo=44139bb1) will be used. <br>
The two motors part numbers can be found through XIlab. And through testing it was found that the azimuth has a resolution of 0.01 degrees per step. and elevation has a resolution of 0.0607 deg / step. 


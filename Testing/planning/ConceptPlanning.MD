# Concept Planning
This document covers the planning, and general process of the anechoic chamber, and reasons for decisions. It will also cover the 

# Positioners and Wave Guides
There are two motors, Azimuth and Elevation. Depending on the orientation of the antennas, the azimuth plane can become polarization instead. The source antenna can be seen here:
<br>
<img src="./images/SourceAntenna.jpg" alt="Source Antenna"  height="400">
<br>
And then the antenna under test:
<br>
<img src="./images/DUT_Antenna.jpg" alt="Antenna Under Test" height="400">
<br>
With this original setup, and the DUT antenna (The one being tested) facing up right, instead elevation and azimuth is now being tested, as the DUT antenna will be rotated, changing the polarization in reference to the source. Therefore the antenna should instead stick up above the foam, and face horizontal, so the azimuth can be obtained. 
<br>


The frequency will be capped at 70 GHz for these tests, as attaching the frequency extenders are a project in their own regard. 

## Standard gain horn antennas available  
The two horns available are two [261V-15-385](https://www.miwv.com/v-band-standard-gain-horn-antennas-wr-15-50-75-ghz-2/) which is a max of 75 GHz, the patterns can also be found. 

# VNA 
SCPI Commands

# Results to be obtained
The general result, as formatted by SAAB shall be used. (#DR B)
This includes:
- **VSWR** against freq. 
- **Realized Boresight gain (dBi)** against freq.
- **Normalized H-Plane Pattern** for one freq. 
- **Normalized H-Plane Pattern** for one freq. 
- **Normalized H-Plane Magnitude (dB)** against freq.
- **Normalized E-Plane Magnitude (dB)** against freq.

# GUI 
WIP



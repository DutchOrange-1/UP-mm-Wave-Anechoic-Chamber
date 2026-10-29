import pyvisa

rm = pyvisa.ResourceManager("@py")

print("VISA backend:", rm.visalib)
print("Available resources:", rm.list_resources())

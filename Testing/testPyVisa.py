import pyvisa

rm = pyvisa.ResourceManager("@py")
# rm = pyvisa.ResourceManager()

print(rm)

print("VISA backend:", rm.visalib)
print("Available resources:", rm.list_resources())

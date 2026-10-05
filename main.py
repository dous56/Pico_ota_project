import time
import machine
import ota

led = machine.Pin(14, machine.Pin.OUT)

while True:

    # OTA service
    # Στην πρώτη κλήση ελέγχει αμέσως.
    # Μετά μόνο όταν περάσουν 5 ή 10 λεπτά.
    ota.ota_service()

    led.value(1)
    time.sleep(1)

    led.value(0)
    time.sleep(1)

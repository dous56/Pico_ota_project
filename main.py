import time
time.sleep(3)  # Δίνει χρόνο στο USB/Thonny να συνδεθεί

try:
    import ota
    ota.run_ota_check()
except Exception as e:
    print("OTA failed:", e)

# --- Κυρίως πρόγραμμα ---
import machine
led = machine.Pin(13, machine.Pin.OUT)

while True:
    led.value(1)
    time.sleep(1)
    led.value(0)
    time.sleep(1)

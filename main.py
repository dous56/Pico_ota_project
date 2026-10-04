# connect_LED.py = main.py
import machine
import time

led = machine.Pin(13, machine.Pin.OUT)

while True:
    led.value(1)  # Άναψε το LED
    time.sleep(1) # Περίμενε 1 δευτερόλεπτο
    led.value(0)  # Σβήσε το LED
    time.sleep(1) # Περίμενε 1 δευτερόλεπτο
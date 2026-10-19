# ------------------------------------------------------------
# Program      : main.py
# Firmware     : 8.2.0
# Device       : Child Logger (LoRa TX Test)
# PCB          : αρχικό 467 -> PCB α/α 122
#
# Status       : STABLE
# Ready        : FIELD TEST       
# ------------------------------------------------------------
# ΙΣΤΟΡΙΚΟ
# ------------------------------------------------------------
# 12-09-2025  Αρχική έκδοση
# 06-11-2025  Αυτόματη αλλαγή τάσεων με κόκκινο κουμπί
# 13-03-2026  Προσθήκη RSSI και SNR
# 31-03-2026  Προσθήκη State Machine
# 12-04-2026  LoRa Inactivity Guard + SW Watchdog
# 02-06-2026  Συχνότητα LoRa από config.json
# 12-07-2026  Γενικές βελτιώσεις
#
# 02-08-2026  Firmware 7.1
#              • Safe I2C/OLED startup
#              • MockOLED (λειτουργία χωρίς οθόνη)
#              • Διόρθωση αρχικών τιμών DO
#              • Προστασία ids.json / power_ids.json
#              • Fallback config.json
#              • Βελτίωση inactivity timer
#              • Βελτίωση logging
#              • Βελτίωση watchdog
#              • Έλεγχος τιμών None
#
# 03-08-2026  Firmware 7.2
#              • Μόνιμη παράκαμψη OLED μετά από runtime failure
#              • Έλεγχος stuck SDA/SCL πριν από I2C scan
#              • Transactional config save με backup/validation
#              • Αποθήκευση config μόνο όταν αλλάζουν ρυθμίσεις
#              • LoRa staged recovery αντί reset ανά 2 λεπτά
#              • Καταγραφή reset reasons και critical errors
#              • Περιοδικό GC και αυστηρότερο validation τιμών
# 19-08-2026 Firmware 7.21
# 19-08-2026 7.22 Προσθήκη offset_battery στο config.ison
# 12-10-2026 Firmware 8.1: Active-LOW relay control GPIO22/21, LoRa RELE commands
# 12-10-2026 Firmware 8.2.0: WiFi OTA for main.py and config.json
# 16-10-2026 διορθώσεις Lora , and blink LED , and OTA 
# ------------------------------------------------------------
import machine
import micropython
from machine import Pin, I2C, ADC, UART
from sx1262 import SX1262
import time
import dht
import math
import struct
import os
import gc
import uos
import random
import json
import sys
import framebuf
import imgfile
# --------------
import network
import ota
# ============================================================
# SOFTWARE WATCHDOG
# ============================================================
try:
    print("Reset cause:", machine.reset_cause())
except Exception:
    pass
SW_WDT_TIMEOUT_MS = 600000
_sw_t = None
_last_feed_ms = 0
_last_progress_ms = time.ticks_ms()
# ------------------------------------------------
def _do_reset(_):
    try:
        print("SW Watchdog triggered - resetting...")
    except Exception:
        pass
    machine.reset()
# ------------------------------------------------    
def _irq_fire(timer):
    try:
        # Εκτέλεση του reset εκτός interrupt context.
        micropython.schedule(_do_reset, 0)
    except Exception:
        # Εφεδρική λύση αν αποτύχει το schedule.
        machine.reset()
# ------------------------------------------------
def watchdog_start(timeout_ms=SW_WDT_TIMEOUT_MS):
    global _sw_t
    global SW_WDT_TIMEOUT_MS
    SW_WDT_TIMEOUT_MS = int(timeout_ms)
    try:
        Timer = machine.Timer
    except Exception:
        Timer = None
    if Timer is None:
        print("[WDT] Timer not available")
        return
    try:
        _sw_t = Timer()
    except Exception:
        _sw_t = Timer(-1)
    try:
        _sw_t.init(
            mode=Timer.ONE_SHOT,
            period=SW_WDT_TIMEOUT_MS,
            callback=_irq_fire
        )
        print(
            "[WDT] SW active, timeout={}ms".format(
                SW_WDT_TIMEOUT_MS
            )
        )
    except Exception as e:
        print("[WDT] Start error:", e)
        _sw_t = None
# ------------------------------------------------
def _sw_kick():
    if _sw_t is None:
        return
    try:
        _sw_t.init(
            mode=machine.Timer.ONE_SHOT,
            period=SW_WDT_TIMEOUT_MS,
            callback=_irq_fire
        )
    except Exception as e:
        print("[WDT] Kick error:", e)
# ------------------------------------------------
def wdt_mark_progress():
    global _last_progress_ms

    _last_progress_ms = time.ticks_ms()
    _sw_kick()
# ------------------------------------------------
def feed_if_due(period_ms=800):
    global _last_feed_ms
    now = time.ticks_ms()
    if time.ticks_diff(now, _last_feed_ms) >= period_ms:
        if time.ticks_diff(now, _last_progress_ms) < SW_WDT_TIMEOUT_MS:
            _sw_kick()
            _last_feed_ms = now
# ------------------------------------------------
def sleep_coop(total_ms):
    end_time = time.ticks_add(
        time.ticks_ms(),
        int(total_ms)
    )
    while time.ticks_diff(end_time, time.ticks_ms()) > 0:
        feed_if_due(300)
        time.sleep_ms(50)
watchdog_start(600000)
# ------------------------------------------------------------
# WiFi interface - μόνο έλεγχος κατάστασης
wlan = network.WLAN(network.STA_IF)
# ============================================================
# OLED
# ============================================================
try:
    from ssd1306 import SSD1306_I2C
    OLED_DRIVER_AVAILABLE = True
except Exception as e:
    SSD1306_I2C = None
    OLED_DRIVER_AVAILABLE = False
    print("[OLED] Driver unavailable:", e)

class MockOLED:
    """
    Εικονική OLED.

    Χρησιμοποιείται όταν:
    - δεν υπάρχει OLED,
    - δεν απαντά η OLED,
    - αποτύχει το I2C scan,
    - αποτύχει η αρχικοποίηση του SSD1306.
    Έτσι ο υπόλοιπος κώδικας μπορεί να καλεί κανονικά
    oled.fill(), oled.text(), oled.show() κλπ.
    """
# ------------------------------------------------
    def fill(self, color):
        pass
# ------------------------------------------------
    def fill_rect(self, x, y, w, h, color):
        pass
# ------------------------------------------------
    def text(self, message, x, y):
        pass
# ------------------------------------------------
    def show(self):
        pass
# ------------------------------------------------
    def blit(self, framebuffer, x, y):
        pass
# ------------------------------------------------
    def rotate(self, value):
        pass
# ------------------------------------------------
# Προεπιλογή: το πρόγραμμα ξεκινά χωρίς πραγματική OLED.
oled = MockOLED()
OLED_AVAILABLE = False
i2c_dev = None
devices = []
# ============================================================
# DO SENSOR DRIVER
# ============================================================
from DO_French261cal import *   # για αισθητήρα προηγούμενης γενιάς 
#from DO_French262cal import *   # για αισθητήρα νέας γενιάς  
sensor = ODO_Sensor()
try:
    from DO_French261cal import set_default_feed_cb # για αισθητήρα προηγούμενης γενιάς 
    #from DO_French262cal import set_default_feed_cb # για αισθητήρα νέας γενιάς 
    set_default_feed_cb(wdt_mark_progress)
except Exception:
    pass
# Αρχικές τιμές αισθητήρα DO
initial_do_values = None    
# ============================================================
# VOLTAGES / LOGS
# ============================================================
#from voltage_reader2 import get_voltages
from voltage_reader3sa import get_voltages, set_feed_cb as set_vr_feed

from main_error6 import (
    WarnLog,
    ErrorLog,
    InfoLog,
    set_device_id,
    initialize_log_files,
    flush_logs
)
VCONF_PATH = "register_setup_voltage.json"
# ============================================================
# LED PINS
# ============================================================
LEDBL = Pin(14, Pin.OUT)
LEDBL.value(0)
LEDP = Pin(13, Pin.OUT)
LEDP.value(0)
Ping_Counter = 0   # μετρήτής για το πόσες φορές δέχεται Ping 
# ============================================================
# αναβόσβημα LEDBL
# LED GPIO14 - Blink during IDLE
# IDLE_LED_INTERVAL_MS = 500
# _idle_led_last_ms = time.ticks_ms()
# 
# def idle_led_tick():
#     global _idle_led_last_ms
# 
#     now = time.ticks_ms()
# 
#     if time.ticks_diff(now, _idle_led_last_ms) >= IDLE_LED_INTERVAL_MS:
#         LEDBL.value(1 - LEDBL.value())
#         _idle_led_last_ms = now
# ==================================================
# BLUE LED HEARTBEAT - GPIO14
# Independent of blocking LoRa receive
# ==================================================

from machine import Timer
LEDBL.value(0)
_blue_led_timer = Timer()

def blue_led_timer_callback(timer):
    LEDBL.value(1 - LEDBL.value())

def start_blue_led_heartbeat():
    _blue_led_timer.init(
        mode=Timer.PERIODIC,
        period=500,
        callback=blue_led_timer_callback
    )

def stop_blue_led_heartbeat():
    _blue_led_timer.deinit()
    LEDBL.value(0)
# ------------------------------------------------------------
# RELAY PINS
# ============================================================
# Active-LOW relays: 0 = ON, 1 = OFF
# Initialize as early as possible to avoid accidental activation.
#Relay1 = Pin(22, Pin.OUT, value=1)
#Relay2 = Pin(21, Pin.OUT, value=1)
Relay1 = Pin(22, Pin.OUT, value=0)
Relay2 = Pin(21, Pin.OUT, value=0)
print("Relay1: GPIO22 - OFF - value=0")
print("Relay2: GPIO21 - OFF - value=0")
# ============================================================
# SAFE OLED / I2C HELPERS
# ============================================================
def disable_oled(reason="unknown"):
    global oled, OLED_AVAILABLE, i2c_dev
    try:
        print("[OLED] Disabled:", reason)
    except Exception:
        pass
    try:
        WarnLog("OLED_DISABLED: {}".format(reason))
    except Exception:
        pass
    oled = MockOLED()
    OLED_AVAILABLE = False
    i2c_dev = None

def oled_safe_call(callback):
    """Execute one OLED transaction; disable display after first failure."""
    if not OLED_AVAILABLE:
        return False
    try:
        callback(oled)
        return True
    except Exception as e:
        disable_oled(e)
        return False

def i2c_lines_released(sda_pin, scl_pin):
    """Avoid I2C scan when a damaged device holds SDA or SCL low."""
    try:
        sda = Pin(sda_pin, Pin.IN, Pin.PULL_UP)
        scl = Pin(scl_pin, Pin.IN, Pin.PULL_UP)
        sleep_coop(5)
        return bool(sda.value() and scl.value())
    except Exception as e:
        print("[I2C] Line precheck error:", e)
        return False

# ============================================================
# I2C / OLED INITIALIZATION
# ============================================================
I2C_SDA_PIN = 4
I2C_SCL_PIN = 5
OLED_ADDRESS_1 = 0x3C
OLED_ADDRESS_2 = 0x3D
try:
    if not OLED_DRIVER_AVAILABLE:
        raise RuntimeError("SSD1306 driver unavailable")
    if not i2c_lines_released(I2C_SDA_PIN, I2C_SCL_PIN):
        raise RuntimeError("SDA/SCL held LOW - I2C bypassed")
    print(
        "[I2C] Initializing I2C0, SDA={}, SCL={}".format(
            I2C_SDA_PIN,
            I2C_SCL_PIN
        )
    )
    i2c_dev = machine.I2C(
        0, sda=machine.Pin(I2C_SDA_PIN),
        scl=machine.Pin(I2C_SCL_PIN), freq=100000
    )
    feed_if_due(200)
    devices = i2c_dev.scan()
    feed_if_due(200)
    print(
        "[I2C] Devices:",
        ["0x{:02X}".format(address) for address in devices]
    )
    oled_address = None
    if OLED_ADDRESS_1 in devices:
        oled_address = OLED_ADDRESS_1
    elif OLED_ADDRESS_2 in devices:
        oled_address = OLED_ADDRESS_2
    if oled_address is not None:
        try:
            oled = SSD1306_I2C(
                128,
                32,
                i2c_dev,
                addr=oled_address
            )
            oled.fill(0)
            oled.show()
            OLED_AVAILABLE = True
            print(
                "[OLED] Found at address 0x{:02X}".format(
                    oled_address
                )
            )
        except Exception as e:
            print("[OLED] Initialization error:", e)

            oled = MockOLED()
            OLED_AVAILABLE = False
    else:
        print("[OLED] Not found - using MockOLED")

        oled = MockOLED()
        OLED_AVAILABLE = False
except Exception as e:
    print("[I2C/OLED] Initialization error:", e)
    print("[I2C/OLED] Continuing without display")

    devices = []
    i2c_dev = None
    oled = MockOLED()
    OLED_AVAILABLE = False
# ============================================================
# LOAD AVAILABLE IDs
# ============================================================
DEFAULT_AVAILABLE_IDS = ["20"]
try:
    with open("ids.json", "r") as f:
        ids_data = json.load(f)
    AVAILABLE_IDS = ids_data.get(
        "available_ids",
        DEFAULT_AVAILABLE_IDS
    )
    # Αν λείπει η λίστα, δεν είναι list ή είναι άδεια,
    # χρησιμοποιούμε την προεπιλεγμένη τιμή.
    if not isinstance(AVAILABLE_IDS, list) or not AVAILABLE_IDS:
        print(
            "ids.json has no valid IDs - using default:",
            DEFAULT_AVAILABLE_IDS
        )
        AVAILABLE_IDS = DEFAULT_AVAILABLE_IDS[:]
    # Μετατροπή όλων των IDs σε καθαρά strings.
    cleaned_ids = []
    for device_id in AVAILABLE_IDS:
        device_id = str(device_id).strip()
        if device_id:
            cleaned_ids.append(device_id)
    AVAILABLE_IDS = cleaned_ids
    # Δεύτερη προστασία, αν όλα τα στοιχεία ήταν κενά.
    if not AVAILABLE_IDS:
        print(
            "ids.json contained only empty IDs - using default:",
            DEFAULT_AVAILABLE_IDS
        )
        AVAILABLE_IDS = DEFAULT_AVAILABLE_IDS[:]
    print("Available IDs:", AVAILABLE_IDS)
except Exception as e:
    print("Error loading ids.json:", e)
    AVAILABLE_IDS = DEFAULT_AVAILABLE_IDS[:]
    sleep_coop(3000)
# ============================================================
# LOAD AVAILABLE LORA POWER VALUES
# ============================================================
DEFAULT_AVAILABLE_POWERS = [
    -5, -4, -3, -2,
    0, 3, 5, 7,
    10, 13, 15, 17, 20
]
try:
    with open("power_ids.json", "r") as f:
        power_data = json.load(f)
    AVAILABLE_POWERS = power_data.get(
        "available_powers",
        DEFAULT_AVAILABLE_POWERS
    )
    if not isinstance(AVAILABLE_POWERS, list) or not AVAILABLE_POWERS:
        print(
            "power_ids.json has no valid powers - using defaults"
        )
        AVAILABLE_POWERS = DEFAULT_AVAILABLE_POWERS[:]
    # Κρατά μόνο τιμές που μπορούν να μετατραπούν σε int.
    validated_powers = []
    for power_value in AVAILABLE_POWERS:
        try:
            validated_powers.append(int(power_value))
        except Exception:
            print("Invalid power value ignored:", power_value)
    if validated_powers:
        AVAILABLE_POWERS = validated_powers
    else:
        AVAILABLE_POWERS = DEFAULT_AVAILABLE_POWERS[:]
    print("Available powers:", AVAILABLE_POWERS)
except Exception as e:
    print("Error loading power_ids.json:", e)
    AVAILABLE_POWERS = DEFAULT_AVAILABLE_POWERS[:]
# ============================================================
# DEFAULT CONFIGURATION
# ============================================================
DEFAULT_CONFIG = {
    "USE_REAL_SENSOR": False,
    "CURRENT_ID": "20",
    "my_Power": 13,
    "myRotate": 0,
    "myFreq": 869.5,
    "mybw": 250.0,
    "offsetBattery": 0.3,
}

# ============================================================
# LOAD CONFIG.JSON
# ============================================================
try:
    with open("config.json", "r") as f:
        config = json.load(f)

    CURRENT_ID = str(config.get("CURRENT_ID", DEFAULT_CONFIG["CURRENT_ID"])).strip()
    ORIGINAL_ID = CURRENT_ID
    my_Power = int(config.get("my_Power", DEFAULT_CONFIG["my_Power"]))
    myFreq = float(config.get("myFreq", DEFAULT_CONFIG["myFreq"]))
    mybw = float(config.get("mybw", DEFAULT_CONFIG["mybw"]))
    USE_REAL_SENSOR = bool(config.get("USE_REAL_SENSOR", DEFAULT_CONFIG["USE_REAL_SENSOR"]))
    myRotate = int(config.get("myRotate", DEFAULT_CONFIG["myRotate"]))
    offsetBattery = float(config.get("offsetBattery", DEFAULT_CONFIG["offsetBattery"]))

except Exception as e:
    print("Σφάλμα φόρτωσης config.json:", e)
    print("Χρήση προεπιλεγμένων ρυθμίσεων")

    config = DEFAULT_CONFIG.copy()
    CURRENT_ID = DEFAULT_CONFIG["CURRENT_ID"]
    ORIGINAL_ID = CURRENT_ID
    my_Power = DEFAULT_CONFIG["my_Power"]
    myFreq = DEFAULT_CONFIG["myFreq"]
    mybw = DEFAULT_CONFIG["mybw"]
    USE_REAL_SENSOR = DEFAULT_CONFIG["USE_REAL_SENSOR"]
    myRotate = DEFAULT_CONFIG["myRotate"]
    offsetBattery = DEFAULT_CONFIG["offsetBattery"]
# ============================================================
# CONFIG VALIDATION
# ============================================================
# Αν το CURRENT_ID του config δεν υπάρχει στη λίστα,
# χρησιμοποιείται το πρώτο διαθέσιμο ID.
if CURRENT_ID not in AVAILABLE_IDS:
    print(
        "Configured ID {} not found in ids.json - using {}".format(
            CURRENT_ID,
            AVAILABLE_IDS[0]
        )
    )
    CURRENT_ID = AVAILABLE_IDS[0]
if my_Power not in AVAILABLE_POWERS:
    print(
        "Configured power {} not found in power_ids.json - using {}".format(
            my_Power,
            AVAILABLE_POWERS[0]
        )
    )
    my_Power = AVAILABLE_POWERS[0]
try:
    current_id_index = AVAILABLE_IDS.index(CURRENT_ID)
except Exception:
    current_id_index = 0
    CURRENT_ID = AVAILABLE_IDS[0]
try:
    current_power_index = AVAILABLE_POWERS.index(my_Power)
except Exception:
    current_power_index = 0
    my_Power = AVAILABLE_POWERS[0]
print("ID:", CURRENT_ID)
print("Power:", my_Power)
print("Freq:", myFreq)
print("BW:", mybw)
freq = myFreq
# Αρχικοποίηση αρχείου καταγραφής σφαλμάτων
try:
    set_device_id(CURRENT_ID)
    initialize_log_files()
    InfoLog("Logger startup")
except Exception as e:
    print("Log initialization error:", e)
# ============================================================
# OLED SPLASH
# ============================================================
if OLED_AVAILABLE:
    try:
        oled.fill(0)
        try:
            oled.rotate(myRotate == 1)
        except Exception as e:
            print("[OLED] Rotation not supported:", e)
        try:
            buffer, img_res = imgfile.get_img()
            fb = framebuf.FrameBuffer(
                buffer,
                img_res[0],
                img_res[1],
                framebuf.MONO_HMSB
            )
            oled.fill(0)
            oled.blit(fb, 0, 0)
            oled.show()
        except Exception as e:
            print("[OLED] Splash image error:", e)
        sleep_coop(2000)
    except Exception as e:
        print("[OLED] Splash display error:", e)
        # Αν η OLED βγάλει σφάλμα κατά τη λειτουργία,
        # το πρόγραμμα συνεχίζει με MockOLED.
        disable_oled(e)
else:
    print("[OLED] Program running without display")
# Ασφαλές και για πραγματική OLED και για MockOLED.
oled.fill(0)
# ============================================================
# DISPLAY CURRENT SETTINGS
# ============================================================
def show_current_status(msg=""):
    global oled
    global OLED_AVAILABLE
    try:
        oled.fill(0)
        oled.text("Selected ID:", 0, 0)
        oled.text("ID: {}".format(CURRENT_ID), 0, 12)
        oled.text("Power:", 0, 24)
        oled.text("{:>4}dBm".format(my_Power), 62, 24)
        if msg:
            oled.fill_rect(0, 0, 128, 8, 0)
            oled.text(str(msg), 0, 0)
        oled.show()
    except Exception as e:
        print("[OLED] Draw error:", e)
        disable_oled(e)
# -----------------------------------------------------
def next_id():
    global current_id_index, CURRENT_ID, settings_dirty
    current_id_index = (current_id_index + 1) % len(AVAILABLE_IDS)
    CURRENT_ID = AVAILABLE_IDS[current_id_index]
    set_device_id(CURRENT_ID)
    settings_dirty = True
    show_current_status("Next ID")
# -----------------------------------------------
def next_power():
    global current_power_index, my_Power, settings_dirty
    current_power_index = (current_power_index + 1) % len(AVAILABLE_POWERS)
    my_Power = AVAILABLE_POWERS[current_power_index]
    settings_dirty = True
    show_current_status("Next Power")
    LEDBL.value(1); sleep_coop(150); LEDBL.value(0)
    #save_settings()  # <-- ΠΡΟΣΘΗΚΗ ΕΔΩ

# -----------------------------------------------
def _load_json_file(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default

# def _atomic_write_json(path, obj):
#     """Write JSON with tmp + backup + read-back validation."""
#     tmp_path = path + ".tmp"
#     bak_path = path + ".bak"
#     payload = json.dumps(obj)
# 
#     with open(tmp_path, "w") as f:
#         f.write(payload)
#     try:
#         os.sync()
#     except Exception:
#         pass
# 
#     # Validate the temporary file before touching the active config.
#     with open(tmp_path, "r") as f:
#         json.load(f)
# 
#     try:
#         if bak_path in os.listdir():
#             os.remove(bak_path)
#     except Exception:
#         pass
# 
#     if path in os.listdir():
#         os.rename(path, bak_path)
#     try:
#         os.rename(tmp_path, path)
#         with open(path, "r") as f:
#             json.load(f)
#     except Exception:
#         try:
#             if path in os.listdir():
#                 os.remove(path)
#             if bak_path in os.listdir():
#                 os.rename(bak_path, path)
#         except Exception:
#             pass
#         raise
# 
#     try:
#         if bak_path in os.listdir():
#             os.remove(bak_path)
#     except Exception:
#         pass
# ---------------------------------------------
# 19-10-2026 
def _atomic_write_json(path, obj):
    """Save JSON with one parameter per line, backup and validation."""

    tmp_path = path + ".tmp"
    bak_path = path + ".bak"

    # Μία παράμετρος ανά γραμμή
    try:
        payload = json.dumps(obj, indent=2)
    except TypeError:
        # Εφεδρική μορφοποίηση για παλαιότερο MicroPython
        lines = []
        items = list(obj.items())

        for i, (key, value) in enumerate(items):
            line = "  {}: {}".format(
                json.dumps(key),
                json.dumps(value)
            )
            if i < len(items) - 1:
                line += ","
            lines.append(line)

        payload = "{\n" + "\n".join(lines) + "\n}"

    with open(tmp_path, "w") as f:
        f.write(payload)
        f.write("\n")

    try:
        os.sync()
    except Exception:
        pass

    # Έλεγχος πριν αντικατασταθεί το αρχικό αρχείο
    with open(tmp_path, "r") as f:
        json.load(f)

    try:
        if bak_path in os.listdir():
            os.remove(bak_path)
    except Exception:
        pass

    if path in os.listdir():
        os.rename(path, bak_path)

    try:
        os.rename(tmp_path, path)

        with open(path, "r") as f:
            json.load(f)

    except Exception:
        try:
            if path in os.listdir():
                os.remove(path)

            if bak_path in os.listdir():
                os.rename(bak_path, path)
        except Exception:
            pass
        raise

    try:
        if bak_path in os.listdir():
            os.remove(bak_path)
    except Exception:
        pass

# ------------------------------------------------
def save_settings():
    global settings_dirty
    try:
        existing_config = _load_json_file("config.json", {})
        existing_config["CURRENT_ID"] = CURRENT_ID
        existing_config["my_Power"] = my_Power
        existing_config["myFreq"] = myFreq
        existing_config["mybw"] = mybw
        existing_config["myRotate"] = myRotate
        _atomic_write_json("config.json", existing_config)
        settings_dirty = False
        print(
            "Saved: ID={}, Power={}dBm, Freq={}, bw={}, Rotate={}".format(
                CURRENT_ID, my_Power, myFreq, mybw, myRotate
            )
        )
        InfoLog("CONFIG_SAVED")
        show_current_status("Saved!")
        return True
    except Exception as e:
        print("Save error:", e)
        ErrorLog("CONFIG_SAVE", e)
        return False
# -----------------------------------------------
def toggle_swap_channels(cfg_path=VCONF_PATH):
    obj = {}
    try:
        with open(cfg_path, "r") as f:
            obj = json.load(f) or {}
    except Exception:
        obj = {}
    cur = bool(obj.get("SWAP_CHANNELS", False))
    new_val = not cur
    obj["SWAP_CHANNELS"] = new_val
    _atomic_write_json(cfg_path, obj)
    print("SWAP_CHANNELS:", new_val)
    return new_val

def handle_calibration(sensor, oled_display=None, max_cycles=50, threshold_do=0.1):
    def oled_msg(line1="", line2="", line3=""):
        if oled_display:
            try:
                oled_display.fill(0)
                oled_display.text(line1, 0, 0)
                oled_display.text(line2, 0, 13)
                oled_display.text(line3, 0, 25)
                oled_display.show()
            except: pass
    oled_msg("Start Calibr", "", "")
    last_do = None
    stable_count = 0
    do_percent = 0.0
    o2_mg = 0.0
    for cycle in range(max_cycles):
        feed_if_due(300)
        result = sensor.read_values()
        if result:
            temp, do_percent, o2_mg = result
            oled_msg("Start Calibr", "C{}: DO={:.2f}%".format(cycle+1, do_percent), "Working")
            if last_do is not None and abs(do_percent - last_do) < threshold_do:
                stable_count += 1
            else:
                stable_count = 0
            last_do = do_percent
            if stable_count >= 3:
                oled_msg("Start Calibr", "C{}: DO={:.2f}%".format(cycle+1, do_percent), "Stable ✅")
                break
        else:
            oled_msg("Start Calibr", "C{}: No data".format(cycle+1), "Working")
        sleep_coop(2000)
        gc.collect()
    oled_msg("Calibration Done", "DO={:.2f}%".format(do_percent), "O2={:.2f} mg/L".format(o2_mg))
    return do_percent, o2_mg

# ------------------------------------------------------
# Button Setup
# ------------------------------------------------------
button_next = Pin(16, Pin.IN, Pin.PULL_UP)
button_power = Pin(17, Pin.IN, Pin.PULL_UP)
button_cal = Pin(18, Pin.IN, Pin.PULL_UP)

DEBOUNCE_MS = 100
TIMEOUT_MS = 10000
last_press_time = time.ticks_ms()

show_current_status("Ready")
cal_pressed = False
settings_dirty = False

# Configuration loop
while True:
    now = time.ticks_ms()
    elapsed = time.ticks_diff(now, last_press_time)
    wdt_mark_progress()
    feed_if_due(400)
    if not button_next.value():
        sleep_coop(DEBOUNCE_MS)
        if not button_next.value():
            next_id()
            last_press_time = now
            while not button_next.value():
                sleep_coop(20)
    if not button_power.value():
        sleep_coop(DEBOUNCE_MS)
        if not button_power.value():
            next_power()
            last_press_time = now
            while not button_power.value():
                sleep_coop(20)
    if not button_cal.value():
        if not cal_pressed:
            cal_pressed = True
            try:
                with open(VCONF_PATH) as f:
                    data = json.load(f)
                    cur_swap = data.get("SWAP_CHANNELS", False)
            except:
                cur_swap = False
            print(f"Button pressed — SWAP={cur_swap}")
            sleep_coop(50)
    else:
        if cal_pressed:
            cal_pressed = False
            feed_if_due(200)
            try:
                test = sensor.read_values()
                if test:
                    print("Sensor found - starting calibration")
                    handle_calibration(sensor, oled)
                else:
                    new_swap = toggle_swap_channels(VCONF_PATH)
                    print("No sensor - toggled SWAP to", new_swap)
                    LEDBL.value(1); sleep_coop(200); LEDBL.value(0)
                    LEDBL.value(1); sleep_coop(200); LEDBL.value(0)
            except Exception as e:
                print("Error:", e)
            sleep_coop(500)
    if elapsed > TIMEOUT_MS:
        if settings_dirty:
            save_settings()
        else:
            print("Settings unchanged - flash write skipped")
        break
    feed_if_due(400)
    sleep_coop(50)

# ========================= LoRa Initialization ===========================
sx = SX1262(spi_bus=1, clk=10, mosi=11, miso=12, cs=3, irq=20, rst=15, gpio=2)
sx.begin(freq=myFreq, bw=mybw, sf=12, cr=8, syncWord=0x12,
         power=my_Power, currentLimit=60.0, preambleLength=8,
         implicit=False, implicitLen=0xFF,
         crcOn=True, txIq=False, rxIq=False,
         tcxoVoltage=1.7, useRegulatorLDO=False, blocking=True)

# ========================= Sensor Functions ======================
dSensor = dht.DHT22(Pin(7))
analog_value = ADC(27)

water_temperature = 79.12
Dissolved_Oxygen = 8.7
water_oxygen = 3.1

def read_ODO_Sensor(sensor, stabilize=False, threshold_do=0.1, max_wait=25):
    global Dissolved_Oxygen, water_oxygen, water_temperature
    water_temperature, Dissolved_Oxygen, water_oxygen = -1, -1, -1
    try:
        wdt_mark_progress()
        result = sensor.read_values()
        wdt_mark_progress()
        if result:
            water_temperature, Dissolved_Oxygen, water_oxygen = result
        else:
            raise ValueError("No data")
    except Exception as e:
        WarnLog("ODO_READ: {}".format(e))
    return (water_temperature, Dissolved_Oxygen, water_oxygen)
# -19_08_2026 ------------------------------------------------
def read_battery_voltage(samples=16, delay_ms=5):
    try:
        total = 0
        for _ in range(samples):
            total += analog_value.read_u16()
            try:
                wdt_mark_progress()
            except:
                pass
            time.sleep_ms(delay_ms)
        avg_raw = total / samples
        # Υπολογισμός τάσης από ADC
        battery_voltage = avg_raw * 40.96 / 65535
        # Offset βαθμονόμησης από config.json
        try:
            offsetBattery = float(config.get("offsetBattery", 0.0))
        except:
            offsetBattery = 0.0
        # Θετικό ή αρνητικό offset
        battery_voltage += offsetBattery
        return battery_voltage
    except Exception as e:
        print("Battery voltage error:", e)   
        return None
# -19_08_2026 ------------------------------------------------    
def readDHT():
    try:
        wdt_mark_progress()
        dSensor.measure()
        temp = dSensor.temperature()
        hum = dSensor.humidity()
        batteryVoltage = read_battery_voltage(
            samples=16,
            delay_ms=5
        )
        wdt_mark_progress()
        return temp, hum, batteryVoltage
    except OSError as e:
        print("DHT22 error:", e)
        # Ακόμη κι αν χαλάσει ο DHT,
        # προσπάθησε να διαβάσεις την μπαταρία.
        try:
            batteryVoltage = read_battery_voltage(
                samples=16,
                delay_ms=5
            )
        except:
            batteryVoltage = None
        return None, None, batteryVoltage
    except Exception as e:
        print("readDHT error:", e)
        return None, None, None
# -19_08_2026 ------------------------------------------------

# Initial readings
Temperature1 = Humidity1 = batteryVoltage = None
v_init = {"pv": -1.0, "charge": -1.0}
wt = do = wo = -1.0
print("\n===== Initial readings =====")
Temperature1, Humidity1, batteryVoltage = readDHT()
#if Temperature1:   
if Temperature1 is not None:
    print(f"DHT: {Temperature1:.2f}C, {Humidity1:.2f}%, Battery: {batteryVoltage:.2f}V")
voltage_data = get_voltages()
water_temperature, Dissolved_Oxygen, water_oxygen = read_ODO_Sensor(sensor)

# OLED display initial
try:
    oled.fill(0)
    oled.text("Logger - " + str(CURRENT_ID), 1, 3)
    oled.text("T:{:.1f}C".format(Temperature1 or 0), 1, 13)
    oled.text("H:{:.1f}%".format(Humidity1 or 0), 66, 13)
    oled.text("V:{:.2f}V".format(batteryVoltage or 0), 1, 25)
    oled.show()
    sleep_coop(3000)
except: pass

print("Child ready")
LEDBL.value(1); sleep_coop(1000); LEDBL.value(0)

# ========== FORCE INITIAL SENSOR READ ==========
print("\n[INIT] Reading DO sensor before state machine...")
initial_do_values = None
try:
    wdt_mark_progress()
    init_res = sensor.read_values()
    wdt_mark_progress()
    if init_res:
        wt, do, wo = init_res
        initial_do_values = (wt, do, wo)
        print(f"[INIT] DO Sensor OK - Temp: {wt}C, DO: {do}%, O2: {wo}mg/L")
    else:
        print("[INIT] DO Sensor returned None")
except Exception as e:
    print(f"[INIT] DO Sensor error: {e}")
# ----------------------------------------------
# ====  Διάβασμα τάσεων στην εκκίνηση ==========
print("\n[INIT] Reading voltages...")
try:
    v_init = get_voltages()
    print(f"[INIT] PV: {v_init.get('pv', 0):.2f}V, Charge: {v_init.get('charge', 0):.2f}V")
except Exception as e:
    print(f"[INIT] Voltage error: {e}")

# Μετά το διάβασμα των τάσεων, ενημέρωση OLED:
try:
    oled.fill(0)
    oled.text("Logger - " + str(CURRENT_ID), 1, 3)
    oled.text("T:{:.1f}C".format(Temperature1 or 0), 1, 13)
    oled.text("H:{:.1f}%".format(Humidity1 or 0), 66, 13)
    oled.text("V:{:.2f}V".format(batteryVoltage or 0), 1, 25)
    oled.show()
    sleep_coop(2000)
    
    oled.fill(0)
    oled.text("Sensor ", 0, 0)
    #oled.text("F:{:.2f} - R:{:.2f}".format(v_init.get('pv', 0), v_init.get('charge', 0)), 0, 12)
    #oled.text("DO:{:.1f}% T:{:.1f}C".format(do or 0, wt or 0), 0, 25)
    oled.text("F:{:.2f}-DO:{:.1f}%".format(v_init.get('pv', 0), do or 0), 0, 12)
    #oled.text("DO:{:.1f}% T:{:.1f}C".format(do or 0, wt or 0), 0, 25)
    if wlan.isconnected():
        oled.text("OK WiFi", 0, 25)
    else:
        oled.text("NO WiFi", 0, 25)
    oled.show()
    sleep_coop(2000)
#except:
#    pass
except Exception as e:
    print("[OLED] Error:", e)
    #
# ---------------------------------------------
# ========== RELAY CONTROL FUNCTIONS ==========
def control_relay(relay_num, action):
    """Active-HIGH relays: a=ON (1), k=OFF (0)."""

    target = Relay1 if relay_num == "1" else Relay2 if relay_num == "2" else None

    if target is None:
        return False, "INVALID_RELAY"

    if action == "a":
        target.value(1)
        state = "ON"

    elif action == "k":
        target.value(0)
        state = "OFF"

    else:
        return False, "INVALID_ACTION"

    print("Relay {} -> {}".format(relay_num, state))
    return True, state
# ---------------------------------------------

def handle_rele_message(message):
    """Accept RELE<ID>a1/k1/a2/k2, or RELE<ID>P<dBm>."""
    if not message.startswith("RELE"):
        return False
    cmd = message[4:]
    if not cmd.startswith(CURRENT_ID):
        return True  # message for another device
    suffix = cmd[len(CURRENT_ID):]
    if suffix.startswith("P"):
        # Retain the source protocol's power adjustment command.
        global my_Power  
        try:
            power = int(suffix[1:])
            if not -5 <= power <= 20:
                raise ValueError("out of range")
        except (ValueError, TypeError):
            reply = "FAIL,{},BAD_POWER#".format(CURRENT_ID)
        else:
            old_power = my_Power
            my_Power = power
            if save_settings():
                reply = "ACK,{},POWER,{}#".format(CURRENT_ID, power)
            else:
                my_Power = old_power
                reply = "FAIL,{},SAVE_ERROR#".format(CURRENT_ID)
    elif len(suffix) == 2:
        success, state = control_relay(suffix[1], suffix[0])
        if success:
            reply = "ACK,{},R{},{}#".format(CURRENT_ID, suffix[1], state)
        else:
            reply = "FAIL,{},{}#".format(CURRENT_ID, state)
    else:
        reply = "FAIL,{},BAD_COMMAND#".format(CURRENT_ID)
    try:
        sx.send(reply.encode("utf-8"))
        print("RELE response:", reply)
    except Exception as e:
        print("RELE ACK send error:", e)
    return True

# ========== STATE MACHINE CONSTANTS ==========
STATE_IDLE = 1
STATE_PROCESS = 2
STATE_SEND_PONG = 3
STATE_READ_SENSORS = 4
STATE_ERROR = 5
start_blue_led_heartbeat()
class ChildStateMachine:
    def __init__(self):
        self.state = STATE_IDLE
        self.last_state_change = time.ticks_ms()
        self.last_rssi = 0
        self.last_snr = 0
        now = time.ticks_ms()
        self.last_any_rx_time = now
        self.last_valid_ping_time = now
        self.last_lora_reinit_time = now
        self.last_gc_time = now
        self.lora_reinit_count = 0
        self.Ping_Counter = 0
        # Cache δεδομένων
        self.data_cache = {
            "t": 0.0, "h": 0.0, "v": 0.0,
            "wt": -1.0, "do": -1.0, "wo": -1.0,
            "pv": 0.0, "ch": 0.0
        }
        
        # Φόρτωσε αρχικές τιμές DO αν υπάρχουν
        try:
            if 'initial_do_values' in globals() and initial_do_values:
                self.data_cache["wt"], self.data_cache["do"], self.data_cache["wo"] = initial_do_values
                print(f"[CACHE] Initial DO values loaded: {self.data_cache['do']:.2f}%")
        except:
            pass
        
    def transition(self, new_state):
        self.state = new_state
        self.last_state_change = time.ticks_ms()

    def run(self):
        print("\n" + "="*50)
        print(f"CHILD STATE MACHINE | ID: {CURRENT_ID}")
        print("="*50)
        
        # Initial sensor read
        try:
            t, h, v = readDHT()
            #if t:
            if t is not None and h is not None and v is not None:
                self.data_cache.update({"t": t, "h": h, "v": v})
        except: pass
        
        start_time_ms = time.ticks_ms()
        
        while True:
            try:
                wdt_mark_progress()
                feed_if_due(500)
                   
                # STATE: IDLE
                if self.state == STATE_IDLE:
                    # ΕΝΤΟΛΉ για το led που θα αναβοσβήνει
#                     idle_led_tick()
#                     print("IDLE LED =", LEDBL.value())
                    # ----------------------
                    # OTA runs only during IDLE. Network requests are blocking;
                    # PING/RELE packets can be missed during a check.
                    # The service self-throttles to 10/5 minute intervals.
                    try:
                        ota.ota_service(feed_cb=wdt_mark_progress)
                    except Exception as ota_exc:
                        print("[OTA] Service error:", ota_exc)
                    
                    #msg, err = sx.recv(timeout_ms=100)
                    #print("ΠΡΙΝ ΤΟ LORA RECV")
                    msg, err = sx.recv(timeout_ms=100)
                    #print("ΜΕΤΑ ΤΟ LORA RECV")
                    
                    if msg and not err:
                        self.last_any_rx_time = time.ticks_ms()
                        decoded = msg.decode("utf-8", "ignore").strip()
                        if decoded.startswith("RELE"):
                            # πάει να ενεργοποιήσει Ρελε
                            handle_rele_message(decoded)
                            continue
                        if decoded.startswith("PING,"):
                            parts = decoded.split(",")
                            target = parts[1].strip() if len(parts) > 1 else ""
                            if target == CURRENT_ID.strip():
                                self.last_valid_ping_time = time.ticks_ms() 
                                self.last_rssi = sx.getRSSI()
                                self.last_snr = sx.getSNR()
                                print(f"PING from {target} | RSSI:{self.last_rssi} SNR:{self.last_snr}")
                                # ----------------------
                                self.Ping_Counter += 1
                                if self.Ping_Counter > 100:
                                    self.Ping_Counter = 1
                                # -----------------------
                                try:
                                    oled.fill(0)
                                    oled.text("PING OK > :", 0, 0)
                                    oled.text("{:3d}".format(self.Ping_Counter), 67, 0)
                                    oled.text(f"ID: {CURRENT_ID}", 0, 13)
                                    oled.text(f"R:{self.last_rssi} S:{self.last_snr}", 0, 25)
                                    oled.show()
                                except Exception as oled_exc:
                                    disable_oled(oled_exc)
                                self.transition(STATE_PROCESS)
                
                # STATE: PROCESS
                elif self.state == STATE_PROCESS:
                    LEDBL.value(1)

                    # Το voltage_reader3sa επιστρέφει στο πεδίο 'pv'
                    # το ρεύμα του Φ/Β σε Ampere.
                    v_sys = get_voltages()
                    current_a = v_sys.get('pv', -1.0)

                    # Κρατάμε την ίδια θέση/χαρακτηριστικό στο LoRa packet:
                    # παλιό PV -> τώρα ρεύμα σε Ampere.
                    self.data_cache["pv"] = current_a

                    # Η τάση μπαταρίας είναι ήδη αποθηκευμένη στο cache
                    # από το GP27. Στην παλιά θέση Charge στέλνουμε ισχύ:
                    # Watt = Ampere x Battery Voltage.
                    battery_v = self.data_cache.get("v", 0.0)

                    if current_a >= 0 and battery_v is not None and battery_v > 0:
                        power_w = current_a * battery_v
                    else:
                        power_w = -1.0

                    self.data_cache["ch"] = power_w

                    print(
                        "PV Current: {:.3f} A | Battery: {:.2f} V | Power: {:.2f} W".format(
                            current_a,
                            battery_v if battery_v is not None else 0.0,
                            power_w
                        )
                    )

                    self.transition(STATE_SEND_PONG)
                
                # STATE: SEND_PONG
                elif self.state == STATE_SEND_PONG:
                    response = (
                        f"PONG {CURRENT_ID},"
                        f"{self.data_cache['t']:.2f},{self.data_cache['h']:.2f},{self.data_cache['v']:.2f},"
                        f"{self.data_cache['wt']:.2f},{self.data_cache['do']:.2f},{self.data_cache['wo']:.2f},"
                        f"{self.data_cache['pv']:.2f},{self.data_cache['ch']:.2f}"
                    ).encode()
                    LEDP.value(1)
                    sx.send(response)
                    sleep_coop(10)
                    LEDP.value(0)
                    print(f"Sent: {response}")
                    LEDBL.value(0)
                    self.transition(STATE_READ_SENSORS)
                
                # STATE: READ_SENSORS
                elif self.state == STATE_READ_SENSORS:
                    print("[SENSOR] Reading...")
                    t, h, v = readDHT()
                    if t is not None and h is not None and v is not None:
                        self.data_cache["t"], self.data_cache["h"], self.data_cache["v"] = t, h, v
                    
                    # Direct sensor read - NO wrapper, just WDT
                    try:
                        wdt_mark_progress()
                        res = sensor.read_values()  # Original working call
                        wdt_mark_progress()
                        if res:
                            self.data_cache["wt"], self.data_cache["do"], self.data_cache["wo"] = res
                            print(f"DO: {self.data_cache['do']:.2f}%")
                        else:
                            print("No sensor data")
                    except Exception as e:
                        print(f"Sensor error: {e}")
                    
                    self.transition(STATE_IDLE)
                    try:
                        oled.fill_rect(0, 25, 128, 10, 0)
                        oled.text("...wait master", 0, 25)
                        oled.show()
                    except Exception as oled_exc:
                        disable_oled(oled_exc)
                
                # STATE: ERROR
                elif self.state == STATE_ERROR:
                    print("Recovering...")
                    sleep_coop(2000)
                    self.transition(STATE_IDLE)
                
                # Staged LoRa recovery. A missing valid PING is not an immediate MCU fault.
                now_ms = time.ticks_ms()
                no_valid_ping_ms = time.ticks_diff(now_ms, self.last_valid_ping_time)
                if no_valid_ping_ms > 600000 and time.ticks_diff(now_ms, self.last_lora_reinit_time) > 600000:
                    WarnLog("LORA_NO_VALID_PING_10MIN")
                    print("[LoRa] No valid PING for 10 min - reinitializing radio")
                    try:
                        sx.begin(freq=myFreq, bw=mybw, sf=12, cr=8, syncWord=0x12,
                                 power=my_Power, currentLimit=60.0, preambleLength=8,
                                 implicit=False, implicitLen=0xFF, crcOn=True,
                                 txIq=False, rxIq=False, tcxoVoltage=1.7,
                                 useRegulatorLDO=False, blocking=True)
                        self.last_lora_reinit_time = now_ms
                        self.lora_reinit_count += 1
                    except Exception as radio_exc:
                        ErrorLog("LORA_REINIT", radio_exc)

                if no_valid_ping_ms > 3600000:
                    ErrorLog("LORA_NO_VALID_PING_60MIN")
                    flush_logs()
                    machine.reset()

                # Periodic 24-hour uptime reset.
                if time.ticks_diff(now_ms, start_time_ms) > 86400000:
                    InfoLog("PERIODIC_24H_RESET")
                    flush_logs()
                    machine.reset()

                sleep_coop(5)
                if time.ticks_diff(now_ms, self.last_gc_time) > 30000:
                    gc.collect()
                    self.last_gc_time = now_ms
                
            except Exception as e:
                print(f"State machine error: {e}")
                ErrorLog("STATE_MACHINE", e)
                raise

# ========== MAIN ==========
if __name__ == "__main__":
    sm = ChildStateMachine()
    try:
        sm.run()
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        ErrorLog("CRITICAL", e)
        flush_logs()
        time.sleep_ms(1000)
        machine.reset()
        

# Rob Faludi 2025-26
# WiFi based on code from Rui Santos & Sara Santos
# https://randomnerdtutorials.com/raspberry-pi-pico-w-wi-fi-micropython/
# Flicker based on code from Grant Whitney
# https://grantwinney.com/raspberry-pi-flickering-candle/
# Uses ugit for version control: https://github.com/turfptax/ugit

import uasyncio as asyncio
from machine import reset, WDT
import time
import random
import _thread
import requests
import secrets
import gc
import json
import os
import sys
import network
import ugit
from nature_api import Client
from lantern_hardware import LanternHardware
from wind_manager import WindManager
from lantern_config import DEFAULT_SETTINGS
from settings_store import load_settings, save_settings
from settings_state import LanternSettings
from weather_service import fetch_current_wind, format_weather_timestamp

version = "1.0.71"
print("Wind Lantern NatureAPI - Version:", version)

time.sleep(2) # allow usb connection on startup

# Wi-Fi credentials
ssid = secrets.WIFI_SSID  # your SSID name
password = secrets.WIFI_PASSWORD  # your WiFi password

REBOOT_STATE_FILE = "reboot_state.json"
REBOOT_BACKOFF_BASE_SECONDS = 1
REBOOT_BACKOFF_MAX_SECONDS = 900  # cap at 15 minutes

def _load_reboot_count():
    """Read the persisted consecutive-reboot count (0 if missing or unreadable)."""
    try:
        with open(REBOOT_STATE_FILE, 'r') as f:
            return int(json.loads(f.read()).get('count', 0))
    except Exception:
        return 0

def _save_reboot_count(count):
    """Persist the consecutive-reboot count to flash."""
    try:
        with open(REBOOT_STATE_FILE, 'w') as f:
            f.write(json.dumps({'count': count}))
    except Exception as e:
        print('Warning: could not persist reboot count:', e)

def apply_reboot_backoff():
    """Sleep with exponential backoff based on prior reboot count, then increment it."""
    # Reboot count survives resets via flash; used to space out repeated crash/watchdog reboots.
    count = _load_reboot_count()
    if count > 0:
        delay = min(REBOOT_BACKOFF_BASE_SECONDS * (2 ** (count - 1)), REBOOT_BACKOFF_MAX_SECONDS)
        delay += random.uniform(0, delay * 0.1)  # jitter so multiple lanterns don't retry in lockstep
        print(f"Detected {count} consecutive reboot(s); waiting {delay:.1f}s before retrying...")
        time.sleep(delay)
    _save_reboot_count(count + 1)

def clear_reboot_backoff():
    """Reset the reboot count after a healthy run."""
    _save_reboot_count(0)

runtime_settings = LanternSettings(DEFAULT_SETTINGS)
lantern_mac = None
night_mode_current = 100
night_mode_target = 100
night_mode_fade_from = 100
night_mode_fade_to = 100
night_mode_fade_start = None
initial_time_sync_complete = False
cached_timezone_offset_hours = 0
night_mode_next_update = 0
NIGHT_MODE_UPDATE_INTERVAL_MS = 1000  # recompute night mode at most once per second
PERIODIC_UPDATE_INTERVAL_MS = 15 * 60 * 1000  # check Wi-Fi and update weather every 15 minutes
TIME_SYNC_INTERVAL_MS = 12 * 60 * 60 * 1000

SETUP_AP_MAX_ATTEMPTS = 10
SETUP_AP_TIMEOUT_SECONDS = 600

terminateThread = False
candle_running = False

hardware = LanternHardware()
setup_requested = hardware.setup_button_pressed()

if not setup_requested:  # a deliberate setup request should not wait out a reboot backoff
    hardware.set_candle_duties(3, 70, 3, 70)
    apply_reboot_backoff()

wdt = WDT(timeout=8388)  # 8-second watchdog timer

nature_client = Client(ssid, password, debug_mode=False, watchdog=wdt)
ipgeolocation_key = getattr(secrets, 'IPGEOLOCATION_API_KEY', None)
if ipgeolocation_key:
    try:
        nature_client.set_api_key('ipgeolocation', ipgeolocation_key)
    except Exception as e:
        print('Warning: failed to set ipgeolocation API key:', e)

def connect_to_wifi():
    """Connect via the Nature client; returns True on success."""
    wdt.feed()
    connection_success = nature_client.connect_wifi()
    return connection_success

def get_lantern_mac():
    """Return the station MAC address as an uppercase hex string."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    mac_bytes = wlan.config('mac')
    return ''.join('{:02X}'.format(value) for value in mac_bytes)

def set_led_color(red, green, blue):
    """Set both LED sets from 0-100 brightness values (outputs are inverted: 100 = off)."""
    hardware.set_status_color(red, green)

SETUP_STATUS_COLORS = {
    "ap_started": (0, 0, 80),
    "connecting": (60, 40, 0),
    "failed": (80, 0, 0),
    "success": (0, 80, 0),
    "timeout": (80, 0, 0),
}

def setup_status_led(state):
    """on_status callback: show setup progress on the LEDs."""
    color = SETUP_STATUS_COLORS.get(state)
    if color:
        set_led_color(*color)

def save_wifi_credentials(new_ssid, new_password):
    """Rewrite only the WIFI_SSID/WIFI_PASSWORD lines of secrets.py, atomically."""
    replacements = {
        "WIFI_SSID": "WIFI_SSID = " + json.dumps(new_ssid),
        "WIFI_PASSWORD": "WIFI_PASSWORD = " + json.dumps(new_password),
    }
    with open("secrets.py", "r") as f:
        lines = f.read().split("\n")
    found = set()
    for i, line in enumerate(lines):
        for key, new_line in replacements.items():
            if line.replace(" ", "").startswith(key + "="):
                lines[i] = new_line
                found.add(key)
    for key, new_line in replacements.items():
        if key not in found:
            lines.append(new_line)
    with open("secrets.tmp", "w") as f:
        f.write("\n".join(lines))
    os.rename("secrets.tmp", "secrets.py")
    secrets.WIFI_SSID = new_ssid
    secrets.WIFI_PASSWORD = new_password

def run_wifi_setup():
    """Run the open-AP setup portal and store the new credentials; always resets afterward."""
    print("Setup button pressed, starting Wi-Fi setup AP")
    try:
        ap_name = "WindLantern-Setup-" + get_lantern_mac()[-4:]
        new_ssid, new_password = nature_client.provision_wifi(
            ap_ssid=ap_name,
            ap_password=None,
            timeout=SETUP_AP_TIMEOUT_SECONDS,
            max_attempts=SETUP_AP_MAX_ATTEMPTS,
            on_status=setup_status_led,
            firmware_version=version,
            instructions=(
                "Enter your Wi-Fi network name and password.\n"
                "On success, this form closes automatically.\n"
            ),
        )
        save_wifi_credentials(new_ssid, new_password)
        print("Wi-Fi credentials saved for:", new_ssid, "- rebooting")
    except Exception as e:
        print("Wi-Fi setup failed, rebooting:", e)
    clear_reboot_backoff()
    reset()

if setup_requested:
    run_wifi_setup()

def get_settings_url():
    """Build the remote settings URL identifying this lantern and firmware version."""
    return runtime_settings["settings_endpoint"] + '?mac=' + get_lantern_mac() + '&version=' + version

def fetch_and_display_weather_data():
    """Fetch current weather data and display the wind conditions."""
    try:
        reading = fetch_current_wind(nature_client, wdt)
        if reading is not None:
            wind_manager.set_wind(reading["wind_speed_mps"], reading["wind_gusts_mps"])
            print('Timestamp:', format_weather_timestamp(reading["timestamp"]))
            print(f"Speed: {reading['wind_speed_kph']} kph, Gusts: {reading['wind_gusts_kph']} kph")
            print(f"Speed: {reading['wind_speed_mps'] * 2.23693629:.2f} mph, Gusts: {reading['wind_gusts_mps'] * 2.23693629:.2f} mph")
            print(f"Speed {reading['wind_speed_mps']:.2f} m/s, Gusts {reading['wind_gusts_mps']:.2f} m/s")
            print("Wind factor:", wind_manager.wind_factor, "Gust factor:", wind_manager.gust_factor)
        else:
            print('No weather data available')
    except Exception as e:
        print('Error fetching weather data:', e)

def get_current_settings():
    """Snapshot the current runtime settings as a dict."""
    return runtime_settings.snapshot()

def apply_settings(values, fallback=None):
    """Normalize and apply a full or partial settings update."""
    runtime_settings.apply(values, fallback)
    wind_manager.flicker_intensity = runtime_settings["flicker_intensity"]

def fetch_address(url):
    """GET a URL and return its parsed JSON, or None on error."""
    for attempt in range(3):
        response = None
        try:
            # Free fragmented heap before the large network/JSON allocations
            gc.collect()
            print('Free memory:', gc.mem_free())
            wdt.feed()
            response = requests.get(url, timeout=8)
            print('Response code: ', response.status_code)
            text = response.text
            config_raw = json.loads(text)
            print('Configuration: ', config_raw)
            return config_raw
        except Exception as e:
            print('Error fetching settings (attempt %d/3):' % (attempt + 1), e)
            sys.print_exception(e)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            gc.collect()
        time.sleep(1)
    return None

    
async def update_settings():
    """Pull remote settings, resolve address to coordinates/timezone, save, and optionally update firmware."""
    refresh_local_timezone_offset()
    settings = fetch_address(get_settings_url())
    if settings is not None:
        remote_settings = settings.copy()
        remote_settings.pop("latitude", None)
        remote_settings.pop("longitude", None)
        previous_address = runtime_settings["address"]
        apply_settings(remote_settings)
        print("Using Address:", runtime_settings["address"])
        if runtime_settings["address"]:
            try:
                nature_client.set_location(runtime_settings["address"])
                client_location = nature_client.get_location()
                if client_location:
                    runtime_settings.set_location(
                        client_location['latitude'], client_location['longitude']
                    )
                    try:
                        nature_client.set_timezone_from_location()
                    except Exception as e:
                        print('Warning: failed to set timezone from location:', e)
                    if runtime_settings["address"] != previous_address:
                        fetch_and_display_weather_data()
                else:
                    raise ValueError('Location lookup returned no coordinates')
            except Exception as e:
                print('Error setting location:', e)
        save_settings(get_current_settings())
        if settings.get('update_firmware') is True:
            update_firmware()
    else:
        print("Using default settings")

async def wait_with_watchdog(milliseconds):
    """Async sleep for the given duration while feeding the watchdog each second."""
    start_time = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start_time) < milliseconds:
        wdt.feed()
        await asyncio.sleep_ms(1000)


def scale_brightness(duty, brightness_override=None):
    """Scale an inverted (100 = off) duty value by a brightness percentage."""
    if brightness_override is None:
        brightness_override = runtime_settings["lantern_brightness"]
    # Fade values are fractional; normalize_brightness rejects non-integers and would return 100.
    brightness_level = min(max(brightness_override, 0), 100)
    return 100 - ((100 - duty) * brightness_level / 100)

def refresh_local_timezone_offset():
    """Look up the lantern's own UTC offset by public IP; keep the cached value if the lookup fails."""
    global cached_timezone_offset_hours
    offset_seconds = nature_client.get_local_timezone_offset()
    if offset_seconds is not None:
        cached_timezone_offset_hours = int(offset_seconds / 3600)
        print('Local timezone offset (hours):', cached_timezone_offset_hours)

def get_local_timezone_offset_hours():
    """Return the cached lantern UTC offset in hours (no network I/O; safe for the LED thread)."""
    return cached_timezone_offset_hours

def is_night_window(local_hour, start_hour, end_hour):
    """True if local_hour falls in [start, end), supporting windows that wrap past midnight."""
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return local_hour >= start_hour and local_hour < end_hour
    return local_hour >= start_hour or local_hour < end_hour

def update_night_mode_state():
    """Return current brightness, fading over 10s between day and night levels."""
    global night_mode_current, night_mode_target, night_mode_fade_from, night_mode_fade_to, night_mode_fade_start, night_mode_next_update
    if not initial_time_sync_complete:
        return runtime_settings["lantern_brightness"]
    now = time.ticks_ms()
    # Skip recompute on most frames; the flicker loop calls this far more often than the state can change.
    if night_mode_fade_start is None and time.ticks_diff(now, night_mode_next_update) < 0:
        return night_mode_current
    night_mode_next_update = time.ticks_add(now, NIGHT_MODE_UPDATE_INTERVAL_MS)

    offset_hours = get_local_timezone_offset_hours()
    local_hour = (time.gmtime()[3] + offset_hours) % 24
    # print(f"Local hour: {local_hour}, Night start: {runtime_settings['night_start']}, Night end: {runtime_settings['night_end']}")
    target = runtime_settings["night_brightness"] if is_night_window(
        local_hour, runtime_settings["night_start"], runtime_settings["night_end"]
    ) else runtime_settings["lantern_brightness"]

    if night_mode_target != target:
        night_mode_fade_from = night_mode_current
        night_mode_fade_to = target
        night_mode_fade_start = now
        night_mode_target = target

    if night_mode_fade_start is not None:
        elapsed = time.ticks_diff(now, night_mode_fade_start)
        if elapsed >= 10000:
            night_mode_current = night_mode_fade_to
            night_mode_fade_start = None
        else:
            progress = elapsed / 10000
            night_mode_current = night_mode_fade_from + (night_mode_fade_to - night_mode_fade_from) * progress
    else:
        night_mode_current = night_mode_target

    return night_mode_current

_fw_deadline = 0

def _feed_during_update(_t):
    if time.ticks_diff(_fw_deadline, time.ticks_ms()) > 0:
        wdt.feed()

def _start_feeder():
    """Start the watchdog-feeding timer; retry after GC since ENOMEM here is heap fragmentation."""
    from machine import Timer
    for _ in range(3):
        try:
            gc.collect()
            feeder = Timer(-1)  # RP2040 only supports the virtual timer id -1
            feeder.init(period=1000, mode=Timer.PERIODIC, callback=_feed_during_update)
            return feeder
        except OSError as e:
            print("Feeder timer init failed:", e)
            time.sleep_ms(200)
    return None

def update_firmware():
    """Pull the latest firmware from GitHub via ugit and reset on success."""
    global _fw_deadline, terminateThread
    print("Starting safe firmware pull from GitHub...")
    wdt.feed()
    # Stop the candle thread to free heap, then hold the lights at a fixed update color.
    terminateThread = True
    for _ in range(50):
        if not candle_running:
            break
        time.sleep_ms(20)
    hardware.set_candle_duties(3, 70, 3, 70)
    gc.collect()
    # ugit blocks for long stretches; feed from a timer, but stop after 3 minutes so a hung update still resets.
    _fw_deadline = time.ticks_add(time.ticks_ms(), 180000)
    feeder = _start_feeder()
    try:
        gc.collect()
        ugit.safe_pull_all(user='faludi', repository='wind-lantern-firmware', branch=None, token=None,
                        ssid=nature_client.ssid, password=nature_client.password, ignore=['/README.md', '/LICENSE', '/secrets.py', '/.gitignore', '/reboot_state.json'],
                        isconnected=True, reset_after=True)
        wdt.feed()
        print("Safe firmware pull completed.")
    except Exception as e:
        print("Safe firmware pull failed:", e)
        if isinstance(e, MemoryError) or (isinstance(e, OSError) and e.args and e.args[0] == 12):
            print("Out of memory; resetting to retry update with a clean heap.")
            time.sleep(1)
            reset()
        # Update failed without a reset: bring the candle back.
        terminateThread = False
        _thread.start_new_thread(light_candle, ())
    finally:
        if feeder:
            feeder.deinit()

def rand_flicker_sleep():
    """Sleep a random 30-100 ms between flicker updates."""
    time.sleep(random.randint(3, 10) / 100.0)

def light_candle():
    """Thread loop: drive the red/green PWM channels to simulate a wind-affected candle."""
    global wind_manager, candle_running
    gc.collect()
    print("Starting candle thread")
    candle_running = True
    while terminateThread == False:
        factor = wind_manager.get_wind_factor()
        intensity = wind_manager.get_intensity()
        brightness_level = update_night_mode_state()
        hardware.set_primary_duties(
            scale_brightness(100 - min(random.uniform(95-factor, 100) * intensity, 100), brightness_level),
            scale_brightness(100 - min(random.uniform(31-factor-runtime_settings["color_temperature"], 32-runtime_settings["color_temperature"]) * intensity, 100), brightness_level),
        )
        rand_flicker_sleep()        
        hardware.set_secondary_duties(
            scale_brightness(100 - min(random.uniform(95-factor, 100) * intensity, 100), brightness_level),
            scale_brightness(100 - min(random.uniform(31-factor-runtime_settings["color_temperature"], 32-runtime_settings["color_temperature"]) * intensity, 100), brightness_level),
        )
        rand_flicker_sleep()
        time.sleep_ms(1)
    candle_running = False

wind_manager = WindManager(runtime_settings["flicker_intensity"])

async def main():
    """Load config, connect Wi-Fi, sync time, then periodically refresh settings and wind data."""
    wdt.feed()
    global lantern_mac, night_mode_current, night_mode_target, night_mode_fade_from, night_mode_fade_to, night_mode_fade_start, initial_time_sync_complete
    loaded_settings = load_settings()
    if loaded_settings is not None:
        apply_settings(loaded_settings, DEFAULT_SETTINGS)

    night_mode_current = runtime_settings["lantern_brightness"]
    night_mode_target = runtime_settings["lantern_brightness"]
    night_mode_fade_from = runtime_settings["lantern_brightness"]
    night_mode_fade_to = runtime_settings["lantern_brightness"]
    night_mode_fade_start = None

    connection = connect_to_wifi()
    if not connection:
        print('Could not connect to Wi-Fi, exiting')
        reset()

    lantern_mac = get_lantern_mac()
    # The settings endpoint is stored in runtime_settings.
    print('Lantern MAC:', lantern_mac)

    if runtime_settings["address"]:
        try:
            nature_client.set_location(runtime_settings["address"])
        except Exception as e:
            print('Warning: initial location setup failed:', e)

    initial_time_sync_succeeded = nature_client.sync_time()
    if not initial_time_sync_succeeded:
        print('NTP sync failed, continuing with local time if available.')
    refresh_local_timezone_offset()
    initial_time_sync_complete = True

    next_periodic_update = time.ticks_ms()
    next_settings_update = next_periodic_update
    next_time_sync = time.ticks_add(
        next_periodic_update,
        TIME_SYNC_INTERVAL_MS if initial_time_sync_succeeded else PERIODIC_UPDATE_INTERVAL_MS,
    )
    first_cycle_complete = False
    while True:
        wdt.feed()
        now = time.ticks_ms()
        if time.ticks_diff(now, next_settings_update) >= 0:
            await update_settings()
            next_settings_update = time.ticks_add(
                time.ticks_ms(), runtime_settings["settings_update_interval"] * 60 * 1000
            )

        if time.ticks_diff(time.ticks_ms(), next_periodic_update) >= 0:
            if not nature_client.wifi_connected:
                break  # exit if no connection
            if time.ticks_diff(time.ticks_ms(), next_time_sync) >= 0:
                try:
                    print('Syncing time via NTP...')
                    wdt.feed()
                    if nature_client.sync_time():
                        print(f"DateTime: {time.gmtime()[0]}-{time.gmtime()[1]:02}-{time.gmtime()[2]:02} {time.gmtime()[3]:02}:{time.gmtime()[4]:02}:{time.gmtime()[5]:02} UTC  ")
                        next_time_sync = time.ticks_add(
                            time.ticks_ms(), TIME_SYNC_INTERVAL_MS
                        )
                    else:
                        print("NTP sync failed; retrying in 15 minutes.")
                except Exception as e:
                    print("Failed to update NTP; retrying in 15 minutes.", e)
            fetch_and_display_weather_data()
            next_periodic_update = time.ticks_add(
                time.ticks_ms(), PERIODIC_UPDATE_INTERVAL_MS
            )

        if not first_cycle_complete:
            # Made it through one full cycle without a watchdog/crash reboot; stop backing off.
            clear_reboot_backoff()
            first_cycle_complete = True

        now = time.ticks_ms()
        settings_wait = max(time.ticks_diff(next_settings_update, now), 0)
        periodic_wait = max(time.ticks_diff(next_periodic_update, now), 0)
        await wait_with_watchdog(min(settings_wait, periodic_wait))

# Create an Event Loop
loop = asyncio.get_event_loop()
# Create a task to run the main function
loop.create_task(main())
_thread.start_new_thread(light_candle, ())

try:
    # Run the event loop indefinitely
    loop.run_forever()
except Exception as e:
    print('Error occurred: ', e)
except KeyboardInterrupt:
    hardware.set_candle_duties(100, 100, 100, 100)
    print('Program Interrupted by the user')
    terminateThread = True

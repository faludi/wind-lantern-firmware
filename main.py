# Rob Faludi 2025-26
# WiFi based on code from Rui Santos & Sara Santos
# https://randomnerdtutorials.com/raspberry-pi-pico-w-wi-fi-micropython/
# Flicker based on code from Grant Whitney
# https://grantwinney.com/raspberry-pi-flickering-candle/
# Uses ugit for version control: https://github.com/turfptax/ugit

import uasyncio as asyncio
from machine import Pin, PWM, reset, WDT
import time
import random
import _thread
import requests
import secrets
import gc
import json
import os
import network
import ugit
from nature_api import Client
from lantern_config import (
    DEFAULT_SETTINGS,
    normalize_brightness,
    normalize_settings,
    serialize_settings,
    settings_match,
)

version = "1.0.57.2"
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

address = DEFAULT_SETTINGS["address"]
latitude = DEFAULT_SETTINGS["latitude"]
longitude = DEFAULT_SETTINGS["longitude"]
settings_endpoint = DEFAULT_SETTINGS["settings_endpoint"]
settings_update_interval = DEFAULT_SETTINGS["settings_update_interval"]  # minutes
lantern_mac = None
day_brightness = DEFAULT_SETTINGS["lantern_brightness"]
night_brightness = DEFAULT_SETTINGS["night_brightness"]
night_start_localtime = DEFAULT_SETTINGS["night_start"]
night_end_localtime = DEFAULT_SETTINGS["night_end"]
color_temperature = DEFAULT_SETTINGS["color_temperature"]
flicker_intensity = DEFAULT_SETTINGS["flicker_intensity"]
night_mode_current = 100
night_mode_target = 100
night_mode_fade_from = 100
night_mode_fade_to = 100
night_mode_fade_start = None
initial_time_sync_complete = False
cached_timezone_offset_hours = 0
timezone_offset_next_refresh = 0
night_mode_next_update = 0
TIMEZONE_OFFSET_REFRESH_MS = 3600000  # re-check timezone offset hourly
NIGHT_MODE_UPDATE_INTERVAL_MS = 1000  # recompute night mode at most once per second

red_pin = 5
green_pin = 6
blue_pin = 7

red_pin_2 = 8
green_pin_2 = 9
blue_pin_2 = 10

SETUP_BUTTON_PIN = 16  # momentary switch to ground
SETUP_AP_MAX_ATTEMPTS = 10
SETUP_AP_TIMEOUT_SECONDS = 600

GUST_INTERVAL_LOW = 15000  # 15 seconds
GUST_INTERVAL_HIGH = 40000  # 40 seconds
GUST_LENGTH_LOW = 3000  # 3 seconds
GUST_LENGTH_HIGH = 15000  # 15 seconds
WIND_REF = 1 # wind speed that passes through unchanged; raise to boost more of the low range
WIND_EXPONENT = 0.7 # 1.0 = no change, lower = more compression (small values boosted, large reduced)

terminateThread = False

class Pulse(PWM):
    def duty(self, percent_duty):
        """Set PWM duty cycle from a 0-100 percentage."""
        return self.duty_u16(int(percent_duty/100 *65535))
    
red_pwm = Pulse(Pin(red_pin))
red_pwm.freq(300)
red_pwm.duty(100)
green_pwm = Pulse(Pin(green_pin))
green_pwm.freq(300)
green_pwm.duty(100)
blue_pwm = Pulse(Pin(blue_pin))
blue_pwm.freq(300)
blue_pwm.duty(100)
red_pwm_2 = Pulse(Pin(red_pin_2))
red_pwm_2.freq(300)
red_pwm_2.duty(100)
green_pwm_2 = Pulse(Pin(green_pin_2))
green_pwm_2.freq(300)
green_pwm_2.duty(100)
blue_pwm_2 = Pulse(Pin(blue_pin_2))
blue_pwm_2.freq(300)
blue_pwm_2.duty(100)

def setup_button_pressed():
    """Return True if the setup button is held low (debounced)."""
    button = Pin(SETUP_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    if button.value():
        return False
    time.sleep_ms(50)
    return not button.value()

setup_requested = setup_button_pressed()

if not setup_requested:  # a deliberate setup request should not wait out a reboot backoff
    red_pwm.duty(3)
    green_pwm.duty(70)
    red_pwm_2.duty(3)
    green_pwm_2.duty(70)
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
    for pwm, level in ((red_pwm, red), (green_pwm, green), (blue_pwm, blue),
                       (red_pwm_2, red), (green_pwm_2, green), (blue_pwm_2, blue)):
        pwm.duty(100 - level)

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
                "1. Enter your Wi-Fi network name and password, then select Connect. Leave the password blank only for an open network. The lights will turn yellow to indicate connecting.\n"
                "2. If the connection is unsuccessful the lights will turn red. Check the network name and password, then try again.\n"
                "3. On success, the lights will turn green, then the lantern will restart and connect to your Wi-Fi automatically."
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
    return settings_endpoint + '?mac=' + get_lantern_mac() + '&version=' + version

def parse_datetime(timestamp):
    """Convert an ISO 'YYYY-MM-DDTHH:MM' string to 'MM/DD/YYYY HH:MM UTC'."""
    # Split the timestamp into date and time
    date_str, time_str = timestamp.split('T')
    # Extract year, month, day
    year, month, day = date_str.split('-')
    # Extract hours and minutes
    hour, minute = time_str.split(':')
    # Combine into final time format
    formatted_time = f"{month}/{day}/{year} {hour:2}:{minute:2} UTC"
    return(formatted_time)

def fetch_weather_data():
    """Fetch current wind speed/gusts (km/h); returns a dict or None on failure."""
    try:
        wdt.feed()
        forecast = nature_client.get_weather("current", "wind_speed_10m,wind_gusts_10m", forecast_days=1, expiry=300)
        if not forecast or forecast.get('wind_speed_10m') is None:
            return None
        timestamp = f"{time.gmtime()[0]:04}-{time.gmtime()[1]:02}-{time.gmtime()[2]:02}T{time.gmtime()[3]:02}:{time.gmtime()[4]:02}"
        return {
            'current': {
                'wind_speed_10m': forecast.get('wind_speed_10m'),
                'wind_gusts_10m': forecast.get('wind_gusts_10m'),
                'time': timestamp
            }
        }
    except Exception as e:
        print('Error fetching weather data:', e)
        return None
    
def open_config():
    """Load normalized settings from config.json, creating it from defaults if absent."""
    try:
        with open('config.json', 'r') as f:
            config_str = f.read()
            config = json.loads(config_str)
            return normalize_settings(config, DEFAULT_SETTINGS)
    except OSError:
        print("Creating configuration file.")
        try:
            with open("config.json", "w") as f:
                f.write(serialize_settings(DEFAULT_SETTINGS))
                return DEFAULT_SETTINGS.copy()
        except Exception as e:
            print("Error storing light levels:", e)

def get_current_settings():
    """Snapshot the current settings globals as a dict."""
    return {
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "settings_endpoint": settings_endpoint,
        "settings_update_interval": settings_update_interval,
        "lantern_brightness": day_brightness,
        "night_brightness": night_brightness,
        "night_start": night_start_localtime,
        "night_end": night_end_localtime,
        "color_temperature": color_temperature,
        "flicker_intensity": flicker_intensity,
    }

def apply_settings(values, fallback=None):
    """Normalize values (falling back to current settings) and assign them to the globals."""
    global address, latitude, longitude, settings_endpoint, settings_update_interval
    global day_brightness, night_brightness, night_start_localtime, night_end_localtime
    global color_temperature, flicker_intensity

    if fallback is None:
        fallback = get_current_settings()
    settings = normalize_settings(values, fallback)
    address = settings["address"]
    latitude = settings["latitude"]
    longitude = settings["longitude"]
    settings_endpoint = settings["settings_endpoint"]
    settings_update_interval = settings["settings_update_interval"]
    day_brightness = settings["lantern_brightness"]
    night_brightness = settings["night_brightness"]
    night_start_localtime = settings["night_start"]
    night_end_localtime = settings["night_end"]
    color_temperature = settings["color_temperature"]
    flicker_intensity = settings["flicker_intensity"]
    wind_manager.flicker_intensity = flicker_intensity

def save_config():
    """Write current settings to config.json only if they differ from the stored copy."""
    settings = get_current_settings()
    try:
        with open("config.json", "r") as f:
            config = json.loads(f.read())
            if settings_match(config, settings):
                print("Configuration unchanged, not saving.")
                return
    except Exception as e:
        print("Error reading config for comparison:", e)
    try:
        with open("config.json", "w") as f:
            f.write(serialize_settings(settings))
            print("Configuration saved.")
    except Exception as e:
        print("Error saving config:", e)

def fetch_address(url):
    """GET a URL and return its parsed JSON, or None on error."""
    try:
        # Make GET request
        wdt.feed()
        response = requests.get(url, timeout=8)
        # Get response code
        response_code = response.status_code
        print('Response code: ', response_code)
        # response_content = response.content
        # print('Response content:', response_content)
        config_raw = response.json()
        # Print results
        print('Configuration: ', config_raw)
        return config_raw
    except Exception as e:
        print('Error fetching settings:', e)
        return None

    
async def update_settings():
    """Pull remote settings, resolve address to coordinates/timezone, save, and optionally update firmware."""
    global latitude, longitude
    settings = fetch_address(get_settings_url())
    if settings is not None:
        remote_settings = settings.copy()
        remote_settings.pop("latitude", None)
        remote_settings.pop("longitude", None)
        apply_settings(remote_settings)
        print("Using Address:", address)
        if address:
            try:
                nature_client.set_location(address)
                client_location = nature_client.get_location()
                if client_location:
                    latitude = float(client_location['latitude'])
                    longitude = float(client_location['longitude'])
                    try:
                        nature_client.set_timezone_from_location()
                    except Exception as e:
                        print('Warning: failed to set timezone from location:', e)
                else:
                    raise ValueError('Location lookup returned no coordinates')
            except Exception as e:
                print('Error setting location:', e)
        save_config()
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


class WindManager:
    """Tracks wind speed/gusts and simulates alternating gust and calm periods to drive flicker."""

    def __init__(self, flicker_intensity=1.0):
        self.flicker_intensity = flicker_intensity
        self.wind_factor = 0
        self.gust_factor = 0
        self.gust_ramp = random.randint(GUST_LENGTH_LOW, GUST_LENGTH_HIGH) * 0.25
        self.speed = 0
        self.gusts = 0
        self.gusting = True
        self.start_time = time.ticks_ms()
        self.delay = 0
        self.initial_run = True

    def set_wind(self, wind_speed, wind_gusts):
        """Store new wind readings (m/s) and recompute the flicker factors."""
        self.speed = wind_speed
        self.gusts = wind_gusts
        self._calc_wind_factor(self.speed, self.gusts)

    def get_wind(self):
        """Return gust speed while gusting, otherwise steady wind speed."""
        if self.gusting:
            return self.gusts
        return self.speed

    def compress(self, x, ref=WIND_REF, exponent=WIND_EXPONENT):
        # Power curve: output equals input at ref; below ref it is boosted, above it is reduced. Never negative.
        if x <= 0:
            return 0
        return ref * (x / ref) ** exponent

    def _calc_wind_factor(self, wind_speed, wind_gusts):
        """Compress readings and scale by flicker intensity into wind/gust factors."""
        self.wind_factor = self.compress(wind_speed)
        self.gust_factor = self.compress(wind_gusts)
        self.wind_factor *= self.flicker_intensity
        self.gust_factor *= self.flicker_intensity

    def _calc_gusting(self):
        """Toggle between gusting and calm states when the current period expires."""
        if time.ticks_ms() - self.start_time > self.delay:
            self.start_time = time.ticks_ms()
            self.gust_ramp = random.randint(GUST_LENGTH_LOW, GUST_LENGTH_HIGH) * 0.25
            if not self.gusting:
                self.gusting = True
                self.gust_factor = self.gust_factor * random.uniform(0.8, 1.2) # add some randomness to gust factor
                self.delay = random.randint(GUST_LENGTH_LOW, GUST_LENGTH_HIGH)
                print("Gusting for", self.delay / 1000, "secs")
            elif self.gusting:
                self.gusting = False
                self.delay = random.randint(GUST_INTERVAL_LOW, GUST_INTERVAL_HIGH)
                print("Next gust in", self.delay / 1000, "secs") 
        return self.wind_factor
    
    def get_wind_factor(self):
        """Return the wind factor, smoothly ramped between wind and gust levels at state changes."""
        self._calc_gusting()
        difference = ( self.gust_factor - self.wind_factor )
        percent = max(( 1- (time.ticks_ms() - self.start_time) / self.gust_ramp ), 0) 
        if self.gusting:
            self.initial_run = False
            # gust, reduced by the full difference with wind at first, then down to no reduction at 1/4 time
            factor = self.gust_factor - ( difference * percent )
        else:
            if self.initial_run:
                return self.wind_factor
            # wind, increased by the full difference with gusts at first, then down to no increase at 1/4 time
            factor = self.wind_factor + ( difference * percent )
        return factor

    def get_intensity(self):
        """Return a random flicker multiplier (<=1.0) whose depth grows with ramped wind speed."""
        self._calc_gusting()
        difference = ( self.gusts - self.speed )
        percent = max(( 1 - (time.ticks_ms() - self.start_time) / self.gust_ramp ), 0)
        if self.gusting:
            intensity = self.get_wind() - ( difference * percent )
        else:
            if self.initial_run:
                intensity = self.get_wind()
            else:
                intensity = self.get_wind() + ( difference * percent )
        intensity *= self.flicker_intensity
        return random.uniform(1-(intensity/100), 1.0)
    

def scale_brightness(duty, brightness_override=None):
    """Scale an inverted (100 = off) duty value by a brightness percentage."""
    if brightness_override is None:
        brightness_override = day_brightness
    brightness_level = normalize_brightness(brightness_override)
    return 100 - ((100 - duty) * brightness_level / 100)

def get_local_timezone_offset_hours():
    """Return the cached local UTC offset in hours, refreshing hourly when online."""
    global cached_timezone_offset_hours, timezone_offset_next_refresh
    if not nature_client.wifi_connected:
        return cached_timezone_offset_hours
    now = time.ticks_ms()
    if time.ticks_diff(now, timezone_offset_next_refresh) < 0:
        return cached_timezone_offset_hours
    timezone_offset_next_refresh = time.ticks_add(now, TIMEZONE_OFFSET_REFRESH_MS)
    try:
        offset_seconds = nature_client.get_local_timezone_offset()
        cached_timezone_offset_hours = int(offset_seconds / 3600)
        print('Local timezone offset (hours):', cached_timezone_offset_hours)
    except Exception as e:
        print('Warning: failed to determine local timezone offset:', e)
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
        return day_brightness
    now = time.ticks_ms()
    # Skip recompute on most frames; the flicker loop calls this far more often than the state can change.
    if night_mode_fade_start is None and time.ticks_diff(now, night_mode_next_update) < 0:
        return night_mode_current
    night_mode_next_update = time.ticks_add(now, NIGHT_MODE_UPDATE_INTERVAL_MS)

    offset_hours = get_local_timezone_offset_hours()
    local_hour = (time.gmtime()[3] + offset_hours) % 24
    # print(f"Local hour: {local_hour}, Night start: {night_start_localtime}, Night end: {night_end_localtime}")
    target = night_brightness if is_night_window(local_hour, night_start_localtime, night_end_localtime) else day_brightness

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

def update_firmware():
    """Pull the latest firmware from GitHub via ugit and reset on success."""
    from machine import Timer
    print("Starting safe firmware pull from GitHub...")
    wdt.feed()
    gc.collect()
    # ugit blocks for long stretches; feed from a timer, but stop after 5 minutes so a hung update still resets.
    deadline = time.ticks_add(time.ticks_ms(), 300000)
    def feed_during_update(_t):
        if time.ticks_diff(deadline, time.ticks_ms()) > 0:
            wdt.feed()
    feeder = Timer(-1)  # RP2040 only supports the virtual timer id -1
    feeder.init(period=1000, mode=Timer.PERIODIC, callback=feed_during_update)
    try:
        gc.collect()
        ugit.safe_pull_all(user='faludi', repository='wind-lantern-firmware', branch=None, token=None,
                        ssid=nature_client.ssid, password=nature_client.password, ignore=['/README.md', '/LICENSE', '/secrets.py', '/.gitignore', '/reboot_state.json'],
                        isconnected=True, reset_after=True)
        wdt.feed()
        print("Safe firmware pull completed.")
    except Exception as e:
        print("Safe firmware pull failed:", e)
    finally:
        feeder.deinit()

def rand_flicker_sleep():
    """Sleep a random 30-100 ms between flicker updates."""
    time.sleep(random.randint(3, 10) / 100.0)

def light_candle():
    """Thread loop: drive the red/green PWM channels to simulate a wind-affected candle."""
    global wind_manager
    gc.collect()
    print("Starting candle thread")
    while terminateThread == False:
        factor = wind_manager.get_wind_factor()
        intensity = wind_manager.get_intensity()
        brightness_level = update_night_mode_state()
        red_pwm.duty(scale_brightness(100 - min(random.uniform(95-factor, 100) * intensity, 100), brightness_level))
        red_pwm_2.duty(scale_brightness(100 - min(random.uniform(95-factor, 100) * intensity, 100), brightness_level))
        green_pwm.duty(scale_brightness(100 - min(random.uniform(31-factor-color_temperature, 32-color_temperature) * intensity, 100), brightness_level))
        green_pwm_2.duty(scale_brightness(100 - min(random.uniform(31-factor-color_temperature, 32-color_temperature) * intensity, 100), brightness_level))
        rand_flicker_sleep()
        time.sleep_ms(1)

wind_manager = WindManager(flicker_intensity)

async def main():
    """Load config, connect Wi-Fi, sync time, then periodically refresh settings, NTP, and wind data."""
    wdt.feed()
    global address, latitude, longitude, settings_endpoint, settings_update_interval, lantern_mac, day_brightness, night_brightness, night_start_localtime, night_end_localtime, color_temperature, flicker_intensity, night_mode_current, night_mode_target, night_mode_fade_from, night_mode_fade_to, night_mode_fade_start, initial_time_sync_complete
    settings = open_config()
    if settings is not None:
        apply_settings(settings, DEFAULT_SETTINGS)

    night_mode_current = day_brightness
    night_mode_target = day_brightness
    night_mode_fade_from = day_brightness
    night_mode_fade_to = day_brightness
    night_mode_fade_start = None

    connection = connect_to_wifi()
    if not connection:
        print('Could not connect to Wi-Fi, exiting')
        reset()

    lantern_mac = get_lantern_mac()
    # settings_endpoint = get_settings_url()
    print('Lantern MAC:', lantern_mac)

    if address:
        try:
            nature_client.set_location(address)
        except Exception as e:
            print('Warning: initial location setup failed:', e)

    if not nature_client.sync_time():
        print('NTP sync failed, continuing with local time if available.')
    initial_time_sync_complete = True

    next_sync = time.time()
    first_cycle_complete = False
    while True:
        wdt.feed()
        if not nature_client.wifi_connected:
            break # exit if no connection
        await update_settings()
        if (time.time() >= next_sync):
            try:
                print('Syncing time via NTP...')
                wdt.feed()
                nature_client.sync_time()
                print(f"DateTime: {time.gmtime()[0]}-{time.gmtime()[1]:02}-{time.gmtime()[2]:02} {time.gmtime()[3]:02}:{time.gmtime()[4]:02}:{time.gmtime()[5]:02} UTC  ")
                next_sync = time.time() + 43200 # update every 12 hours
            except Exception as e:
                next_sync = time.time() + 600 # try again in 10 minutes
                print("Failed to update NTP or solar data, retrying in 10 minutes.", e)
        try:
            # Fetch and display weather data using nature_api
            weather = fetch_weather_data()
            if weather is not None:
                # print('Weather Data:', weather)
                wind_speed = weather['current']['wind_speed_10m']*0.27778
                wind_gusts = weather['current']['wind_gusts_10m']*0.27778
                timestamp = weather['current']['time']
                wind_manager.set_wind(wind_speed, wind_gusts)
                print('Timestamp:', parse_datetime(timestamp))
                print(f"Speed: {weather['current']['wind_speed_10m']} kph, Gusts: {weather['current']['wind_gusts_10m']} kph")
                print(f"Speed: {wind_speed * 2.23693629:.2f} mph, Gusts: {wind_gusts * 2.23693629:.2f} mph") 
                print(f"Speed {wind_speed:.2f} m/s, Gusts {wind_gusts:.2f} m/s")
                print("Wind factor:", wind_manager.wind_factor, "Gust factor:", wind_manager.gust_factor)
            else:
                print('No weather data available')
        except Exception as e:
            print('Error fetching weather data:', e)
        if not first_cycle_complete:
            # Made it through one full cycle without a watchdog/crash reboot; stop backing off.
            clear_reboot_backoff()
            first_cycle_complete = True
        await wait_with_watchdog(settings_update_interval * 60 * 1000)

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
    red_pwm.duty(100)
    red_pwm_2.duty(100)
    green_pwm.duty(100)
    green_pwm_2.duty(100)
    print('Program Interrupted by the user')
    terminateThread = True

import json


DEFAULT_SETTINGS = {
    "address": "350 5th Avenue, New York, NY",
    "latitude": 40.7484773,
    "longitude": -73.9881643,
    "settings_endpoint": "https://shinyshape.com/windlantern/lantern_checkin.php",
    "settings_update_interval": 15,
    "lantern_brightness": 100,
    "night_brightness": 20,
    "night_start": 22,
    "night_end": 8,
    "color_temperature": 0,
    "flicker_intensity": 1.0,
}


def normalize_brightness(value):
    try:
        return min(max(float(value), 0), 100)
    except (TypeError, ValueError):
        return 100


def normalize_color_temperature(value):
    try:
        return min(max(float(value), -10), 10)
    except (TypeError, ValueError):
        return 0


def normalize_flicker_intensity(value):
    try:
        return min(max(float(value), 0), 5)
    except (TypeError, ValueError):
        return 1


def normalize_settings_update_interval(value):
    try:
        interval = int(value)
        if float(value) != interval:
            return 15
        return min(max(interval, 1), 10080)
    except (TypeError, ValueError):
        return 15


def normalize_night_brightness(value):
    try:
        return min(max(float(value), 0), 100)
    except (TypeError, ValueError):
        return 10


def normalize_night_hour(value):
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return 0
    if hour < 0:
        return 0
    if hour > 23:
        return 23
    return hour


_NORMALIZERS = {
    "settings_update_interval": normalize_settings_update_interval,
    "lantern_brightness": normalize_brightness,
    "night_brightness": normalize_night_brightness,
    "night_start": normalize_night_hour,
    "night_end": normalize_night_hour,
    "color_temperature": normalize_color_temperature,
    "flicker_intensity": normalize_flicker_intensity,
}


def normalize_settings(values, fallback=None):
    settings = DEFAULT_SETTINGS.copy()
    if fallback is not None:
        settings.update(fallback)

    for key, value in values.items():
        if key in DEFAULT_SETTINGS:
            normalizer = _NORMALIZERS.get(key)
            settings[key] = normalizer(value) if normalizer else value

    return settings


def settings_match(existing, settings):
    return all(existing.get(key) == settings.get(key) for key in DEFAULT_SETTINGS)


def serialize_settings(settings):
    return json.dumps(normalize_settings(settings))

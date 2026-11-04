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


def _normalize_integer_range(value, minimum, maximum, fallback):
    try:
        normalized = int(value)
        if float(value) != normalized:
            return fallback
        return min(max(normalized, minimum), maximum)
    except (TypeError, ValueError, OverflowError):
        return fallback


def normalize_brightness(value, fallback=100):
    return _normalize_integer_range(value, 0, 100, fallback)


def normalize_color_temperature(value, fallback=0):
    return _normalize_integer_range(value, -10, 10, fallback)


def normalize_flicker_intensity(value, fallback=1):
    try:
        return min(max(float(value), 0), 5)
    except (TypeError, ValueError):
        return fallback


def normalize_settings_update_interval(value, fallback=15):
    return _normalize_integer_range(value, 1, 10080, fallback)


def normalize_night_brightness(value, fallback=10):
    return _normalize_integer_range(value, 0, 100, fallback)


def normalize_night_hour(value, fallback=0):
    return _normalize_integer_range(value, 0, 23, fallback)


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
            settings[key] = normalizer(value, settings[key]) if normalizer else value

    return settings


def settings_match(existing, settings):
    return all(existing.get(key) == settings.get(key) for key in DEFAULT_SETTINGS)


def serialize_settings(settings):
    return json.dumps(normalize_settings(settings))

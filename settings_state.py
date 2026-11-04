from lantern_config import DEFAULT_SETTINGS, normalize_settings


class LanternSettings:
    """Hold the normalized runtime settings and apply partial updates."""

    def __init__(self, values=None):
        if values is None:
            values = DEFAULT_SETTINGS
        self._values = normalize_settings(values, DEFAULT_SETTINGS)

    def __getitem__(self, key):
        return self._values[key]

    def snapshot(self):
        return self._values.copy()

    def apply(self, values, fallback=None):
        if fallback is None:
            fallback = self._values
        self._values = normalize_settings(values, fallback)
        return self.snapshot()

    def set_location(self, latitude, longitude):
        self._values["latitude"] = float(latitude)
        self._values["longitude"] = float(longitude)
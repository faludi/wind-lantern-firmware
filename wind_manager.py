import random
import time


GUST_INTERVAL_LOW = 15000
GUST_INTERVAL_HIGH = 40000
GUST_LENGTH_LOW = 3000
GUST_LENGTH_HIGH = 15000
WIND_REF = 1
WIND_EXPONENT = 0.7


class WindManager:
    """Track wind readings and simulate alternating gust and calm periods."""

    def __init__(self, flicker_intensity=1.0, clock=None, random_source=None):
        self._clock = clock if clock is not None else time
        self._random = random_source if random_source is not None else random
        self.flicker_intensity = flicker_intensity
        self.wind_factor = 0
        self.gust_factor = 0
        self.gust_ramp = self._random.randint(GUST_LENGTH_LOW, GUST_LENGTH_HIGH) * 0.25
        self.speed = 0
        self.gusts = 0
        self.gusting = True
        self.start_time = self._clock.ticks_ms()
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

    def compress(self, value, ref=WIND_REF, exponent=WIND_EXPONENT):
        if value <= 0:
            return 0
        return ref * (value / ref) ** exponent

    def _calc_wind_factor(self, wind_speed, wind_gusts):
        self.wind_factor = self.compress(wind_speed) * self.flicker_intensity
        self.gust_factor = self.compress(wind_gusts) * self.flicker_intensity

    def _calc_gusting(self):
        if self._clock.ticks_ms() - self.start_time > self.delay:
            self.start_time = self._clock.ticks_ms()
            self.gust_ramp = self._random.randint(GUST_LENGTH_LOW, GUST_LENGTH_HIGH) * 0.25
            if not self.gusting:
                self.gusting = True
                self.gust_factor *= self._random.uniform(0.8, 1.2)
                self.delay = self._random.randint(GUST_LENGTH_LOW, GUST_LENGTH_HIGH)
                print("Gusting for", self.delay / 1000, "secs")
            else:
                self.gusting = False
                self.delay = self._random.randint(GUST_INTERVAL_LOW, GUST_INTERVAL_HIGH)
                print("Next gust in", self.delay / 1000, "secs")
        return self.wind_factor

    def get_wind_factor(self):
        """Return the wind factor, ramped between wind and gust levels."""
        self._calc_gusting()
        difference = self.gust_factor - self.wind_factor
        percent = max(1 - (self._clock.ticks_ms() - self.start_time) / self.gust_ramp, 0)
        if self.gusting:
            self.initial_run = False
            factor = self.gust_factor - (difference * percent)
        elif self.initial_run:
            return self.wind_factor
        else:
            factor = self.wind_factor + (difference * percent)
        return factor

    def get_intensity(self):
        """Return a random flicker multiplier (<=1.0) based on ramped wind speed."""
        self._calc_gusting()
        difference = self.gusts - self.speed
        percent = max(1 - (self._clock.ticks_ms() - self.start_time) / self.gust_ramp, 0)
        if self.gusting:
            intensity = self.get_wind() - (difference * percent)
        elif self.initial_run:
            intensity = self.get_wind()
        else:
            intensity = self.get_wind() + (difference * percent)
        intensity *= self.flicker_intensity
        return self._random.uniform(1 - (intensity / 100), 1.0)
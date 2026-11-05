class RuntimeSchedule:
    """Track settings, periodic-work, and NTP deadlines using MicroPython ticks."""

    def __init__(self, ticks, periodic_interval_ms, time_sync_interval_ms, initial_time_sync_succeeded):
        self._ticks = ticks
        now = ticks.ticks_ms()
        self.next_settings_update = now
        self.next_periodic_update = now
        initial_sync_delay = time_sync_interval_ms if initial_time_sync_succeeded else periodic_interval_ms
        self.next_time_sync = ticks.ticks_add(now, initial_sync_delay)
        self.periodic_interval_ms = periodic_interval_ms
        self.time_sync_interval_ms = time_sync_interval_ms

    def settings_due(self, now):
        return self._ticks.ticks_diff(now, self.next_settings_update) >= 0

    def periodic_due(self, now):
        return self._ticks.ticks_diff(now, self.next_periodic_update) >= 0

    def time_sync_due(self, now):
        return self._ticks.ticks_diff(now, self.next_time_sync) >= 0

    def schedule_settings_update(self, now, interval_ms):
        self.next_settings_update = self._ticks.ticks_add(now, interval_ms)

    def schedule_periodic_update(self, now):
        self.next_periodic_update = self._ticks.ticks_add(now, self.periodic_interval_ms)

    def schedule_time_sync(self, now):
        self.next_time_sync = self._ticks.ticks_add(now, self.time_sync_interval_ms)

    def wait_ms(self, now):
        settings_wait = max(self._ticks.ticks_diff(self.next_settings_update, now), 0)
        periodic_wait = max(self._ticks.ticks_diff(self.next_periodic_update, now), 0)
        return min(settings_wait, periodic_wait)
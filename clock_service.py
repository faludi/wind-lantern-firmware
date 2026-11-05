class LanternClock:
    """Coordinate clock synchronization and cached local-timezone conversion."""

    def __init__(self, client, output=print):
        self._client = client
        self._output = output
        self._local_offset_hours = 0

    def sync_time(self):
        return self._client.sync_time()

    def refresh_local_offset(self):
        try:
            offset_seconds = self._client.get_local_timezone_offset()
        except Exception as error:
            self._output("Error fetching local timezone offset:", error)
            return self._local_offset_hours

        if offset_seconds is not None:
            self._local_offset_hours = int(offset_seconds / 3600)
            self._output("Local timezone offset (hours):", self._local_offset_hours)
        return self._local_offset_hours

    def local_hour(self, utc_hour):
        return (utc_hour + self._local_offset_hours) % 24
import time


def format_weather_timestamp(timestamp):
    """Convert an ISO 'YYYY-MM-DDTHH:MM' string to display time in UTC."""
    date_text, time_text = timestamp.split('T')
    year, month, day = date_text.split('-')
    hour, minute = time_text.split(':')
    return "{}/{}/{} {}:{} UTC".format(month, day, year, hour, minute)


def fetch_current_wind(client, watchdog, clock=time, output=print):
    """Fetch current wind readings and return normalized values for the lantern."""
    try:
        watchdog.feed()
        forecast = client.get_weather(
            "current",
            "wind_speed_10m,wind_gusts_10m",
            forecast_days=1,
            expiry=300,
        )
        if not forecast or forecast.get("wind_speed_10m") is None:
            return None

        wind_speed_kph = forecast.get("wind_speed_10m")
        wind_gusts_kph = forecast.get("wind_gusts_10m")
        current_time = clock.gmtime()
        timestamp = "{:04}-{:02}-{:02}T{:02}:{:02}".format(
            current_time[0], current_time[1], current_time[2], current_time[3], current_time[4]
        )
        return {
            "wind_speed_kph": wind_speed_kph,
            "wind_gusts_kph": wind_gusts_kph,
            "wind_speed_mps": wind_speed_kph * 0.27778,
            "wind_gusts_mps": wind_gusts_kph * 0.27778,
            "timestamp": timestamp,
        }
    except Exception as error:
        output("Error fetching weather data:", error)
        return None

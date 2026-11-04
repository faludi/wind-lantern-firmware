import json

from lantern_config import DEFAULT_SETTINGS, normalize_settings, serialize_settings, settings_match


CONFIG_FILE = "config.json"


def load_settings(path=CONFIG_FILE):
    """Load normalized settings, creating defaults when the file is absent."""
    try:
        with open(path, "r") as config_file:
            config = json.loads(config_file.read())
        if not isinstance(config, dict):
            raise ValueError("configuration must be a JSON object")
        return normalize_settings(config, DEFAULT_SETTINGS)
    except OSError:
        print("Creating configuration file.")
        try:
            with open(path, "w") as config_file:
                config_file.write(serialize_settings(DEFAULT_SETTINGS))
            return DEFAULT_SETTINGS.copy()
        except Exception as error:
            print("Error storing light levels:", error)
            return None
    except (TypeError, ValueError) as error:
        print("Error reading configuration:", error)
        return DEFAULT_SETTINGS.copy()


def save_settings(settings, path=CONFIG_FILE):
    """Write settings only when their normalized values differ from the file."""
    try:
        with open(path, "r") as config_file:
            config = json.loads(config_file.read())
            if settings_match(config, settings):
                print("Configuration unchanged, not saving.")
                return False
    except Exception as error:
        print("Error reading config for comparison:", error)

    try:
        with open(path, "w") as config_file:
            config_file.write(serialize_settings(settings))
        print("Configuration saved.")
        return True
    except Exception as error:
        print("Error saving config:", error)
        return False
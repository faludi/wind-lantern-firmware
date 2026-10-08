# A library that connects to realtime weather and natural events data, using open-meteo and other sources.

import time
import network
import requests
from Url_encode import url_encode
import machine
import ntptime

__version__ = "0.1.14"

class Client:
    def __init__(self, ssid, password, debug_mode=False, watchdog=None):
        self.ssid = ssid
        self.password = password
        self.ipgeolocation_api_key = None
        self.watchdog = watchdog
        self.wifi_connected = False
        self.address = None
        self.location = None
        self.utc_offset = 0
        self.headers = {"User-Agent": "rp2"}  # Add a custom user agent
        self.debug_mode = debug_mode
        # In-memory TTL cache for fetched data: key -> { 'value': ..., 'expires_at': ... }
        self._cache = {}
        # Endpoint specifications for generic request handling
        self._endpoint_specs = {
            "weather": {
                "base": "https://api.open-meteo.com/v1/forecast",
                "param_style": "csv",
                "requires_location": True,
                "requires_key": False,
            },
            "marine": {
                "base": "https://marine-api.open-meteo.com/v1/marine",
                "param_style": "csv",
                "requires_location": True,
                "requires_key": False,
            },
            "astronomy": {
                "base": "https://api.ipgeolocation.io/v3/astronomy",
                "param_style": "apiKey+coords",
                "requires_location": True,
                "requires_key": True,
            },
            "earthquakes": {
                "base": "https://earthquake.usgs.gov/fdsnws/event/1/query",
                "param_style": "usgs",
                "requires_location": False,
                "requires_key": False,
            },
        }

    def connect_wifi(self, attempts_per_cycle=10, max_cycles=10):
        while max_cycles > 0:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)
            # Connect to network
            wlan.connect(self.ssid, self.password)
            tries = attempts_per_cycle
            while tries > 0:
                if wlan.status() >= 3:
                    self.wifi_connected = True
                    break
                tries -= 1
                if self.watchdog: self.watchdog.feed()  # Feed the watchdog if configured
                print('Waiting for Wi-Fi connection...')
                time.sleep(1)
            # Check if connection is successful
            if wlan.status() != 3:
                print('Failed to establish a network connection')
                max_cycles -= 1
            else:
                print('Connection successful!')
                network_info = wlan.ifconfig()
                print('IP address:', network_info[0])
                return True
        print('Exceeded maximum connection attempts, resetting device...')
        machine.reset()

    def sync_time(self, max_retries=5):
        for _ in range(max_retries):
            try:
                print('Syncing time via NTP...')
                if self.watchdog: self.watchdog.feed()  # Feed the watchdog if configured
                ntptime.settime()
                return True
            except Exception as e:
                print("Error syncing time:", e)
        print(f"Failed to sync time after {max_retries} attempts.")
        return False
    
    def get_local_timezone_offset(self):
        try:
            if self.watchdog: self.watchdog.feed()  # Feed the watchdog if configured
            response = requests.get(f"https://api.ipgeolocation.io/v3/timezone?apiKey={self.ipgeolocation_api_key}&ip=", headers=self.headers, timeout=10)
            if self.debug_mode:
                print(f"Response: {response.content}")
            timezone_data = response.json()
            if self.debug_mode:
                print(f"Timezone data: {timezone_data}")  # Debugging line to check the timezone data  
            if 'time_zone' in timezone_data and 'offset_with_dst' in timezone_data['time_zone']:
                offset_str = timezone_data['time_zone']['offset_with_dst']
                return int(offset_str) * 60 * 60
        except Exception as e:
            print('Error fetching local timezone offset:', e)
        return 0 # if not available, assume UTC

    def set_timezone_from_location(self):
        if not self.location:
            raise ValueError("Location is not set.")
        
        try:
            if self.watchdog: self.watchdog.feed()  # Feed the watchdog if configured
            if self.ipgeolocation_api_key: 
                response = requests.get(f"https://api.ipgeolocation.io/v3/timezone?apiKey={self.ipgeolocation_api_key}&lat={self.location['latitude']}&long={self.location['longitude']}", headers=self.headers, timeout=10)
            else:        
                response = requests.get(f"https://timeapi.io/api/v1/time/current/coordinate?latitude={self.location['latitude']}&longitude={self.location['longitude']}", headers=self.headers, timeout=10)
            if self.debug_mode:
                print(response.content)
                print('Response code: ', response.status_code)
            timezone_data = response.json()
                
            if 'utc_offset_seconds' in timezone_data:
                self.utc_offset = timezone_data['utc_offset_seconds']
            elif 'time_zone' in timezone_data and 'offset_with_dst' in timezone_data['time_zone']:
                offset_str = timezone_data['time_zone']['offset_with_dst']
                self.utc_offset = int(offset_str) * 60 * 60
            else:
                raise ValueError("UTC offset not found in timezone data.")
        except Exception as e:
            print('Error fetching timezone data:', e)
            return False
        
    def set_location(self, address):
        self.address = address
        url=url_encode()
        encoded_address = url.encode(address)
        if self.debug_mode:
            print(f"Encoded address: {encoded_address}")  # Debugging line to check the encoded address
        try:
            headers = {
                "User-Agent": "rp2"  # Add a custom user agent
            }
            if self.watchdog: self.watchdog.feed()  # Feed the watchdog if configured
            response = requests.get(f"https://nominatim.openstreetmap.org/search?q={encoded_address}&format=json&limit=1", headers=self.headers, timeout=10)
            if self.debug_mode:
                print(response.content)
            response_code = response.status_code
            location_data = response.json()
            if self.debug_mode:
                print('Response code: ', response_code)
            
            if location_data:
                self.location = {
                    "latitude": location_data[0]["lat"],
                    "longitude": location_data[0]["lon"]
                }

            else:
                raise ValueError("Location not found")
        except Exception as e:
            print('Error fetching location data:', e)

    def set_coordinates(self, latitude, longitude):
        self.location = {
            "latitude": latitude,
            "longitude": longitude
        }

    def _cache_key(self, category, parameter):
        """Create a cache key that includes category, parameter and current location.
        Falls back to a generic key if location is not set."""
        if self.location and 'latitude' in self.location and 'longitude' in self.location:
            return f"{category}:{parameter}:{self.location['latitude']},{self.location['longitude']}"
        return f"{category}:{parameter}:none"

    def check_cache(self, category, parameter, expiry):
        """Return cached value if present and unexpired, return 'expired' if it existed but expired, else None."""
        key = self._cache_key(category, parameter)
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() < entry.get('expires_at', 0):
            return entry.get('value')
        # expired
        try:
            del self._cache[key]
        except KeyError:
            pass
        return "expired"

    def set_cache(self, category, parameter, value, expiry):
        """Store a value in the cache with an expiry (seconds)."""
        key = self._cache_key(category, parameter)
        self._cache[key] = {
            'value': value,
            'expires_at': time.time() + int(expiry)
        }

    def _normalize_parameter_list(self, parameters):
        if isinstance(parameters, str):
            parameters = [param.strip() for param in parameters.split(",") if param.strip()]
        if not isinstance(parameters, list):
            raise ValueError("parameters must be a string or list of strings")
        return parameters

    def _fetch_cached_parameters(self, category, parameters, expiry):
        results = {}
        params_to_fetch = []
        for param in parameters:
            cache_return = self.check_cache(category, param, expiry)
            if cache_return is not None and cache_return != "expired":
                if self.debug_mode:
                    print(f"Cache hit for {param}: {cache_return}")
                results[param] = cache_return
            else:
                if cache_return == "expired" and self.debug_mode:
                    print(f"Cache expired for {param}")
                params_to_fetch.append(param)
        return results, params_to_fetch

    def _execute_request(self, url, expiry=900, cache_category=None, cache_key=None):
        if cache_category is not None and cache_key is not None:
            cache_return = self.check_cache(cache_category, cache_key, expiry)
            if cache_return is not None and cache_return != "expired":
                if self.debug_mode:
                    print(f"Cache hit for {cache_category}:{cache_key}")
                return cache_return
            if cache_return == "expired" and self.debug_mode:
                print(f"Cache expired for {cache_category}:{cache_key}")

        if self.debug_mode:
            print(f"Requesting URL: {url}")
        if self.watchdog: self.watchdog.feed()  # Feed the watchdog if configured
        response = requests.get(url, headers=self.headers, timeout=10)
        data = response.json()
        if self.debug_mode:
            print(f"Response data: {data}")
            print('Response code: ', response.status_code)

        if cache_category is not None and cache_key is not None:
            try:
                self.set_cache(cache_category, cache_key, data, expiry)
            except Exception:
                pass

        return data

    def _execute_parameterized_request(self, category, parameters, expiry, build_url_fn, parse_fn):
        parameters = self._normalize_parameter_list(parameters)
        single_parameter = len(parameters) == 1
        results, params_to_fetch = self._fetch_cached_parameters(category, parameters, expiry)

        if params_to_fetch:
            url = build_url_fn(params_to_fetch)
            data = self._execute_request(url, expiry=expiry)
            parsed = parse_fn(data, params_to_fetch)
            for param, val in parsed.items():
                results[param] = val
                try:
                    self.set_cache(category, param, val, expiry)
                except Exception:
                    pass

        if single_parameter:
            return results[parameters[0]]
        return results

    def _build_url_from_spec(self, spec, category, params_to_fetch, extra_opts):
        # Helper builder for simple spec styles (csv and apiKey+coords)
        lat = None
        lon = None
        if self.location and 'latitude' in self.location and 'longitude' in self.location:
            lat = self.location['latitude']
            lon = self.location['longitude']

        if spec.get('param_style') == 'csv':
            params_fetch_string = ",".join(params_to_fetch)
            url = f"{spec['base']}?latitude={lat}&longitude={lon}&{category}={params_fetch_string}"
            # pass-through common extras like forecast_days
            if extra_opts and 'forecast_days' in extra_opts:
                url += f"&forecast_days={extra_opts['forecast_days']}"
            return url

        if spec.get('param_style') == 'apiKey+coords':
            api_key = self.ipgeolocation_api_key
            url = f"{spec['base']}?apiKey={api_key}&lat={lat}&long={lon}"
            return url

        # Fallback: return base URL
        return spec.get('base')

    def _generic_get(self, endpoint_name, category, parameters, expiry=900, parse_fn=None, **opts):
        spec = self._endpoint_specs.get(endpoint_name)
        if not spec:
            raise ValueError(f"Unknown endpoint: {endpoint_name}")

        if spec.get('requires_location') and not self.location:
            raise ValueError("Location is not set.")

        if spec.get('requires_key') and not self.ipgeolocation_api_key:
            raise ValueError("API key is required for this endpoint.")

        style = spec.get('param_style')

        if style in ('csv', 'apiKey+coords'):
            def build_url_fn(params_to_fetch):
                return self._build_url_from_spec(spec, category, params_to_fetch, opts)

            def default_parse_fn(data, params_to_fetch):
                if isinstance(data, dict):
                    category_data = data.get(category)
                else:
                    category_data = None
                return {
                    parameter: category_data[parameter]
                    if isinstance(category_data, dict) and parameter in category_data
                    else None
                    for parameter in params_to_fetch
                }

            chosen_parser = parse_fn if parse_fn is not None else default_parse_fn
            return self._execute_parameterized_request(category, parameters, expiry, build_url_fn, chosen_parser)

        if style == 'usgs':
            # parameters in this case is expected to be a dict of query params
            if not isinstance(parameters, dict):
                raise ValueError('For USGS style endpoints, parameters must be a dict')
            url, query_string = self._build_usgs_url(parameters)
            return self._execute_request(url, expiry=expiry, cache_category='earthquakes', cache_key=query_string)

        raise NotImplementedError(f"Unsupported param_style: {style}")

    def get_location(self):
        if not self.location:
            return None
        return self.location

    def get_address(self):
        if not self.address:
            return None
        return self.address
    
    def get_remote_offset(self):
        return self.utc_offset

    
    def get_weather(self, category, parameters, forecast_days=1, expiry=900):
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        return self._generic_get('weather', category, parameters, expiry=expiry, forecast_days=forecast_days)
    
    def get_forecast(self, category, parameters, forecast_days=1, expiry=900):
        print(('"get_forecast" is deprecated, use "get_weather" instead.'))
        return self.get_weather(category, parameters, forecast_days=forecast_days, expiry=expiry)
    
    def get_marine(self, category, parameters, forecast_days=1, expiry=900):
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        return self._generic_get('marine', category, parameters, expiry=expiry, forecast_days=forecast_days)

    def set_api_key(self, type, key):
        if type == "ipgeolocation":
            self.ipgeolocation_api_key = key
        else:
            raise ValueError("Unsupported API type. Currently only 'ipgeolocation' is supported.")
        
    def get_astronomy(self, category, parameter, expiry=900):
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        if not self.location:
            raise ValueError("Location is not set.")

        if not self.ipgeolocation_api_key:
            raise ValueError("API key is required for astronomy data.")

        def astronomy_parser(data, params_to_fetch):
            if isinstance(data, dict):
                category_data = data.get(category)
            else:
                category_data = None

            results = {}
            for param in params_to_fetch:
                if isinstance(category_data, dict) and param in category_data:
                    results[param] = category_data[param]
                elif isinstance(data, dict) and param in data:
                    results[param] = data[param]
                else:
                    results[param] = None
            return results

        return self._generic_get('astronomy', category, parameter, expiry=expiry, parse_fn=astronomy_parser)

    def _request_hash(self, params):
        if not isinstance(params, dict):
            raise ValueError("params must be a dict")

        normalized_items = sorted((str(k), str(v)) for k, v in params.items())
        normalized = "&".join(f"{k}={v}" for k, v in normalized_items)

        h = 2166136261
        for ch in normalized:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF

        return "{:08x}".format(h)

    def _load_earthquake_id_map(self, filename):
        mapping = {}
        try:
            with open(filename, "r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    hash_key, quake_id = line.split("=", 1)
                    mapping[hash_key] = quake_id
        except OSError:
            pass
        return mapping

    def _build_usgs_url(self, params):
        # Build a USGS query string similar to previous implementation
        if not isinstance(params, dict):
            raise ValueError("params must be a dict of USGS query parameters")

        query_params = dict(params)
        query_params.setdefault('format', 'geojson')

        url_encoder = url_encode()
        query_string = "&".join(
            f"{key}={url_encoder.encode(str(value))}" for key, value in query_params.items()
        )
        return f"https://earthquake.usgs.gov/fdsnws/event/1/query?{query_string}", query_string

    def _save_earthquake_id_map(self, filename, mapping):
        try:
            with open(filename, "w") as fh:
                for hash_key, quake_id in sorted(mapping.items()):
                    fh.write(f"{hash_key}={quake_id}\n")
        except OSError as e:
            if self.debug_mode:
                print("Error saving earthquake ID map:", e)

    def _get_newest_earthquake(self, quake_data):
        if not isinstance(quake_data, dict):
            return None

        features = quake_data.get("features")
        if not isinstance(features, list) or not features:
            return None

        newest = None
        newest_time = -1
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties", {})
            if not isinstance(props, dict):
                continue
            time_value = props.get("time")
            if isinstance(time_value, (int, float)) and time_value > newest_time:
                newest_time = time_value
                newest = feature

        return newest if newest is not None else features[0]

    def get_new_earthquake(self, params, expiry=900, state_file="earthquake_ids.txt"):
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        if not isinstance(params, dict):
            raise ValueError("params must be a dict of USGS query parameters")

        quake_data = self.get_earthquakes(params, expiry=expiry)
        newest_feature = self._get_newest_earthquake(quake_data)
        if not newest_feature:
            return None

        quake_id = newest_feature.get("id")
        if not quake_id:
            return None

        query_hash = self._request_hash(params)
        id_map = self._load_earthquake_id_map(state_file)
        saved_id = id_map.get(query_hash)

        if saved_id == quake_id:
            return None

        id_map[query_hash] = quake_id
        self._save_earthquake_id_map(state_file, id_map)

        if saved_id is None:
            return None

        return quake_data

    def get_earthquakes(self, params, expiry=900):
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        if not isinstance(params, dict):
            raise ValueError("params must be a dict of USGS query parameters")

        if not params:
            raise ValueError("params must contain at least one query parameter")

        quake_url, query_string = self._build_usgs_url(params)
        if self.debug_mode:
            print(f"Requesting earthquakes with query: {query_string}")

        return self._execute_request(quake_url, expiry=expiry, cache_category='earthquakes', cache_key=query_string)

       
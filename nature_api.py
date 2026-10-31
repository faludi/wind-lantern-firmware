# A library that connects to realtime weather and natural events data, using open-meteo and other sources.

import time
import network
import socket
import requests
from Url_encode import url_encode, form_decode
import machine
import ntptime

__version__ = "0.1.17"

class WifiProvisioningError(Exception):
    """Base class for Wi-Fi provisioning failures."""

class WifiConnectError(WifiProvisioningError):
    """The submitted Wi-Fi credentials could not establish a connection."""

class WifiProvisioningTimeout(WifiProvisioningError):
    """No valid credentials were submitted before the provisioning timeout."""

_DEFAULT_PORTAL_INSTRUCTIONS = (
    "1. Enter your Wi-Fi network name and password, then select Connect. Leave the password blank only for an open network.\n"
    "2. If the connection is unsuccessful, check the network name and password, then try again.\n"
    "3. On success, the lantern will restart and connect to your Wi-Fi automatically."
)

_PORTAL_PAGE = (
    '<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Device Wi-Fi Setup</title>'
    '<style>'
    'body{{font-family:-apple-system,sans-serif;max-width:24em;margin:1em auto;padding:0 1em;color:#111}}'
    'label{{display:block;font-weight:600;margin:1.2em 0 .4em}}'
    'input[type=text],input[type=password]{{box-sizing:border-box;width:100%;font-size:18px;padding:12px;'
    'border:2px solid #555;border-radius:8px;background:#fff;color:#000;-webkit-appearance:none;appearance:none}}'
    'input[type=text]:focus,input[type=password]:focus{{outline:none;border-color:#007aff;box-shadow:0 0 0 3px #b3d4ff}}'
    '.show{{display:flex;align-items:center;font-weight:normal;margin:.8em 0}}'
    '.show input{{width:22px;height:22px;margin:0 .6em 0 0}}'
    'button{{width:100%;font-size:18px;font-weight:600;padding:14px;margin-top:1em;border:0;border-radius:8px;'
    'background:#007aff;color:#fff}}'
    '.hint{{font-weight:normal;color:#444;font-size:.85em}}'
    '</style></head>'
    '<body>'
    '<h2>Wi-Fi Setup</h2>{msg}<div style="white-space:pre-wrap;line-height:1.5">{instructions}</div>'
    '<form method="POST" action="/save">'
    '<label for="s">Network name (SSID)</label>'
    '<input id="s" type="text" name="ssid" list="n" maxlength="32" required value="{ssid}" '
    'autocapitalize="none" autocorrect="off" spellcheck="false">'
    '<datalist id="n">{opts}</datalist>'
    '<label for="pw">Password<br><span class="hint">8-63 characters, leave blank for an open network</span></label>'
    '<input name="password" id="pw" type="password" maxlength="63" autocapitalize="none" autocorrect="off">'
    '<label class="show"><input type="checkbox" onclick="document.getElementById(\'pw\').type=this.checked?\'text\':\'password\'"> Show password</label>'
    '<button type="submit">Connect</button></form>'
    '<footer style="margin-top:5em;color:#666;font-size:0.75em;line-height:1.5">'
    'Device MAC: {device_mac}<br>Firmware version: {firmware_version}<br>'
    'NatureAPI version: {nature_api_version}</footer></body></html>'
)

_MESSAGE_PAGE = (
    '<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Device Wi-Fi Setup</title></head>'
    '<body style="font-family:sans-serif;max-width:24em;margin:1em auto;padding:0 1em">'
    '<h2>{title}</h2><p>{text}</p></body></html>'
)

def _html_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

class Client:
    def __init__(self, ssid, password, debug_mode=False, watchdog=None):
        """Initialize the client, connection settings, cache, and endpoint definitions."""
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

    def connect_wifi(self, attempts_per_cycle=10, max_cycles=10, provision_on_failure=False, firmware_version=None,
                     instructions=_DEFAULT_PORTAL_INSTRUCTIONS):
        """Connect to the configured Wi-Fi network, retrying until successful.

        If provision_on_failure is True, a failed connection starts provision_wifi() instead of
        resetting the device; its exceptions propagate to the caller.
        """
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
        if provision_on_failure:
            self.provision_wifi(firmware_version=firmware_version, instructions=instructions)
            return True
        print('Exceeded maximum connection attempts, resetting device...')
        machine.reset()

    def provision_wifi(self, ap_ssid="NatureAPI-Setup", ap_password=None, timeout=300, max_attempts=1, on_status=None,
                       firmware_version=None, instructions=_DEFAULT_PORTAL_INSTRUCTIONS):
        """Start an access point with a Wi-Fi setup page and return (ssid, password) once verified.

        Raises WifiConnectError after max_attempts failed connections, or WifiProvisioningTimeout
        after timeout seconds. on_status(state) receives: ap_started, connecting, failed, success, timeout.
        firmware_version is an optional version string displayed on the setup page.
        """
        if ap_password is not None and not 8 <= len(ap_password) <= 63:
            raise ValueError("ap_password must be 8-63 characters")

        def notify(state):
            if on_status:
                try:
                    on_status(state)
                except Exception as e:
                    print("on_status callback error:", e)

        def feed():
            if self.watchdog: self.watchdog.feed()

        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        ssids = self._scan_ssids(sta)
        ap = network.WLAN(network.AP_IF)
        http = dns = None
        connected = False
        attempts = 0
        error = ""
        ssid_hint = ""
        deadline = time.ticks_add(time.ticks_ms(), int(timeout * 1000))
        try:
            if ap_password:
                ap.config(essid=ap_ssid, security=4194308, password=ap_password)  # WPA2 AES PSK
            else:
                ap.config(essid=ap_ssid, security=0)  # the port otherwise defaults to a built-in WPA2 password
            ap.active(True)
            while not ap.active():
                feed()
                time.sleep_ms(100)
            try:
                device_mac = ":".join("%02x" % byte for byte in ap.config("mac"))
            except Exception:
                device_mac = "Unavailable"
            display_firmware_version = firmware_version if firmware_version is not None else "Not provided"

            http = socket.socket()
            http.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            http.bind(("0.0.0.0", 80))
            http.listen(2)
            http.setblocking(False)
            dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            dns.bind(("0.0.0.0", 53))
            dns.setblocking(False)
            notify("ap_started")

            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                feed()
                self._serve_dns(dns)
                try:
                    conn, _ = http.accept()
                except OSError:
                    time.sleep_ms(50)
                    continue
                try:
                    conn.settimeout(3)
                    request = self._read_request(conn)
                    if request is None:
                        continue
                    method, path, form = request
                    if not (method == "POST" and path == "/save"):
                        if method == "GET" and path == "/":
                            self._send_http(conn, "200 OK", self._portal_form(
                                ssids, error, ssid_hint, device_mac, display_firmware_version, instructions))
                        else:
                            self._send_http(conn, "302 Found", "", "Location: http://192.168.4.1/\r\n")
                        continue

                    new_ssid = form.get("ssid", "")
                    new_pass = form.get("password", "")
                    if not 1 <= len(new_ssid.encode()) <= 32:
                        error = "The network name must be 1-32 characters."
                    elif new_pass and not 8 <= len(new_pass) <= 63:
                        error = "The password must be 8-63 characters, or blank for an open network."
                    else:
                        error = ""
                    ssid_hint = new_ssid
                    if error:
                        self._send_http(conn, "200 OK", self._portal_form(
                            ssids, error, ssid_hint, device_mac, display_firmware_version, instructions))
                        continue

                    notify("connecting")
                    if self.debug_mode:
                        print("Provisioning: trying SSID", new_ssid, "with password ****")
                    if self._try_sta_connect(sta, new_ssid, new_pass):
                        self.ssid = new_ssid
                        self.password = new_pass
                        self.wifi_connected = True
                        self._send_http(conn, "200 OK", _MESSAGE_PAGE.format(
                            title="Connected", text="The device joined " + _html_escape(new_ssid) + ". You can close this page."))
                        connected = True
                        notify("success")
                        return new_ssid, new_pass

                    attempts += 1
                    notify("failed")
                    if attempts >= max_attempts:
                        self._send_http(conn, "200 OK", _MESSAGE_PAGE.format(
                            title="Connection failed", text="Could not join the network. Setup has ended."))
                        raise WifiConnectError("Could not connect to the submitted Wi-Fi network")
                    error = "Could not connect. Check the name and password and try again."
                    self._send_http(conn, "200 OK", self._portal_form(
                        ssids, error, ssid_hint, device_mac, display_firmware_version, instructions))
                except (OSError, ValueError):
                    continue
                finally:
                    conn.close()

            notify("timeout")
            raise WifiProvisioningTimeout("Wi-Fi provisioning timed out")
        finally:
            for s in (http, dns):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
            if connected:
                feed()
                time.sleep(1)  # let the success page reach the client before the AP goes down
            ap.active(False)

    def _scan_ssids(self, sta):
        """Return up to 20 nearby SSIDs, strongest first."""
        try:
            results = sta.scan()
        except OSError:
            return []
        if self.watchdog: self.watchdog.feed()
        best = {}
        for entry in results:
            try:
                name = entry[0].decode()
            except UnicodeError:
                continue
            if name and (name not in best or entry[3] > best[name]):
                best[name] = entry[3]
        return sorted(best, key=lambda n: best[n], reverse=True)[:20]

    def _try_sta_connect(self, sta, ssid, password, timeout_s=20):
        """Attempt a station connection; return True once an IP address is obtained."""
        sta.disconnect()
        if password:
            sta.connect(ssid, password)
        else:
            sta.connect(ssid)
        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self.watchdog: self.watchdog.feed()
            status = sta.status()
            if status == 3:
                return True
            if status < 0:
                break
            time.sleep_ms(500)
        sta.disconnect()
        return False

    def _read_request(self, conn):
        """Read one HTTP request; return (method, path, form_dict) or None if malformed."""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(512)
            if not chunk or len(data) > 2048:
                return None
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None
        length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        if length > 1024:
            return None
        while len(body) < length:
            chunk = conn.recv(512)
            if not chunk:
                break
            body += chunk
        form = {}
        if parts[0] == "POST":
            for pair in body.decode().split("&"):
                key, _, value = pair.partition("=")
                form[form_decode(key)] = form_decode(value)
        return parts[0], parts[1].split("?")[0], form

    def _send_http(self, conn, status, body, extra_headers=""):
        payload = body.encode()
        header = "HTTP/1.1 %s\r\nContent-Type: text/html\r\nContent-Length: %d\r\nConnection: close\r\n%s\r\n" % (
            status, len(payload), extra_headers)
        conn.sendall(header.encode() + payload)

    def _portal_form(self, ssids, error, ssid_hint, device_mac, firmware_version,
                     instructions=_DEFAULT_PORTAL_INSTRUCTIONS):
        opts = "".join('<option value="%s">' % _html_escape(s) for s in ssids)
        msg = '<p style="color:#b00"><b>%s</b></p>' % _html_escape(error) if error else ""
        return _PORTAL_PAGE.format(
            msg=msg,
            instructions=_html_escape(instructions),
            ssid=_html_escape(ssid_hint),
            opts=opts,
            device_mac=_html_escape(device_mac),
            firmware_version=_html_escape(str(firmware_version)),
            nature_api_version=_html_escape(__version__),
        )

    def _serve_dns(self, sock):
        """Answer any pending DNS query with the AP address so clients show the setup page."""
        try:
            data, addr = sock.recvfrom(256)
        except OSError:
            return
        i = 12
        while i < len(data) and data[i]:
            i += data[i] + 1
        end = i + 5  # terminating zero byte + QTYPE + QCLASS
        if len(data) < 12 or end > len(data):
            return
        is_a_query = data[end - 4] == 0 and data[end - 3] == 1
        answer = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\xc0\xa8\x04\x01" if is_a_query else b""
        response = (data[:2] + b"\x81\x80\x00\x01" + (b"\x00\x01" if is_a_query else b"\x00\x00")
                    + b"\x00\x00\x00\x00" + data[12:end] + answer)
        try:
            sock.sendto(response, addr)
        except OSError:
            pass

    def sync_time(self, max_retries=5):
        """Synchronize the device clock with an NTP server."""
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
        """Fetch the timezone offset associated with the current public IP."""
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
        """Look up and store the UTC offset for the configured coordinates."""
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
        """Resolve an address to coordinates and store both the address and location."""
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
        """Set the client's location directly from latitude and longitude."""
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
        """Convert a comma-separated string or list into a validated parameter list."""
        if isinstance(parameters, str):
            parameters = [param.strip() for param in parameters.split(",") if param.strip()]
        if not isinstance(parameters, list):
            raise ValueError("parameters must be a string or list of strings")
        return parameters

    def _fetch_cached_parameters(self, category, parameters, expiry):
        """Separate cached parameter values from those that still need fetching."""
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
        """Fetch JSON from a URL and optionally cache the response."""
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
        """Fetch uncached parameters, parse their response, and return the requested values."""
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
        """Build a request URL for a supported endpoint parameter style."""
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
        """Dispatch a data request according to the selected endpoint specification."""
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
                """Build the URL for this endpoint and set of uncached parameters."""
                return self._build_url_from_spec(spec, category, params_to_fetch, opts)

            def default_parse_fn(data, params_to_fetch):
                """Extract requested values from the response's category object."""
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
        """Return the configured coordinates, or None if no location is set."""
        if not self.location:
            return None
        return self.location

    def get_address(self):
        """Return the address most recently supplied to set_location."""
        if not self.address:
            return None
        return self.address
    
    def get_remote_offset(self):
        """Return the stored UTC offset in seconds."""
        return self.utc_offset

    
    def get_weather(self, category, parameters, forecast_days=1, expiry=900):
        """Fetch one or more weather parameters for the configured location."""
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        return self._generic_get('weather', category, parameters, expiry=expiry, forecast_days=forecast_days)
    
    def get_forecast(self, category, parameters, forecast_days=1, expiry=900):
        """Fetch weather data through the deprecated forecast method."""
        print(('"get_forecast" is deprecated, use "get_weather" instead.'))
        return self.get_weather(category, parameters, forecast_days=forecast_days, expiry=expiry)
    
    def get_marine(self, category, parameters, forecast_days=1, expiry=900):
        """Fetch one or more marine parameters for the configured location."""
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        return self._generic_get('marine', category, parameters, expiry=expiry, forecast_days=forecast_days)

    def set_api_key(self, type, key):
        """Store an API key for a supported service."""
        if type == "ipgeolocation":
            self.ipgeolocation_api_key = key
        else:
            raise ValueError("Unsupported API type. Currently only 'ipgeolocation' is supported.")
        
    def get_astronomy(self, category, parameter, expiry=900):
        """Fetch astronomy data for the configured location."""
        if not self.wifi_connected:
            raise ConnectionError("Wi-Fi is not connected.")

        if not self.location:
            raise ValueError("Location is not set.")

        if not self.ipgeolocation_api_key:
            raise ValueError("API key is required for astronomy data.")

        def astronomy_parser(data, params_to_fetch):
            """Extract requested astronomy values from the service response."""
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
        """Create a stable hash for a set of earthquake query parameters."""
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
        """Load saved query-hash to earthquake-ID entries from a file."""
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
        """Build a USGS query URL and its encoded query string."""
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
        """Write query-hash to earthquake-ID entries to a file."""
        try:
            with open(filename, "w") as fh:
                for hash_key, quake_id in sorted(mapping.items()):
                    fh.write(f"{hash_key}={quake_id}\n")
        except OSError as e:
            if self.debug_mode:
                print("Error saving earthquake ID map:", e)

    def _get_newest_earthquake(self, quake_data):
        """Return the newest feature in an earthquake response, if available."""
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
        """Return earthquake data when a newer event appears for the query."""
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
        """Fetch earthquake data matching the supplied USGS query parameters."""
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

       
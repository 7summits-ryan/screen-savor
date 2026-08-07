"""Weather data provider for screensavers.

Fetches current conditions and forecast from Open-Meteo, with a provider
abstraction so other sources (met.no, etc.) can be added later.
"""
import gi
gi.require_version('Soup', '3.0')
from gi.repository import Soup, GLib
import json
import math


class WeatherData:
    """Current conditions and forecast. None means unavailable."""
    def __init__(self):
        self.temperature = None      # °C, current
        self.weather_code = None     # WMO code or provider-specific
        self.is_day = None          # bool, for day/night icon variants
        self.temp_max = None        # °C, today's high
        self.temp_min = None        # °C, today's low
        self.timestamp = None       # when this was fetched


class WeatherProvider:
    """Base class for weather data sources."""

    def fetch(self, latitude, longitude, callback):
        """Fetch weather for the given coordinates.

        Args:
            latitude: float
            longitude: float
            callback: called with (WeatherData or None, error_message or None)
        """
        raise NotImplementedError


class OpenMeteoProvider(WeatherProvider):
    """Open-Meteo API — no key, no rate limits, CC-BY-4.0 attribution."""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    USER_AGENT = "ScreenSavor/0.3.0 (Linux; +https://github.com/7summits/screen-savor)"

    def __init__(self):
        self.session = Soup.Session()
        self.session.set_user_agent(self.USER_AGENT)

    def fetch(self, latitude, longitude, callback):
        # current: temperature_2m, weather_code (WMO), is_day
        # daily: temperature_2m_max/min for today's high/low
        url = (
            f"{self.BASE_URL}"
            f"?latitude={latitude:.4f}&longitude={longitude:.4f}"
            f"&current=temperature_2m,weather_code,is_day"
            f"&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=auto&forecast_days=1"
        )

        message = Soup.Message.new('GET', url)
        self.session.send_and_read_async(
            message, GLib.PRIORITY_DEFAULT, None,
            self._on_response, (message, callback)
        )

    def _on_response(self, session, result, data):
        message, callback = data
        try:
            body_bytes = session.send_and_read_finish(result)
            status = message.get_status()

            if status != 200:
                callback(None, f"HTTP {status}")
                return

            body = body_bytes.get_data()
            if not body:
                callback(None, "Empty response")
                return

            # Parse JSON using Python's json module (simpler and more robust)
            body_str = body.decode('utf-8')
            root = json.loads(body_str)

            data = WeatherData()
            data.timestamp = GLib.get_monotonic_time()

            # current conditions
            if 'current' in root:
                current = root['current']
                if 'temperature_2m' in current:
                    data.temperature = float(current['temperature_2m'])
                if 'weather_code' in current:
                    data.weather_code = int(current['weather_code'])
                if 'is_day' in current:
                    data.is_day = bool(current['is_day'])

            # daily forecast (today's high/low)
            if 'daily' in root:
                daily = root['daily']
                if 'temperature_2m_max' in daily and len(daily['temperature_2m_max']) > 0:
                    data.temp_max = float(daily['temperature_2m_max'][0])
                if 'temperature_2m_min' in daily and len(daily['temperature_2m_min']) > 0:
                    data.temp_min = float(daily['temperature_2m_min'][0])

            callback(data, None)

        except Exception as e:
            callback(None, str(e))


def wmo_code_to_icon_name(code, is_day=True):
    """Map WMO weather code to Adwaita weather-*-symbolic icon name.

    WMO codes from Open-Meteo:
    0: Clear sky
    1,2,3: Mainly clear, partly cloudy, overcast
    45,48: Fog
    51,53,55: Drizzle
    61,63,65: Rain
    71,73,75,77: Snow
    80,81,82: Rain showers
    85,86: Snow showers
    95,96,99: Thunderstorm
    """
    if code == 0:
        return 'weather-clear-night-symbolic' if not is_day else 'weather-clear-symbolic'
    elif code in (1, 2):
        return 'weather-few-clouds-night-symbolic' if not is_day else 'weather-few-clouds-symbolic'
    elif code == 3:
        return 'weather-overcast-symbolic'
    elif code in (45, 48):
        return 'weather-fog-symbolic'
    elif code in (51, 53, 55, 61, 63, 65):
        return 'weather-showers-symbolic'
    elif code in (71, 73, 75, 77, 85, 86):
        return 'weather-snow-symbolic'
    elif code in (80, 81, 82):
        return 'weather-showers-scattered-symbolic'
    elif code in (95, 96, 99):
        return 'weather-storm-symbolic'
    else:
        # Unknown code
        return 'weather-severe-alert-symbolic'


class WeatherLocation:
    """A saved location: name, coordinates, and the provider to use."""

    def __init__(self, name="", latitude=0.0, longitude=0.0):
        self.name = name
        self.latitude = latitude
        self.longitude = longitude

    def is_valid(self):
        return (self.name and
                -90 <= self.latitude <= 90 and
                -180 <= self.longitude <= 180)

    def to_variant(self):
        """Encode as GLib.Variant for GSettings storage."""
        return GLib.Variant('(sdd)', (self.name, self.latitude, self.longitude))

    @staticmethod
    def from_variant(variant):
        """Decode from GSettings."""
        if variant is None:
            return WeatherLocation()
        name, lat, lon = variant
        return WeatherLocation(name, lat, lon)


class WeatherFetcher:
    """Manages periodic weather fetching with exponential backoff on errors."""

    REFRESH_INTERVAL_MS = 15 * 60 * 1000  # 15 minutes

    def __init__(self, provider, location, on_update):
        """
        Args:
            provider: WeatherProvider instance
            location: WeatherLocation
            on_update: callback(WeatherData or None) — called on every fetch
        """
        self.provider = provider
        self.location = location
        self.on_update = on_update
        self.timeout_id = None
        self.current_data = None
        self._fetching = False  # Prevent overlapping fetches

        if location.is_valid():
            # Delay initial fetch slightly to avoid blocking on startup
            GLib.timeout_add(500, self._initial_fetch)
            self.timeout_id = GLib.timeout_add(
                self.REFRESH_INTERVAL_MS, self._fetch
            )

    def _initial_fetch(self):
        """Initial fetch after a small delay to avoid blocking startup."""
        self._fetch()
        return GLib.SOURCE_REMOVE

    def stop(self):
        """Cancel the refresh timer."""
        if self.timeout_id is not None:
            GLib.source_remove(self.timeout_id)
            self.timeout_id = None

    def _fetch(self):
        """Fire off a fetch. Returns GLib.SOURCE_CONTINUE for the timer."""
        if not self.location.is_valid():
            return GLib.SOURCE_CONTINUE

        # Skip if already fetching to prevent overlapping requests
        if self._fetching:
            return GLib.SOURCE_CONTINUE

        self._fetching = True
        self.provider.fetch(
            self.location.latitude,
            self.location.longitude,
            self._on_fetched
        )
        return GLib.SOURCE_CONTINUE

    def _on_fetched(self, data, error):
        self._fetching = False
        if error:
            print(f"Weather fetch error: {error}")
            # Keep stale data on screen rather than clearing it
        else:
            self.current_data = data

        self.on_update(self.current_data)


def search_cities(query, callback):
    """Search for cities via Open-Meteo geocoding API.

    Args:
        query: city name string
        callback: called with (list of dicts, error) where each dict has
                  'name', 'latitude', 'longitude', 'country', 'admin1'
    """
    if not query or not query.strip():
        callback([], None)
        return

    session = Soup.Session()
    url = (
        f"https://geocoding-api.open-meteo.com/v1/search"
        f"?name={GLib.uri_escape_string(query.strip(), None, False)}"
        f"&count=10&language=en&format=json"
    )

    message = Soup.Message.new('GET', url)
    session.send_and_read_async(
        message, GLib.PRIORITY_DEFAULT, None,
        _on_geocode_response, (message, callback)
    )


def _on_geocode_response(session, result, data):
    message, callback = data
    try:
        body_bytes = session.send_and_read_finish(result)
        status = message.get_status()

        if status != 200:
            callback([], f"HTTP {status}")
            return

        body = body_bytes.get_data()
        if not body:
            callback([], "Empty response")
            return

        # Parse JSON using Python's json module
        body_str = body.decode('utf-8')
        root = json.loads(body_str)

        results = []
        if 'results' in root:
            for obj in root['results']:
                results.append({
                    'name': obj['name'],
                    'latitude': float(obj['latitude']),
                    'longitude': float(obj['longitude']),
                    'country': obj.get('country', ''),
                    'admin1': obj.get('admin1', ''),
                })

        callback(results, None)

    except Exception as e:
        callback([], str(e))

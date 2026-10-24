"""Open-Meteo and MET Norway weather API helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from math import trunc
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MET_FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 2
BACKOFF_SECONDS = 0.25

WEATHER_CODE_DESCRIPTIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherError(Exception):
    """Base class for weather lookup and forecast errors."""


class WeatherRequestError(WeatherError):
    """Raised when a weather API request fails after its retry limit."""


class NoLocationFoundError(WeatherError):
    """Raised when geocoding returns no candidate locations."""


class AmbiguousLocationError(WeatherError):
    """Raised when more than one geocoding candidate needs a choice."""


@dataclass(frozen=True)
class ApiResponse:
    """JSON response and request metadata returned by an API call."""

    payload: dict[str, Any]
    status_code: int
    content_type: str
    elapsed_seconds: float


@dataclass(frozen=True)
class Location:
    """A geocoding candidate returned by Open-Meteo."""

    name: str
    latitude: float
    longitude: float
    country: str
    admin1: str = ""

    def __str__(self) -> str:
        region = f", {self.admin1}" if self.admin1 else ""
        country = f", {self.country}" if self.country else ""
        return f"{self.name}{region}{country} ({self.latitude:.4f}, {self.longitude:.4f})"


def request_json(
    url: str,
    *,
    params: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = MAX_RETRIES,
    backoff_seconds: float = BACKOFF_SECONDS,
    get: Callable[..., requests.Response] | None = None,
) -> ApiResponse:
    """Request JSON with a timeout, bounded retries, and explicit failures."""
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if retries < 0:
        raise ValueError("retries cannot be negative")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds cannot be negative")

    request_get = requests.get if get is None else get
    for attempt in range(retries + 1):
        try:
            response = request_get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as error:
                raise WeatherRequestError(f"Weather API returned invalid JSON from {url}") from error
            if not isinstance(payload, dict):
                raise WeatherRequestError(f"Weather API returned a non-object JSON response from {url}")
            return ApiResponse(
                payload=payload,
                status_code=response.status_code,
                content_type=response.headers.get("Content-Type", ""),
                elapsed_seconds=response.elapsed.total_seconds(),
            )
        except requests.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else None
            if status_code is None or status_code < 500 or attempt == retries:
                raise WeatherRequestError(
                    f"Weather API request to {url} failed with HTTP status {status_code}"
                ) from error
        except (requests.ConnectionError, requests.Timeout) as error:
            if attempt == retries:
                raise WeatherRequestError(
                    f"Weather API request to {url} failed after {attempt + 1} attempts: {error}"
                ) from error
        except requests.RequestException as error:
            raise WeatherRequestError(f"Weather API request to {url} failed: {error}") from error

        time.sleep(backoff_seconds * (2**attempt))

    raise WeatherRequestError(f"Weather API request to {url} failed after {retries + 1} attempts")


def parse_locations(payload: Mapping[str, Any]) -> list[Location]:
    """Convert geocoding JSON into validated location candidates."""
    results = payload.get("results", [])
    if results is None:
        return []
    if not isinstance(results, list):
        raise WeatherError("Geocoding response has an invalid 'results' field")

    locations: list[Location] = []
    for result in results:
        try:
            locations.append(
                Location(
                    name=str(result["name"]),
                    latitude=float(result["latitude"]),
                    longitude=float(result["longitude"]),
                    country=str(result.get("country", "")),
                    admin1=str(result.get("admin1", "")),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WeatherError("Geocoding response contains an invalid location") from error
    return locations


def geocode(name: str, *, count: int = 10, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[Location]:
    """Return matching locations from Open-Meteo's geocoding service."""
    query = name.strip()
    if not query:
        raise ValueError("Location name cannot be empty")
    if count < 1:
        raise ValueError("count must be at least one")
    response = request_json(
        GEOCODING_URL,
        params={"name": query, "count": count, "language": "en", "format": "json"},
        timeout=timeout,
    )
    return parse_locations(response.payload)


def geocode_with_urllib(
    name: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = MAX_RETRIES,
    backoff_seconds: float = BACKOFF_SECONDS,
    open_url: Callable[..., Any] | None = None,
) -> list[Location]:
    """Geocode a name using only urllib.request, urllib.parse, and json."""
    query = name.strip()
    if not query:
        raise ValueError("Location name cannot be empty")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if retries < 0:
        raise ValueError("retries cannot be negative")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds cannot be negative")
    url = f"{GEOCODING_URL}?{urlencode({'name': query, 'count': 10, 'language': 'en', 'format': 'json'})}"
    request = Request(url, headers={"Accept": "application/json"})
    request_open = urlopen if open_url is None else open_url
    for attempt in range(retries + 1):
        try:
            with request_open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise WeatherError("Geocoding API returned a non-object JSON response")
            return parse_locations(payload)
        except HTTPError as error:
            if error.code < 500 or attempt == retries:
                raise WeatherRequestError(
                    f"Geocoding request to {url} failed with HTTP status {error.code}"
                ) from error
        except (URLError, TimeoutError) as error:
            if attempt == retries:
                raise WeatherRequestError(
                    f"Geocoding request to {url} failed after {attempt + 1} attempts: {error}"
                ) from error
        except json.JSONDecodeError as error:
            raise WeatherRequestError(f"Geocoding API returned invalid JSON from {url}") from error

        time.sleep(backoff_seconds * (2**attempt))
    raise WeatherRequestError(f"Geocoding request to {url} failed after {retries + 1} attempts")


def choose_location(locations: list[Location], index: int | None = None) -> Location:
    """Choose one candidate, requiring an explicit index for ambiguous names."""
    if not locations:
        raise NoLocationFoundError("No matching locations were found")
    if len(locations) > 1 and index is None:
        candidates = "\n".join(f"{position}: {location}" for position, location in enumerate(locations))
        raise AmbiguousLocationError(f"Choose a candidate by index:\n{candidates}")
    selected_index = 0 if index is None else index
    if not 0 <= selected_index < len(locations):
        raise IndexError(f"Location index {selected_index} is outside the available candidates")
    return locations[selected_index]


def parse_forecast(payload: Mapping[str, Any]) -> pd.DataFrame:
    """Convert an Open-Meteo daily forecast response to a labelled table."""
    try:
        daily = payload["daily"]
        dates = daily["time"]
        columns = {
            "Date": dates,
            "Maximum temperature (°C)": daily["temperature_2m_max"],
            "Minimum temperature (°C)": daily["temperature_2m_min"],
            "Precipitation (mm)": daily["precipitation_sum"],
            "Maximum wind speed (km/h)": daily["wind_speed_10m_max"],
            "Weather": [
                WEATHER_CODE_DESCRIPTIONS.get(int(code), f"Unknown weather code ({code})")
                for code in daily["weather_code"]
            ],
        }
    except (KeyError, TypeError, ValueError) as error:
        raise WeatherError("Open-Meteo response is missing valid daily forecast fields") from error

    lengths = {len(values) for values in columns.values()}
    if len(lengths) != 1:
        raise WeatherError("Open-Meteo daily forecast fields have inconsistent lengths")
    return pd.DataFrame(columns)


def parse_met_forecast(payload: Mapping[str, Any]) -> pd.DataFrame:
    """Parse a MET Norway compact forecast into a table with units."""
    try:
        timeseries = payload["properties"]["timeseries"]
    except (KeyError, TypeError) as error:
        raise WeatherError("MET Norway response is missing its forecast timeseries") from error
    if not isinstance(timeseries, list):
        raise WeatherError("MET Norway forecast timeseries must be a list")

    rows: list[dict[str, Any]] = []
    for entry in timeseries:
        if not isinstance(entry, Mapping):
            raise WeatherError("MET Norway forecast entry must be an object")
        try:
            timestamp = entry["time"]
            data = entry["data"]
            instant = data["instant"]["details"]
            temperature = float(instant["air_temperature"])
            wind_speed = float(instant["wind_speed"])
        except (KeyError, TypeError, ValueError) as error:
            raise WeatherError("MET Norway response contains invalid instant details") from error

        if not isinstance(data, Mapping):
            raise WeatherError("MET Norway forecast data must be an object")
        next_period = next(
            (
                data[key]
                for key in ("next_1_hours", "next_6_hours", "next_12_hours")
                if key in data
            ),
            {},
        )
        if not isinstance(next_period, Mapping):
            raise WeatherError("MET Norway forecast period must be an object")
        period_details = next_period.get("details", {})
        summary = next_period.get("summary", {})
        if not isinstance(period_details, Mapping) or not isinstance(summary, Mapping):
            raise WeatherError("MET Norway forecast period details must be objects")
        precipitation = period_details.get("precipitation_amount")
        try:
            precipitation_value = None if precipitation is None else float(precipitation)
        except (TypeError, ValueError) as error:
            raise WeatherError("MET Norway precipitation amount is invalid") from error
        rows.append(
            {
                "Time (UTC)": timestamp,
                "Temperature (°C)": temperature,
                "Wind speed (m/s)": wind_speed,
                "Precipitation (mm)": precipitation_value,
                "Weather": summary.get("symbol_code", "Not provided"),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Time (UTC)",
            "Temperature (°C)",
            "Wind speed (m/s)",
            "Precipitation (mm)",
            "Weather",
        ],
    )


def get_forecast(
    latitude: float,
    longitude: float,
    days: int,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[pd.DataFrame, ApiResponse]:
    """Fetch and parse a location-specific Open-Meteo daily forecast."""
    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if not 1 <= days <= 16:
        raise ValueError("days must be between 1 and 16")

    response = request_json(
        FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,weather_code",
            "timezone": "auto",
            "forecast_days": days,
        },
        timeout=timeout,
    )
    return parse_forecast(response.payload), response


def get_met_forecast(
    latitude: float,
    longitude: float,
    contact_email: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ApiResponse:
    """Fetch a MET Norway compact forecast with the caller's contact email."""
    if not -90 <= latitude <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if "@" not in contact_email or not contact_email.strip():
        raise ValueError("Set contact_email to your real university email address")
    return request_json(
        MET_FORECAST_URL,
        params={
            "lat": trunc(latitude * 10_000) / 10_000,
            "lon": trunc(longitude * 10_000) / 10_000,
        },
        headers={"User-Agent": f"oop-weather-assignment/1.0 ({contact_email.strip()})"},
        timeout=timeout,
    )

"""Offline tests for the weather API helpers."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request

import pytest
import requests

from src import weather

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def test_parse_locations_reads_candidates_from_saved_json() -> None:
    locations = weather.parse_locations(load_fixture("geocoding.json"))

    assert len(locations) == 2
    assert locations[0].admin1 == "Illinois"
    assert locations[1].longitude == pytest.approx(-72.5898)


def test_missing_results_is_a_valid_empty_geocoding_result() -> None:
    assert weather.parse_locations({}) == []
    assert weather.parse_locations({"results": None}) == []


def test_choose_location_requires_selection_for_ambiguous_results() -> None:
    locations = weather.parse_locations(load_fixture("geocoding.json"))

    with pytest.raises(weather.AmbiguousLocationError, match="Choose a candidate"):
        weather.choose_location(locations)
    assert weather.choose_location(locations, index=1) == locations[1]


def test_choose_location_reports_no_results_and_bad_index() -> None:
    with pytest.raises(weather.NoLocationFoundError):
        weather.choose_location([])
    with pytest.raises(IndexError):
        weather.choose_location(weather.parse_locations(load_fixture("geocoding.json")), index=2)


def test_parse_forecast_adds_units_and_weather_descriptions() -> None:
    forecast = weather.parse_forecast(load_fixture("forecast.json"))

    assert list(forecast.columns) == [
        "Date",
        "Maximum temperature (°C)",
        "Minimum temperature (°C)",
        "Precipitation (mm)",
        "Maximum wind speed (km/h)",
        "Weather",
    ]
    assert forecast.loc[0, "Weather"] == "Partly cloudy"
    assert forecast.loc[1, "Weather"] == "Slight rain"


def test_parse_forecast_rejects_inconsistent_daily_fields() -> None:
    payload = load_fixture("forecast.json")
    payload["daily"]["weather_code"].pop()

    with pytest.raises(weather.WeatherError, match="inconsistent lengths"):
        weather.parse_forecast(payload)


def test_parse_met_forecast_uses_saved_json_and_labels_units() -> None:
    forecast = weather.parse_met_forecast(load_fixture("met_forecast.json"))

    assert forecast.loc[0, "Temperature (°C)"] == pytest.approx(12.4)
    assert forecast.loc[0, "Wind speed (m/s)"] == pytest.approx(3.2)
    assert forecast.loc[0, "Precipitation (mm)"] == pytest.approx(0.0)
    assert forecast.loc[0, "Weather"] == "partlycloudy_day"


def test_parse_met_forecast_rejects_missing_timeseries() -> None:
    with pytest.raises(weather.WeatherError, match="timeseries"):
        weather.parse_met_forecast({})


def test_urllib_geocoding_retries_temporary_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(load_fixture("geocoding.json")).encode("utf-8")
    calls = 0

    class FakeUrlResponse:
        def __enter__(self) -> FakeUrlResponse:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    def fake_open(request: Request, *, timeout: float) -> FakeUrlResponse:
        nonlocal calls
        assert request.full_url.startswith(weather.GEOCODING_URL)
        assert timeout == 0.5
        calls += 1
        if calls == 1:
            raise URLError("temporary network failure")
        return FakeUrlResponse()

    monkeypatch.setattr(weather.time, "sleep", lambda _: None)
    locations = weather.geocode_with_urllib(
        "Springfield",
        timeout=0.5,
        retries=1,
        open_url=fake_open,
    )

    assert calls == 2
    assert len(locations) == 2


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json; charset=utf-8"}
        self.elapsed = timedelta(milliseconds=125)

    def raise_for_status(self) -> None:
        response = requests.Response()
        response.status_code = self.status_code
        response.url = "https://example.test"
        response.raise_for_status()

    def json(self) -> dict[str, Any]:
        return self._payload


def test_request_json_retries_server_errors_and_returns_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [FakeResponse({}, 503), FakeResponse({"ok": True})]
    calls = 0

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        nonlocal calls
        assert args[0] == "https://example.test"
        assert kwargs["timeout"] == weather.DEFAULT_TIMEOUT_SECONDS
        calls += 1
        return responses.pop(0)

    monkeypatch.setattr(weather.time, "sleep", lambda _: None)
    result = weather.request_json("https://example.test", get=fake_get, retries=1)

    assert calls == 2
    assert result.payload == {"ok": True}
    assert result.status_code == 200
    assert result.content_type.startswith("application/json")
    assert result.elapsed_seconds == pytest.approx(0.125)


def test_request_json_does_not_retry_client_errors() -> None:
    calls = 0

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        nonlocal calls
        assert args[0] == "https://example.test"
        assert kwargs["timeout"] == weather.DEFAULT_TIMEOUT_SECONDS
        calls += 1
        return FakeResponse({}, 404)

    with pytest.raises(weather.WeatherRequestError, match="HTTP status 404"):
        weather.request_json("https://example.test", get=fake_get, retries=3)
    assert calls == 1


def test_forecast_validates_coordinates_and_days() -> None:
    with pytest.raises(ValueError, match="latitude"):
        weather.get_forecast(91, 0, 1)
    with pytest.raises(ValueError, match="days"):
        weather.get_forecast(0, 0, 17)


def test_met_request_requires_real_contact_email() -> None:
    with pytest.raises(ValueError, match="real university email"):
        weather.get_met_forecast(51.05, 13.75, "")


def test_met_request_truncates_coordinates_and_identifies_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request_json(
        url: str,
        *,
        params: dict[str, float],
        headers: dict[str, str],
        timeout: float,
    ) -> weather.ApiResponse:
        captured.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return weather.ApiResponse({}, 200, "application/json", 0.1)

    monkeypatch.setattr(weather, "request_json", fake_request_json)
    weather.get_met_forecast(51.123456, -1.234567, "test@example.invalid")

    assert captured["params"] == {"lat": 51.1234, "lon": -1.2345}
    assert captured["headers"]["User-Agent"].endswith("(test@example.invalid)")

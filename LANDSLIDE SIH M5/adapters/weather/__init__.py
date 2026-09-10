import abc
import httpx
from typing import Any, Dict
from adapters.base import BaseAdapter

class WeatherAdapter(BaseAdapter):
    """Adapter for a fictional Weather data provider.

    Expected JSON payload example:
    ```
    {
        "location": "area-123",
        "observed_at": "2026-09-09T15:05:00Z",
        "temperature_c": 28.7,
        "wind_kph": 15.2,
        "humidity": 78
    }
    ```
    The adapter normalizes the data to the M5 observation schema:
    ```
    {
        "area_id": "area-123",
        "event_timestamp": "2026-09-09T15:05:00Z",
        "metric": "temperature",
        "value": 28.7,
        "source": "weather"
    }
    ```
    Additional metrics (wind, humidity) could be emitted as separate observations by the caller.
    """

    def __init__(self, endpoint: str, api_key: str | None = None):
        self.endpoint = endpoint
        self.api_key = api_key
        self.client = httpx.AsyncClient()

    async def fetch(self, **kwargs) -> Any:
        params = kwargs.get("params", {})
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self.client.get(self.endpoint, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def validate(self, raw_data: Any) -> Dict[str, Any]:
        if not isinstance(raw_data, dict):
            raise ValueError("Weather payload must be a JSON object")
        required = {"location", "observed_at", "temperature_c"}
        missing = required - raw_data.keys()
        if missing:
            raise ValueError(f"Missing required fields in weather data: {missing}")
        return {
            "area_id": raw_data["location"],
            "timestamp": raw_data["observed_at"],
            "temperature": float(raw_data["temperature_c"]),
        }

    def normalize(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "area_id": validated_data["area_id"],
            "event_timestamp": validated_data["timestamp"],
            "metric": "temperature",
            "value": validated_data["temperature"],
            "source": "weather",
        }

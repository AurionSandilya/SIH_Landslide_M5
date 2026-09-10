import abc
import httpx
from typing import Any, Dict
from adapters.base import BaseAdapter

class EarthquakeAdapter(BaseAdapter):
    """Adapter for a fictional Earthquake data provider.

    Expected JSON payload (list of events) example:
    ```
    [
        {
            "id": "eq-001",
            "region": "area-456",
            "time": "2026-09-09T14:30:00Z",
            "magnitude": 5.3,
            "depth_km": 10.0
        },
        {...}
    ]
    ```
    The adapter processes a **single** earthquake event (the caller can iterate).
    Normalized observation schema:
    ```
    {
        "area_id": "area-456",
        "event_timestamp": "2026-09-09T14:30:00Z",
        "metric": "earthquake_magnitude",
        "value": 5.3,
        "source": "earthquake"
    }
    ```
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
        # Expect a dict representing a single event
        if not isinstance(raw_data, dict):
            raise ValueError("Earthquake payload must be a JSON object representing one event")
        required = {"region", "time", "magnitude"}
        missing = required - raw_data.keys()
        if missing:
            raise ValueError(f"Missing required fields in earthquake data: {missing}")
        return {
            "area_id": raw_data["region"],
            "timestamp": raw_data["time"],
            "magnitude": float(raw_data["magnitude"]),
        }

    def normalize(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "area_id": validated_data["area_id"],
            "event_timestamp": validated_data["timestamp"],
            "metric": "earthquake_magnitude",
            "value": validated_data["magnitude"],
            "source": "earthquake",
        }

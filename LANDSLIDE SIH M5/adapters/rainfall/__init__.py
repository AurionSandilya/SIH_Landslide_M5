import abc
import httpx
from typing import Any, Dict
from adapters.base import BaseAdapter

class RainfallAdapter(BaseAdapter):
    """Adapter for a fictional Rainfall data provider.

    The provider returns JSON with the following shape:
    ```
    {
        "region": "area-123",
        "time": "2026-09-09T15:00:00Z",
        "rain_mm": 12.4
    }
    ```
    The adapter converts this into the canonical observation dict used by M5:
    ```
    {
        "area_id": "area-123",
        "event_timestamp": "2026-09-09T15:00:00Z",
        "metric": "rainfall",
        "value": 12.4,
        "source": "rainfall"
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
        if not isinstance(raw_data, dict):
            raise ValueError("Rainfall payload must be a JSON object")
        required = {"region", "time", "rain_mm"}
        missing = required - raw_data.keys()
        if missing:
            raise ValueError(f"Missing required fields in rainfall data: {missing}")
        return {
            "area_id": raw_data["region"],
            "timestamp": raw_data["time"],
            "value": float(raw_data["rain_mm"]),
        }

    def normalize(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "area_id": validated_data["area_id"],
            "event_timestamp": validated_data["timestamp"],
            "metric": "rainfall",
            "value": validated_data["value"],
            "source": "rainfall",
        }

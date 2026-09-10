import abc
import httpx
from typing import Any, Dict
from adapters.base import BaseAdapter

class SARAdapter(BaseAdapter):
    """Adapter for a fictional Synthetic Aperture Radar (SAR) data provider.

    Expected JSON payload example (single observation):
    ```
    {
        "scene_id": "sar-789",
        "area": "area-321",
        "acquired_at": "2026-09-09T13:45:00Z",
        "backscatter": 0.87
    }
    ```
    Normalized observation schema for M5:
    ```
    {
        "area_id": "area-321",
        "event_timestamp": "2026-09-09T13:45:00Z",
        "metric": "sar_backscatter",
        "value": 0.87,
        "source": "sar"
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
            raise ValueError("SAR payload must be a JSON object")
        required = {"area", "acquired_at", "backscatter"}
        missing = required - raw_data.keys()
        if missing:
            raise ValueError(f"Missing required fields in SAR data: {missing}")
        return {
            "area_id": raw_data["area"],
            "timestamp": raw_data["acquired_at"],
            "backscatter": float(raw_data["backscatter"]),
        }

    def normalize(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "area_id": validated_data["area_id"],
            "event_timestamp": validated_data["timestamp"],
            "metric": "sar_backscatter",
            "value": validated_data["backscatter"],
            "source": "sar",
        }

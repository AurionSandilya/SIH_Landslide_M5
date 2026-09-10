import abc
from typing import Any, Dict

class BaseAdapter(abc.ABC):
    """Abstract base class for all environment data adapters.

    Each concrete adapter must implement three methods:
    * ``fetch`` – Retrieve raw data from the external source.
    * ``validate`` – Ensure the payload adheres to the expected schema.
    * ``normalize`` – Convert the raw payload into the canonical observation
      dictionary returned by the engine.
    """

    @abc.abstractmethod
    async def fetch(self, **kwargs) -> Any:
        """Fetch raw data from the external provider.

        ``kwargs`` may contain authentication tokens, query parameters, or
        pagination details as required by the specific provider.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def validate(self, raw_data: Any) -> Dict[str, Any]:
        """Validate the raw payload and return a dict of the cleaned fields.

        Should raise ``ValueError`` if validation fails.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def normalize(self, validated_data: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministically transform validated data into the engine's
        observation schema.
        """
        raise NotImplementedError

    async def process(self, **kwargs) -> Dict[str, Any]:
        """Convenience helper that runs the full pipeline: fetch → validate →
        normalize.
        """
        raw = await self.fetch(**kwargs)
        valid = self.validate(raw)
        return self.normalize(valid)

import httpx

from ...core.schema import OllamaConfig
from ..schema import (
    Message,
    OllamaMessagePayload,
    OllamaRequest,
    OllamaResponse,
)


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.model = config.model
        self.base_url = config.base_url

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/api/chat"

        request = OllamaRequest(
            model=self.model,
            messages=[
                OllamaMessagePayload(role=m.role, content=m.content) for m in messages
            ],
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=request.model_dump())
            response.raise_for_status()
            data = response.json()

        result = OllamaResponse.model_validate(data)
        return result.message.content

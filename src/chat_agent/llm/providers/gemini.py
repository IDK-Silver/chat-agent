import httpx

from ...core.schema import GeminiConfig
from ..schema import (
    GeminiContent,
    GeminiPart,
    GeminiRequest,
    GeminiResponse,
    GeminiSystemInstruction,
    Message,
)


class GeminiClient:
    def __init__(self, config: GeminiConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        # Convert messages to Gemini format
        # Gemini uses "user" and "model" roles (not "assistant")
        contents = []
        system_instruction = None

        for m in messages:
            if m.role == "system":
                system_instruction = GeminiSystemInstruction(
                    parts=[GeminiPart(text=m.content)]
                )
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append(
                    GeminiContent(role=role, parts=[GeminiPart(text=m.content)])
                )

        request = GeminiRequest(
            contents=contents, system_instruction=system_instruction
        )

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url,
                params=params,
                headers=headers,
                json=request.model_dump(exclude_none=True),
            )
            response.raise_for_status()
            data = response.json()

        result = GeminiResponse.model_validate(data)
        return result.candidates[0].content.parts[0].text

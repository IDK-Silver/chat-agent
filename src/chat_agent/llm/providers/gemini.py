import uuid
from typing import Any

import httpx

from ...core.schema import GeminiConfig
from ..schema import (
    GeminiContent,
    GeminiFunctionCall,
    GeminiFunctionDeclaration,
    GeminiFunctionResponse,
    GeminiPart,
    GeminiResponse,
    GeminiSystemInstruction,
    GeminiToolConfig,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
)


class GeminiClient:
    def __init__(self, config: GeminiConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[GeminiToolConfig]:
        """Convert ToolDefinition list to Gemini tools format."""
        declarations = [
            GeminiFunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=tool.to_json_schema(),
            )
            for tool in tools
        ]
        return [GeminiToolConfig(function_declarations=declarations)]

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[GeminiSystemInstruction | None, list[GeminiContent]]:
        """Convert Message list to Gemini format. Returns (system_instruction, contents)."""
        system_instruction = None
        contents: list[GeminiContent] = []

        for m in messages:
            if m.role == "system":
                system_instruction = GeminiSystemInstruction(
                    parts=[GeminiPart(text=m.content)]
                )
            elif m.role == "tool":
                # Tool result as function response
                contents.append(
                    GeminiContent(
                        role="user",
                        parts=[
                            GeminiPart(
                                function_response=GeminiFunctionResponse(
                                    name=m.name or "",
                                    response={"result": m.content or ""},
                                )
                            )
                        ],
                    )
                )
            elif m.role == "assistant" and m.tool_calls:
                # Assistant with tool calls
                parts: list[GeminiPart] = []
                if m.content:
                    parts.append(GeminiPart(text=m.content))
                for tc in m.tool_calls:
                    parts.append(
                        GeminiPart(
                            function_call=GeminiFunctionCall(
                                name=tc.name,
                                args=tc.arguments,
                            )
                        )
                    )
                contents.append(GeminiContent(role="model", parts=parts))
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append(
                    GeminiContent(role=role, parts=[GeminiPart(text=m.content)])
                )

        return system_instruction, contents

    def _parse_response(self, response: GeminiResponse) -> LLMResponse:
        """Parse Gemini response into unified LLMResponse."""
        content = None
        tool_calls = []

        for part in response.candidates[0].content.parts:
            if part.text:
                content = part.text
            elif part.function_call:
                tool_calls.append(
                    ToolCall(
                        id=str(uuid.uuid4()),  # Gemini doesn't provide IDs
                        name=part.function_call.name,
                        arguments=part.function_call.args,
                    )
                )

        return LLMResponse(content=content, tool_calls=tool_calls)

    def _serialize_request(
        self,
        contents: list[GeminiContent],
        system_instruction: GeminiSystemInstruction | None,
        tools: list[GeminiToolConfig] | None,
    ) -> dict[str, Any]:
        """Serialize request to JSON-compatible format."""
        result: dict[str, Any] = {
            "contents": [c.model_dump(exclude_none=True) for c in contents]
        }
        if system_instruction:
            result["system_instruction"] = system_instruction.model_dump(
                exclude_none=True
            )
        if tools:
            result["tools"] = [t.model_dump(exclude_none=True) for t in tools]
        return result

    def chat(self, messages: list[Message]) -> str:
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        system_instruction, contents = self._convert_messages(messages)
        request_data = self._serialize_request(contents, system_instruction, None)

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url,
                params=params,
                headers=headers,
                json=request_data,
            )
            response.raise_for_status()
            data = response.json()

        result = GeminiResponse.model_validate(data)
        # Find text content
        for part in result.candidates[0].content.parts:
            if part.text:
                return part.text
        return ""

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Send messages with tool definitions and return response."""
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        system_instruction, contents = self._convert_messages(messages)
        gemini_tools = self._convert_tools(tools) if tools else None
        request_data = self._serialize_request(contents, system_instruction, gemini_tools)

        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                url,
                params=params,
                headers=headers,
                json=request_data,
            )
            response.raise_for_status()
            data = response.json()

        result = GeminiResponse.model_validate(data)
        return self._parse_response(result)

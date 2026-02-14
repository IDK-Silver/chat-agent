import uuid
from typing import Any

import httpx

from ...core.schema import GeminiConfig
from ..reasoning import map_gemini_thinking_config
from ..schema import (
    ContentPart,
    GeminiContent,
    GeminiFunctionCall,
    GeminiFunctionDeclaration,
    GeminiFunctionResponse,
    GeminiInlineData,
    GeminiPart,
    GeminiResponse,
    GeminiSystemInstruction,
    GeminiToolConfig,
    LLMResponse,
    MalformedFunctionCallError,
    Message,
    ToolCall,
    ToolDefinition,
)


class GeminiClient:
    def __init__(self, config: GeminiConfig):
        self.model = config.model
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.max_tokens = config.max_tokens
        self.request_timeout = config.request_timeout
        self.thinking_config = map_gemini_thinking_config(
            config.reasoning,
            provider_overrides=config.provider_overrides,
        )

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

    @staticmethod
    def _content_parts_to_gemini(parts: list[ContentPart]) -> list[GeminiPart]:
        """Convert ContentPart list to Gemini parts."""
        result: list[GeminiPart] = []
        for part in parts:
            if part.type == "text" and part.text is not None:
                result.append(GeminiPart(text=part.text))
            elif part.type == "image" and part.data and part.media_type:
                result.append(GeminiPart(
                    inline_data=GeminiInlineData(
                        mime_type=part.media_type,
                        data=part.data,
                    )
                ))
        return result

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[GeminiSystemInstruction | None, list[GeminiContent]]:
        """Convert Message list to Gemini format. Returns (system_instruction, contents)."""
        system_instruction = None
        contents: list[GeminiContent] = []

        for m in messages:
            if m.role == "system":
                if isinstance(m.content, str):
                    system_instruction = GeminiSystemInstruction(
                        parts=[GeminiPart(text=m.content)]
                    )
            elif m.role == "tool":
                # Tool result as function response
                # For multimodal tool results, extract text for function response
                # and add image parts separately
                if isinstance(m.content, list):
                    from ..content import content_to_text
                    text_result = content_to_text(m.content)
                    tool_parts: list[GeminiPart] = [
                        GeminiPart(
                            function_response=GeminiFunctionResponse(
                                name=m.name or "",
                                response={"result": text_result},
                            )
                        ),
                    ]
                    # Add image parts for the model to see
                    for cp in m.content:
                        if cp.type == "image" and cp.data and cp.media_type:
                            tool_parts.append(GeminiPart(
                                inline_data=GeminiInlineData(
                                    mime_type=cp.media_type,
                                    data=cp.data,
                                )
                            ))
                    contents.append(GeminiContent(role="user", parts=tool_parts))
                else:
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
                parts: list[GeminiPart] = []
                if isinstance(m.content, str) and m.content:
                    parts.append(GeminiPart(text=m.content))
                for tc in m.tool_calls:
                    parts.append(
                        GeminiPart(
                            function_call=GeminiFunctionCall(
                                name=tc.name,
                                args=tc.arguments,
                            ),
                            thought_signature=tc.thought_signature,
                        )
                    )
                contents.append(GeminiContent(role="model", parts=parts))
            else:
                role = "model" if m.role == "assistant" else "user"
                if isinstance(m.content, list):
                    contents.append(GeminiContent(
                        role=role,
                        parts=self._content_parts_to_gemini(m.content),
                    ))
                else:
                    contents.append(
                        GeminiContent(role=role, parts=[GeminiPart(text=m.content)])
                    )

        return system_instruction, contents

    def _parse_response(self, response: GeminiResponse) -> LLMResponse:
        """Parse Gemini response into unified LLMResponse."""
        if not response.candidates:
            return LLMResponse(content=None, tool_calls=[])

        candidate = response.candidates[0]
        if (
            candidate.finish_reason == "MALFORMED_FUNCTION_CALL"
            and not candidate.content.parts
        ):
            detail = candidate.finish_message or "Gemini returned malformed function call."
            raise MalformedFunctionCallError(
                f"Gemini returned MALFORMED_FUNCTION_CALL: {detail}"
            )

        text_parts: list[str] = []
        tool_calls = []

        for part in candidate.content.parts:
            if part.text:
                text_parts.append(part.text)
            elif part.function_call:
                tool_calls.append(
                    ToolCall(
                        id=str(uuid.uuid4()),  # Gemini doesn't provide IDs
                        name=part.function_call.name,
                        arguments=part.function_call.args,
                        thought_signature=part.thought_signature,
                    )
                )

        content = "".join(text_parts) if text_parts else None
        return LLMResponse(content=content, tool_calls=tool_calls)

    def _serialize_request(
        self,
        contents: list[GeminiContent],
        system_instruction: GeminiSystemInstruction | None,
        tools: list[GeminiToolConfig] | None,
        generation_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Serialize request to JSON-compatible format."""
        result: dict[str, Any] = {
            "contents": [
                c.model_dump(exclude_none=True, by_alias=True)
                for c in contents
            ]
        }
        if system_instruction:
            result["system_instruction"] = system_instruction.model_dump(
                exclude_none=True,
                by_alias=True,
            )
        if tools:
            result["tools"] = [
                t.model_dump(exclude_none=True, by_alias=True)
                for t in tools
            ]
        if generation_config:
            result["generationConfig"] = generation_config
        return result

    def _build_generation_config(self) -> dict[str, Any]:
        """Build generationConfig with maxOutputTokens and optional thinkingConfig."""
        config: dict[str, Any] = {"maxOutputTokens": self.max_tokens}
        if self.thinking_config is not None:
            config["thinkingConfig"] = self.thinking_config
        return config

    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        system_instruction, contents = self._convert_messages(messages)
        generation_config = self._build_generation_config()
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema
        request_data = self._serialize_request(
            contents,
            system_instruction,
            None,
            generation_config,
        )
        data = self._post(url, params, headers, request_data)

        result = GeminiResponse.model_validate(data)
        if not result.candidates:
            return ""
        candidate = result.candidates[0]
        if (
            candidate.finish_reason == "MALFORMED_FUNCTION_CALL"
            and not candidate.content.parts
        ):
            detail = candidate.finish_message or "Gemini returned malformed function call."
            raise MalformedFunctionCallError(
                f"Gemini returned MALFORMED_FUNCTION_CALL: {detail}"
            )
        # Concatenate all text parts in-order.
        text_parts: list[str] = []
        for part in candidate.content.parts:
            if part.text:
                text_parts.append(part.text)
        return "".join(text_parts)

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
        generation_config = self._build_generation_config()
        request_data = self._serialize_request(
            contents,
            system_instruction,
            gemini_tools,
            generation_config,
        )
        data = self._post(url, params, headers, request_data)

        result = GeminiResponse.model_validate(data)
        return self._parse_response(result)

    def _post(
        self,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str],
        request_data: dict[str, Any],
    ) -> dict[str, Any]:
        """POST request for Gemini API."""
        with httpx.Client(timeout=self.request_timeout) as client:
            response = client.post(
                url,
                params=params,
                headers=headers,
                json=request_data,
            )
            response.raise_for_status()
            return response.json()

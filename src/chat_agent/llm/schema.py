"""Pydantic models for LLM request/response schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


# === Tool Definitions ===
class ToolParameter(BaseModel):
    """A parameter definition for a tool."""

    type: Literal["string", "number", "integer", "boolean", "object", "array"]
    description: str
    enum: list[str] | None = None


class ToolDefinition(BaseModel):
    """A tool definition that can be passed to LLM."""

    name: str
    description: str
    parameters: dict[str, ToolParameter]
    required: list[str] = []

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format for OpenAI/Anthropic."""
        properties: dict[str, Any] = {}
        for name, param in self.parameters.items():
            prop: dict[str, Any] = {"type": param.type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            properties[name] = prop

        return {
            "type": "object",
            "properties": properties,
            "required": self.required,
        }


class ToolCall(BaseModel):
    """A tool call made by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Unified response from LLM that may contain tool calls."""

    content: str | None = None
    tool_calls: list[ToolCall] = []

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# === Shared ===
class Message(BaseModel):
    """A message in a conversation."""

    role: Literal["user", "assistant", "system", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None  # For assistant messages with tool calls
    tool_call_id: str | None = None  # For tool result messages
    name: str | None = None  # Tool name for tool result messages
    timestamp: datetime | None = None  # UTC timestamp when message was created


# === OpenAI ===
class OpenAIFunctionDef(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class OpenAITool(BaseModel):
    type: Literal["function"] = "function"
    function: OpenAIFunctionDef


class OpenAIToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: "OpenAIFunctionCall"


class OpenAIFunctionCall(BaseModel):
    name: str
    arguments: str  # JSON string


class OpenAIMessagePayload(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[OpenAIToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class OpenAIRequest(BaseModel):
    model: str
    messages: list[OpenAIMessagePayload]
    max_tokens: int
    tools: list[OpenAITool] | None = None


class OpenAIResponseMessage(BaseModel):
    content: str | None = None
    tool_calls: list[OpenAIToolCall] | None = None


class OpenAIChoice(BaseModel):
    message: OpenAIResponseMessage


class OpenAIResponse(BaseModel):
    choices: list[OpenAIChoice]


# === Anthropic ===
class AnthropicToolInputSchema(BaseModel):
    type: Literal["object"] = "object"
    properties: dict[str, Any]
    required: list[str] = []


class AnthropicTool(BaseModel):
    name: str
    description: str
    input_schema: AnthropicToolInputSchema


class AnthropicTextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class AnthropicToolUseContent(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class AnthropicToolResultContent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str


AnthropicContent = AnthropicTextContent | AnthropicToolUseContent | AnthropicToolResultContent


class AnthropicMessagePayload(BaseModel):
    role: str
    content: str | list[AnthropicContent]


class AnthropicRequest(BaseModel):
    model: str
    messages: list[AnthropicMessagePayload]
    max_tokens: int
    system: str | None = None
    tools: list[AnthropicTool] | None = None


class AnthropicContentBlock(BaseModel):
    type: str = "text"
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


class AnthropicResponse(BaseModel):
    content: list[AnthropicContentBlock]
    stop_reason: str | None = None


# === Gemini ===
class GeminiFunctionDeclaration(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class GeminiToolConfig(BaseModel):
    function_declarations: list[GeminiFunctionDeclaration]


class GeminiFunctionCall(BaseModel):
    name: str
    args: dict[str, Any]


class GeminiFunctionResponse(BaseModel):
    name: str
    response: dict[str, Any]


class GeminiPart(BaseModel):
    text: str | None = None
    function_call: GeminiFunctionCall | None = None
    function_response: GeminiFunctionResponse | None = None


class GeminiContent(BaseModel):
    role: str
    parts: list[GeminiPart]


class GeminiSystemInstruction(BaseModel):
    parts: list[GeminiPart]


class GeminiRequest(BaseModel):
    contents: list[GeminiContent]
    system_instruction: GeminiSystemInstruction | None = None
    tools: list[GeminiToolConfig] | None = None


class GeminiCandidate(BaseModel):
    content: GeminiContent


class GeminiResponse(BaseModel):
    candidates: list[GeminiCandidate]


# === Ollama ===
class OllamaMessagePayload(BaseModel):
    role: str
    content: str


class OllamaRequest(BaseModel):
    model: str
    messages: list[OllamaMessagePayload]
    stream: bool = False


class OllamaResponseMessage(BaseModel):
    content: str
    thinking: str | None = None


class OllamaResponse(BaseModel):
    message: OllamaResponseMessage

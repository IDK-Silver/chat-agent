"""Pydantic models for LLM request/response schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


# === Exceptions ===
class MalformedFunctionCallError(RuntimeError):
    """LLM returned a malformed function call; retryable."""


class ContextLengthExceededError(RuntimeError):
    """Prompt token count exceeds the model's context length limit; not retryable."""


# === Multimodal Content ===
class ContentPart(BaseModel):
    """A single part of multimodal message content."""

    type: Literal["text", "image"]
    text: str | None = None
    media_type: str | None = None   # e.g. "image/png"
    data: str | None = None         # base64-encoded image data
    width: int | None = None
    height: int | None = None
    cache_control: dict[str, str] | None = None  # e.g. {"type": "ephemeral", "ttl": "1h"}


# === Tool Definitions ===
class ToolParameter(BaseModel):
    """A parameter definition for a tool."""

    type: Literal["string", "number", "integer", "boolean", "object", "array"]
    description: str
    enum: list[str] | None = None
    items: dict[str, Any] | None = None  # JSON Schema items for array type
    json_schema: dict[str, Any] | None = None


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
            if param.json_schema:
                prop = dict(param.json_schema)
                prop.setdefault("type", param.type)
                prop.setdefault("description", param.description)
                if param.enum and "enum" not in prop:
                    prop["enum"] = param.enum
            else:
                prop = {"type": param.type, "description": param.description}
                if param.enum:
                    prop["enum"] = param.enum
                if param.items:
                    prop["items"] = param.items
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
    thought_signature: str | None = None


class LLMResponse(BaseModel):
    """Unified response from LLM that may contain tool calls."""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# === Shared ===
class Message(BaseModel):
    """A message in a conversation."""

    role: Literal["user", "assistant", "system", "tool"]
    content: str | list[ContentPart] | None = None
    reasoning_content: str | None = None  # Thinking blocks for cache-friendly round-trip
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
    content: str | list[dict[str, Any]] | None = None
    reasoning: str | None = None  # OpenRouter reasoning round-trip
    tool_calls: list[OpenAIToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class OpenAIRequest(BaseModel):
    model: str
    messages: list[OpenAIMessagePayload]
    max_tokens: int | None = None
    tools: list[OpenAITool] | None = None
    reasoning_effort: str | None = None
    reasoning: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    temperature: float | None = None


class OpenAIResponseMessage(BaseModel):
    content: str | None = None
    reasoning_content: str | None = Field(
        default=None,
        # OpenRouter Gemini returns "reasoning", DeepSeek/Qwen use "reasoning_content",
        # some proxies use "reasoning_text".
        validation_alias=AliasChoices("reasoning_content", "reasoning", "reasoning_text"),
        serialization_alias="reasoning_content",
    )
    tool_calls: list[OpenAIToolCall] | None = None


class OpenAIChoice(BaseModel):
    message: OpenAIResponseMessage
    finish_reason: str | None = None


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
    content: str | list[AnthropicContent | dict[str, Any]]


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
    function_declarations: list[GeminiFunctionDeclaration] = Field(
        validation_alias=AliasChoices(
            "function_declarations",
            "functionDeclarations",
        ),
        serialization_alias="functionDeclarations",
    )


class GeminiFunctionCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class GeminiFunctionResponse(BaseModel):
    name: str
    response: dict[str, Any]


class GeminiInlineData(BaseModel):
    """Inline binary data for Gemini multimodal requests."""

    mime_type: str = Field(
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )
    data: str  # base64-encoded


class GeminiPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    inline_data: GeminiInlineData | None = Field(
        default=None,
        validation_alias=AliasChoices("inline_data", "inlineData"),
        serialization_alias="inlineData",
    )
    function_call: GeminiFunctionCall | None = Field(
        default=None,
        validation_alias=AliasChoices("function_call", "functionCall"),
        serialization_alias="functionCall",
    )
    function_response: GeminiFunctionResponse | None = Field(
        default=None,
        validation_alias=AliasChoices("function_response", "functionResponse"),
        serialization_alias="functionResponse",
    )
    thought_signature: str | None = Field(
        default=None,
        validation_alias=AliasChoices("thought_signature", "thoughtSignature"),
        serialization_alias="thoughtSignature",
    )


class GeminiContent(BaseModel):
    role: str | None = None
    parts: list[GeminiPart] = Field(default_factory=list)


class GeminiSystemInstruction(BaseModel):
    parts: list[GeminiPart] = Field(default_factory=list)


class GeminiRequest(BaseModel):
    contents: list[GeminiContent]
    system_instruction: GeminiSystemInstruction | None = None
    tools: list[GeminiToolConfig] | None = None


class GeminiCandidate(BaseModel):
    content: GeminiContent = Field(default_factory=GeminiContent)
    finish_reason: str | None = Field(
        default=None,
        validation_alias=AliasChoices("finish_reason", "finishReason"),
        serialization_alias="finishReason",
    )
    finish_message: str | None = Field(
        default=None,
        validation_alias=AliasChoices("finish_message", "finishMessage"),
        serialization_alias="finishMessage",
    )


class GeminiResponse(BaseModel):
    candidates: list[GeminiCandidate]


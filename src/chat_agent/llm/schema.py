"""Pydantic models for LLM request/response schemas."""

from pydantic import BaseModel
from typing import Literal


# === Shared ===
class Message(BaseModel):
    """A message in a conversation."""

    role: Literal["user", "assistant", "system"]
    content: str


# === OpenAI ===
class OpenAIMessagePayload(BaseModel):
    role: str
    content: str


class OpenAIRequest(BaseModel):
    model: str
    messages: list[OpenAIMessagePayload]
    max_tokens: int


class OpenAIResponseMessage(BaseModel):
    content: str


class OpenAIChoice(BaseModel):
    message: OpenAIResponseMessage


class OpenAIResponse(BaseModel):
    choices: list[OpenAIChoice]


# === Anthropic ===
class AnthropicMessagePayload(BaseModel):
    role: str
    content: str


class AnthropicRequest(BaseModel):
    model: str
    messages: list[AnthropicMessagePayload]
    max_tokens: int
    system: str | None = None


class AnthropicContentBlock(BaseModel):
    text: str


class AnthropicResponse(BaseModel):
    content: list[AnthropicContentBlock]


# === Gemini ===
class GeminiPart(BaseModel):
    text: str


class GeminiContent(BaseModel):
    role: str
    parts: list[GeminiPart]


class GeminiSystemInstruction(BaseModel):
    parts: list[GeminiPart]


class GeminiRequest(BaseModel):
    contents: list[GeminiContent]
    system_instruction: GeminiSystemInstruction | None = None


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


class OllamaResponse(BaseModel):
    message: OllamaResponseMessage

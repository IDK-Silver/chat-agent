"""Shared test fakes for OpenAI-compatible providers."""


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHttpxClient:
    """Fake httpx.Client that records calls and replays effects."""

    def __init__(self, effects: list, calls: list[dict]):
        self.effects = effects
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, headers: dict, json: dict) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return FakeResponse(effect)


def make_openai_payload(content: str = "ok") -> dict:
    return {"choices": [{"message": {"content": content}}]}

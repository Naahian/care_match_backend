from typing import Any

from app.core.config import settings
from app.llm.client import LLMClient
from app.llm.prompts import EMERGENCY_GUIDANCE
from app.llm.schemas import TriageOutput, TriageUrgency


class _FakeResponse:
    def __init__(self, parsed: Any = None, text: str = "") -> None:
        self.parsed = parsed
        self.text = text


class _FakeModels:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.call: dict[str, Any] = {}

    def generate_content(self, **kwargs: Any) -> _FakeResponse:
        self.call = kwargs
        return self.response


class _FakeGemini:
    def __init__(self, response: _FakeResponse) -> None:
        self.models = _FakeModels(response)


def _client_with_fake(response: _FakeResponse) -> tuple[LLMClient, _FakeGemini]:
    client = LLMClient(api_key="fake-key")
    fake = _FakeGemini(response)
    client._client = fake
    return client, fake


def test_triage_returns_parsed_structured_output() -> None:
    expected = TriageOutput(
        urgency=TriageUrgency.HIGH,
        summary="Persistent cough",
        recommended_specialties=["pulmonology"],
        red_flags=["fever"],
    )
    client, fake = _client_with_fake(_FakeResponse(parsed=expected))

    result = client.triage({"symptoms": "persistent cough"})

    assert result is expected
    assert fake.models.call["model"] == client._model
    assert fake.models.call["config"].response_mime_type == "application/json"
    assert fake.models.call["config"].response_schema is TriageOutput
    assert EMERGENCY_GUIDANCE in fake.models.call["config"].system_instruction
    assert "persistent cough" in fake.models.call["contents"]


def test_triage_falls_back_to_json_text_when_parsed_missing() -> None:
    raw = '{"urgency": "low", "summary": "Mild rash", "recommended_specialties": []}'
    client, _ = _client_with_fake(_FakeResponse(parsed=None, text=raw))

    result = client.triage({"symptoms": "mild rash"})

    assert result.urgency is TriageUrgency.LOW
    assert result.summary == "Mild rash"


def test_triage_falls_back_when_api_raises() -> None:
    client = LLMClient(api_key="fake-key")

    class _Boom:
        @property
        def models(self) -> Any:
            raise RuntimeError("quota exceeded")

    client._client = _Boom()

    result = client.triage({"symptoms": "severe chest pain"})

    assert result.urgency is TriageUrgency.EMERGENCY
    assert result.needs_human_review is True


def test_timeout_is_converted_to_milliseconds() -> None:
    client = LLMClient(api_key="fake-key", timeout=12.5)
    gemini = client._get_client()
    assert gemini._api_client._http_options.timeout == 12500


def test_empty_api_key_disables_client() -> None:
    client = LLMClient(api_key="")

    def _explode() -> Any:
        raise AssertionError("API must not be called without a key")

    client._get_client = _explode  # type: ignore[method-assign]

    assert client.enabled is False
    assert client.triage({"symptoms": "sore throat"}).urgency is TriageUrgency.MEDIUM


def test_none_api_key_falls_back_to_config() -> None:
    """``None`` means "use the configured key"; only "" disables explicitly."""
    client = LLMClient(api_key=None)

    assert client._api_key == settings.gemini_api_key
    assert client.enabled is bool(settings.gemini_api_key)

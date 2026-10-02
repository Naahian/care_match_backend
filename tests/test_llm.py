import pytest

from app.core.config import settings
from app.llm.client import LLMClient, _fallback_triage
from app.llm.schemas import TriageOutput, TriageUrgency


def test_fallback_detects_emergency() -> None:
    result = _fallback_triage({"symptoms": "severe chest pain"})
    assert isinstance(result, TriageOutput)
    assert result.urgency is TriageUrgency.EMERGENCY
    assert result.red_flags


def test_fallback_requires_human_review() -> None:
    result = _fallback_triage({"symptoms": "mild headache"})
    assert result.needs_human_review is True


def test_client_without_api_key_uses_fallback() -> None:
    client = LLMClient(api_key="")
    assert client.enabled is False
    result = client.triage({"symptoms": "mild sore throat"})
    assert result.urgency in tuple(TriageUrgency)


def test_client_reads_gemini_settings_from_config() -> None:
    client = LLMClient()
    assert client._model == settings.gemini_model
    assert client.enabled is bool(settings.gemini_api_key)


@pytest.mark.parametrize(
    "symptoms,expected",
    [
        ("chest pain radiating to arm", TriageUrgency.EMERGENCY),
        ("runny nose for two days", TriageUrgency.MEDIUM),
    ],
)
def test_fallback_matrix(symptoms: str, expected: TriageUrgency) -> None:
    assert _fallback_triage({"symptoms": symptoms}).urgency is expected

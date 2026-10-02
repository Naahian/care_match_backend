from asyncio.log import logger
import json
from typing import Any, Optional

from app.core.config import settings
from app.llm.prompts import EMERGENCY_GUIDANCE, SYSTEM_TRIAGE, build_triage_prompt
from app.llm.schemas import TriageOutput, TriageUrgency
from google import genai
from google.genai import types


class LLMClient:
    """Thin wrapper around the Gemini API via ``google-genai``.

    Uses Gemini's native structured output (``response_schema``), so the model
    is constrained to :class:`TriageOutput` instead of being asked to emit raw
    JSON. Falls back to a conservative rule-based assessment when no API key is
    configured or the remote call fails, so the triage endpoint stays available.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        # `is None` checks, not truthiness: an explicit "" means "disabled" and
        # must not fall back to the configured key.
        self._api_key = settings.gemini_api_key if api_key is None else api_key
        self._model = settings.gemini_model if model is None else model
        self._timeout = settings.llm_timeout_seconds if timeout is None else timeout
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key != "" or None)

    def _get_client(self) -> Any:
        if self._client is None:           
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=int(self._timeout * 1000)),
            )
        return self._client

    def triage(self, payload: dict) -> TriageOutput:
        if not self.enabled:
            _fallback_triage(payload=payload)
        try:
            from google.genai import types

            response = self._get_client().models.generate_content(
                model=self._model,
                contents=build_triage_prompt(payload),
                config=types.GenerateContentConfig(
                    system_instruction=f"{SYSTEM_TRIAGE}\n\n{EMERGENCY_GUIDANCE}",
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=TriageOutput,
                ),
            )
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, TriageOutput):
                return parsed
            return TriageOutput.model_validate(json.loads(response.text or "{}"))
        except Exception as exc:
            logger.warning(f"\n fallback reason:{exc}\n")
            return _fallback_triage(payload)


def _fallback_triage(payload: dict) -> TriageOutput:
    text = (payload.get("symptoms") or "").lower()
    emergency_markers = ("chest pain", "can't breathe", "unconscious", "stroke")
    urgent = any(marker in text for marker in emergency_markers)

    return TriageOutput(
        urgency=TriageUrgency.EMERGENCY if urgent else TriageUrgency.MEDIUM,
        summary="Automated fallback assessment; manual clinician review required.",
        recommended_specialties=["general_practice"],
        red_flags=["possible medical emergency"] if urgent else [],
        needs_human_review=True,
    )


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client

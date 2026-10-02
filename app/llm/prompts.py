"""Prompt templates for care_match_backend LLM calls."""

SYSTEM_TRIAGE = (
    "You are a clinical triage assistant. Assess the patient's reported symptoms "
    "and return a structured risk assessment. Never diagnose. Escalate to "
    "emergency care when red flags are present. Respond only with the requested "
    "structured output."
)

USER_TRIAGE = """Patient details:
- Symptoms: {symptoms}
- Age: {age}
- Symptom duration (days): {duration_days}
- Location: {location}

Return a triage assessment with: urgency, summary, recommended_specialties,
red_flags, needs_human_review."""

EMERGENCY_GUIDANCE = (
    "If the patient describes chest pain, severe breathing difficulty, stroke "
    "symptoms, uncontrolled bleeding, or loss of consciousness, set urgency to "
    "emergency and advise immediate local emergency services."
)


def build_triage_prompt(payload: dict) -> str:
    return USER_TRIAGE.format(
        symptoms=payload.get("symptoms", ""),
        age=payload.get("age", "unknown"),
        duration_days=payload.get("duration_days", "unknown"),
        location=payload.get("location", "not provided"),
    )

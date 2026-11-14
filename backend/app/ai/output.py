"""Parse and validate what a model returns. Nothing reaches the database unvalidated.

Steps: extract the JSON object (models sometimes wrap it in ``` fences or add a sentence),
validate its shape, clean every string (control characters, length, secret redaction), and
drop "evidence" that does not actually appear in the log — a model may invent a plausible
error line, and evidence the user cannot find in the log is worse than none.
"""

import json
import re

from pydantic import BaseModel, ValidationError, field_validator

from app.redaction import redact_secrets
from app.schemas import AnalysisResult, Confidence

MAX_ITEMS = 5
MAX_ITEM_CHARS = 500
MAX_SUMMARY_CHARS = 1_500

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


class InvalidAIOutputError(Exception):
    """The model's reply could not be turned into a valid AnalysisResult."""


class _RawResult(BaseModel):
    summary: str
    possible_causes: list[str] = []
    evidence: list[str] = []
    suggested_fixes: list[str] = []
    confidence: str

    @field_validator("possible_causes", "evidence", "suggested_fixes", mode="before")
    @classmethod
    def _single_string_to_list(cls, value):
        return [value] if isinstance(value, str) else value


def parse_analysis_output(text: str, logs: str) -> AnalysisResult:
    data = _extract_json_object(text)
    try:
        raw = _RawResult.model_validate(data)
    except ValidationError as exc:
        count = exc.error_count()
        raise InvalidAIOutputError(f"Reply has the wrong shape ({count} error(s)).") from exc

    confidence = raw.confidence.strip().lower()
    if confidence not in ("low", "medium", "high"):
        raise InvalidAIOutputError(f"Unknown confidence value: {raw.confidence[:20]!r}.")

    summary = _clean(raw.summary, MAX_SUMMARY_CHARS)
    if not summary:
        raise InvalidAIOutputError("Reply has an empty summary.")

    evidence = _clean_list(raw.evidence)
    normalized_logs = _normalize(logs)
    grounded = [item for item in evidence if _appears_in(item, normalized_logs)]
    if evidence and not grounded:
        # Every quote was invented or paraphrased: keep the analysis but say it's weak.
        confidence = "low"

    return AnalysisResult(
        summary=summary,
        possible_causes=_clean_list(raw.possible_causes),
        evidence=grounded,
        suggested_fixes=_clean_list(raw.suggested_fixes),
        confidence=_as_confidence(confidence),
    )


def _extract_json_object(text: str) -> object:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise InvalidAIOutputError("Reply contains no JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidAIOutputError(f"Reply is not valid JSON: {exc.msg}.") from exc


def _clean(value: str, limit: int) -> str:
    value = _CONTROL_CHARS.sub("", value).strip()
    value = redact_secrets(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _clean_list(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = _clean(value, MAX_ITEM_CHARS)
        if item and item not in cleaned:
            cleaned.append(item)
        if len(cleaned) == MAX_ITEMS:
            break
    return cleaned


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def _appears_in(item: str, normalized_logs: str) -> bool:
    needle = _normalize(item.strip("`'\" "))
    return bool(needle) and needle in normalized_logs


def _as_confidence(value: str) -> Confidence:
    return value  # type: ignore[return-value]  # checked above

"""Email classification via a local Ollama LLM."""

from __future__ import annotations

import json
from typing import Any

import requests

ALLOWED_CATEGORIES: set[str] = {
    "AI/Needs Attention",
    "AI/Receipts",
    "AI/Bills",
    "AI/Personal",
    "AI/Work",
    "AI/Finance",
    "AI/Travel",
    "AI/Marketing",
    "AI/Newsletters",
    "AI/Unknown",
}

ARCHIVABLE_CATEGORIES: set[str] = {"AI/Marketing", "AI/Newsletters"}

UNKNOWN_RESULT: dict[str, Any] = {
    "category": "AI/Unknown",
    "confidence": 0.0,
    "should_archive": False,
    "reason": "",
}


class Classifier:
    """Classifies an email by prompting a local Ollama model."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        timeout_seconds: int = 60,
        confidence_threshold: float = 0.75,
        prompt_path: str = "prompts/classify_email.txt",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.confidence_threshold = confidence_threshold
        with open(prompt_path, "r", encoding="utf-8") as handle:
            self._template = handle.read()

    def build_prompt(self, email: dict[str, Any]) -> str:
        """Render the prompt template with the email's fields.

        Uses explicit token replacement (not ``str.format``) so the literal
        JSON braces in the template are preserved.
        """
        labels = email.get("labelIds", [])
        replacements = {
            "{confidence_threshold}": str(self.confidence_threshold),
            "{subject}": str(email.get("subject", "")),
            "{from}": str(email.get("from", "")),
            "{to}": str(email.get("to", "")),
            "{date}": str(email.get("date", "")),
            "{snippet}": str(email.get("snippet", "")),
            "{body}": str(email.get("body", "")),
            "{labels}": ", ".join(labels) if isinstance(labels, list) else str(labels),
        }
        prompt = self._template
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        return prompt

    def classify(self, email: dict[str, Any]) -> dict[str, Any]:
        """Classify an email and return a validated result dict.

        On any failure (Ollama down, bad JSON, etc.) returns a safe
        ``AI/Unknown`` result with confidence 0 and should_archive False.
        """
        prompt = self.build_prompt(email)
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            parsed = json.loads(raw)
        except (requests.RequestException, ValueError, KeyError):
            return dict(UNKNOWN_RESULT)

        return self._validate(parsed)

    def _validate(self, parsed: Any) -> dict[str, Any]:
        """Coerce a raw model response into a safe, valid result."""
        if not isinstance(parsed, dict):
            return dict(UNKNOWN_RESULT)

        category = parsed.get("category", "AI/Unknown")
        if category not in ALLOWED_CATEGORIES:
            category = "AI/Unknown"

        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        should_archive = bool(parsed.get("should_archive", False))
        reason = str(parsed.get("reason", ""))

        # Enforce archiving policy regardless of what the model claimed.
        if category not in ARCHIVABLE_CATEGORIES:
            should_archive = False
        if confidence < self.confidence_threshold:
            should_archive = False

        return {
            "category": category,
            "confidence": confidence,
            "should_archive": should_archive,
            "reason": reason,
        }

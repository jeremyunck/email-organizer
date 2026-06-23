"""Management of Gmail labels used by the organizer."""

from __future__ import annotations

from typing import Any, Optional


class LabelManager:
    """Ensures organizer labels exist and resolves names to label IDs.

    Labels are never deleted. This class only reads existing labels and
    creates missing ones.
    """

    def __init__(self, service: Any) -> None:
        """Create a manager bound to an authenticated Gmail service."""
        self._service = service
        self._cache: dict[str, str] = {}

    def _refresh_cache(self) -> None:
        """Reload the local name -> id cache from Gmail."""
        resp = self._service.users().labels().list(userId="me").execute()
        self._cache = {
            label["name"]: label["id"] for label in resp.get("labels", []) or []
        }

    def ensure_labels(self, label_names: list[str]) -> dict[str, str]:
        """Ensure every name in ``label_names`` exists.

        Returns a mapping of label name to Gmail label ID for the
        requested labels. Creates any that are missing.
        """
        self._refresh_cache()
        result: dict[str, str] = {}
        for name in label_names:
            label_id = self._cache.get(name)
            if label_id is None:
                label_id = self.create_label(name)
            result[name] = label_id
        return result

    def get_label_id(self, name: str) -> Optional[str]:
        """Return the Gmail label ID for ``name``, or None if absent."""
        if name in self._cache:
            return self._cache[name]
        self._refresh_cache()
        return self._cache.get(name)

    def create_label(self, name: str) -> str:
        """Create a label and return its Gmail label ID."""
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created = (
            self._service.users()
            .labels()
            .create(userId="me", body=body)
            .execute()
        )
        label_id = created["id"]
        self._cache[name] = label_id
        return label_id

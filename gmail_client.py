"""Gmail API client: OAuth, message listing, fetching and modification."""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# gmail.modify allows adding/removing labels and archiving, but does NOT
# permit permanent deletion. This is intentional for safety.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

DEFAULT_BODY_LIMIT = 4000


class GmailClient:
    """Thin wrapper around the Gmail API for this organizer."""

    def __init__(
        self,
        credentials_file: str = "credentials.json",
        token_file: str = "token.json",
        body_excerpt_chars: int = DEFAULT_BODY_LIMIT,
    ) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.body_excerpt_chars = body_excerpt_chars
        self._service: Optional[Any] = None

    # -- Authentication ---------------------------------------------------

    def authenticate(self) -> None:
        """Authenticate with Gmail, reusing/refreshing a cached token.

        - Uses ``token.json`` if present and valid.
        - Refreshes an expired token when a refresh token is available.
        - Otherwise runs the local OAuth installed-app flow.

        Raises on failure so the caller can abort the run.
        """
        creds: Optional[Credentials] = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        f"OAuth client file not found: {self.credentials_file}. "
                        "See the README for how to create credentials.json."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self.token_file, "w", encoding="utf-8") as token:
                token.write(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds)

    @property
    def service(self) -> Any:
        """Return the authenticated Gmail service, or raise if missing."""
        if self._service is None:
            raise RuntimeError("GmailClient.authenticate() must be called first.")
        return self._service

    # -- Listing / fetching -----------------------------------------------

    def list_messages(self, query: str, limit: int) -> list[str]:
        """Return up to ``limit`` message IDs matching the Gmail ``query``."""
        ids: list[str] = []
        page_token: Optional[str] = None
        while len(ids) < limit:
            remaining = limit - len(ids)
            resp = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(remaining, 100),
                    pageToken=page_token,
                )
                .execute()
            )
            for msg in resp.get("messages", []):
                ids.append(msg["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids[:limit]

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch a message and return a normalized dict of useful fields.

        Never raises on malformed/missing fields; returns best-effort data.
        """
        msg = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        payload = msg.get("payload", {}) or {}
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in payload.get("headers", []) or []
        }
        body = self._extract_body(payload)
        if len(body) > self.body_excerpt_chars:
            body = body[: self.body_excerpt_chars]

        return {
            "id": msg.get("id", message_id),
            "threadId": msg.get("threadId", ""),
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "labelIds": msg.get("labelIds", []) or [],
            "body": body,
        }

    def modify_message(
        self,
        message_id: str,
        add_label_ids: list[str],
        remove_label_ids: list[str],
    ) -> None:
        """Apply label additions/removals to a message.

        Only label changes are performed — nothing is deleted or trashed.
        """
        body = {
            "addLabelIds": add_label_ids,
            "removeLabelIds": remove_label_ids,
        }
        self.service.users().messages().modify(
            userId="me", id=message_id, body=body
        ).execute()

    # -- Body extraction --------------------------------------------------

    def _extract_body(self, payload: dict[str, Any]) -> str:
        """Recursively extract a plain-text body, preferring text/plain.

        Falls back to stripped HTML when no plain part exists. Attachments
        are ignored. Returns an empty string on any failure.
        """
        plain = self._find_part(payload, "text/plain")
        if plain:
            return plain
        html = self._find_part(payload, "text/html")
        if html:
            return self._strip_html(html)
        return ""

    def _find_part(self, part: dict[str, Any], mime_type: str) -> str:
        """Depth-first search for decoded text of the given MIME type."""
        if not isinstance(part, dict):
            return ""

        # Skip attachments (parts with a filename).
        filename = part.get("filename") or ""
        if filename:
            return ""

        if part.get("mimeType") == mime_type:
            data = (part.get("body") or {}).get("data")
            decoded = self._decode_base64url(data)
            if decoded:
                return decoded

        for sub in part.get("parts", []) or []:
            found = self._find_part(sub, mime_type)
            if found:
                return found
        return ""

    @staticmethod
    def _decode_base64url(data: Optional[str]) -> str:
        """Decode Gmail base64url body data; return '' on failure."""
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return ""

    @staticmethod
    def _strip_html(html: str) -> str:
        """Strip tags from HTML, returning readable text; '' on failure."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text(separator=" ", strip=True)
        except Exception:
            return ""

"""CLI entry point for the local Gmail organizer.

Safe by default: runs in dry-run mode unless --apply is passed. Never
deletes, trashes, or marks messages as spam.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Optional

import yaml

from classifier import Classifier
from gmail_client import GmailClient
from label_manager import LabelManager
from state import State

INBOX_LABEL_ID = "INBOX"


def load_config(config_path: Optional[str]) -> dict[str, Any]:
    """Load YAML config from ``config_path`` or fall back to defaults.

    Resolution order:
      1. Explicit ``--config`` path (must exist).
      2. ``config.yaml`` if present.
      3. ``config.example.yaml``.

    Raises if no config file can be loaded.
    """
    candidates: list[str]
    if config_path:
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")
        candidates = [config_path]
    else:
        candidates = ["config.yaml", "config.example.yaml"]

    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            print(f"Loaded config from {path}")
            return data

    raise FileNotFoundError(
        "No config file found (tried config.yaml and config.example.yaml)."
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Classify and organize recent Gmail messages using a "
        "local Ollama LLM. Dry-run by default."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without modifying Gmail (default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply label changes to Gmail.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of messages to process (default: 20).",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Gmail search query (defaults to config default_query).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a YAML config file.",
    )
    return parser.parse_args(argv)


def extract_domain(from_header: str) -> str:
    """Extract the lowercase sender domain from a From header."""
    match = re.search(r"[\w.+-]+@([\w.-]+)", from_header or "")
    if not match:
        return ""
    return match.group(1).lower().rstrip(".")


def is_protected(domain: str, protected_domains: list[str]) -> bool:
    """Return True if ``domain`` equals or is a subdomain of a protected one."""
    if not domain:
        return False
    for protected in protected_domains:
        protected = protected.lower().strip()
        if not protected:
            continue
        if domain == protected or domain.endswith("." + protected):
            return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    """Run the organizer. Returns a process exit code."""
    args = parse_args(argv)
    apply_mode = args.apply  # dry-run is the default unless --apply given.

    # --- Fatal-on-failure setup ----------------------------------------
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"FATAL: could not load config: {exc}", file=sys.stderr)
        return 1

    gmail_cfg = config.get("gmail", {})
    ollama_cfg = config.get("ollama", {})
    class_cfg = config.get("classification", {})
    labels_cfg = config.get("labels", {})

    processed_label = labels_cfg.get("processed", "AI/Processed")
    category_labels = labels_cfg.get("categories", [])
    all_labels = list(category_labels) + [processed_label]
    protected_domains = class_cfg.get("protected_domains", [])
    confidence_threshold = float(class_cfg.get("confidence_threshold", 0.75))
    body_excerpt_chars = int(class_cfg.get("body_excerpt_chars", 4000))

    query = args.query or gmail_cfg.get(
        "default_query", "in:inbox -label:AI/Processed newer_than:7d"
    )

    client = GmailClient(
        credentials_file=gmail_cfg.get("credentials_file", "credentials.json"),
        token_file=gmail_cfg.get("token_file", "token.json"),
        body_excerpt_chars=body_excerpt_chars,
    )
    try:
        client.authenticate()
    except Exception as exc:
        print(f"FATAL: Gmail authentication failed: {exc}", file=sys.stderr)
        return 1

    label_manager = LabelManager(client.service)
    try:
        label_ids = label_manager.ensure_labels(all_labels)
    except Exception as exc:
        print(f"FATAL: could not ensure labels exist: {exc}", file=sys.stderr)
        return 1

    classifier = Classifier(
        base_url=ollama_cfg.get("base_url", "http://localhost:11434"),
        model=ollama_cfg.get("model", "llama3.1:8b"),
        timeout_seconds=int(ollama_cfg.get("timeout_seconds", 60)),
        confidence_threshold=confidence_threshold,
        prompt_path=os.path.join("prompts", "classify_email.txt"),
    )

    state = State("state.db")

    mode_label = "APPLY" if apply_mode else "DRY-RUN"
    print(f"\n=== Gmail Organizer ({mode_label}) ===")
    print(f"Query: {query}")
    print(f"Limit: {args.limit}\n")

    # --- Per-message loop (errors are non-fatal) -----------------------
    scanned = 0
    classified = 0
    skipped = 0
    errors = 0
    action_counts: dict[str, int] = {}

    try:
        message_ids = client.list_messages(query, args.limit)
    except Exception as exc:
        print(f"FATAL: could not list messages: {exc}", file=sys.stderr)
        state.close()
        return 1

    for message_id in message_ids:
        scanned += 1
        try:
            if state.is_processed(message_id):
                skipped += 1
                print(f"- Skipping already-processed message {message_id}")
                continue

            email = client.get_message(message_id)
            result = classifier.classify(email)

            domain = extract_domain(email.get("from", ""))
            protected = is_protected(domain, protected_domains)
            if protected and result["should_archive"]:
                result["should_archive"] = False
                protected_note = (
                    f"  (archive suppressed: sender domain '{domain}' is protected)"
                )
            else:
                protected_note = ""

            category = result["category"]
            should_archive = result["should_archive"]

            add_label_ids = []
            cat_label_id = label_ids.get(category)
            if cat_label_id:
                add_label_ids.append(cat_label_id)
            add_label_ids.append(label_ids[processed_label])
            remove_label_ids = [INBOX_LABEL_ID] if should_archive else []

            action = "ARCHIVE+LABEL" if should_archive else "LABEL"

            print(f"\nSubject: {email.get('subject', '')}")
            print(f"From: {email.get('from', '')}")
            print(f"Category: {category}")
            print(f"Confidence: {result['confidence']:.2f}")
            print(f"Should archive: {should_archive}")
            print(f"Reason: {result['reason']}")
            if protected_note:
                print(protected_note)

            if apply_mode:
                client.modify_message(message_id, add_label_ids, remove_label_ids)
                state.record(
                    gmail_id=email.get("id", message_id),
                    thread_id=email.get("threadId", ""),
                    category=category,
                    confidence=result["confidence"],
                    should_archive=should_archive,
                    reason=result["reason"],
                )
                print(f"Action: APPLIED [{action}]")
            else:
                print(f"Action: DRY-RUN [would {action}]")

            classified += 1
            action_counts[category] = action_counts.get(category, 0) + 1

        except Exception as exc:  # noqa: BLE001 - keep processing other emails
            errors += 1
            print(f"ERROR processing message {message_id}: {exc}", file=sys.stderr)

    state.close()

    # --- Summary -------------------------------------------------------
    verb = "Applied" if apply_mode else "Would apply"
    print("\n=== Summary ===")
    print(f"Mode: {mode_label}")
    print(f"Scanned: {scanned}")
    print(f"Classified: {classified}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")
    print(f"{verb} labels:")
    if action_counts:
        for category in sorted(action_counts):
            print(f"  {category}: {action_counts[category]}")
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

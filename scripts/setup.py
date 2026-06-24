#!/usr/bin/env python3
"""Interactive setup for email-organizer.

Walks you through everything the tool needs before its first run:

  1. Python version check.
  2. Installing Python dependencies (optionally into a virtualenv).
  3. Viewing, editing, and saving ``config.yaml`` (model, Ollama base URL,
     and the agent request timeout), with the current values shown.
  4. Locating the Gmail OAuth ``credentials.json`` file.
  5. Checking that Ollama is reachable and the configured model is pulled.
  6. Running the one-time Gmail OAuth flow to create ``token.json``.

Every step is optional and can be skipped — re-run the script any time to
pick up where you left off. It only ever writes ``config.yaml`` and
``token.json`` (both git-ignored); it never deletes anything.

Usage:
    python scripts/setup.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

# Run from the project root regardless of where the script is invoked.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_EXAMPLE = "config.example.yaml"
CONFIG_FILE = "config.yaml"
REQUIREMENTS = "requirements.txt"
DEFAULT_CREDENTIALS = "credentials.json"
DEFAULT_TOKEN = "token.json"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_TIMEOUT = 60
# Default max messages processed per run; mirrors main.py's --limit default.
DEFAULT_LIMIT = 20

MIN_PYTHON = (3, 11)


# --- small console helpers -------------------------------------------------


def banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def warn(msg: str) -> None:
    print(f"  [warn] {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


def ask(prompt: str, default: str = "") -> str:
    """Prompt for a line of input, returning ``default`` on empty input."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt. Returns ``default`` on empty or non-interactive input."""
    choices = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {prompt} [{choices}]: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def ask_int(prompt: str, default: int) -> int:
    """Prompt for a positive integer, re-asking until one is given."""
    while True:
        raw = ask(prompt, str(default))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            warn("Please enter a whole number.")
            continue
        if value <= 0:
            warn("Please enter a positive number.")
            continue
        return value


# --- steps -----------------------------------------------------------------


def check_python() -> None:
    banner("1. Python version")
    current = sys.version_info[:3]
    if current >= MIN_PYTHON:
        ok(f"Python {'.'.join(map(str, current))} (>= 3.11 required).")
    else:
        warn(
            f"Python {'.'.join(map(str, current))} detected; this project "
            "needs 3.11 or newer. Please upgrade before continuing."
        )


def install_dependencies() -> None:
    banner("2. Python dependencies")
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        ok(f"Running inside a virtualenv: {sys.prefix}")
    else:
        warn("You are not in a virtualenv.")
        info("Recommended (run these yourself, then re-run setup):")
        info("    python -m venv .venv && source .venv/bin/activate")
        if not confirm(
            "Install dependencies into the current environment anyway?",
            default=False,
        ):
            info("Skipping dependency installation.")
            return

    if not confirm(f"Run 'pip install -r {REQUIREMENTS}' now?", default=True):
        info("Skipping dependency installation.")
        return

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS],
        cwd=PROJECT_ROOT,
    )
    if result.returncode == 0:
        ok("Dependencies installed.")
    else:
        warn("pip install failed; see output above.")


def create_config() -> dict:
    """Show, edit, and save the configuration (config.yaml).

    Displays the current value of each setting, lets you edit the editable
    ones (model, Ollama base URL, and the agent request timeout), and saves
    the result back to ``config.yaml`` on confirmation. Works whether or not
    ``config.yaml`` already exists.

    Returns a dict with at least ``ollama_url``, ``model``, and
    ``timeout_seconds`` so later steps can check Ollama.
    """
    banner("3. Configuration (config.yaml)")
    config_path = os.path.join(PROJECT_ROOT, CONFIG_FILE)
    example_path = os.path.join(PROJECT_ROOT, CONFIG_EXAMPLE)

    exists = os.path.exists(config_path)
    source_path = config_path if exists else example_path

    if not os.path.exists(source_path):
        warn(f"{CONFIG_EXAMPLE} is missing; cannot create {CONFIG_FILE}.")
        return {
            "ollama_url": DEFAULT_OLLAMA_URL,
            "model": DEFAULT_MODEL,
            "timeout_seconds": DEFAULT_TIMEOUT,
        }

    settings = _read_settings(source_path)
    if exists:
        ok(f"{CONFIG_FILE} already exists.")
    else:
        info(f"No {CONFIG_FILE} yet; it will be created from {CONFIG_EXAMPLE}.")
    _show_current_settings(settings)

    if exists and not confirm("Edit these settings now?", default=False):
        info("Leaving config.yaml unchanged.")
        return settings

    info("Press Enter to keep the current value shown in brackets.")
    model = ask("Ollama model name", str(settings.get("model", DEFAULT_MODEL)))
    base_url = ask(
        "Ollama base URL", str(settings.get("ollama_url", DEFAULT_OLLAMA_URL))
    )
    timeout = ask_int(
        "Agent request timeout (seconds)",
        int(settings.get("timeout_seconds", DEFAULT_TIMEOUT)),
    )

    # The "save button": nothing is written unless you confirm here.
    if not confirm("Save these settings to config.yaml?", default=True):
        info("Discarded changes; config.yaml not written.")
        return settings

    with open(source_path, "r", encoding="utf-8") as handle:
        text = handle.read()

    text, _ = _set_scalar(text, "model", model, quote=True)
    text, _ = _set_scalar(text, "base_url", base_url, quote=True)
    text, set_timeout = _set_scalar(text, "timeout_seconds", timeout)
    if not set_timeout:
        text = _insert_timeout(text, timeout)

    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(text)

    ok(
        f"Saved {CONFIG_FILE} (model={model}, base_url={base_url}, "
        f"timeout_seconds={timeout})."
    )
    settings.update(
        {"ollama_url": base_url, "model": model, "timeout_seconds": timeout}
    )
    return settings


def _show_current_settings(settings: dict) -> None:
    """Print the current value of each setting so the user can see them."""
    info("Current settings:")
    info(f"    Ollama model              : {settings.get('model', DEFAULT_MODEL)}")
    info(
        "    Ollama base URL           : "
        f"{settings.get('ollama_url', DEFAULT_OLLAMA_URL)}"
    )
    info(
        "    Agent request timeout (s) : "
        f"{settings.get('timeout_seconds', DEFAULT_TIMEOUT)}"
    )
    if "confidence_threshold" in settings:
        info(
            "    Confidence threshold      : "
            f"{settings['confidence_threshold']}"
        )
    if "body_excerpt_chars" in settings:
        info(
            "    Body excerpt chars (max)  : "
            f"{settings['body_excerpt_chars']}"
        )
    info(
        f"    Max messages per run      : {DEFAULT_LIMIT} "
        "(override at runtime with --limit)"
    )


def _read_settings(config_path: str) -> dict:
    """Best-effort read of the current settings from a config file."""
    settings: dict = {
        "ollama_url": DEFAULT_OLLAMA_URL,
        "model": DEFAULT_MODEL,
        "timeout_seconds": DEFAULT_TIMEOUT,
    }
    try:
        import yaml  # type: ignore
    except ImportError:
        return settings
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        return settings

    ollama = data.get("ollama", {}) or {}
    classification = data.get("classification", {}) or {}
    if ollama.get("base_url"):
        settings["ollama_url"] = ollama["base_url"]
    if ollama.get("model"):
        settings["model"] = ollama["model"]
    if ollama.get("timeout_seconds") is not None:
        settings["timeout_seconds"] = ollama["timeout_seconds"]
    if classification.get("confidence_threshold") is not None:
        settings["confidence_threshold"] = classification["confidence_threshold"]
    if classification.get("body_excerpt_chars") is not None:
        settings["body_excerpt_chars"] = classification["body_excerpt_chars"]
    return settings


def _set_scalar(text: str, key: str, value, quote: bool = False) -> tuple[str, bool]:
    """Replace the value of a unique ``key:`` line, preserving indentation.

    Returns the updated text and whether a replacement was made.
    """
    rendered = f'"{value}"' if quote else str(value)
    pattern = re.compile(rf"^(?P<indent>\s*){re.escape(key)}:.*$", re.MULTILINE)

    def repl(match: re.Match) -> str:
        return f"{match.group('indent')}{key}: {rendered}"

    new_text, count = pattern.subn(repl, text, count=1)
    return new_text, count > 0


def _insert_timeout(text: str, timeout: int) -> str:
    """Add a ``timeout_seconds`` line under the Ollama ``model`` line.

    Fallback for configs that predate the timeout setting.
    """
    pattern = re.compile(r"^(?P<indent>\s*)model:.*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return text
    indent = match.group("indent")
    addition = f"{match.group(0)}\n{indent}timeout_seconds: {timeout}"
    return text[: match.start()] + addition + text[match.end() :]


def check_credentials() -> None:
    banner("4. Gmail OAuth credentials (credentials.json)")
    creds_path = os.path.join(PROJECT_ROOT, DEFAULT_CREDENTIALS)
    if os.path.exists(creds_path):
        if _looks_like_oauth_client(creds_path):
            ok(f"{DEFAULT_CREDENTIALS} found and looks like an OAuth client.")
        else:
            warn(
                f"{DEFAULT_CREDENTIALS} exists but doesn't look like a Desktop "
                "OAuth client. Double-check you downloaded the right file."
            )
        return

    warn(f"{DEFAULT_CREDENTIALS} not found in the project root.")
    info("To create it:")
    info("  1. Open https://console.cloud.google.com/ and pick/create a project.")
    info("  2. APIs & Services -> Library -> enable the 'Gmail API'.")
    info("  3. APIs & Services -> OAuth consent screen -> External; add your")
    info("     own Google account as a Test user.")
    info("  4. APIs & Services -> Credentials -> Create Credentials ->")
    info("     OAuth client ID -> Application type: Desktop app.")
    info(f"  5. Download the JSON and save it as '{DEFAULT_CREDENTIALS}' here:")
    info(f"     {creds_path}")
    info("Then re-run this script to continue with Gmail authentication.")


def _looks_like_oauth_client(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return False
    return "installed" in data or "web" in data


def check_ollama(settings: dict) -> None:
    banner("5. Ollama")
    base_url = settings.get("ollama_url", DEFAULT_OLLAMA_URL)
    model = settings.get("model", DEFAULT_MODEL)
    tags_url = base_url.rstrip("/") + "/api/tags"

    try:
        with urllib.request.urlopen(tags_url, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        warn(f"Could not reach Ollama at {base_url} ({exc}).")
        info("Install it from https://ollama.com, then start it with:")
        info("    ollama serve")
        info(f"and pull the model with:  ollama pull {model}")
        return

    ok(f"Ollama is reachable at {base_url}.")
    names = {m.get("name", "") for m in payload.get("models", [])}
    # Ollama reports e.g. "llama3.1:8b"; also match the bare model name.
    have_model = model in names or any(n.split(":")[0] == model for n in names)
    if have_model:
        ok(f"Model '{model}' is available.")
    else:
        warn(f"Model '{model}' is not pulled yet.")
        if shutil.which("ollama") and confirm(
            f"Run 'ollama pull {model}' now?", default=True
        ):
            result = subprocess.run(["ollama", "pull", model])
            if result.returncode == 0:
                ok(f"Pulled '{model}'.")
            else:
                warn("ollama pull failed; see output above.")
        else:
            info(f"Pull it later with:  ollama pull {model}")


def run_gmail_auth() -> None:
    banner("6. Gmail authentication (token.json)")
    creds_path = os.path.join(PROJECT_ROOT, DEFAULT_CREDENTIALS)
    token_path = os.path.join(PROJECT_ROOT, DEFAULT_TOKEN)

    if os.path.exists(token_path):
        ok(f"{DEFAULT_TOKEN} already exists; Gmail is authenticated.")
        if not confirm("Re-run authentication anyway?", default=False):
            return

    if not os.path.exists(creds_path):
        warn(
            f"Cannot authenticate without {DEFAULT_CREDENTIALS} (see step 4). "
            "Skipping."
        )
        return

    if not confirm(
        "Authenticate with Gmail now? A browser window will open.", default=True
    ):
        info("Skipping. Run this script again, or just run 'python main.py'.")
        return

    try:
        from gmail_client import GmailClient
    except ImportError as exc:
        warn(
            f"Could not import GmailClient ({exc}). Install dependencies first "
            "(step 2), then re-run this script."
        )
        return

    client = GmailClient(
        credentials_file=DEFAULT_CREDENTIALS, token_file=DEFAULT_TOKEN
    )
    try:
        client.authenticate()
    except Exception as exc:  # noqa: BLE001 - surface any auth failure cleanly
        warn(f"Authentication failed: {exc}")
        return
    ok(f"Authenticated. Token cached in {DEFAULT_TOKEN}.")


def main() -> int:
    os.chdir(PROJECT_ROOT)
    print("email-organizer interactive setup")
    print("Press Ctrl-C at any time to stop; nothing is destructive.")

    check_python()
    install_dependencies()
    settings = create_config()
    check_credentials()
    check_ollama(settings)
    run_gmail_auth()

    banner("Setup summary")
    info("Next steps:")
    info("  - Preview without changes:  python main.py --dry-run --limit 20")
    info("  - Apply labels to Gmail:    python main.py --apply --limit 20")
    print()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nSetup interrupted. Re-run 'python scripts/setup.py' any time.")
        raise SystemExit(130)

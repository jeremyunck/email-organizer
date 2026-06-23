#!/usr/bin/env python3
"""Interactive setup for email-organizer.

Walks you through everything the tool needs before its first run:

  1. Python version check.
  2. Installing Python dependencies (optionally into a virtualenv).
  3. Creating ``config.yaml`` from the example (with a couple of prompts).
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
    """Create config.yaml if needed and return the effective settings.

    Returns a dict with at least ``ollama_url`` and ``model`` so later steps
    can check Ollama, even if config.yaml already existed.
    """
    banner("3. Configuration (config.yaml)")
    config_path = os.path.join(PROJECT_ROOT, CONFIG_FILE)
    example_path = os.path.join(PROJECT_ROOT, CONFIG_EXAMPLE)

    settings = {"ollama_url": DEFAULT_OLLAMA_URL, "model": DEFAULT_MODEL}

    if os.path.exists(config_path):
        ok(f"{CONFIG_FILE} already exists; leaving it untouched.")
        settings.update(_read_ollama_settings(config_path))
        return settings

    if not os.path.exists(example_path):
        warn(f"{CONFIG_EXAMPLE} is missing; cannot create {CONFIG_FILE}.")
        return settings

    info("Let's create config.yaml. Press Enter to accept the defaults.")
    model = ask("Ollama model name", DEFAULT_MODEL)
    base_url = ask("Ollama base URL", DEFAULT_OLLAMA_URL)

    with open(example_path, "r", encoding="utf-8") as handle:
        text = handle.read()

    text = text.replace(f'model: "{DEFAULT_MODEL}"', f'model: "{model}"')
    text = text.replace(
        f'base_url: "{DEFAULT_OLLAMA_URL}"', f'base_url: "{base_url}"'
    )

    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(text)

    ok(f"Wrote {CONFIG_FILE} (model={model}, base_url={base_url}).")
    settings.update({"ollama_url": base_url, "model": model})
    return settings


def _read_ollama_settings(config_path: str) -> dict:
    """Best-effort read of the Ollama url/model from an existing config."""
    settings: dict = {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return settings
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        ollama = data.get("ollama", {})
        if ollama.get("base_url"):
            settings["ollama_url"] = ollama["base_url"]
        if ollama.get("model"):
            settings["model"] = ollama["model"]
    except Exception:
        pass
    return settings


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

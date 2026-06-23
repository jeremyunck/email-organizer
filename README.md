# email-organizer

A local, CLI-only Python tool that organizes your Gmail inbox by classifying
each recent message with a **locally running Ollama LLM** and applying Gmail
**labels** (and optionally archiving) based on the result.

It authenticates to Gmail with OAuth, reads recent inbox messages, asks a local
model to categorize each one, and prints the proposed action. Nothing leaves
your machine except the Gmail API calls — classification happens locally via
Ollama.

## ⚠️ Safety disclaimer

- **Dry-run by default.** No changes are made to Gmail unless you pass
  `--apply`.
- **Never deletes email.** This tool only adds labels and (optionally) removes
  the `INBOX` label to archive. It never permanently deletes, never moves to
  Trash, and never marks anything as spam.
- It uses the `gmail.modify` scope, which does **not** grant permanent delete.
- "Protected" sender domains (e.g. banks, IRS) are never auto-archived.

## What it does

1. Authenticates to Gmail via OAuth.
2. Ensures a set of `AI/*` labels exist.
3. Lists recent inbox messages matching a query.
4. Extracts a clean text excerpt of each message.
5. Sends each to a local Ollama model for JSON classification.
6. Applies validation + safety rules.
7. In dry-run: prints the proposed label/archive action.
   In apply: applies labels, archives if appropriate, and records the message
   ID in SQLite so it is not reprocessed.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally
- Gmail OAuth credentials (`credentials.json`)

## Setup

### Quick start (interactive script)

The fastest way to get going is the interactive setup script, which walks you
through every piece the tool needs and tells you what's still missing:

```bash
python -m venv .venv
source .venv/bin/activate
python scripts/setup.py
```

It will, step by step:

1. Check your Python version (3.11+).
2. Offer to install the Python dependencies.
3. Create `config.yaml` from the example, prompting for the Ollama model and
   base URL.
4. Check for the Gmail OAuth `credentials.json` and, if it's missing, print
   exactly how to create one.
5. Verify Ollama is reachable and offer to `ollama pull` the model.
6. Run the one-time Gmail OAuth flow to create `token.json`.

Every step is optional and safe to skip — the script never deletes anything,
and you can re-run it any time to pick up where you left off (for example,
after you've downloaded `credentials.json`).

### Manual setup

If you'd rather do it by hand:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Edit `config.yaml` as needed (model name, query, thresholds, protected
domains).

### Creating Gmail `credentials.json`

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Enable the **Gmail API**:
   *APIs & Services → Library → search "Gmail API" → Enable*.
4. Configure the **OAuth consent screen**:
   *APIs & Services → OAuth consent screen*. Choose **External**, fill in the
   required fields, and add your own Google account as a **Test user**.
5. Create credentials:
   *APIs & Services → Credentials → Create Credentials → OAuth client ID →
   Application type: **Desktop app***.
6. Download the JSON and save it as `credentials.json` in the project root.

On first run a browser window opens for you to grant access. The resulting
token is cached in `token.json`.

> `credentials.json`, `token.json`, `config.yaml`, and `state.db` are
> git-ignored — never commit them.

### Running Ollama

```bash
ollama pull llama3.1:8b
ollama serve
```

Ollama listens on `http://localhost:11434` by default, matching
`config.example.yaml`.

## Usage

Dry-run (default, safe):

```bash
python main.py --dry-run --limit 20
```

Apply changes to Gmail:

```bash
python main.py --apply --limit 20
```

Custom query:

```bash
python main.py --query "in:inbox -label:AI/Processed newer_than:7d" --dry-run
```

Custom config:

```bash
python main.py --config config.yaml --dry-run --limit 10
```

If neither `--dry-run` nor `--apply` is given, the tool runs in dry-run mode.

At the end of each run a summary prints: scanned, classified, skipped, errors,
and per-category label counts (labeled "Would apply" in dry-run, "Applied" in
apply mode).

## Labels

All labels live under the `AI/` namespace (Gmail labels, not folders):

| Label                | Meaning                                              |
| -------------------- | ---------------------------------------------------- |
| `AI/Needs Attention` | Important emails requiring action or a response      |
| `AI/Receipts`        | Receipts, order/shipping confirmations               |
| `AI/Bills`           | Bills, statements, payment reminders                 |
| `AI/Personal`        | Friends, family, personal correspondence             |
| `AI/Work`            | Professional / job-related email                      |
| `AI/Finance`         | Banks, investments, tax, insurance                   |
| `AI/Travel`          | Flights, hotels, reservations, trip logistics        |
| `AI/Marketing`       | Ads, promotions, discounts, product announcements    |
| `AI/Newsletters`     | Recurring newsletters, digests, subscriptions        |
| `AI/Unknown`         | Unclear or ambiguous                                 |
| `AI/Processed`       | Added to every email the tool has handled            |

Every classified email gets exactly one category label plus `AI/Processed`.
Only `AI/Marketing` and `AI/Newsletters` (with confidence above the threshold,
and not from a protected domain) are archived by removing `INBOX`.

## Troubleshooting

- **`invalid_grant`** — The cached token is stale or revoked. Delete
  `token.json` and re-run to re-authenticate.
- **Ollama connection refused** — Ollama isn't running or is on a different
  host/port. Start it with `ollama serve` and confirm `base_url` in your
  config. When Ollama is unreachable, messages are classified as `AI/Unknown`
  with confidence 0 (and never archived).
- **Gmail API not enabled** — Enable the Gmail API in the Google Cloud Console
  for your project (see setup above).
- **OAuth scope warning / "unverified app"** — Expected for a personal project.
  Add your account as a **Test user** on the OAuth consent screen and proceed
  through the warning.

## Project layout

```
README.md
requirements.txt
config.example.yaml
main.py             # CLI + workflow orchestration
gmail_client.py     # OAuth, fetching, body extraction, modify
classifier.py       # Ollama classification + validation
label_manager.py    # ensure/create/resolve labels
state.py            # SQLite tracking of processed message IDs
prompts/
  classify_email.txt
scripts/
  setup.py          # interactive first-time setup
  verify.sh         # compile + config sanity checks
```

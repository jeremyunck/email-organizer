#!/usr/bin/env bash
set -euo pipefail

# Compile-check all core modules.
python -m py_compile main.py gmail_client.py classifier.py label_manager.py state.py

# Verify config.example.yaml is valid YAML with the expected structure.
python - <<'PY'
import yaml

with open("config.example.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

assert config["gmail"]["credentials_file"] == "credentials.json"
assert config["ollama"]["base_url"] == "http://localhost:11434"
assert "AI/Marketing" in config["labels"]["categories"]
print("config.example.yaml parsed successfully")
PY

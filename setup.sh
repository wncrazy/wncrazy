#!/usr/bin/env bash
# Installs everything needed to run Photo Culler locally:
#   - a Python virtualenv with the project's dependencies
#   - Ollama (if missing)
#   - a local vision model pulled into Ollama
#
# Usage:
#   ./setup.sh                    # full setup with the default model (gemma3:4b)
#   ./setup.sh --model gemma3:12b # pull a different model instead
#   ./setup.sh --skip-ollama      # only set up the Python venv, skip Ollama entirely
#
# Safe to re-run: every step checks whether it's already done before acting.

set -euo pipefail

MODEL="gemma3:4b"
SKIP_OLLAMA=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="$2"
      shift 2
      ;;
    --skip-ollama)
      SKIP_OLLAMA=1
      shift
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$1"; }
warn()  { printf '\033[1;33m!\033[0m %s\n' "$1"; }
fail()  { printf '\033[1;31m✗\033[0m %s\n' "$1" >&2; exit 1; }

OS="$(uname -s)"

# ---------- 1. Python ----------

info "Checking Python"
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
[[ -n "$PYTHON_BIN" ]] || fail "Python 3.10+ not found. Install it first (e.g. 'apt install python3' / 'brew install python3')."

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ok "Found $PYTHON_BIN (Python $PY_VERSION)"

# ---------- 2. Virtualenv + dependencies ----------

if [[ ! -d ".venv" ]]; then
  info "Creating virtualenv (.venv)"
  "$PYTHON_BIN" -m venv .venv
  ok "Virtualenv created"
else
  ok "Virtualenv already exists"
fi

info "Installing Python dependencies"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
ok "Python dependencies installed"

# ---------- 3. Ollama ----------

if [[ "$SKIP_OLLAMA" -eq 1 ]]; then
  warn "Skipping Ollama setup (--skip-ollama). AI scoring will be unavailable until you install it."
else
  if command -v ollama >/dev/null 2>&1; then
    ok "Ollama is already installed"
  else
    info "Installing Ollama"
    case "$OS" in
      Linux)
        curl -fsSL https://ollama.com/install.sh | sh
        ;;
      Darwin)
        if command -v brew >/dev/null 2>&1; then
          brew install ollama
        else
          fail "Homebrew not found. Install Ollama manually from https://ollama.com/download and re-run this script."
        fi
        ;;
      *)
        fail "Unsupported OS for automatic Ollama install ($OS). On Windows, install Ollama from https://ollama.com/download, or run this script inside WSL."
        ;;
    esac
    command -v ollama >/dev/null 2>&1 || fail "Ollama install finished but 'ollama' is still not on PATH. Open a new shell and re-run this script."
    ok "Ollama installed"
  fi

  # Make sure the Ollama server is reachable; start it in the background if not.
  info "Checking Ollama server"
  if curl -fsS http://127.0.0.1:11434 >/dev/null 2>&1; then
    ok "Ollama server is running"
  else
    info "Starting Ollama server in the background"
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    for _ in $(seq 1 20); do
      curl -fsS http://127.0.0.1:11434 >/dev/null 2>&1 && break
      sleep 0.5
    done
    curl -fsS http://127.0.0.1:11434 >/dev/null 2>&1 || fail "Could not start Ollama server. Check /tmp/ollama-serve.log."
    ok "Ollama server started"
  fi

  info "Pulling vision model: $MODEL (this can take a while on first run)"
  ollama pull "$MODEL"
  ok "Model $MODEL is ready"
fi

# ---------- done ----------

echo
ok "Setup complete."
echo
echo "To run Photo Culler:"
echo "  source .venv/bin/activate"
echo "  python run.py"
echo
echo "Then open http://127.0.0.1:8000 in your browser."

#!/usr/bin/env bash
#
# BonsaiWire setup: install project dependencies, install the console script,
# seed a realistic demo store, then EXIT.  It never starts a server and never
# installs system packages - Python 3.9+ with the standard library is all the
# product needs (see INSTALL.md for prerequisites).
#
# Safe to re-run: the console-script install and the seed are idempotent.

set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
DEMO_PASSPHRASE="${BONSAIWIRE_PASSPHRASE:-cenius}"

printf '== BonsaiWire setup ==\n'
printf 'Python: %s (%s)\n' "$("$PY" --version 2>&1)" "$(command -v "$PY")"

# 1. Tooling + declared dependencies (requirements.txt pins none: stdlib only).
if ! "$PY" -m pip install --upgrade pip setuptools wheel --disable-pip-version-check >/dev/null 2>&1; then
  printf 'note: pip tooling upgrade skipped (offline or already current)\n'
fi
"$PY" -m pip install --disable-pip-version-check -r requirements.txt
if ! "$PY" -m pip install --disable-pip-version-check --no-build-isolation -e . >/dev/null 2>&1; then
  printf 'note: console script "bonsaiwire" not installed; run the tool as: %s bonsaiwire.py\n' "$PY"
fi

# 2. Self-check: the module must import and the CLI must answer.
"$PY" -c "import bonsaiwire; print('import ok:', bonsaiwire.PROG, bonsaiwire.VERSION)"
"$PY" bonsaiwire.py --version

# 3. Seed the demo store (idempotent: existing records are skipped).
BONSAIWIRE_PASSPHRASE="$DEMO_PASSPHRASE" "$PY" seed_demo.py --data-dir ./.bonsaiwire
"$PY" bonsaiwire.py tree list

printf '\nSetup complete - nothing is left running.\n'
printf 'Start using it:  %s bonsaiwire.py help\n' "$PY"
printf 'Run the tests  :  %s -m unittest discover -s tests -v\n' "$PY"
printf 'Demo grower    :  owner "cenius", passphrase "cenius" (cenius@cenius.ai / cenius)\n'

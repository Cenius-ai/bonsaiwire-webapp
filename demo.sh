#!/usr/bin/env bash
#
# BonsaiWire end-to-end demo - non-interactive, offline, exits 0.
#
# It walks a fresh store through every command group: profile setup, tree
# registry, wire calculator, wiring schedule (add / list / remove / export),
# growth log, plus the validation failures the tool is expected to reject.
#
# Run:  bash demo.sh

set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
DEMO_DIR="${DEMO_DIR:-./.bonsaiwire-demo}"
export BONSAIWIRE_PASSPHRASE="${BONSAIWIRE_PASSPHRASE:-cenius}"
export NO_COLOR=1

rm -rf "$DEMO_DIR"

run() {
  printf '\n$ bonsaiwire %s\n' "$*"
  "$PY" bonsaiwire.py --data-dir "$DEMO_DIR" "$@"
}

reject() {
  printf '\n$ bonsaiwire %s\n' "$*"
  if "$PY" bonsaiwire.py --data-dir "$DEMO_DIR" "$@" 2>&1; then
    printf '!! expected a non-zero exit code\n'
    exit 1
  else
    printf '(exit %s - rejected as expected)\n' "$?"
  fi
}

section() {
  printf '\n==============================================================\n'
  printf '== %s\n' "$1"
  printf '==============================================================\n'
}

printf 'BonsaiWire %s demo - branch wiring schedule, wire calculator, growth log.\n' \
  "$("$PY" bonsaiwire.py --version | awk '{print $2}')"
printf 'Data directory for this demo: %s\n' "$DEMO_DIR"
printf 'Demo grower: owner cenius / passphrase cenius  (hint: cenius@cenius.ai / cenius)\n'

section 'Read-only commands work with no profile at all'
run tree list
run calc wire --branch-diameter-mm 12 --branch-length-cm 90

section 'Profile setup (local offline write gate)'
run profile setup --owner cenius
run profile status

section 'Write commands are locked until the passphrase matches'
printf '\n$ BONSAIWIRE_PASSPHRASE=wrong bonsaiwire tree add --name Maple --trunk-diameter-mm 45\n'
if BONSAIWIRE_PASSPHRASE=wrong "$PY" bonsaiwire.py --data-dir "$DEMO_DIR" tree add \
  --name Maple --trunk-diameter-mm 45 2>&1; then
  printf '!! expected a non-zero exit code\n'
  exit 1
else
  printf '(exit %s - rejected as expected)\n' "$?"
fi

section 'Tree registry'
run tree add --name Maple --species 'Acer palmatum' --trunk-diameter-mm 45 --notes 'north bed; repotted spring 2024'
run tree add --name Juniper --species 'Juniperus procumbens' --trunk-diameter-mm 22 --notes 'cascade training pot'
run tree add --name Elm --species 'Ulmus parvifolia' --trunk-diameter-mm 33 --notes 'shohin bench'
run tree add --name 'Trident Maple' --species 'Acer buergerianum' --trunk-diameter-mm 58
run tree list
reject tree add --name MAPLE --trunk-diameter-mm 30
reject tree add --name Oak --trunk-diameter-mm 600

section 'Wire gauge and length calculator'
run calc wire --branch-diameter-mm 12 --branch-length-cm 90
run calc wire --branch-diameter-mm 4.5 --branch-length-cm 55
run calc wire --branch-diameter-mm 12 --branch-length-cm 90 --wire-gauge-mm 5.0
reject calc wire --branch-diameter-mm 12 --branch-length-cm 90 --wire-gauge-mm 3.2
reject calc wire --branch-diameter-mm 24.1 --branch-length-cm 30
reject calc wire --branch-diameter-mm 12 --branch-length-cm 501

section 'Wiring schedule'
run schedule add --tree maple --branch-label 'left primary' --branch-diameter-mm 12 --branch-length-cm 90 --notes 'anchor at the first fork'
run schedule add --tree Maple --branch-label 'rear secondary' --branch-diameter-mm 6 --branch-length-cm 40 --notes 'pull back toward the trunk'
run schedule add --tree Maple --branch-label 'apex leader' --branch-diameter-mm 3 --branch-length-cm 22 --wire-gauge-mm 1.0 --notes 'fine tip, gauge chosen by hand'
run schedule add --tree Juniper --branch-label 'cascade main' --branch-diameter-mm 8 --branch-length-cm 120 --notes 'long run down the fallen trunk'
run schedule add --tree elm --branch-label 'trunk to first branch' --branch-diameter-mm 10 --branch-length-cm 75
run schedule list
run schedule list --tree MAPLE
reject schedule add --tree Oak --branch-label leader --branch-diameter-mm 6 --branch-length-cm 40
run schedule remove --tree Maple --id 1
run schedule list --tree Maple
run schedule export --format csv

section 'Growth progress log'
run log add --tree maple --height-cm 30 --trunk-diameter-mm 19 --date 2025-04-06 --note 'first wiring, trunk still flexible'
run log add --tree Maple --height-cm 36 --trunk-diameter-mm 22 --date 2025-09-14 --note 'wire removed, no bite marks'
run log add --tree maple --height-cm 42 --trunk-diameter-mm 26 --note 'strong spring flush'
run log show --tree maple
run log show --tree Elm
reject log add --tree maple --height-cm 0

section 'Help and exit codes'
run help

section 'Final tree list (counts come from schedule.json and log.json)'
run tree list

printf '\nDemo complete. Everything above ran offline against %s.\n' "$DEMO_DIR"
printf 'Interactive use:  python3 bonsaiwire.py help\n'

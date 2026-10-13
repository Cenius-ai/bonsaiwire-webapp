#!/usr/bin/env python3
"""Seed a realistic BonsaiWire demo store.

Idempotent: every record is written through BonsaiWire's own commands (so the
demo profile passphrase is hashed by the real PBKDF2 code path) and skipped
when it is already present.  This script never starts a server; it seeds and
exits.

Usage:
    BONSAIWIRE_PASSPHRASE=cenius python3 seed_demo.py [--data-dir ./.bonsaiwire]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bonsaiwire  # noqa: E402  (path set up above for plain-script use)

DEMO_OWNER = "cenius"
DEMO_PASSPHRASE_FALLBACK = "cenius"

# Growth measurements are seeded relative to today so every screen always
# renders recent, plausible content.
TREES: list[dict[str, str]] = [
    {
        "name": "Maple",
        "species": "Acer palmatum",
        "trunk": "45",
        "notes": "North bed; repotted spring 2024",
    },
    {
        "name": "Juniper",
        "species": "Juniperus procumbens",
        "trunk": "22",
        "notes": "Cascade training pot, kept outdoors",
    },
    {
        "name": "Elm",
        "species": "Ulmus parvifolia",
        "trunk": "33",
        "notes": "Shohin bench; shaded after midsummer",
    },
    {
        "name": "Trident Maple",
        "species": "Acer buergerianum",
        "trunk": "58",
        "notes": "Field grown since 2021, ready for the second wiring",
    },
]

SCHEDULE: list[dict[str, str | None]] = [
    {
        "tree": "Maple",
        "label": "left primary",
        "diameter": "12",
        "length": "90",
        "gauge": None,
        "notes": "anchor at the first fork",
    },
    {
        "tree": "Maple",
        "label": "rear secondary",
        "diameter": "6",
        "length": "40",
        "gauge": None,
        "notes": "pull back toward the trunk",
    },
    {
        "tree": "Maple",
        "label": "apex leader",
        "diameter": "3",
        "length": "22",
        "gauge": "1.0",
        "notes": "grower chose 1.0 mm by hand for the fine tip",
    },
    {
        "tree": "Juniper",
        "label": "cascade main",
        "diameter": "8",
        "length": "120",
        "gauge": None,
        "notes": "long run down the fallen trunk",
    },
    {
        "tree": "Juniper",
        "label": "right pad",
        "diameter": "4.5",
        "length": "55",
        "gauge": None,
        "notes": None,
    },
    {
        "tree": "Elm",
        "label": "trunk to first branch",
        "diameter": "10",
        "length": "75",
        "gauge": None,
        "notes": "watch the bark, wire comes off in autumn",
    },
]

LOG: list[dict[str, str | None]] = [
    {"tree": "Maple", "days_ago": "420", "height": "28", "trunk": "18", "note": "first wiring, trunk still flexible"},
    {"tree": "Maple", "days_ago": "300", "height": "32", "trunk": "21", "note": "wire removed, no bite marks"},
    {"tree": "Maple", "days_ago": "150", "height": "37.5", "trunk": "24", "note": "strong spring flush, repotted"},
    {"tree": "Maple", "days_ago": "12", "height": "42", "trunk": "26", "note": "ready for the second wiring"},
    {"tree": "Juniper", "days_ago": "300", "height": "18", "trunk": "20", "note": "buy-in from the club auction"},
    {"tree": "Juniper", "days_ago": "140", "height": "20.5", "trunk": "22", "note": None},
    {"tree": "Juniper", "days_ago": "9", "height": "21", "trunk": "22.5", "note": "cascade pads thinned"},
    {"tree": "Elm", "days_ago": "240", "height": "26", "trunk": "29", "note": "root pruning, strong back budding"},
    {"tree": "Elm", "days_ago": "95", "height": "28.5", "trunk": "31", "note": None},
    {"tree": "Elm", "days_ago": "6", "height": "31", "trunk": "33", "note": "defoliated for ramification"},
    {"tree": "Trident Maple", "days_ago": "70", "height": "52", "trunk": "56", "note": "field lifted, potted in a training box"},
]


def invoke_cli(*arguments: str) -> None:
    """Call one BonsaiWire command in process: arguments are passed as values,
    nothing is handed to a shell or to another program."""
    exit_code = bonsaiwire.main(list(arguments))
    if exit_code != 0:
        raise SystemExit(f"seed step failed (exit {exit_code}): {' '.join(arguments)}")


def seed_profile(data_dir: str, passphrase: str) -> bool:
    """Create the demo grower profile when none exists.  False = skip seeding."""
    store = bonsaiwire.Store(Path(data_dir))
    profile = store.read_profile()
    if profile is None:
        invoke_cli("--data-dir", data_dir, "profile", "setup", "--owner", DEMO_OWNER)
        return True
    if str(profile.get("owner_name", "")) != DEMO_OWNER:
        print(f"Seed skipped: {data_dir} already belongs to grower '{profile.get('owner_name')}'.")
        return False
    candidate = bonsaiwire.derive_hash(
        passphrase,
        str(profile.get("passphrase_salt", "")),
        int(profile.get("iterations", bonsaiwire.KDF_ITERATIONS)),
    )
    if candidate != str(profile.get("passphrase_hash", "")):
        print(f"Seed skipped: the profile in {data_dir} uses a different passphrase.")
        return False
    return True


def seed_trees(data_dir: str) -> None:
    store = bonsaiwire.Store(Path(data_dir))
    registered = {str(tree.get("name", "")).casefold() for tree in store.read_trees()}
    for tree in TREES:
        if tree["name"].casefold() in registered:
            continue
        invoke_cli(
            "--data-dir", data_dir, "tree", "add",
            "--name", tree["name"],
            "--species", tree["species"],
            "--trunk-diameter-mm", tree["trunk"],
            "--notes", tree["notes"],
        )


def seed_schedule(data_dir: str) -> None:
    store = bonsaiwire.Store(Path(data_dir))
    existing = {
        (str(entry.get("tree", "")), str(entry.get("branch_label", "")))
        for entry in store.read_schedule()
    }
    for entry in SCHEDULE:
        if (entry["tree"], entry["label"]) in existing:
            continue
        cli_args = [
            "--data-dir", data_dir, "schedule", "add",
            "--tree", str(entry["tree"]),
            "--branch-label", str(entry["label"]),
            "--branch-diameter-mm", str(entry["diameter"]),
            "--branch-length-cm", str(entry["length"]),
        ]
        if entry["gauge"]:
            cli_args += ["--wire-gauge-mm", str(entry["gauge"])]
        if entry["notes"]:
            cli_args += ["--notes", str(entry["notes"])]
        invoke_cli(*cli_args)


def seed_log(data_dir: str) -> None:
    store = bonsaiwire.Store(Path(data_dir))
    existing = {
        (str(entry.get("tree", "")), str(entry.get("date", "")))
        for entry in store.read_log()
    }
    today = date.today()
    for entry in LOG:
        when = (today - timedelta(days=int(str(entry["days_ago"])))).isoformat()
        if (entry["tree"], when) in existing:
            continue
        cli_args = [
            "--data-dir", data_dir, "log", "add",
            "--tree", str(entry["tree"]),
            "--height-cm", str(entry["height"]),
            "--date", when,
        ]
        if entry["trunk"]:
            cli_args += ["--trunk-diameter-mm", str(entry["trunk"])]
        if entry["note"]:
            cli_args += ["--note", str(entry["note"])]
        invoke_cli(*cli_args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a realistic BonsaiWire demo store.")
    parser.add_argument("--data-dir", default=".bonsaiwire", help="store directory to seed")
    args = parser.parse_args(argv)

    passphrase = os.environ.get(bonsaiwire.PASSPHRASE_ENV) or DEMO_PASSPHRASE_FALLBACK
    os.environ[bonsaiwire.PASSPHRASE_ENV] = passphrase

    print(f"Seeding BonsaiWire demo data in {args.data_dir}")
    if not seed_profile(args.data_dir, passphrase):
        return 0
    seed_trees(args.data_dir)
    seed_schedule(args.data_dir)
    seed_log(args.data_dir)
    print("Seed complete.  Demo: cenius@cenius.ai / cenius  (profile owner 'cenius', passphrase 'cenius')")
    return 0


if __name__ == "__main__":
    sys.exit(main())

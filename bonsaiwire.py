#!/usr/bin/env python3
"""BonsaiWire - branch wiring schedule, wire gauge calculator and growth log.

A single-file, standard-library-only command line tool for a bonsai grower who
wires branches by hand.  It keeps three local store files (trees.json,
schedule.json, log.json) plus one local grower profile (profile.json) in a data
directory (./.bonsaiwire by default, --data-dir to override).  Every write
command is gated behind the local grower passphrase; every read-only command
works with no passphrase at all.

Design direction (committed, see README "Design direction"):
  calm-precise / terminal dark / monospace / compact / aligned columns;
  exactly one accent, oklch(0.58 0.12 48) = #b26235, used only for primary
  status lines and never as the sole carrier of meaning.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import tempfile
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

PROG = "bonsaiwire"
VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Design tokens (the committed design source of truth for this CLI surface).
# --------------------------------------------------------------------------
THEME = {
    "surface": "terminal dark",
    "accent_oklch": "oklch(0.58 0.12 48)",
    "accent_hex": "#b26235",
    "accent_ansi": "\x1b[38;2;178;98;53m",
    "dim_ansi": "\x1b[38;2;138;132;124m",
    "reset_ansi": "\x1b[0m",
    "rule_char": "-",
    "type_family": "monospace",
    "density": "compact",
    "line_width": 78,
}

# --------------------------------------------------------------------------
# Domain constants.
# --------------------------------------------------------------------------
STANDARD_WIRE_GAUGES_MM: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)
WIRE_LENGTH_FACTOR = 1.15
STANDARD_GAUGE_LIST = ", ".join(f"{gauge:.1f}" for gauge in STANDARD_WIRE_GAUGES_MM)

# field key -> (minimum, maximum)
BOUNDS: dict[str, tuple[float, float]] = {
    "branch_diameter_mm": (0.5, 24.0),
    "branch_length_cm": (1.0, 500.0),
    "height_cm": (1.0, 500.0),
    "trunk_diameter_mm": (0.5, 500.0),
}

OVER_RANGE_MESSAGES: dict[str, str] = {
    "branch_diameter_mm": (
        "measurement exceeds the supported range "
        "(max 24 mm branch diameter -> 8.0 mm wire)"
    ),
    "branch_length_cm": "measurement exceeds the supported range (max 500 cm branch length)",
    "height_cm": "measurement exceeds the supported range (max 500 cm height)",
    "trunk_diameter_mm": "measurement exceeds the supported range (max 500 mm trunk diameter)",
}

UNDER_RANGE_MESSAGES: dict[str, str] = {
    "branch_diameter_mm": "measurement below the supported range (min 0.5 mm branch diameter)",
    "branch_length_cm": "measurement below the supported range (min 1 cm branch length)",
    "height_cm": "measurement below the supported range (min 1 cm height)",
    "trunk_diameter_mm": "measurement below the supported range (min 0.5 mm trunk diameter)",
}

FIELD_FLAGS: dict[str, str] = {
    "branch_diameter_mm": "--branch-diameter-mm",
    "branch_length_cm": "--branch-length-cm",
    "height_cm": "--height-cm",
    "trunk_diameter_mm": "--trunk-diameter-mm",
}

MAX_LABEL_CHARS = 60
EPSILON = 1e-9

DATA_DIR_DEFAULT_NAME = ".bonsaiwire"
CONFIG_FILE_NAME = ".bonsaiwirerc.json"
PASSPHRASE_ENV = "BONSAIWIRE_PASSPHRASE"
KDF_NAME = "PBKDF2-HMAC-SHA256"
KDF_ITERATIONS = 200_000
KDF_SALT_BYTES = 16

TREES_FILE = "trees.json"
SCHEDULE_FILE = "schedule.json"
LOG_FILE = "log.json"
PROFILE_FILE = "profile.json"


# --------------------------------------------------------------------------
# Errors: every one of these is reported as a message plus a stable exit code.
# --------------------------------------------------------------------------
class BonsaiError(Exception):
    """Base class: the CLI boundary prints this and exits non-zero."""


class UserError(BonsaiError):
    """Bad input or bad state from the grower -> exit code 1."""


class UsageError(BonsaiError):
    """Wrong command shape -> exit code 2."""


class StoreError(BonsaiError):
    """Unreadable or unwritable store file -> exit code 1."""


# --------------------------------------------------------------------------
# Small formatting helpers.
# --------------------------------------------------------------------------
def fmt_number(value: float) -> str:
    """45.0 -> '45', 0.5 -> '0.5', 103.5 -> '103.5' (no trailing zeros)."""
    if abs(value - round(value)) < EPSILON:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def fmt_gauge(value: float) -> str:
    """Standard wire gauges always print with one decimal: 4.0 -> '4.0'."""
    return f"{value:.1f}"


def fmt_height(value: float) -> str:
    return f"{fmt_number(value)} cm"


def parse_date(raw: str | None) -> str:
    """Validate a YYYY-MM-DD date, or fall back to today."""
    if raw is None:
        return date.today().isoformat()
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise UsageError(f"--date expects YYYY-MM-DD, got '{raw}'") from None


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --------------------------------------------------------------------------
# Palette: colour is decorative only, never the sole carrier of a signal.
# --------------------------------------------------------------------------
class Palette:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def accent(self, text: str) -> str:
        if not self.enabled:
            return text
        return f"{THEME['accent_ansi']}{text}{THEME['reset_ansi']}"

    def dim(self, text: str) -> str:
        if not self.enabled:
            return text
        return f"{THEME['dim_ansi']}{text}{THEME['reset_ansi']}"


# --------------------------------------------------------------------------
# Shared validator: one place owns every bound and every message.
# --------------------------------------------------------------------------
def parse_number(raw: str | None, flag: str) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise UsageError(f"{flag} expects a number, got '{raw}'") from None
    if math.isnan(value) or math.isinf(value):
        raise UsageError(f"{flag} expects a finite number, got '{raw}'")
    return value


def validate_measurement(field: str, value: float) -> float:
    """Accept a measurement only inside its documented range."""
    low, high = BOUNDS[field]
    if value < low - EPSILON:
        raise UserError(UNDER_RANGE_MESSAGES[field])
    if value > high + EPSILON:
        raise UserError(OVER_RANGE_MESSAGES[field])
    return value


def validate_text(value: str, what: str, flag: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise UserError(f"{what} is required; pass {flag}")
    if len(cleaned) > MAX_LABEL_CHARS:
        raise UserError(f"{what} must be 1-{MAX_LABEL_CHARS} characters")
    return cleaned


def is_standard_gauge(value: float) -> bool:
    return any(abs(value - gauge) < EPSILON for gauge in STANDARD_WIRE_GAUGES_MM)


def validate_gauge(value: float) -> float:
    if not is_standard_gauge(value):
        raise UserError(
            f"wire gauge {fmt_gauge(value)} mm is not a standard size "
            f"(choose from {STANDARD_GAUGE_LIST})"
        )
    return value


def compute_gauge(branch_diameter_mm: float) -> float:
    """Smallest standard size >= branch diameter / 3 (D3 rule)."""
    ratio = branch_diameter_mm / 3.0
    for gauge in STANDARD_WIRE_GAUGES_MM:
        if gauge + EPSILON >= ratio:
            return gauge
    # Unreachable while branch diameter stays <= 24 mm; kept as a fail-closed stop.
    raise UserError(OVER_RANGE_MESSAGES["branch_diameter_mm"])


def compute_wire_length_cm(branch_length_cm: float) -> int:
    """10% run past the anchor plus 5% tie-off/service loop, rounded up."""
    return int(math.ceil(branch_length_cm * WIRE_LENGTH_FACTOR - EPSILON))


def resolve_tree(trees: Sequence[dict[str, Any]], name: str) -> str:
    """Case-insensitive lookup returning the canonical registered spelling."""
    wanted = name.strip()
    if not wanted:
        raise UserError("tree name is required; pass --tree <name>")
    folded = wanted.casefold()
    for tree in trees:
        if str(tree.get("name", "")).casefold() == folded:
            return str(tree["name"])
    registered = ", ".join(sorted((str(t.get("name", "")) for t in trees), key=str.casefold))
    if not registered:
        raise UserError(
            f"unknown tree '{wanted}'; no trees registered yet; run: "
            f"{PROG} tree add --name <name> --trunk-diameter-mm <mm>"
        )
    raise UserError(f"unknown tree '{wanted}'; registered trees: {registered}")


def next_entry_id(entries: Sequence[dict[str, Any]]) -> int:
    return max((int(entry.get("id", 0)) for entry in entries), default=0) + 1


# --------------------------------------------------------------------------
# Local JSON store: atomic writes, one file per entity family.
# --------------------------------------------------------------------------
STORE_FILES: tuple[str, ...] = (TREES_FILE, SCHEDULE_FILE, LOG_FILE, PROFILE_FILE)


def read_json_file(target: Path) -> Any:
    """Parse one UTF-8 JSON file with the standard JSON reader.

    The contents are parsed strictly as a JSON document (a data format, not an
    object graph), and a malformed file becomes a plain user-facing message.
    """
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise StoreError(f"cannot read {target}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise StoreError(
            f"cannot read {target}: not valid JSON ({exc}); fix or delete the file"
        ) from exc


def safe_store_path(base_dir: Path, filename: str) -> Path:
    """Resolve one allowlisted store file inside the data directory.

    The file name must be one of the four known store files and the resolved
    path must stay inside the resolved data directory, so a crafted name can
    never walk out of the store.
    """
    base = Path(base_dir).expanduser().resolve()
    if filename not in STORE_FILES:
        raise StoreError(f"refusing unknown store file {filename!r}")
    candidate = (base / filename).resolve()
    if candidate.parent != base:
        raise StoreError(f"refusing to touch {candidate}: outside {base}")
    return candidate


def operator_path(value: str) -> Path:
    """Normalise a path the operator asked for.

    A relative path is confined to the working directory (so a stray '../../..'
    cannot move the store somewhere else); an absolute path is the operator's
    explicit choice and is used as given.  Every file inside the directory is
    then resolved through safe_store_path().
    """
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    base = Path.cwd().resolve()
    if not (base / candidate).resolve().is_relative_to(base):
        raise StoreError(f"refusing data directory outside {base}: {candidate}")
    return candidate


def atomic_write_json(target: Path, payload: Any) -> None:
    """Write JSON through a temp file in the same directory, then rename it."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".bonsaiwire-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
    except OSError as exc:
        raise StoreError(f"cannot write {target}: {exc}") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


class Store:
    """The four JSON files of one grower data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def path(self, filename: str) -> Path:
        return safe_store_path(self.data_dir, filename)

    def ensure(self) -> None:
        """Create the data directory and the three empty entity files."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StoreError(f"cannot create data directory {self.data_dir}: {exc}") from exc
        for filename in (TREES_FILE, SCHEDULE_FILE, LOG_FILE):
            target = self.path(filename)
            if not target.exists():
                atomic_write_json(target, [])

    def _read_list(self, filename: str) -> list[dict[str, Any]]:
        target = self.path(filename)
        if not target.exists():
            return []
        payload = read_json_file(target)
        if not isinstance(payload, list):
            raise StoreError(f"cannot read {target}: expected a JSON list of records")
        return [row for row in payload if isinstance(row, dict)]

    def read_trees(self) -> list[dict[str, Any]]:
        return self._read_list(TREES_FILE)

    def read_schedule(self) -> list[dict[str, Any]]:
        return self._read_list(SCHEDULE_FILE)

    def read_log(self) -> list[dict[str, Any]]:
        return self._read_list(LOG_FILE)

    def write_trees(self, trees: Sequence[dict[str, Any]]) -> None:
        atomic_write_json(self.path(TREES_FILE), list(trees))

    def write_schedule(self, entries: Sequence[dict[str, Any]]) -> None:
        atomic_write_json(self.path(SCHEDULE_FILE), list(entries))

    def write_log(self, entries: Sequence[dict[str, Any]]) -> None:
        atomic_write_json(self.path(LOG_FILE), list(entries))

    def read_profile(self) -> dict[str, Any] | None:
        target = self.path(PROFILE_FILE)
        if not target.exists():
            return None
        payload = read_json_file(target)
        if not isinstance(payload, dict):
            raise StoreError(f"cannot read {target}: expected a JSON object")
        return payload

    def write_profile(self, profile: dict[str, Any]) -> None:
        atomic_write_json(self.path(PROFILE_FILE), profile)


def resolve_data_dir(raw: str | None) -> Path:
    """--data-dir wins, then ~/.bonsaiwirerc.json, then ./.bonsaiwire."""
    if raw:
        return operator_path(raw)
    config_path = Path.home() / CONFIG_FILE_NAME
    if config_path.exists():
        config = read_json_file(config_path)
        if isinstance(config, dict) and config.get("data_dir"):
            return operator_path(str(config["data_dir"]))
    return operator_path(DATA_DIR_DEFAULT_NAME)


# --------------------------------------------------------------------------
# Local grower profile: passphrase gate over every write command.
# --------------------------------------------------------------------------
def derive_hash(passphrase: str, salt_hex: str, iterations: int) -> str:
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations)
    return digest.hex()


def read_passphrase(prompt: str = "Passphrase: ") -> str:
    """Prefer the environment; never block a non-interactive run on a prompt."""
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env is not None and from_env != "":
        return from_env
    if not sys.stdin.isatty():
        raise UserError(
            f"no passphrase available; set {PASSPHRASE_ENV} or run in a terminal"
        )
    return getpass.getpass(prompt)


def require_passphrase(store: Store) -> None:
    """Gate a write command: profile must exist and the passphrase must match."""
    profile = store.read_profile()
    if profile is None:
        raise UserError(f"no grower profile set up; run: {PROG} profile setup")
    supplied = read_passphrase()
    stored_hash = str(profile.get("passphrase_hash", ""))
    iterations = int(profile.get("iterations", KDF_ITERATIONS))
    salt_hex = str(profile.get("passphrase_salt", ""))
    candidate = derive_hash(supplied, salt_hex, iterations)
    if not hmac.compare_digest(candidate, stored_hash):
        raise UserError("passphrase did not match")


# --------------------------------------------------------------------------
# Table rendering: aligned columns, ASCII only, never wider than 78 columns.
# --------------------------------------------------------------------------
def clip(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def render_columns(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    max_width: int = THEME["line_width"],
    gap: str = "  ",
    on_clip: Callable[[str, str], None] | None = None,
) -> list[str]:
    body = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    total = sum(widths) + len(gap) * (len(headers) - 1)
    last = len(widths) - 1
    if total > max_width and widths[last] > 8:
        widths[last] -= min(total - max_width, widths[last] - 8)
        total = sum(widths) + len(gap) * (len(headers) - 1)
    while total > max_width:
        widest = max(range(len(widths)), key=lambda i: widths[i])
        if widths[widest] <= 4:
            break
        widths[widest] -= 1
        total -= 1
    lines = [gap.join(clip(str(h), widths[i]).ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    rule_width = sum(widths) + len(gap) * (len(headers) - 1)
    lines.append(THEME["rule_char"] * min(rule_width, max_width))
    for row in body:
        rendered = []
        for index, cell in enumerate(row):
            shown = clip(cell, widths[index])
            if on_clip is not None and shown != cell:
                on_clip(str(headers[index]), cell)
            rendered.append(shown.ljust(widths[index]))
        lines.append(gap.join(rendered).rstrip())
    return lines


def print_hint_block(ctx: Any, sentence: str, lead: str, command: str) -> None:
    """Empty-state prose: the sentence, then the exact command to run, wrapped
    so the whole block stays inside the 78-column frame."""
    ctx.out(sentence)
    for line in textwrap.wrap(f"{lead}{command}", width=THEME["line_width"] - 2) or [lead.strip()]:
        ctx.out("  " + line)


def print_table(ctx: Any, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """Print an aligned 78-column table; cells clipped for width are repeated
    in full on indented lines underneath, so nothing is silently lost."""
    clipped: list[tuple[str, str]] = []

    def remember(header: str, value: str) -> None:
        if (header, value) not in clipped:
            clipped.append((header, value))

    for line in render_columns(headers, rows, on_clip=remember):
        ctx.out(line)
    for header, value in clipped:
        prefix = f"    {header.lower()}: "
        first, *rest = textwrap.wrap(value, width=max(24, THEME["line_width"] - len(prefix))) or [""]
        ctx.out(prefix + first)
        for extra in rest:
            ctx.out(" " * len(prefix) + extra)


# --------------------------------------------------------------------------
# Command context: everything a command needs, resolved once.
# --------------------------------------------------------------------------
class Context:
    def __init__(self, args: argparse.Namespace, ensure_store: bool = True) -> None:
        self.args = args
        self.palette = Palette(color_enabled(args))
        self.store = Store(resolve_data_dir(getattr(args, "data_dir", None)))
        if ensure_store:
            self.store.ensure()

    def out(self, line: str = "") -> None:
        print(line)

    def title(self, text: str) -> None:
        self.out(self.palette.accent(text))

    def hint(self, text: str) -> None:
        self.out(self.palette.dim(text))

    def save_trees(self, trees: Sequence[dict[str, Any]]) -> None:
        self.store.write_trees(trees)

    def save_schedule(self, entries: Sequence[dict[str, Any]]) -> None:
        self.store.write_schedule(entries)

    def save_log(self, entries: Sequence[dict[str, Any]]) -> None:
        self.store.write_log(entries)


def color_enabled(args: argparse.Namespace) -> bool:
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


# --------------------------------------------------------------------------
# tree add / tree list
# --------------------------------------------------------------------------
def cmd_tree_add(ctx: Context, args: argparse.Namespace) -> int:
    require_passphrase(ctx.store)
    name = validate_text(args.name, "tree name", "--name")
    species = (args.species or "").strip()
    notes = (args.notes or "").strip()
    trunk = parse_number(args.trunk_diameter_mm, "--trunk-diameter-mm")
    if trunk is not None:
        validate_measurement("trunk_diameter_mm", trunk)

    trees = ctx.store.read_trees()
    folded = name.casefold()
    for tree in trees:
        if str(tree.get("name", "")).casefold() == folded:
            raise UserError(f"tree '{name}' already exists as '{tree['name']}'")

    trees.append(
        {
            "name": name,
            "species": species,
            "trunk_diameter_mm": trunk,
            "notes": notes,
            "created_at": now_iso(),
        }
    )
    trees.sort(key=lambda tree: str(tree.get("name", "")).casefold())
    ctx.save_trees(trees)
    details = []
    if species:
        details.append(species)
    if trunk is not None:
        details.append(f"{fmt_number(trunk)} mm trunk")
    suffix = f" ({', '.join(details)})" if details else ""
    ctx.out(f"Registered tree '{name}'{suffix}.")
    if notes:
        ctx.hint(f"note: {notes}")
    return 0


def cmd_tree_list(ctx: Context, args: argparse.Namespace) -> int:
    trees = sorted(ctx.store.read_trees(), key=lambda t: str(t.get("name", "")).casefold())
    if not trees:
        print_hint_block(
            ctx,
            "No trees registered yet.",
            "Add one with: ",
            f"{PROG} tree add --name <name> --trunk-diameter-mm <mm>",
        )
        return 0

    schedule = ctx.store.read_schedule()
    log = ctx.store.read_log()
    ctx.title(f"Trees in {ctx.store.data_dir}")
    rows = []
    for tree in trees:
        name = str(tree.get("name", ""))
        trunk = tree.get("trunk_diameter_mm")
        rows.append(
            [
                name,
                str(tree.get("species", "")) or "-",
                f"{fmt_number(float(trunk))}" if trunk is not None else "-",
                sum(1 for entry in schedule if entry.get("tree") == name),
                sum(1 for entry in log if entry.get("tree") == name),
                str(tree.get("notes", "")) or "-",
            ]
        )
    print_table(ctx, ["NAME", "SPECIES", "TRUNK MM", "WIRES", "GROWTH", "NOTES"], rows)
    ctx.hint(f"{len(trees)} tree(s) registered.")
    return 0


# --------------------------------------------------------------------------
# schedule add / list / remove / export
# --------------------------------------------------------------------------
def cmd_schedule_add(ctx: Context, args: argparse.Namespace) -> int:
    require_passphrase(ctx.store)
    trees = ctx.store.read_trees()
    if not args.tree:
        raise UsageError(
            "--tree is required; example: "
            f"{PROG} schedule add --tree Maple --branch-label 'left primary' "
            "--branch-diameter-mm 12 --branch-length-cm 90"
        )
    canonical = resolve_tree(trees, args.tree)
    branch_label = validate_text(args.branch_label, "branch label", "--branch-label")
    diameter = parse_number(args.branch_diameter_mm, "--branch-diameter-mm")
    length = parse_number(args.branch_length_cm, "--branch-length-cm")
    if diameter is None:
        raise UsageError(
            "--branch-diameter-mm is required; example: "
            f"{PROG} schedule add --tree {canonical} --branch-label '{branch_label}' "
            "--branch-diameter-mm 12 --branch-length-cm 90"
        )
    if length is None:
        raise UsageError(
            "--branch-length-cm is required; example: "
            f"{PROG} schedule add --tree {canonical} --branch-label '{branch_label}' "
            "--branch-diameter-mm 12 --branch-length-cm 90"
        )
    validate_measurement("branch_diameter_mm", diameter)
    validate_measurement("branch_length_cm", length)

    supplied = parse_number(args.wire_gauge_mm, "--wire-gauge-mm")
    if supplied is not None:
        validate_gauge(supplied)
        gauge, source = supplied, "supplied"
    else:
        gauge, source = compute_gauge(diameter), "computed"

    entries = ctx.store.read_schedule()
    entry = {
        "id": next_entry_id(entries),
        "tree": canonical,
        "branch_label": branch_label,
        "branch_diameter_mm": diameter,
        "branch_length_cm": length,
        "wire_gauge_mm": gauge,
        "wire_length_cm": compute_wire_length_cm(length),
        "gauge_source": source,
        "notes": (args.notes or "").strip(),
        "created_at": now_iso(),
    }
    entries.append(entry)
    ctx.save_schedule(entries)
    ctx.out(
        f"Added wiring entry {entry['id']} for '{canonical}': branch '{branch_label}', "
        f"{fmt_number(diameter)} mm branch, {fmt_number(length)} cm long -> "
        f"wire gauge {fmt_gauge(gauge)} mm ({source}), "
        f"wire length {entry['wire_length_cm']} cm."
    )
    if entry["notes"]:
        ctx.hint(f"note: {entry['notes']}")
    return 0


def _group_schedule(entries: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault(str(entry.get("tree", "")), []).append(entry)
    for rows in grouped.values():
        rows.sort(key=lambda entry: int(entry.get("id", 0)))
    return sorted(grouped.items(), key=lambda item: item[0].casefold())


def _subtotal_line(tree: str, rows: Sequence[dict[str, Any]]) -> str:
    gauges = sorted({float(entry.get("wire_gauge_mm", 0.0)) for entry in rows})
    gauge_text = ", ".join(fmt_gauge(gauge) for gauge in gauges)
    total_wire = sum(int(entry.get("wire_length_cm", 0)) for entry in rows)
    return (
        f"{tree}: {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}, "
        f"gauge{'s' if len(gauges) != 1 else ''} {gauge_text} mm, {total_wire} cm wire total"
    )


def cmd_schedule_list(ctx: Context, args: argparse.Namespace) -> int:
    entries = ctx.store.read_schedule()
    tree_filter = None
    if args.tree:
        tree_filter = resolve_tree(ctx.store.read_trees(), args.tree)
        entries = [entry for entry in entries if entry.get("tree") == tree_filter]

    if not entries:
        target = tree_filter if tree_filter else "<name>"
        print_hint_block(
            ctx,
            f"No wiring entries for {target} yet.",
            "Add one with: ",
            f"{PROG} schedule add --tree {target} --branch-label <label> "
            "--branch-diameter-mm <mm> --branch-length-cm <cm>",
        )
        return 0

    ctx.title("Wiring schedule" + (f" - {tree_filter}" if tree_filter else ""))
    any_supplied = False
    for tree, rows in _group_schedule(entries):
        table_rows = []
        for entry in rows:
            source = str(entry.get("gauge_source", "computed"))
            any_supplied = any_supplied or source == "supplied"
            table_rows.append(
                [
                    entry.get("id", ""),
                    entry.get("branch_label", ""),
                    fmt_number(float(entry.get("branch_diameter_mm", 0.0))),
                    fmt_number(float(entry.get("branch_length_cm", 0.0))),
                    fmt_gauge(float(entry.get("wire_gauge_mm", 0.0))) + ("*" if source == "supplied" else ""),
                    entry.get("wire_length_cm", 0),
                    str(entry.get("notes", "")) or "-",
                ]
            )
        print_table(
            ctx,
            ["ID", "BRANCH", "DIA MM", "LEN CM", "GAUGE MM", "WIRE CM", "NOTES"],
            table_rows,
        )
        ctx.out(_subtotal_line(tree, rows))
    if any_supplied:
        ctx.hint("* gauge supplied by the grower; unmarked gauges are computed by the D3 rule.")
    return 0


def cmd_schedule_remove(ctx: Context, args: argparse.Namespace) -> int:
    require_passphrase(ctx.store)
    if not args.tree:
        raise UsageError(
            "--tree is required; example: " f"{PROG} schedule remove --tree Maple --id 3"
        )
    if args.id is None:
        raise UsageError(
            "--id is required; example: " f"{PROG} schedule remove --tree Maple --id 3"
        )
    canonical = resolve_tree(ctx.store.read_trees(), args.tree)
    try:
        entry_id = int(args.id)
    except (TypeError, ValueError):
        raise UsageError(f"--id expects an integer, got '{args.id}'") from None

    entries = ctx.store.read_schedule()
    remaining = [
        entry
        for entry in entries
        if not (int(entry.get("id", 0)) == entry_id and entry.get("tree") == canonical)
    ]
    if len(remaining) == len(entries):
        raise UserError(f"no wiring entry {entry_id} for '{canonical}'")
    removed = next(
        entry
        for entry in entries
        if int(entry.get("id", 0)) == entry_id and entry.get("tree") == canonical
    )
    ctx.save_schedule(remaining)
    ctx.out(
        f"Removed wiring entry {entry_id} for '{canonical}': branch '{removed.get('branch_label', '')}', "
        f"{fmt_gauge(float(removed.get('wire_gauge_mm', 0.0)))} mm wire, "
        f"{removed.get('wire_length_cm', 0)} cm."
    )
    return 0


def cmd_schedule_export(ctx: Context, args: argparse.Namespace) -> int:
    entries = ctx.store.read_schedule()
    if args.tree:
        canonical = resolve_tree(ctx.store.read_trees(), args.tree)
        entries = [entry for entry in entries if entry.get("tree") == canonical]
    ordered = [entry for _, rows in _group_schedule(entries) for entry in rows]
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "tree",
            "branch_label",
            "branch_diameter_mm",
            "branch_length_cm",
            "wire_gauge_mm",
            "wire_length_cm",
            "gauge_source",
            "notes",
            "created_at",
        ]
    )
    for entry in ordered:
        writer.writerow(
            [
                entry.get("id", ""),
                entry.get("tree", ""),
                entry.get("branch_label", ""),
                fmt_number(float(entry.get("branch_diameter_mm", 0.0))),
                fmt_number(float(entry.get("branch_length_cm", 0.0))),
                fmt_gauge(float(entry.get("wire_gauge_mm", 0.0))),
                entry.get("wire_length_cm", ""),
                entry.get("gauge_source", ""),
                entry.get("notes", ""),
                entry.get("created_at", ""),
            ]
        )
    return 0


# --------------------------------------------------------------------------
# log add / log show
# --------------------------------------------------------------------------
def cmd_log_add(ctx: Context, args: argparse.Namespace) -> int:
    require_passphrase(ctx.store)
    if not args.tree:
        raise UsageError(
            "--tree is required; example: " f"{PROG} log add --tree Maple --height-cm 42"
        )
    canonical = resolve_tree(ctx.store.read_trees(), args.tree)
    height = parse_number(args.height_cm, "--height-cm")
    if height is None:
        raise UsageError(
            "--height-cm is required; example: " f"{PROG} log add --tree {canonical} --height-cm 42"
        )
    validate_measurement("height_cm", height)
    trunk = parse_number(args.trunk_diameter_mm, "--trunk-diameter-mm")
    if trunk is not None:
        validate_measurement("trunk_diameter_mm", trunk)
    entry_date = parse_date(args.date)

    entries = ctx.store.read_log()
    entry = {
        "id": next_entry_id(entries),
        "tree": canonical,
        "date": entry_date,
        "height_cm": height,
        "trunk_diameter_mm": trunk,
        "note": (args.note or "").strip(),
        "created_at": now_iso(),
    }
    entries.append(entry)
    ctx.save_log(entries)
    trunk_text = f", trunk {fmt_number(trunk)} mm" if trunk is not None else ""
    ctx.out(
        f"Logged growth for '{canonical}' on {entry_date}: "
        f"{fmt_number(height)} cm tall{trunk_text}."
    )
    if entry["note"]:
        ctx.hint(f"note: {entry['note']}")
    return 0


def cmd_log_show(ctx: Context, args: argparse.Namespace) -> int:
    if not args.tree:
        raise UsageError(
            "--tree is required; example: " f"{PROG} log show --tree Maple"
        )
    canonical = resolve_tree(ctx.store.read_trees(), args.tree)
    entries = [entry for entry in ctx.store.read_log() if entry.get("tree") == canonical]
    entries.sort(key=lambda entry: (str(entry.get("date", "")), int(entry.get("id", 0))))
    if not entries:
        print_hint_block(
            ctx,
            f"No growth entries for '{canonical}' yet.",
            "Add one with: ",
            f"{PROG} log add --tree {canonical} --height-cm <cm>",
        )
        return 0

    ctx.title(f"Growth log - {canonical}")
    rows = []
    for entry in entries:
        trunk = entry.get("trunk_diameter_mm")
        rows.append(
            [
                entry.get("date", ""),
                fmt_number(float(entry.get("height_cm", 0.0))),
                fmt_number(float(trunk)) if trunk is not None else "-",
                str(entry.get("note", "")) or "-",
            ]
        )
    print_table(ctx, ["DATE", "HEIGHT CM", "TRUNK MM", "NOTE"], rows)
    first = float(entries[0].get("height_cm", 0.0))
    latest = float(entries[-1].get("height_cm", 0.0))
    delta = latest - first
    sign = "+" if delta >= 0 else ""
    ctx.out(
        f"Totals: first {fmt_height(first)}, latest {fmt_height(latest)}, "
        f"total growth {sign}{fmt_number(delta)} cm over {len(entries)} "
        f"entr{'y' if len(entries) == 1 else 'ies'}."
    )
    return 0


# --------------------------------------------------------------------------
# calc wire
# --------------------------------------------------------------------------
def cmd_calc_wire(ctx: Context, args: argparse.Namespace) -> int:
    diameter = parse_number(args.branch_diameter_mm, "--branch-diameter-mm")
    length = parse_number(args.branch_length_cm, "--branch-length-cm")
    example = (
        f"{PROG} calc wire --branch-diameter-mm 12 --branch-length-cm 90"
    )
    if diameter is None:
        raise UsageError(f"--branch-diameter-mm is required; example: {example}")
    if length is None:
        raise UsageError(f"--branch-length-cm is required; example: {example}")
    validate_measurement("branch_diameter_mm", diameter)
    validate_measurement("branch_length_cm", length)

    supplied = parse_number(args.wire_gauge_mm, "--wire-gauge-mm")
    if supplied is not None:
        validate_gauge(supplied)
        gauge, source = supplied, "supplied"
    else:
        gauge, source = compute_gauge(diameter), "computed"
    wire_length = compute_wire_length_cm(length)

    ctx.title("BonsaiWire wire calculator")
    ctx.out(f"{'branch diameter':<16}: {fmt_number(diameter)} mm")
    ctx.out(f"{'branch length':<16}: {fmt_number(length)} cm")
    ctx.out(f"{'wire gauge':<16}: {fmt_gauge(gauge)} mm ({source})")
    ctx.out(f"{'wire length':<16}: {wire_length} cm")
    ctx.out(f"{'standard sizes':<16}: {STANDARD_GAUGE_LIST} mm")
    note = (
        "gauge computed by the D3 standard-size rule (smallest size >= diameter / 3)."
        if source == "computed"
        else "gauge supplied by the grower and checked against the standard set."
    )
    ctx.hint(note)
    return 0


# --------------------------------------------------------------------------
# profile setup / profile status
# --------------------------------------------------------------------------
def cmd_profile_setup(ctx: Context, args: argparse.Namespace) -> int:
    owner = validate_text(args.owner or "", "owner name", "--owner")
    passphrase = read_passphrase("Choose a passphrase: ")
    if not passphrase:
        raise UserError("passphrase must not be empty")
    if sys.stdin.isatty() and os.environ.get(PASSPHRASE_ENV) in (None, ""):
        repeat = getpass.getpass("Repeat passphrase: ")
        if repeat != passphrase:
            raise UserError("passphrases did not match")

    salt_hex = secrets.token_bytes(KDF_SALT_BYTES).hex()
    profile = {
        "owner_name": owner,
        "passphrase_salt": salt_hex,
        "passphrase_hash": derive_hash(passphrase, salt_hex, KDF_ITERATIONS),
        "iterations": KDF_ITERATIONS,
        "algorithm": KDF_NAME,
        "created_at": now_iso(),
    }
    ctx.store.write_profile(profile)
    ctx.out(f"Grower profile saved for '{owner}' ({KDF_NAME}, {KDF_ITERATIONS} iterations).")
    ctx.hint("the passphrase is stored only as a salted hash; write commands will ask for it.")
    return 0


def cmd_profile_status(ctx: Context, args: argparse.Namespace) -> int:
    profile = ctx.store.read_profile()
    if profile is None:
        print_hint_block(
            ctx,
            "No grower profile set up; write commands are locked.",
            "Set one up with: ",
            f"{PROG} profile setup --owner <name>",
        )
        return 0
    ctx.title("Grower profile")
    ctx.out(f"{'owner':<16}: {profile.get('owner_name', '')}")
    ctx.out(
        f"{'passphrase':<16}: set (salted {profile.get('algorithm', KDF_NAME)} hash, "
        f"{profile.get('iterations', KDF_ITERATIONS)} iterations)"
    )
    ctx.out(f"{'created':<16}: {profile.get('created_at', '')}")
    ctx.out(f"{'data directory':<16}: {ctx.store.data_dir}")
    ctx.hint(f"write commands read {PASSPHRASE_ENV} or prompt for the passphrase.")
    return 0


# --------------------------------------------------------------------------
# help
# --------------------------------------------------------------------------
HELP_TEXT = """\
BonsaiWire - branch wiring schedule, wire calculator and growth log.

Usage: bonsaiwire <group> <command> [options]
       python3 bonsaiwire.py <group> <command> [options]

Global options:
  --data-dir PATH   store directory (default ./.bonsaiwire, or the data_dir
                    value in ~/.bonsaiwirerc.json)
  --no-color        plain output even on a colour terminal
  --version         print the version and exit
  -h, --help        help for the current command

Tree registry
  tree add --name <name> [--species <text>] [--trunk-diameter-mm <mm>] [--notes <text>]
      Example: bonsaiwire tree add --name Maple --species 'Acer palmatum' --trunk-diameter-mm 45
      Error  : bonsaiwire tree add --name MAPLE --trunk-diameter-mm 30
               -> tree 'MAPLE' already exists as 'Maple'
  tree list
      Example: bonsaiwire tree list

Wiring schedule
  schedule add --tree <name> --branch-label <label> --branch-diameter-mm <mm> --branch-length-cm <cm>
               [--wire-gauge-mm <mm>] [--notes <text>]
      Example: bonsaiwire schedule add --tree maple --branch-label 'left primary' \\
                 --branch-diameter-mm 12 --branch-length-cm 90
      Error  : bonsaiwire schedule add --tree oak --branch-label leader \\
                 --branch-diameter-mm 6 --branch-length-cm 40
               -> unknown tree 'oak'; registered trees: Maple
  schedule list [--tree <name>]
      Example: bonsaiwire schedule list --tree MAPLE
  schedule remove --tree <name> --id <id>
      Example: bonsaiwire schedule remove --tree Maple --id 3
  schedule export [--tree <name>] [--format csv]
      Example: bonsaiwire schedule export --format csv

Growth log
  log add --tree <name> --height-cm <cm> [--trunk-diameter-mm <mm>] [--date YYYY-MM-DD] [--note <text>]
      Example: bonsaiwire log add --tree Maple --height-cm 42 --trunk-diameter-mm 24
      Error  : bonsaiwire log add --tree Maple --height-cm 0
               -> measurement below the supported range (min 1 cm height)
  log show --tree <name>
      Example: bonsaiwire log show --tree maple

Wire calculator
  calc wire --branch-diameter-mm <mm> --branch-length-cm <cm> [--wire-gauge-mm <mm>]
      Example: bonsaiwire calc wire --branch-diameter-mm 12 --branch-length-cm 90
      Error  : bonsaiwire calc wire --branch-diameter-mm 24.1 --branch-length-cm 30
               -> measurement exceeds the supported range (max 24 mm branch diameter -> 8.0 mm wire)

Grower profile
  profile setup --owner <name>
      Example: BONSAIWIRE_PASSPHRASE=cenius bonsaiwire profile setup --owner cenius
  profile status
      Example: bonsaiwire profile status

Exit codes:
  0 success | 1 validation or user error | 2 usage error

Demo passphrase for a seeded store: cenius (owner 'cenius').
"""

EXAMPLES: dict[str, tuple[str, str]] = {
    "tree add": (
        "bonsaiwire tree add --name Maple --species 'Acer palmatum' "
        "--trunk-diameter-mm 45 --notes 'north bed'",
        "bonsaiwire tree add --name MAPLE --trunk-diameter-mm 30"
        "  -> tree 'MAPLE' already exists as 'Maple'",
    ),
    "tree list": ("bonsaiwire tree list", "bonsaiwire tree list --data-dir ./missing -> store error, exit 1"),
    "schedule add": (
        "bonsaiwire schedule add --tree maple --branch-label 'left primary' "
        "--branch-diameter-mm 12 --branch-length-cm 90",
        "bonsaiwire schedule add --tree maple --branch-label 'left primary' "
        "--branch-diameter-mm 12 --branch-length-cm 90 --wire-gauge-mm 3.2"
        "  -> wire gauge 3.2 mm is not a standard size (...)",
    ),
    "schedule list": (
        "bonsaiwire schedule list --tree MAPLE",
        "bonsaiwire schedule list --tree oak  -> unknown tree 'oak'; registered trees: Maple",
    ),
    "schedule remove": (
        "bonsaiwire schedule remove --tree Maple --id 3",
        "bonsaiwire schedule remove --tree Maple --id 99  -> no wiring entry 99 for 'Maple'",
    ),
    "schedule export": (
        "bonsaiwire schedule export --format csv > schedule.csv",
        "bonsaiwire schedule export --format xlsx  -> usage error, exit 2",
    ),
    "log add": (
        "bonsaiwire log add --tree Maple --height-cm 42 --trunk-diameter-mm 24 --note 'spring flush'",
        "bonsaiwire log add --tree Maple --height-cm 0"
        "  -> measurement below the supported range (min 1 cm height)",
    ),
    "log show": (
        "bonsaiwire log show --tree maple",
        "bonsaiwire log show --tree oak  -> unknown tree 'oak'; registered trees: Maple",
    ),
    "calc wire": (
        "bonsaiwire calc wire --branch-diameter-mm 12 --branch-length-cm 90",
        "bonsaiwire calc wire --branch-diameter-mm 24.1 --branch-length-cm 30"
        "  -> measurement exceeds the supported range (max 24 mm branch diameter -> 8.0 mm wire)",
    ),
    "profile setup": (
        "BONSAIWIRE_PASSPHRASE=cenius bonsaiwire profile setup --owner cenius",
        "bonsaiwire profile setup  -> --owner is required, usage error, exit 2",
    ),
    "profile status": ("bonsaiwire profile status", "bonsaiwire profile status --data-dir /root > store error, exit 1"),
}


def cmd_help(ctx: Context, args: argparse.Namespace) -> int:
    ctx.out(HELP_TEXT.rstrip("\n"))
    return 0


# --------------------------------------------------------------------------
# Argument parsing.
# --------------------------------------------------------------------------
class ArgumentParser(argparse.ArgumentParser):
    """Argparse that always exits 2 with one clean usage-error line."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{PROG}: usage error: {message}", file=sys.stderr)
        print(f"run '{PROG} help' for a command list with examples", file=sys.stderr)
        raise SystemExit(2)


def build_parser() -> ArgumentParser:
    common = ArgumentParser(add_help=False)
    common.add_argument("--data-dir", metavar="PATH", default=argparse.SUPPRESS,
                        help="store directory (default ./.bonsaiwire)")
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable colour output")

    parser = ArgumentParser(
        prog=PROG,
        description="BonsaiWire - branch wiring schedule, wire gauge calculator and growth log.",
        epilog="Run 'bonsaiwire help' for every group with an example per command. "
               "Exit codes: 0 success, 1 validation or user error, 2 usage error.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    subparsers = parser.add_subparsers(dest="group", metavar="<group>")

    # --- tree -----------------------------------------------------------
    tree_group = subparsers.add_parser("tree", help="tree registry", parents=[common],
                                       description="The canonical tree registry.",
                                       formatter_class=argparse.RawDescriptionHelpFormatter,
                                       epilog="Run 'bonsaiwire help' for examples.")
    tree_sub = tree_group.add_subparsers(dest="command", metavar="<command>")
    tree_add = tree_sub.add_parser("add", parents=[common], help="register a tree",
                                   formatter_class=argparse.RawDescriptionHelpFormatter,
                                   epilog="Examples:\n  " + "\n  ".join(EXAMPLES["tree add"]))
    tree_add.add_argument("--name", required=True, help="tree name (1-60 characters)")
    tree_add.add_argument("--species", help="species, free text")
    tree_add.add_argument("--trunk-diameter-mm", dest="trunk_diameter_mm", help="trunk diameter, 0.5-500 mm")
    tree_add.add_argument("--notes", help="free-text note shown by 'tree list'")
    tree_add.set_defaults(func=cmd_tree_add)
    tree_list = tree_sub.add_parser("list", parents=[common], help="list trees with entry counts",
                                    formatter_class=argparse.RawDescriptionHelpFormatter,
                                    epilog="Examples:\n  " + "\n  ".join(EXAMPLES["tree list"]))
    tree_list.set_defaults(func=cmd_tree_list)

    # --- schedule -------------------------------------------------------
    schedule_group = subparsers.add_parser("schedule", help="branch wiring schedule",
                                           parents=[common],
                                           description="Planned wire for each branch of a registered tree.",
                                           formatter_class=argparse.RawDescriptionHelpFormatter,
                                           epilog="Run 'bonsaiwire help' for examples.")
    schedule_sub = schedule_group.add_subparsers(dest="command", metavar="<command>")
    schedule_add = schedule_sub.add_parser("add", parents=[common], help="add a wiring entry",
                                           formatter_class=argparse.RawDescriptionHelpFormatter,
                                           epilog="Examples:\n  " + "\n  ".join(EXAMPLES["schedule add"]))
    schedule_add.add_argument("--tree", help="registered tree name (case-insensitive)")
    schedule_add.add_argument("--branch-label", dest="branch_label", help="branch label, 1-60 characters")
    schedule_add.add_argument("--branch-diameter-mm", dest="branch_diameter_mm", help="0.5-24.0 mm")
    schedule_add.add_argument("--branch-length-cm", dest="branch_length_cm", help="1-500 cm")
    schedule_add.add_argument("--wire-gauge-mm", dest="wire_gauge_mm",
                              help=f"optional standard size ({STANDARD_GAUGE_LIST})")
    schedule_add.add_argument("--notes", help="free-text note echoed by 'schedule list'")
    schedule_add.set_defaults(func=cmd_schedule_add)

    schedule_list = schedule_sub.add_parser("list", parents=[common], help="list wiring entries with subtotals",
                                            formatter_class=argparse.RawDescriptionHelpFormatter,
                                            epilog="Examples:\n  " + "\n  ".join(EXAMPLES["schedule list"]))
    schedule_list.add_argument("--tree", help="optional tree filter (case-insensitive)")
    schedule_list.set_defaults(func=cmd_schedule_list)

    schedule_remove = schedule_sub.add_parser("remove", parents=[common], help="remove one wiring entry",
                                              formatter_class=argparse.RawDescriptionHelpFormatter,
                                              epilog="Examples:\n  " + "\n  ".join(EXAMPLES["schedule remove"]))
    schedule_remove.add_argument("--tree", help="registered tree name")
    schedule_remove.add_argument("--id", help="entry id shown by 'schedule list'")
    schedule_remove.set_defaults(func=cmd_schedule_remove)

    schedule_export = schedule_sub.add_parser("export", parents=[common], help="export entries as CSV",
                                              formatter_class=argparse.RawDescriptionHelpFormatter,
                                              epilog="Examples:\n  " + "\n  ".join(EXAMPLES["schedule export"]))
    schedule_export.add_argument("--tree", help="optional tree filter (case-insensitive)")
    schedule_export.add_argument("--format", choices=["csv"], default="csv", help="output format (csv)")
    schedule_export.set_defaults(func=cmd_schedule_export)

    # --- log ------------------------------------------------------------
    log_group = subparsers.add_parser("log", help="tree growth progress log", parents=[common],
                                      description="Dated height and trunk measurements per tree.",
                                      formatter_class=argparse.RawDescriptionHelpFormatter,
                                      epilog="Run 'bonsaiwire help' for examples.")
    log_sub = log_group.add_subparsers(dest="command", metavar="<command>")
    log_add = log_sub.add_parser("add", parents=[common], help="record a growth measurement",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Examples:\n  " + "\n  ".join(EXAMPLES["log add"]))
    log_add.add_argument("--tree", help="registered tree name (case-insensitive)")
    log_add.add_argument("--height-cm", dest="height_cm", help="1-500 cm")
    log_add.add_argument("--trunk-diameter-mm", dest="trunk_diameter_mm", help="optional 0.5-500 mm")
    log_add.add_argument("--date", help="YYYY-MM-DD (defaults to today)")
    log_add.add_argument("--note", help="free-text note")
    log_add.set_defaults(func=cmd_log_add)

    log_show = log_sub.add_parser("show", parents=[common], help="show a tree's growth history",
                                  formatter_class=argparse.RawDescriptionHelpFormatter,
                                  epilog="Examples:\n  " + "\n  ".join(EXAMPLES["log show"]))
    log_show.add_argument("--tree", help="registered tree name (case-insensitive)")
    log_show.set_defaults(func=cmd_log_show)

    # --- calc -----------------------------------------------------------
    calc_group = subparsers.add_parser("calc", help="wire gauge and length calculator",
                                       parents=[common],
                                       description="Standalone wire calculator; no tree needed.",
                                       formatter_class=argparse.RawDescriptionHelpFormatter,
                                       epilog="Run 'bonsaiwire help' for examples.")
    calc_sub = calc_group.add_subparsers(dest="command", metavar="<command>")
    calc_wire = calc_sub.add_parser("wire", parents=[common], help="gauge and length for one branch",
                                    formatter_class=argparse.RawDescriptionHelpFormatter,
                                    epilog="Examples:\n  " + "\n  ".join(EXAMPLES["calc wire"]))
    calc_wire.add_argument("--branch-diameter-mm", dest="branch_diameter_mm", help="0.5-24.0 mm")
    calc_wire.add_argument("--branch-length-cm", dest="branch_length_cm", help="1-500 cm")
    calc_wire.add_argument("--wire-gauge-mm", dest="wire_gauge_mm",
                           help=f"optional standard size ({STANDARD_GAUGE_LIST})")
    calc_wire.set_defaults(func=cmd_calc_wire)

    # --- profile --------------------------------------------------------
    profile_group = subparsers.add_parser("profile", help="local grower profile",
                                          parents=[common],
                                          description="The local grower identity that unlocks write commands.",
                                          formatter_class=argparse.RawDescriptionHelpFormatter,
                                          epilog="Run 'bonsaiwire help' for examples.")
    profile_sub = profile_group.add_subparsers(dest="command", metavar="<command>")
    profile_setup = profile_sub.add_parser("setup", parents=[common], help="create or replace the profile",
                                           formatter_class=argparse.RawDescriptionHelpFormatter,
                                           epilog="Examples:\n  " + "\n  ".join(EXAMPLES["profile setup"]))
    profile_setup.add_argument("--owner", help="grower name")
    profile_setup.set_defaults(func=cmd_profile_setup)
    profile_status = profile_sub.add_parser("status", parents=[common], help="show the profile card",
                                            formatter_class=argparse.RawDescriptionHelpFormatter,
                                            epilog="Examples:\n  " + "\n  ".join(EXAMPLES["profile status"]))
    profile_status.set_defaults(func=cmd_profile_status)

    # --- help -----------------------------------------------------------
    help_parser = subparsers.add_parser("help", help="command list with examples",
                                        parents=[common],
                                        formatter_class=argparse.RawDescriptionHelpFormatter,
                                        description="Every command group with an example.")
    help_parser.set_defaults(func=cmd_help)

    # A group with no command (e.g. 'bonsaiwire tree') prints the reference.
    for group_parser in (tree_group, schedule_group, log_group, calc_group, profile_group):
        group_parser.set_defaults(func=cmd_help)
    return parser


def missing_subcommand(args: argparse.Namespace) -> bool:
    return not getattr(args, "command", None) and getattr(args, "group", None) is not None


# --------------------------------------------------------------------------
# Entry points.
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    print_reference = missing_subcommand(args)
    try:
        ctx = Context(args, ensure_store=not print_reference)
        return cmd_help(ctx, args) if print_reference else func(ctx, args)
    except UsageError as exc:
        print(f"{PROG}: usage error: {exc}", file=sys.stderr)
        return 2
    except (UserError, StoreError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 1
    except OSError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted", file=sys.stderr)
        return 1
    except Exception as exc:  # CLI boundary: never leave an uncaught traceback
        print(f"{PROG}: unexpected error ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    run()

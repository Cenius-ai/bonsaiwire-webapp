"""BonsaiWire test suite.

Every test drives the real command line (a subprocess of bonsaiwire.py) against
a throwaway data directory and a throwaway HOME, so nothing touches the
developer's own store.  Pure helpers (gauge mapping, bounds, wire length) are
also exercised directly.

Run:  python3 -m unittest discover -s tests -v     (or: python3 -m pytest)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bonsaiwire.py"
PASSPHRASE = "cenius"

sys.path.insert(0, str(ROOT))
import bonsaiwire  # noqa: E402


class CliTestCase(unittest.TestCase):
    """Shared harness: isolated data dir, isolated HOME, deterministic output."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.data_dir = base / "store"
        self.home = base / "home"
        self.home.mkdir()

    def run_cli(
        self,
        *args: str,
        passphrase: str | None = PASSPHRASE,
        data_dir: bool = True,
        expect: int | None = None,
        stdin: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(CLI)]
        if data_dir:
            command += ["--data-dir", str(self.data_dir)]
        command += list(args)
        env = dict(os.environ)
        env.pop("BONSAIWIRE_PASSPHRASE", None)
        env.pop("NO_COLOR", None)
        env["HOME"] = str(self.home)
        if passphrase is not None:
            env["BONSAIWIRE_PASSPHRASE"] = passphrase
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            timeout=60,
            stdin=subprocess.DEVNULL if stdin else None,
        )
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                f"exit {proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}",
            )
        return proc

    def write_config(self, data_dir: Path) -> None:
        (self.home /".bonsaiwirerc.json").write_text(
            json.dumps({"data_dir": str(data_dir)}), encoding="utf-8"
        )

    def setup_profile(self, owner: str = "cenius") -> None:
        self.run_cli("profile", "setup", "--owner", owner, expect=0)

    def store_file(self, name: str) -> Path:
        return self.data_dir / name

    def read_json(self, name: str):
        return json.loads(self.store_file(name).read_text(encoding="utf-8"))


class TestUsageAndDispatch(CliTestCase):
    def test_help_lists_every_command_group(self) -> None:
        proc = self.run_cli("--help", data_dir=False, expect=0)
        for group in ("tree", "schedule", "log", "calc", "profile"):
            self.assertIn(group, proc.stdout)

    def test_version(self) -> None:
        proc = self.run_cli("--version", data_dir=False, expect=0)
        self.assertIn(bonsaiwire.VERSION, proc.stdout)

    def test_unknown_command_is_a_usage_error(self) -> None:
        proc = self.run_cli("tree", "lst", expect=2)
        self.assertIn("invalid choice", proc.stderr)
        self.assertIn("lst", proc.stderr)

    def test_unknown_group_is_a_usage_error(self) -> None:
        proc = self.run_cli("branches", expect=2)
        self.assertIn("usage error", proc.stderr)

    def test_help_command_prints_examples_and_exit_codes(self) -> None:
        proc = self.run_cli("help", data_dir=False, expect=0)
        self.assertIn("calc wire", proc.stdout)
        self.assertIn("Exit codes", proc.stdout)

    def test_group_without_command_prints_the_reference(self) -> None:
        proc = self.run_cli("tree", data_dir=False, expect=0)
        self.assertIn("tree add", proc.stdout)

    def test_subcommand_help_shows_two_examples(self) -> None:
        proc = self.run_cli("calc", "wire", "--help", data_dir=False, expect=0)
        self.assertIn("Examples:", proc.stdout)
        self.assertIn("--branch-length-cm 90", proc.stdout)
        self.assertIn("24.1", proc.stdout)

    def test_non_numeric_measurement_is_a_usage_error(self) -> None:
        proc = self.run_cli("calc", "wire", "--branch-diameter-mm", "wide", "--branch-length-cm", "30", expect=2)
        self.assertIn("expects a number", proc.stderr)

    def test_missing_calculator_input_prints_the_usage_example(self) -> None:
        proc = self.run_cli("calc", "wire", "--branch-length-cm", "30", expect=2)
        self.assertIn("--branch-diameter-mm is required", proc.stderr)
        self.assertIn("calc wire --branch-diameter-mm 12 --branch-length-cm 90", proc.stderr)


class TestStoreAndEmptyStates(CliTestCase):
    def test_first_read_creates_the_store_files(self) -> None:
        proc = self.run_cli("tree", "list", expect=0)
        self.assertIn("No trees registered yet", proc.stdout)
        self.assertIn("bonsaiwire tree add --name <name> --trunk-diameter-mm <mm>", proc.stdout)
        for name in ("trees.json", "schedule.json", "log.json"):
            self.assertTrue(self.store_file(name).exists(), name)

    def test_writes_are_atomic_and_leave_no_temp_files(self) -> None:
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)
        leftovers = [p.name for p in self.data_dir.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])
        self.assertEqual(len(self.read_json("trees.json")), 1)

    def test_corrupt_store_is_reported_without_a_traceback(self) -> None:
        self.store_file("trees.json").parent.mkdir(parents=True, exist_ok=True)
        self.store_file("trees.json").write_text("{not json", encoding="utf-8")
        proc = self.run_cli("tree", "list", expect=1)
        self.assertIn("not valid JSON", proc.stderr)

    def test_config_file_supplies_the_default_data_dir(self) -> None:
        configured = Path(self._tmp.name) / "configured-store"
        self.write_config(configured)
        proc = self.run_cli("tree", "list", data_dir=False, expect=0)
        self.assertIn("No trees registered yet", proc.stdout)
        self.assertTrue((configured / "trees.json").exists())

    def test_missing_tree_reports_the_registered_spellings(self) -> None:
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)
        self.run_cli("tree", "add", "--name", "Elm", "--trunk-diameter-mm", "33", expect=0)
        proc = self.run_cli("schedule", "add", "--tree", "oak", "--branch-label", "leader",
                            "--branch-diameter-mm", "6", "--branch-length-cm", "40", expect=1)
        self.assertIn("unknown tree 'oak'; registered trees: Elm, Maple", proc.stderr)


class TestTreeRegistry(CliTestCase):
    def test_add_then_list_shows_measurements_counts_and_notes(self) -> None:
        self.setup_profile()
        proc = self.run_cli("tree", "add", "--name", "Maple", "--species", "Acer palmatum",
                            "--trunk-diameter-mm", "45", "--notes", "north bed", expect=0)
        self.assertIn("Registered tree 'Maple'", proc.stdout)
        listing = self.run_cli("tree", "list", expect=0)
        self.assertIn("Maple", listing.stdout)
        self.assertIn("Acer palmatum", listing.stdout)
        self.assertIn("45", listing.stdout)
        self.assertIn("north bed", listing.stdout)
        self.assertEqual(len(self.read_json("trees.json")), 1)

    def test_duplicate_tree_is_rejected_case_insensitively(self) -> None:
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)
        proc = self.run_cli("tree", "add", "--name", "MAPLE", "--trunk-diameter-mm", "30", expect=1)
        self.assertIn("tree 'MAPLE' already exists as 'Maple'", proc.stderr)
        self.assertEqual(len(self.read_json("trees.json")), 1)

    def test_trunk_diameter_bounds(self) -> None:
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Bonsai A", "--trunk-diameter-mm", "0.5", expect=0)
        self.run_cli("tree", "add", "--name", "Bonsai B", "--trunk-diameter-mm", "500", expect=0)
        over = self.run_cli("tree", "add", "--name", "Elm", "--trunk-diameter-mm", "600", expect=1)
        self.assertIn("measurement exceeds the supported range (max 500 mm trunk diameter)", over.stderr)
        under = self.run_cli("tree", "add", "--name", "Elm", "--trunk-diameter-mm", "0.4", expect=1)
        self.assertIn("measurement below the supported range (min 0.5 mm trunk diameter)", under.stderr)
        self.assertEqual(len(self.read_json("trees.json")), 2)

    def test_empty_state_on_a_fresh_store(self) -> None:
        proc = self.run_cli("tree", "list", expect=0)
        self.assertIn("No trees registered yet", proc.stdout)


class TestWireCalculator(CliTestCase):
    def test_computed_gauge_and_wire_length(self) -> None:
        proc = self.run_cli("calc", "wire", "--branch-diameter-mm", "12", "--branch-length-cm", "90", expect=0)
        self.assertIn("4.0 mm (computed)", proc.stdout)
        self.assertIn("104 cm", proc.stdout)
        self.assertIn("1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0", proc.stdout)

    def test_supplied_gauge_is_accepted_and_labelled(self) -> None:
        proc = self.run_cli("calc", "wire", "--branch-diameter-mm", "12",
                            "--branch-length-cm", "90", "--wire-gauge-mm", "5.0", expect=0)
        self.assertIn("5.0 mm (supplied)", proc.stdout)

    def test_non_standard_gauge_is_rejected(self) -> None:
        proc = self.run_cli("calc", "wire", "--branch-diameter-mm", "12",
                            "--branch-length-cm", "90", "--wire-gauge-mm", "3.2", expect=1)
        self.assertIn(
            "wire gauge 3.2 mm is not a standard size (choose from 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)",
            proc.stderr,
        )

    def test_diameter_upper_bound_and_just_over(self) -> None:
        ok = self.run_cli("calc", "wire", "--branch-diameter-mm", "24.0", "--branch-length-cm", "30", expect=0)
        self.assertIn("8.0 mm (computed)", ok.stdout)
        bad = self.run_cli("calc", "wire", "--branch-diameter-mm", "24.1", "--branch-length-cm", "30", expect=1)
        self.assertIn(
            "measurement exceeds the supported range (max 24 mm branch diameter -> 8.0 mm wire)",
            bad.stderr,
        )

    def test_branch_length_bounds(self) -> None:
        low = self.run_cli("calc", "wire", "--branch-diameter-mm", "12", "--branch-length-cm", "0.5", expect=1)
        self.assertIn("measurement below the supported range (min 1 cm branch length)", low.stderr)
        self.run_cli("calc", "wire", "--branch-diameter-mm", "12", "--branch-length-cm", "1", expect=0)
        self.run_cli("calc", "wire", "--branch-diameter-mm", "12", "--branch-length-cm", "500", expect=0)
        high = self.run_cli("calc", "wire", "--branch-diameter-mm", "12", "--branch-length-cm", "501", expect=1)
        self.assertIn("measurement exceeds the supported range (max 500 cm branch length)", high.stderr)


class TestSharedValidator(unittest.TestCase):
    """The one validator every command leans on, tested at its edges."""

    GAUGE_TABLE = (
        (0.5, 1.0), (1.5, 1.0), (3.0, 1.0), (4.5, 1.5), (6.0, 2.0),
        (7.5, 2.5), (9.0, 3.0), (12.0, 4.0), (15.0, 5.0), (18.0, 6.0),
        (21.0, 8.0), (24.0, 8.0),
    )

    def test_gauge_mapping_boundaries(self) -> None:
        for diameter, gauge in self.GAUGE_TABLE:
            self.assertEqual(bonsaiwire.compute_gauge(diameter), gauge, f"{diameter} mm")

    def test_wire_length_is_ceiling_of_115_percent(self) -> None:
        self.assertEqual(bonsaiwire.compute_wire_length_cm(90), 104)
        self.assertEqual(bonsaiwire.compute_wire_length_cm(40), 46)
        self.assertEqual(bonsaiwire.compute_wire_length_cm(100), 115)
        self.assertEqual(bonsaiwire.compute_wire_length_cm(20), 23)
        self.assertEqual(bonsaiwire.compute_wire_length_cm(1), 2)

    def test_measurement_bounds_accept_edges_reject_outside(self) -> None:
        self.assertEqual(bonsaiwire.validate_measurement("branch_diameter_mm", 0.5), 0.5)
        self.assertEqual(bonsaiwire.validate_measurement("branch_diameter_mm", 24.0), 24.0)
        self.assertEqual(bonsaiwire.validate_measurement("height_cm", 500.0), 500.0)
        for field, value in (("branch_diameter_mm", 24.1), ("branch_length_cm", 501.0),
                             ("height_cm", 501.0), ("trunk_diameter_mm", 600.0)):
            with self.assertRaises(bonsaiwire.UserError):
                bonsaiwire.validate_measurement(field, value)
        for field, value in (("branch_diameter_mm", 0.4), ("branch_length_cm", 0.9),
                             ("height_cm", 0.0), ("trunk_diameter_mm", 0.4)):
            with self.assertRaises(bonsaiwire.UserError):
                bonsaiwire.validate_measurement(field, value)

    def test_standard_gauge_membership(self) -> None:
        for gauge in bonsaiwire.STANDARD_WIRE_GAUGES_MM:
            self.assertTrue(bonsaiwire.is_standard_gauge(gauge))
        for gauge in (0.9, 1.2, 3.2, 7.0, 9.0):
            self.assertFalse(bonsaiwire.is_standard_gauge(gauge))

    def test_canonical_spelling_resolution_is_case_insensitive(self) -> None:
        trees = [{"name": "Maple"}, {"name": "Trunk Elm"}]
        self.assertEqual(bonsaiwire.resolve_tree(trees, "maple"), "Maple")
        self.assertEqual(bonsaiwire.resolve_tree(trees, "TRUNK ELM"), "Trunk Elm")
        with self.assertRaises(bonsaiwire.UserError) as caught:
            bonsaiwire.resolve_tree(trees, "oak")
        self.assertIn("unknown tree 'oak'; registered trees: Maple, Trunk Elm", str(caught.exception))

    def test_display_helpers_are_ascii_friendly(self) -> None:
        self.assertEqual(bonsaiwire.fmt_number(45.0), "45")
        self.assertEqual(bonsaiwire.fmt_number(0.5), "0.5")
        self.assertEqual(bonsaiwire.fmt_gauge(4.0), "4.0")


class TestWiringSchedule(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)

    def add_entry(self, *extra: str, tree: str = "maple") -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "schedule", "add", "--tree", tree, "--branch-label", extra[0],
            "--branch-diameter-mm", extra[1], "--branch-length-cm", extra[2],
            *extra[3:], expect=0,
        )

    def test_add_persists_the_canonical_spelling_and_computes_the_wire(self) -> None:
        proc = self.add_entry("left primary", "12", "90", "--notes", "front anchor")
        self.assertIn("Added wiring entry 1 for 'Maple'", proc.stdout)
        self.assertIn("wire gauge 4.0 mm (computed)", proc.stdout)
        self.assertIn("wire length 104 cm", proc.stdout)
        stored = self.read_json("schedule.json")
        self.assertEqual(stored[0]["tree"], "Maple")
        self.assertEqual(stored[0]["wire_gauge_mm"], 4.0)
        self.assertEqual(stored[0]["wire_length_cm"], 104)
        self.assertEqual(stored[0]["gauge_source"], "computed")
        self.assertEqual(stored[0]["notes"], "front anchor")

    def test_list_shows_entries_notes_and_per_tree_subtotals(self) -> None:
        self.add_entry("left primary", "12", "90", "--notes", "front anchor")
        self.add_entry("rear secondary", "6", "40")
        proc = self.run_cli("schedule", "list", "--tree", "MAPLE", expect=0)
        self.assertIn("left primary", proc.stdout)
        self.assertIn("front anchor", proc.stdout)
        self.assertIn("Maple: 2 entries, gauges 2.0, 4.0 mm, 150 cm wire total", proc.stdout)

    def test_supplied_gauge_is_flagged_in_the_listing(self) -> None:
        self.add_entry("apex leader", "3", "22", "--wire-gauge-mm", "1.0")
        proc = self.run_cli("schedule", "list", expect=0)
        self.assertIn("1.0*", proc.stdout)
        self.assertIn("gauge supplied by the grower", proc.stdout)

    def test_unknown_tree_is_rejected_and_nothing_is_written(self) -> None:
        proc = self.run_cli("schedule", "add", "--tree", "Oak", "--branch-label", "leader",
                            "--branch-diameter-mm", "6", "--branch-length-cm", "40", expect=1)
        self.assertIn("unknown tree 'Oak'; registered trees: Maple", proc.stderr)
        self.assertEqual(self.read_json("schedule.json"), [])

    def test_remove_names_the_removed_entry(self) -> None:
        self.add_entry("left primary", "12", "90")
        self.add_entry("rear secondary", "6", "40")
        proc = self.run_cli("schedule", "remove", "--tree", "Maple", "--id", "1", expect=0)
        self.assertIn("Removed wiring entry 1 for 'Maple'", proc.stdout)
        self.assertIn("left primary", proc.stdout)
        self.assertEqual([entry["id"] for entry in self.read_json("schedule.json")], [2])

    def test_remove_of_an_unknown_id_is_a_user_error(self) -> None:
        self.add_entry("left primary", "12", "90")
        proc = self.run_cli("schedule", "remove", "--tree", "Maple", "--id", "99", expect=1)
        self.assertIn("no wiring entry 99 for 'Maple'", proc.stderr)
        self.assertEqual(len(self.read_json("schedule.json")), 1)

    def test_list_empty_state_names_the_add_command(self) -> None:
        proc = self.run_cli("schedule", "list", expect=0)
        self.assertIn("No wiring entries for <name> yet", proc.stdout)
        self.assertIn("bonsaiwire schedule add --tree <name>", proc.stdout)

    def test_export_writes_csv_to_stdout(self) -> None:
        self.add_entry("left primary", "12", "90", "--notes", "front anchor")
        proc = self.run_cli("schedule", "export", "--format", "csv", expect=0)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(
            lines[0],
            "id,tree,branch_label,branch_diameter_mm,branch_length_cm,wire_gauge_mm,"
            "wire_length_cm,gauge_source,notes,created_at",
        )
        self.assertTrue(lines[1].startswith("1,Maple,left primary,12,90,4.0,104,computed,front anchor,"))


class TestGrowthLog(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)
        self.run_cli("tree", "add", "--name", "Elm", "--trunk-diameter-mm", "33", expect=0)

    def test_show_lists_oldest_first_with_the_growth_total(self) -> None:
        self.run_cli("log", "add", "--tree", "maple", "--height-cm", "30",
                     "--date", "2025-04-06", "--note", "first wiring", expect=0)
        proc = self.run_cli("log", "add", "--tree", "MAPLE", "--height-cm", "42",
                            "--trunk-diameter-mm", "24", "--date", "2025-09-14", expect=0)
        self.assertIn("Logged growth for 'Maple' on 2025-09-14", proc.stdout)
        shown = self.run_cli("log", "show", "--tree", "maple", expect=0)
        self.assertLess(shown.stdout.index("2025-04-06"), shown.stdout.index("2025-09-14"))
        self.assertIn("total growth +12 cm over 2 entries", shown.stdout)
        stored = self.read_json("log.json")
        self.assertEqual([entry["tree"] for entry in stored], ["Maple", "Maple"])

    def test_empty_state_for_a_tree_without_entries(self) -> None:
        proc = self.run_cli("log", "show", "--tree", "Elm", expect=0)
        self.assertIn("No growth entries for 'Elm' yet", proc.stdout)
        self.assertIn("bonsaiwire log add --tree Elm --height-cm <cm>", proc.stdout)

    def test_height_below_the_range_is_rejected(self) -> None:
        proc = self.run_cli("log", "add", "--tree", "maple", "--height-cm", "0", expect=1)
        self.assertIn("measurement below the supported range (min 1 cm height)", proc.stderr)
        self.assertEqual(self.read_json("log.json"), [])

    def test_height_above_the_range_is_rejected(self) -> None:
        proc = self.run_cli("log", "add", "--tree", "maple", "--height-cm", "501", expect=1)
        self.assertIn("measurement exceeds the supported range (max 500 cm height)", proc.stderr)

    def test_bad_date_is_a_usage_error(self) -> None:
        proc = self.run_cli("log", "add", "--tree", "maple", "--height-cm", "30",
                            "--date", "06/04/2025", expect=2)
        self.assertIn("--date expects YYYY-MM-DD", proc.stderr)

    def test_unknown_tree_is_rejected(self) -> None:
        proc = self.run_cli("log", "show", "--tree", "oak", expect=1)
        self.assertIn("unknown tree 'oak'", proc.stderr)


class TestProfileGate(CliTestCase):
    def test_write_command_without_a_profile_is_locked(self) -> None:
        proc = self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=1)
        self.assertIn("no grower profile set up; run: bonsaiwire profile setup", proc.stderr)
        self.assertEqual(self.read_json("trees.json"), [])

    def test_status_before_setup(self) -> None:
        proc = self.run_cli("profile", "status", expect=0)
        self.assertIn("No grower profile set up; write commands are locked", proc.stdout)
        self.assertIn("bonsaiwire profile setup --owner <name>", proc.stdout)

    def test_setup_stores_only_a_salted_hash(self) -> None:
        self.setup_profile()
        profile_text = self.store_file("profile.json").read_text(encoding="utf-8")
        profile = json.loads(profile_text)
        self.assertEqual(profile["owner_name"], "cenius")
        self.assertEqual(profile["algorithm"], "PBKDF2-HMAC-SHA256")
        # the passphrase itself is stored nowhere: only a salt-backed PBKDF2 digest
        self.assertNotEqual(profile["passphrase_hash"], PASSPHRASE)
        for key, value in profile.items():
            if key != "owner_name":
                self.assertNotEqual(str(value), PASSPHRASE, key)
        self.assertEqual(
            profile["passphrase_hash"],
            bonsaiwire.derive_hash(
                PASSPHRASE, profile["passphrase_salt"], profile["iterations"]
            ),
        )
        self.assertEqual(profile["iterations"], bonsaiwire.KDF_ITERATIONS)
        status = self.run_cli("profile", "status", expect=0)
        self.assertIn("cenius", status.stdout)
        self.assertIn("salted PBKDF2-HMAC-SHA256 hash", status.stdout)
        self.assertIn(str(self.data_dir), status.stdout)

    def test_wrong_passphrase_leaves_the_store_untouched(self) -> None:
        self.setup_profile()
        proc = self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45",
                            passphrase="not-the-passphrase", expect=1)
        self.assertIn("passphrase did not match", proc.stderr)
        self.assertEqual(self.read_json("trees.json"), [])

    def test_matching_passphrase_unlocks_the_write(self) -> None:
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)
        self.assertEqual(len(self.read_json("trees.json")), 1)

    def test_read_only_commands_need_no_passphrase(self) -> None:
        self.setup_profile()
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45", expect=0)
        for args in (("tree", "list"), ("schedule", "list"), ("log", "show", "--tree", "Maple"),
                     ("calc", "wire", "--branch-diameter-mm", "12", "--branch-length-cm", "90"),
                     ("profile", "status")):
            self.run_cli(*args, passphrase=None, expect=0)

    def test_no_passphrase_anywhere_fails_closed_without_hanging(self) -> None:
        self.setup_profile()
        proc = self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "45",
                            passphrase=None, expect=1)
        self.assertIn("no passphrase available", proc.stderr)
        self.assertEqual(self.read_json("trees.json"), [])


class TestRenderedWidth(CliTestCase):
    def test_long_names_and_notes_stay_inside_eighty_columns(self) -> None:
        self.setup_profile()
        long_name = "Japanese" + " " + "Pine" * 1 + " " + "Yatsubusa" * 4  # 1-60 chars
        long_note = "keep the wire off the bark " * 4
        self.run_cli("tree", "add", "--name", long_name, "--species", "Pinus thunbergii var. corticosa",
                     "--trunk-diameter-mm", "45", "--notes", long_note, expect=0)
        self.run_cli("tree", "add", "--name", "Maple", "--trunk-diameter-mm", "12", expect=0)
        self.run_cli("schedule", "add", "--tree", "Maple",
                     "--branch-label", "left primary that runs along the whole trunk",
                     "--branch-diameter-mm", "12", "--branch-length-cm", "90",
                     "--notes", long_note, expect=0)
        for args in (("tree", "list"), ("schedule", "list"), ("log", "show", "--tree", "Maple")):
            proc = self.run_cli(*args, expect=0)
            for line in proc.stdout.splitlines():
                self.assertTrue(line.isascii(), f"non-ascii line: {line!r}")
                self.assertLessEqual(len(line), 80, f"over 80 columns: {line!r}")
        listing = self.run_cli("tree", "list", expect=0).stdout
        self.assertIn(long_name, listing)  # clipped cells are repeated in full below the table


class TestSeedDemo(unittest.TestCase):
    """The shipped seed script must fill every screen and be re-runnable."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name) / "store"

    def seed(self) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("BONSAIWIRE_PASSPHRASE", None)
        env["NO_COLOR"] = "1"
        return subprocess.run(
            [sys.executable, str(ROOT / "seed_demo.py"), "--data-dir", str(self.data_dir)],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
            stdin=subprocess.DEVNULL,
        )

    def test_seed_is_idempotent_and_populates_every_screen(self) -> None:
        first = self.seed()
        self.assertEqual(first.returncode, 0, first.stderr)
        counts_after_first = json.loads((self.data_dir / "trees.json").read_text(encoding="utf-8"))
        second = self.seed()
        self.assertEqual(second.returncode, 0, second.stderr)
        counts_after_second = json.loads((self.data_dir / "trees.json").read_text(encoding="utf-8"))
        self.assertEqual(len(counts_after_first), len(counts_after_second))
        self.assertGreaterEqual(len(counts_after_first), 3)
        self.assertTrue((self.data_dir / "profile.json").exists())

        def cli(*args: str) -> str:
            proc = subprocess.run(
                [sys.executable, str(CLI), "--data-dir", str(self.data_dir), *args],
                capture_output=True, text=True, cwd=str(ROOT), timeout=60,
                stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1"},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return proc.stdout

        listing = cli("tree", "list")
        self.assertNotIn("No trees registered yet", listing)
        self.assertIn("Maple", listing)
        self.assertIn("Trident Maple", listing)  # the two-word name survives rendering
        schedule = cli("schedule", "list")
        self.assertIn("wire total", schedule)
        growth = cli("log", "show", "--tree", "Maple")
        self.assertIn("total growth", growth)
        self.assertIn("Totals:", growth)
        self.assertNotIn("No growth entries", growth)


if __name__ == "__main__":
    unittest.main()

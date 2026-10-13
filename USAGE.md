# Using BonsaiWire

Every example below uses a scratch store so you can paste them in order without
touching your own data:

```bash
export DATA=./.bonsaiwire-usage        # any directory
export BONSAIWIRE_PASSPHRASE=cenius    # passphrase for the write commands
```

## 1. Set up the local grower profile

Write commands (`tree add`, `schedule add`, `schedule remove`, `log add`) are
locked behind one local profile. The passphrase is never stored - only a random
salt and a PBKDF2-HMAC-SHA256 digest.

```bash
python3 bonsaiwire.py --data-dir "$DATA" profile setup --owner cenius
python3 bonsaiwire.py --data-dir "$DATA" profile status
```

```
Grower profile
owner           : cenius
passphrase      : set (salted PBKDF2-HMAC-SHA256 hash, 200000 iterations)
created         : 2026-04-18T09:12:44Z
data directory  : ./.bonsaiwire-usage
write commands read BONSAIWIRE_PASSPHRASE or prompt for the passphrase.
```

With no profile yet, every write command prints

```
$ python3 bonsaiwire.py tree add --name Maple --trunk-diameter-mm 45
bonsaiwire: no grower profile set up; run: bonsaiwire profile setup
```

and exits `1` leaving the store untouched. A wrong passphrase prints
`passphrase did not match` and exits `1` the same way.

## 2. Register trees

```bash
python3 bonsaiwire.py --data-dir "$DATA" tree add \
  --name Maple --species 'Acer palmatum' --trunk-diameter-mm 45 --notes 'north bed'
python3 bonsaiwire.py --data-dir "$DATA" tree add \
  --name Juniper --species 'Juniperus procumbens' --trunk-diameter-mm 22
python3 bonsaiwire.py --data-dir "$DATA" tree list
```

```
Trees in ./.bonsaiwire-usage
NAME     SPECIES               TRUNK MM  WIRES  GROWTH  NOTES
------------------------------------------------------------------
Juniper  Juniperus procumbens  22        0      0       -
Maple    Acer palmatum         45        0      0       north bed
2 tree(s) registered.
```

Names are unique **case-insensitively** and the registered spelling is what gets
stored everywhere else:

```
$ python3 bonsaiwire.py tree add --name MAPLE --trunk-diameter-mm 30
bonsaiwire: tree 'MAPLE' already exists as 'Maple'
```

Trunk diameter is accepted only from 0.5 to 500 mm:

```
$ python3 bonsaiwire.py tree add --name Elm --trunk-diameter-mm 600
bonsaiwire: measurement exceeds the supported range (max 500 mm trunk diameter)
```

## 3. Calculate wire before you cut it

No tree has to exist for this. The gauge is the smallest D3 standard size that
is at least a third of the branch diameter; the length is the branch length plus
10% for the run past the anchor and 5% for the tie-off/service loop, rounded up.

```bash
python3 bonsaiwire.py --data-dir "$DATA" calc wire --branch-diameter-mm 12 --branch-length-cm 90
python3 bonsaiwire.py --data-dir "$DATA" calc wire --branch-diameter-mm 4.5 --branch-length-cm 55
python3 bonsaiwire.py --data-dir "$DATA" calc wire --branch-diameter-mm 12 --branch-length-cm 90 --wire-gauge-mm 5.0
```

```
branch diameter : 12 mm
branch length   : 90 cm
wire gauge      : 4.0 mm (computed)
wire length     : 104 cm
standard sizes  : 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0 mm
```

A gauge you supply by hand is accepted only if it is one of the nine standard
sizes, and the output says the gauge was *supplied* rather than computed:

```
$ python3 bonsaiwire.py calc wire --branch-diameter-mm 12 --branch-length-cm 90 --wire-gauge-mm 3.2
bonsaiwire: wire gauge 3.2 mm is not a standard size (choose from 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)
```

Branch diameter is capped at 24 mm - exactly the point where the computed gauge
reaches the largest standard size - so the calculator always has an answer:

```
$ python3 bonsaiwire.py calc wire --branch-diameter-mm 24.1 --branch-length-cm 30
bonsaiwire: measurement exceeds the supported range (max 24 mm branch diameter -> 8.0 mm wire)
```

## 4. Keep the wiring schedule

```bash
python3 bonsaiwire.py --data-dir "$DATA" schedule add --tree maple \
  --branch-label 'left primary' --branch-diameter-mm 12 --branch-length-cm 90 \
  --notes 'anchor at the first fork'
python3 bonsaiwire.py --data-dir "$DATA" schedule add --tree Maple \
  --branch-label 'rear secondary' --branch-diameter-mm 6 --branch-length-cm 40
python3 bonsaiwire.py --data-dir "$DATA" schedule add --tree Maple \
  --branch-label 'apex leader' --branch-diameter-mm 3 --branch-length-cm 22 --wire-gauge-mm 1.0
python3 bonsaiwire.py --data-dir "$DATA" schedule list --tree MAPLE
```

```
Wiring schedule - Maple
ID  BRANCH          DIA MM  LEN CM  GAUGE MM  WIRE CM  NOTES
---------------------------------------------------------------------
1   left primary    12      90      4.0       104      anchor at the fi...
2   rear secondary  6       40      2.0       46       -
3   apex leader     3       22      1.0*      26       -
Maple: 3 entries, gauges 1.0, 2.0, 4.0 mm, 176 cm wire total
* gauge supplied by the grower; unmarked gauges are computed by the D3 rule.
```

* `--tree` is case-insensitive and the **registered spelling** (`Maple`) is what
  gets persisted, so the entry always counts against the right tree.
* An unknown tree is refused before anything is written, and the message lists
  what is registered: `unknown tree 'oak'; registered trees: Maple, Juniper`.
* Entries are listed by tree and then by id; each tree gets a subtotal of entry
  count, gauges in use and total wire length.
* Drop a branch you no longer plan to wire:

```bash
python3 bonsaiwire.py --data-dir "$DATA" schedule remove --tree Maple --id 2
# Removed wiring entry 2 for 'Maple': branch 'rear secondary', 2.0 mm wire, 46 cm.
```

* Take the schedule to a spreadsheet:

```bash
python3 bonsaiwire.py --data-dir "$DATA" schedule export --format csv > schedule.csv
```

The empty state names the exact command to run:

```
No wiring entries for <name> yet.
  Add one with: bonsaiwire schedule add --tree <name> --branch-label <label>
  --branch-diameter-mm <mm> --branch-length-cm <cm>
```

## 5. Log growth over the years

```bash
python3 bonsaiwire.py --data-dir "$DATA" log add --tree maple --height-cm 30 \
  --trunk-diameter-mm 19 --date 2025-04-06 --note 'first wiring, trunk still flexible'
python3 bonsaiwire.py --data-dir "$DATA" log add --tree Maple --height-cm 36 \
  --trunk-diameter-mm 22 --date 2025-09-14 --note 'wire removed, no bite marks'
python3 bonsaiwire.py --data-dir "$DATA" log add --tree MAPLE --height-cm 42 \
  --trunk-diameter-mm 26 --note 'strong spring flush'
python3 bonsaiwire.py --data-dir "$DATA" log show --tree maple
```

```
Growth log - Maple
DATE        HEIGHT CM  TRUNK MM  NOTE
------------------------------------------------------------------------
2025-04-06  30         19        first wiring, trunk still flexible
2025-09-14  36         22        wire removed, no bite marks
2026-04-18  42         26        strong spring flush
Totals: first 30 cm, latest 42 cm, total growth +12 cm over 3 entries.
```

`--date` defaults to today (and is validated as `YYYY-MM-DD`), `--height-cm`
takes 1-500 and the optional `--trunk-diameter-mm` 0.5-500. Entries print
oldest-first, followed by the first height, latest height, growth delta and the
entry count. A tree with no entries gets its own empty state:

```
$ python3 bonsaiwire.py --data-dir "$DATA" log show --tree Elm
No growth entries for 'Elm' yet.
  Add one with: bonsaiwire log add --tree Elm --height-cm <cm>
```

## 6. Moving the store, scripting, colour

```bash
python3 bonsaiwire.py --data-dir /media/usb/bonsai log show --tree Maple
echo '{"data_dir": "/media/usb/bonsai"}' > ~/.bonsaiwirerc.json   # make it the default
python3 bonsaiwire.py --no-color tree list > listing.txt           # plain text output
NO_COLOR=1 python3 bonsaiwire.py tree list                         # same thing
python3 bonsaiwire.py --version
```

`--data-dir` beats `~/.bonsaiwirerc.json`, which beats `./.bonsaiwire`. Results
go to stdout, every error goes to stderr, and the exit codes are stable: `0`
success, `1` validation or user error, `2` usage error. Long data is never lost
to the 78-column frame - a clipped table cell is repeated in full on an indented
line underneath the table.

## 7. The whole tour in one command

```bash
bash demo.sh
```

`demo.sh` walks a fresh store through profile setup, the tree registry, the
calculator (including every rejection), the schedule (add / list / remove /
export), the growth log and `help`, then reports success. It is non-interactive,
offline and exits `0`.

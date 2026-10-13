# Installing BonsaiWire

BonsaiWire is a plain Python command line tool. It has **no third-party
dependencies** and needs **no build step, no server, no database and no network**.

## 1. Prerequisites (install these yourself - no script does it for you)

| Requirement | Version | Why |
|-------------|---------|-----|
| Python      | 3.9+ (3.11 recommended) | the tool is written against the standard library only |
| pip         | any recent (optional)   | only used to install the optional `bonsaiwire` console script |
| Terminal    | POSIX-ish (`bash`)      | `install.sh` and `demo.sh` are bash scripts |

Nothing else is required: no compiler, no `apt`/`brew` package, no
`libsqlite3-dev`, no Node, no Docker, no external service, no API key.
BonsaiWire never reads the network - it works on a machine with Wi-Fi off.

## 2. Install the project dependencies (step 1)

```bash
bash install.sh
```

What it does, in order:

1. `python3 -m pip install --upgrade pip setuptools wheel` (skipped gracefully if
   the machine is offline - the tool does not need it),
2. `python3 -m pip install -r requirements.txt` (that file pins nothing: the
   product is standard-library only),
3. `python3 -m pip install --no-build-isolation -e .` to put the optional
   `bonsaiwire` console script on `PATH` (best effort; if it cannot be installed
   the tool still runs as `python3 bonsaiwire.py`),
4. a self-check: `python3 -c "import bonsaiwire"` and `python3 bonsaiwire.py --version`.

Equivalent manual steps:

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install --no-build-isolation -e .      # optional convenience
python3 -c "import bonsaiwire; print(bonsaiwire.VERSION)"
```

## 3. Seed the demo store (step 2, its own step)

```bash
BONSAIWIRE_PASSPHRASE=cenius python3 seed_demo.py --data-dir ./.bonsaiwire
```

This writes four trees, six wiring entries and eleven growth measurements (dated
relative to today, so the log always looks current) through BonsaiWire's own
commands, so the demo profile passphrase is hashed by the real PBKDF2 code path.
It is **idempotent**: run it again and existing records are skipped. It never
starts a server and never blocks on a prompt.

`install.sh` already performs this step; run it manually only if you installed
the tool by hand or want to re-seed.

## 4. Run it (step 3)

```bash
python3 bonsaiwire.py help                  # the command reference
python3 bonsaiwire.py tree list             # read-only: works with no passphrase
python3 bonsaiwire.py profile status
```

There is nothing to start in the background: every command is a short-lived
process that reads the JSON store it needs, prints its result and exits.

Demo grower: owner `cenius`, passphrase `cenius` (`cenius@cenius.ai` / `cenius`).
Set `BONSAIWIRE_PASSPHRASE=cenius` for non-interactive use, or run
`python3 bonsaiwire.py profile setup --owner <you>` to create your own profile.

## 5. Verify the installation

```bash
python3 -m unittest discover -s tests -v     # 52 tests, standard library runner
python3 -m pytest                            # the same suite, if pytest is present
bash demo.sh                                 # full end-to-end terminal transcript
```

The suite uses only throwaway data directories (`tempfile`) and a throwaway
`HOME`, so it never touches your real store. It needs no network and no fixtures
outside the repository.

## Uninstall

```bash
python3 -m pip uninstall bonsaiwire     # only if you installed the console script
rm -rf ./.bonsaiwire                    # your data (or keep it - it is just JSON)
rm -rf ./.bonsaiwire-demo               # left behind by demo.sh
```

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `no grower profile set up; run: bonsaiwire profile setup` | Write commands are locked until a profile exists. Run `profile setup --owner <name>`, or `bash install.sh` to get the seeded demo profile. |
| `passphrase did not match` | The passphrase differs from the one stored in `profile.json`. Use the right one, or re-run `profile setup` to replace the profile. |
| `no passphrase available; set BONSAIWIRE_PASSPHRASE or run in a terminal` | The command ran without a TTY (script, CI, piped input). Export `BONSAIWIRE_PASSPHRASE` for non-interactive use. |
| `cannot read .../trees.json: not valid JSON` | The file was hand-edited into invalid JSON. Fix it, or delete it to start from an empty list. |
| The wrong store is being used | `--data-dir PATH` wins over `~/.bonsaiwirerc.json`, which wins over `./.bonsaiwire`. Check `python3 bonsaiwire.py profile status` for the directory in use. |

# epm.exe: the local app in one file

`epm.exe` is the local app (the engine, `fm`, and `fm serve` with its web UI) in a single
Windows program. Nothing else is needed on the machine: no Python, no Node, no network.
Send someone the file and a model, and the model opens and calculates the same way it does
for you.

## Using it

- **Double-click it next to a model.** Put `epm.exe` in the same folder as a `model.json`
  (or a single `.epm` file) and double-click it. The model opens in your browser.
- **Drop a model onto it.** Drag a `model.json`, a model folder or a `.epm` file onto
  `epm.exe`.
- **Choose one.** With no model next to it, it shows an Open dialog.

A window stays open while the model is served and shows its address. Every change is saved
to the model as you make it; close the window to stop. Opening a model that is already open
just brings it back up in the browser.

From a terminal:

```text
epm.exe path\to\model.json            open a model (also a folder or a .epm file)
epm.exe --no-browser --port 9000      serve without opening the browser, on port 9000
epm.exe fm -m model.json validate     any fm command: validate, run, table show, sweep, ...
epm.exe --self-test                   check that epm works on this machine
epm.exe --version                     the source commit and library versions it was built from
```

## Sharing a model

A model is its `model.json` plus, when it has them, its `inputs/` folder (CSV and Excel
files it reads) and `functions.py` (custom functions). To send all of it as one file, pack
it into an `.epm` archive:

```text
epm.exe fm pack path\to\model-folder deal.epm
```

The recipient puts `deal.epm` next to `epm.exe` and double-clicks, as with a `model.json`.

Keep the name `model.json`: a browser that saves a second download as `model (1).json` breaks
the link to its `inputs/` and `functions.py`, and epm says so rather than guess.

`functions.py` runs as code on the machine that opens the model. It runs in a separate
process with a restricted set of imports, but that is not a security boundary: only open
models whose `functions.py` you trust.

## When Windows warns

The program is not code-signed, so the first time a downloaded copy runs, Windows
SmartScreen may say it "protected your PC". Check the checksum (below), then choose
**More info → Run anyway**. Some antivirus products are wary of single-file Python programs
in general; the checksum tells you the file is the one that was built.

Each build publishes `epm.exe.sha256` beside the program. To check a copy:

```text
certutil -hashfile epm.exe SHA256
```

## Reproducibility

- **Same numbers everywhere.** The program carries its own Python and exact library
  versions, and runs in UTF-8 mode, so a model calculates the same on every machine and
  Windows locale. `epm.exe --version` names the source commit and the versions inside.
- **Checked before it is published.** CI runs `--self-test`, compares every value of the
  example model calculated by the program with the same calculation by the source it was
  built from, and opens a model the way a double-click does
  ([`check_exe.py`](check_exe.py)).
- **Rebuildable.** [`requirements.txt`](requirements.txt) pins every package with its hash;
  the web UI is built with `npm ci` from `frontend-local/package-lock.json`; the commit is
  recorded in the program.

One difference from `pip install epm`: the program leaves out pyarrow, which halves its
size. pyarrow only stores calculated series in a model's `cache/` folder; without it they are
recalculated when a model opens. Results are the same.

## Building it

CI builds it on every push to this branch, and for tags `v*` attaches it to a GitHub
release ([`windows-exe.yml`](../../.github/workflows/windows-exe.yml)): download it from the
run's **Artifacts** (`epm-windows-x64`) or from the release.

To build it yourself on Windows, from the repository root, with Python 3.13 and Node 22:

```text
cd frontend-local && npm ci && npm run build && cd ..
python -m pip install --require-hashes --no-deps -r packaging/windows/requirements.txt
python -m pip install --no-deps --no-build-isolation .
pyinstaller --clean --noconfirm packaging/windows/epm.spec
python packaging/windows/check_exe.py dist/epm.exe
```

The recipe also builds a single-file `dist/epm` on Linux and macOS, which is how it is tried
out away from Windows: install the versions in `requirements.txt` without
`--require-hashes`, since the hashes are those of the Windows packages. Build the Windows
program on Windows; PyInstaller does not cross-compile.

After changing dependencies, lock them again (the command is at the top of
[`requirements.in`](requirements.in)).

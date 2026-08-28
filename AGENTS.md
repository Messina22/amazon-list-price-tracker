# AGENTS.md

## Cursor Cloud specific instructions

This is a pure-Python 3.12 project (Amazon list price tracker): a CLI package in
`price_tracker/` plus a static dashboard in `dashboard/`. There is no database or
long-running backend service; GitHub Actions is the scheduler and the git repo is
the data store. Standard commands are documented in `README.md`.

Environment notes:

- Dependencies are installed with `python3 -m pip install --user
  --break-system-packages -r requirements.txt pytest` (this is the startup
  update script). The base image ships system `pip` but no `ensurepip`/`venv`,
  so a `--user` install is used instead of a virtualenv. Run everything with the
  system interpreter, e.g. `python3 -m pytest` and
  `python3 -m price_tracker <command>`. The `pytest`/`py.test` console scripts
  land in `~/.local/bin` (not on PATH by default); invoke via `python3 -m
  pytest` to avoid PATH issues.
- Tests: `python3 -m pytest` (config in `pyproject.toml`; `pythonpath` is set to
  the repo root so the package imports without installation).
- Lint: there is no linter configured in this repo (CI in
  `.github/workflows/tests.yml` runs only pytest).

Running the app:

- Dashboard (the main UI): `python3 -m price_tracker dashboard --no-open`
  serves at http://127.0.0.1:8000/dashboard/. Use `--no-open` in headless
  environments so it does not try to launch a browser. It regenerates
  `data/dashboard.json` from `data/price_history.csv` on start, then serves the
  repo root so both `/dashboard/` and `/data/` resolve.
- `python -m price_tracker run` scrapes a live public Amazon list over the
  network. Amazon frequently serves CAPTCHAs to automated clients, so this
  command may record nothing in a cloud/CI environment — this is expected and
  not a setup failure. The `history`, `report`, and `dashboard` commands work
  offline against the committed data in `data/`.

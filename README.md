# ESP Design Automation

Prototype, synthetic demonstration data only, not for field use.

This repository contains the working Python engine and API, the Vue interface used in the demo, the test suite, and all three synthetic well scenarios. You can run it on your own computer without an API key, a paid AI account, or access to Perplexity's runtime.

## Before you start

Install Python **3.14**, Node.js **22 LTS** (22.12 or newer), and Git if they are not already installed. Use the official installers for your operating system. On Windows, select Python's **Add python.exe to PATH** option during installation, then open a new terminal.

You need access to this private GitHub repository. Clone it with GitHub Desktop, or use the following command in Terminal (macOS/Linux) or PowerShell (Windows):

```sh
git clone https://github.com/mjf1288/esp_design_automation.git
cd esp_design_automation
```

If GitHub asks you to sign in, use your own GitHub account. Do not paste a GitHub token into a project file. Keep this terminal open in the `esp_design_automation` folder.

## Start the backend

You only need to create the environment and install dependencies the first time, or when the dependency file changes.

### macOS or Linux

Copy these commands into your first terminal:

```sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run_backend.py
```

### Windows PowerShell

Copy these commands into your first terminal. These use the environment's Python directly, so there is no PowerShell activation-policy step.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_backend.py
```

Leave this terminal running. Wait for `Application startup complete`, then open <http://127.0.0.1:8000/docs> to check that the API is available.

The launcher binds the API to your own computer only and defaults external AI generation to **off**, matching the demo preview. You do not need to create an `.env` file. If you have previously set `ESP_AGENTIC_LAYER=on` in your shell, unset it or set it to `off` before launching.

## Start the interface

Open a **second terminal** in the same `esp_design_automation` folder. Run:

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open <http://127.0.0.1:5173>. Leave both terminals running while you use the app. The interface already points to `http://localhost:8000` when run locally; no URL replacement or frontend configuration is needed.

`npm ci` is needed on the first run and after `package-lock.json` changes. To stop either server, press **Ctrl+C** in its terminal.

## Open a synthetic well and calculate a design

No manual catalog import is required. Starting the backend creates a local SQLite database and seeds the demo cases without overwriting cases that already exist.

At the top of the interface, use **Demonstration case**:

| Select this case | What it demonstrates |
|---|---|
| SYN-A · Replacement with history | A replacement case with fictional operating history |
| SYN-B · New well, uncertain trajectory | A new well with less certain input and trajectory scenarios |
| SYN-C · Gassy well, section screening | Gas corrections, warnings and a preliminary two-section configuration |

The replacement case opens by default. A fresh installation has no saved calculations: click **Run design engine** to calculate the selected case, then wait for the queued/running status to finish. Do not expect the precomputed screenshots or another computer's database to be present.

Use **Engineering decisions** to change ranking mode, gas coefficients, cable choice, section settings and bend thresholds. The controls that recalculate a design save those changes locally. Use **Results** and **Timeline** to inspect the output.

The three cases automatically use the synthetic catalog defined in `backend/esp_engine/synthetic.py`. Its API is <http://127.0.0.1:8000/api/catalog/synthetic>. The older estimated-data demo remains available separately for historical comparison; it is also not manufacturer-verified data.

**Selection is not approval.** A candidate may stay in the ranking while carrying head, thermal or mechanical warnings. Time inside the pump's operating zone is not a predicted field life and does not establish sufficient head.

## Start again tomorrow

Open a terminal in the repository folder:

```sh
# macOS/Linux
source .venv/bin/activate
python run_backend.py
```

On Windows, use `.\.venv\Scripts\python.exe run_backend.py` instead. In the second terminal, run `cd frontend` and the same `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort` command.

Your local inputs, saved runs and project preferences persist under `data/perimeters/`. They are ignored by Git and are not uploaded. Restarting the app does not reset your experiments.

## Run the tests

From the repository root with the backend environment activated:

```sh
python -m pytest -q
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The tests use temporary databases and fake AI responses; they require no model credentials. The engine suite takes several minutes. A warning about the model named `TestPoint` being skipped during test collection is known and is not a failed test.

To verify the frontend production build as well:

```sh
cd frontend
npm run build
```

Build before running the Python suite if you also want its built-frontend network checks. Without a build, bundle-only checks may be skipped. `dist/` is generated, not committed; the same source and lockfile produce the local frontend.

## Credentials and optional AI

**You must supply your own credentials for any optional AI integration. None are included or needed for the default app.** Never commit real `.env` files, tokens, private keys, credentials, local databases or logs, even to a private repository. These are excluded by `.gitignore`; that protection does not remove a file already tracked by Git.

The narrative probe in `backend/scripts/probe_narrative.py` is an optional platform-specific diagnostic, not a local setup step. It refers to `PPLX_LLM_API_ADDRESS` and `PPLX_LLM_API_KEY` environment-variable names only. Its original platform SDK is not distributed with this repository, and supplying a normal public Perplexity API key does not make that adapter portable. Leave it disabled unless you separately have the required SDK/access or deliberately implement a provider adapter with your own credentials.

The default app runs the deterministic calculations, API, reports, charts and engineering controls without that adapter. Free-text AI intake and generated AI narrative are unavailable in this default mode, just as in the current preview. Installing Python/npm dependencies requires internet access; local calculations do not require a hosted model.

## Get updates without losing your experiments

Before changing source code, make your own branch:

```sh
git switch -c my-experiments
```

To download new commits without changing any local files:

```sh
git fetch origin
```

If you have no uncommitted edits and your `main` branch has no personal commits, update with:

```sh
git switch main
git pull --ff-only origin main
```

If Git refuses, **stop**. Do not use `reset --hard`, a force pull, or a force push. Keep your changes and ask how to merge them. Re-run the dependency-install commands if the dependency files changed.

After each assisted iteration, the working app should be tested, scanned and pushed here as a normal new commit with a short, plain-language message. Never force-push. A changed remote branch must be reviewed before proceeding. Changes that exist only on your computer are invisible to the assistant; pushing a commit to GitHub does not modify your local checkout or its database.

## If something does not start

- **“Command not found”:** check that Python 3.14, Node.js and Git are installed, then open a new terminal.
- **“Address already in use”:** stop the older app terminal with Ctrl+C. Keep the documented ports 8000 and 5173 for this setup.
- **Interface shows a fixture/offline warning:** it is not using a live backend. Check that the first terminal still says the API is running and that the API docs page opens. Do not treat fallback fixture values as a fresh calculation.
- **A run fails:** preserve the error text and ask for help. You do not need to modify Python to complete the documented setup.
- **Private clone is denied:** sign into a GitHub account with access to this repository.

## Current engineering limits

Licensed manufacturer data, complete assembly compatibility, field validation and production authentication are not complete. Cable voltage drop remains resistance-only, the two-section calculation is screening-only, and numerical gas coefficients beyond 25% GVF require an explicit engineering method or override. Do not expose this development app to the public internet or use it to approve equipment for installation.

The `docs/` folder contains technical background and historical iteration notes. Follow this README for current startup instructions; older notes may describe the original hosted workspace or superseded engineering behavior.

## First-push verification

On 2 October 2026, a separate clean checkout and new Python environment passed **441 tests**, built the frontend successfully, and calculated all three synthetic cases from an empty local database with model credentials absent. The generated frontend JavaScript matched the demo preview bundle. These checks ran on Linux with Python 3.14.3 and Node.js 20.20.1; the macOS and Windows commands above are provided but were not exercised on those operating systems.

A Gitleaks scan of the source and the existing local Git history found no credential leaks. The first GitHub commit is a clean current-state snapshot, not an upload of workspace logs, local databases or the earlier internal Git history. No scanner can guarantee that every possible secret is detectable; the ignore rules and review-before-push workflow remain required.

# Repository instructions

## Product boundary

- This is a local-first GitHub portfolio project, not a production navigation service.
- The product plans one battery-electric passenger-car trip. It is not a fleet EVRP solver.
- OSRM owns road-network routing and geometry. `chargepath` owns energy feasibility, charging-stop selection, charging amount, and trip-time optimization.
- Turn-by-turn navigation, accounts, payments, reservations, live vehicle connections, and production hosting are out of scope unless the project plan is explicitly revised.
- Never present static station data, estimated SOC, or estimated charging time as live or guaranteed.

## Sources of truth

Read these before changing scope or declaring a milestone complete:

1. `PROJECT_STATE.md`
2. `PROJECT_PLAN.md`
3. `docs/architecture.md`
4. `docs/algorithm.md`
5. `docs/data_strategy.md`

## Engineering rules

- Python 3.11+ is authoritative for the optimization core.
- Keep the optimizer deterministic for identical inputs.
- Keep network calls behind provider interfaces; unit tests must not need the internet.
- Mark every bundled sample as synthetic unless it is accompanied by source, retrieval time, and license/provenance metadata.
- Use conservative energy rounding and enforce the reserve-SOC invariant on every driven leg.
- Do not add a framework, database, frontend, or container until its milestone requires it.
- Preserve unrelated dirty work under `D:\kişisel projeler`.
- Do not commit, push, tag, or publish without an explicit user request.

## Verification

During implementation, run only the smallest relevant test or check for the files and behavior that
changed. Do not run the full repository gate after every edit.

Examples:

```powershell
python -m pytest -q tests/test_optimizer.py
python -m pytest -q tests/test_fixtures.py
python -m ruff check src/chargepath/fixtures.py tests/test_fixtures.py
python -m mypy src/chargepath/verification.py
```

Run the full repository gate only when closing a milestone, before final handoff, after dependency or
verification-script changes, or when a change has broad cross-module impact:

Run from the repository root:

```powershell
.\scripts\check.ps1
```

The script prefers the repository `.venv`, then falls back to an installed Python 3.11
launcher. It runs tests, byte-compilation, the synthetic demo, Ruff, mypy, and `pip check`.

A milestone is not complete until its named acceptance checks are recorded in `PROJECT_STATE.md`.

# Contributing to healing-hertz

This document covers how the project is built, how to run it
locally, and how to make the most common kinds of change - most of which is *adding a
new diagnostic check*, which the codebase is deliberately structured to make easy.

If you only want to run the app, the [README](README.md) is the place to start.

## Getting set up

Requirements: **Python 3.12+** via [uv](https://docs.astral.sh/uv/), **Node 20+**.

```bash
make setup            # installs both dependency sets from the lockfiles
make demo             # runs the stack against bundled sample data
make check            # lint + tests + dependency audit — run this before a PR
```

`make demo` uses its own database (`backend/healing_hertz.demo.db`) so it can't touch
real scan history, and it needs no UniFi hardware or credentials. Almost all
development can be done this way.

Run `make` on its own for the full target list.

## How a scan works

Everything flows through one pipeline, in `app/scan/orchestrator.py`:

```
collect → enrich → analyze → advise → persist
```

1. **collect** (`app/collectors/snapshot.py`) pulls sites, devices, per-device detail
   and statistics, and clients from the UniFi Integration API into a single `Snapshot`
   dataclass. Everything downstream reads from that snapshot — no rule ever calls the
   network itself.
2. **enrich** (`app/collectors/enrich.py`) optionally attaches extra data the official
   API can't provide: client RF details via the legacy controller API, NextDNS
   analytics, and an active internet probe. Each runs only if configured, and each
   fails soft — an integration that breaks logs a warning and leaves its slot `None`.
3. **analyze** (`app/rules/`) runs every registered rule over the snapshot, applies any
   standing dismissals, and computes the health score.
4. **advise** (`app/advisor/`) optionally asks an LLM for a prioritized plan. Skipped
   entirely without an API key; failures are recorded but never fail the scan.
5. **persist** (`app/db/repo.py`) writes the run, findings, suggestions and metric
   snapshots to SQLite.

Progress is streamed to the UI over SSE (`app/scan/progress.py`), with polling as a
fallback.

## Layout

```
backend/app/
  unifi/          Integration API client + Pydantic models
  integrations/   Optional extras: legacy UniFi API, NextDNS, WAN probe
  collectors/     Snapshot assembly and enrichment
  rules/          Diagnostic checks, grouped by area
  advisor/        LLM prompt building, schema and call
  scan/           Orchestrator and progress streaming
  db/             SQLAlchemy models, migrations-by-hand, repository
  api/            FastAPI routes
  demo/           Fixture JSON for demo mode (doubles as test fixtures)
frontend/src/
  api/            Typed fetch client + shared types
  pages/          Dashboard, Findings, Trends, History, Settings
  components/     Reusable pieces (finding card, chart, badges…)
  theme.ts        Theme state + chart colors resolved from CSS variables
```

## Adding a diagnostic check

This is the most valuable contribution, and it's about 20 lines. There are currently 26
rules across `device_health.py`, `wifi.py`, `wired.py`, `clients.py`, `wan.py` and
`dns.py`.

A rule is any object with an `id` and an `evaluate()` method. It receives the snapshot
and the run history, and returns zero or more `Finding`s.

```python
# app/rules/wired.py

class PoeLimited:
    id = "wired.poe_limited"

    def evaluate(self, snapshot: Snapshot, history: RunHistory) -> list[Finding]:
        findings = []
        for dev_id, detail in snapshot.device_details.items():
            for port in detail.interfaces.ports:
                if port.poe is None or port.poe.state != "LIMITED":
                    continue
                findings.append(Finding(
                    rule_id=self.id,
                    severity=Severity.MEDIUM,
                    category=Category.WIRED,
                    title=f"PoE limited on {detail.name} port {port.idx}",
                    summary="…what this means, in one or two sentences…",
                    evidence={"device": detail.name, "port": port.idx,
                              "poeState": port.poe.state},
                    recommendation="…what the operator should actually do…",
                    subject_type="device",
                    subject_id=dev_id,
                    subject_name=detail.name,
                ))
        return findings


RULES = [UplinkNegotiation(), PoeLimited(), GatewaySaturation()]
```

Then add it to that module's `RULES` list — `app/rules/__init__.py` aggregates them.

**What makes a good rule:**

- **Evidence over assertion.** Put the actual numbers in `evidence`; the UI renders it
  as a table and the operator judges for themselves.
- **A recommendation someone can act on.** Name the UniFi settings page or the physical
  thing to check, not "investigate further".
- **Severity that matches consequence.** `CRITICAL` costs 25 score points, `HIGH` 10,
  `MEDIUM` 4, `LOW` 1, `INFO` 0. A finding that fires constantly at HIGH makes the
  score useless.
- **Set `subject_id`** whenever the finding is about a specific device. It's the key
  used for dismissals and for the new/resolved/persisting diff between runs.
- **Stay quiet when things are fine.** Rules that always fire train people to ignore them.

**Cross-run rules** get `history` — the last few runs' device uptimes, radio retry
percentages and site metrics. `device.reboot_loop` and `wifi.retries_worsening` are the
examples to copy.

**Rules that need data we don't have** belong in `app/rules/unsupported.py`, which
declares the check and the reason it can't run. This is deliberate: the UI shows these
so coverage gaps are visible rather than silently absent. When an optional integration
supplies the missing data, the check disappears from that list automatically.

### Testing a rule

Tests run against the demo fixtures — no network, no hardware. Add a case in
`backend/tests/test_rules.py` that asserts your rule fires *and* one that asserts it
stays quiet:

```python
async def test_my_rule_fires(snapshot):
    snapshot.device_stats["gw1"].cpu_utilization_pct = 97.0
    findings, _ = run_rules(snapshot)
    assert "device.high_cpu" in {f.rule_id for f in findings}
```

If your rule needs telemetry the fixtures don't have, extend the JSON in
`backend/app/demo/fixtures/` — that improves demo mode at the same time.

## Adding an integration

Integrations live in `app/integrations/` and follow three rules:

1. **Self-contained client.** One module, its own HTTP client, its own dataclass result.
2. **Wired in via `enrich.py`**, which runs configured integrations concurrently and
   swallows failures — a broken integration must never fail a scan.
3. **Attached to the `Snapshot`** as an optional field. Rules check `if snapshot.dns is
   None: return []` and behave normally when it isn't configured.

Add matching settings to `app/config.py` and `.env.example`, and expose an enabled flag
in `routes_settings.py` so the Settings page can show its status.

## Conventions worth knowing

**The app is read-only.** It uses only `GET` endpoints against UniFi. Please don't add
write operations — the read-only guarantee is the reason people are willing to give it
an API key.

**Nothing sensitive goes to the LLM.** `app/advisor/prompts.py` sanitizes the payload:
MAC addresses, IPs and serials are stripped by key *and* by regex, client names are
pseudonymized, SSIDs dropped. If you add a field to the advisor payload, check it
against `test_payload_is_sanitized_and_compact` in `backend/tests/test_advisor.py`.
Note the sanitizer treats `name`/`hostname` keys as client identity — use `ap`, `device`
or similar for infrastructure so equipment names survive.

**Frontend theming is CSS variables only.** Every color comes from a variable defined
in both themes in `src/styles.css`; components never hardcode hex values, and charts
resolve colors at runtime through `useChartColors()` so they follow the theme. If you
add a variable, add it to *both* blocks.

**Validate palettes, don't eyeball them.** Colors that encode meaning need real
contrast and colorblind separation. Severity colors in particular are close in hue by
nature and are always paired with a text label for that reason.

## Gotchas

- **macOS ships bash 3.2.** `dev.sh` must avoid bash 4+ syntax (`wait -n`, associative
  arrays). It also has to stay in the terminal's foreground process group, or Ctrl-C
  never reaches it.
- **No migration framework.** The SQLite schema is created at startup, and additive
  column changes are applied by hand in `app/db/engine.py:init_db()`. Follow that
  pattern for new columns so existing databases keep working.
- **One scan at a time**, guarded by a module-level lock; a concurrent trigger returns
  409.
- **Demo fixtures are also test fixtures.** Changing them can change test expectations.
- **`.env` is never committed.** `.env.example` documents every setting and must stay
  free of real values.

## Before opening a PR

```bash
make check      # ruff + TypeScript typecheck, the test suite, dependency CVE audit
```

Then, briefly: what changed and why. If you added a rule, mention what it catches and
roughly how often you'd expect it to fire on a healthy network — that's the main thing
reviewers will want to reason about.

Bug reports are just as welcome as code. If you hit a false positive, the finding's
evidence table plus your device model and UniFi Network version is usually enough to
reproduce it.

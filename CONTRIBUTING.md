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
  rules/          Diagnostic checks; catalog/ declares them, sources.py feeds them
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

This is the most valuable contribution, and most checks are pure YAML.

Every rule is declared in the catalog at `app/rules/catalog/`, one file per area.
The catalog is the registry: it owns each check's id, category, severity,
thresholds and prose. Most rules express their logic there too; a few whose
algorithm isn't expressible as data keep a Python class and point at it.

```yaml
# app/rules/catalog/03-wired.yaml
- id: wired.poe_limited
  kind: declarative
  category: wired
  emits:
    - source: device_ports
      where: [poe_state, eq, LIMITED]
      severity: medium
      title: "PoE limited on {device_name} port {port_idx}"
      summary: >-
        …what this means, in one or two sentences…
      recommendation: >-
        …what the operator should actually do…
      evidence:
        device: {raw: device_name}
        port: {raw: port_idx}
        poeState: {raw: poe_state}
```

That's the whole rule. Add the entry, run the tests, done.

**`source`** names an iterable over the snapshot — `devices`, `device_stats`,
`device_ports`, `online_ap_radios`, `pending_devices`. A source does the joins and
None-guards in Python and yields a flat row of plain values, so YAML only ever
names a binding. `app/rules/sources.py` lists what each one provides; adding a
binding there is cheap.

**`where`** is a predicate over those bindings. `[binding, op, value]` is the
compact form; `{all: [...]}`, `{any: [...]}` and `{not: ...}` nest. Operators are
`eq ne lt lte gt gte in not_in contains is_null is_not_null`. A comparison against
a missing reading is false rather than an error, so you rarely need an explicit
null guard.

**`severity`** is a name, or a base plus escalations when it depends on the value
that matched:

```yaml
      severity:
        base: medium
        escalate:
          - {when: [cpu_pct, gte, 90], to: high}
```

**`compute`** derives named values before `where` runs, because templates do no
arithmetic:

```yaml
      compute:
        uptime_days: {op: floordiv, of: uptime_sec, by: 86400}
```

Ops are `floordiv`, `ratio`, `scale` and `round`. That list is deliberately
closed — a rule that needs a fifth op is a rule whose logic isn't data, and
belongs in Python. Values shared by more than one rule go in `_constants.yaml`
and are referenced as `$NAME`.

**Templates** are `str.format` over the bindings, so `{cpu_pct:.0f}` works as you'd
expect. A replacement field must be a bare name — no attribute access, no
indexing. That restriction is what makes rule files safe to accept from
operators, so it isn't negotiable.

### When your rule needs Python

Graph walks, medians over history, group-by, and anything cross-row can't be
expressed as a predicate over one row. Those keep a class — but only their
*logic*. The prose still lives in the catalog.

The class returns `Binding`s instead of `Finding`s: it works out what is true and
hands back the values it computed.

```python
# app/rules/wifi.py
class MeshUplink:
    id = "wifi.mesh_uplink"

    def evaluate(self, snapshot, history) -> list[Binding]:
        ...                                    # walk the uplink chain
        return [Binding(
            vars={"device_name": detail.name, "hops": hops, ...},
            subject_type="device", subject_id=dev_id, subject_name=detail.name,
        )]
```

```yaml
- id: wifi.mesh_uplink
  kind: python
  impl: app.rules.wifi:MeshUplink
  category: wifi
  provides: [device_name, uplink_device_name, hops, hop_phrase]
  emits:
    - severity:
        base: medium
        escalate:
          - {when: [hops, gte, 2], to: high}
      title: "{device_name} is wirelessly meshed via {uplink_device_name}"
      summary: >-
        …
      recommendation: >-
        …
      evidence:
        wirelessHops: {raw: hops}
```

**`provides`** lists the bindings the class guarantees. Templates, evidence and
escalations are checked against it at load time, so a rule and its wording can't
drift apart without the catalog failing to load.

**Multiple emit blocks** handle a rule that reports more than one kind of thing
under one id — `wan.latency_loss` reports loss and latency separately. Give each
block a `key` and return `Binding(key=...)` to match. They share a rule id, so
they share a dismissal.

Add a docstring saying *why* the rule can't be data. Every `kind: python` rule has
one, so the boundary is documented where someone would question it.

Don't force a rule into YAML that doesn't fit. A catalog with a healthy number of
`kind: python` entries is still a complete catalog — what matters is that no
rule's wording is hidden in code.

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

- **Never change a rule id.** It's the key for dismissals, for the
  new/resolved/persisting diff between runs, and for linking LLM suggestions back to
  findings. Renaming one orphans every dismissal against it, makes the finding
  reappear, and drops the score on every historical run. Use `enabled: false` to
  retire a check instead of deleting it.

**Cross-run rules** get `history` — the last few runs' device uptimes, radio retry
percentages and site metrics. They're `kind: python`; `device.reboot_loop` and
`wifi.retries_worsening` are the examples to copy.

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

Also add a scenario to `backend/tests/rule_scenarios.py` and regenerate the golden
fixture:

```
uv run python -m tests.golden.generate
```

`tests/golden/findings.json` pins every field of every finding across all
scenarios. It's what proves a change to the engine — or a rule moving from Python
to YAML — didn't quietly reword a finding or stop a rule firing, so the diff is
worth reading line by line before committing it. A rule with no scenario isn't
covered by any of that.

`uv run python -m app.rules.validate` checks the catalog loads without running a
scan.

## Your own checks, without touching the repo

Set `RULES_DIR` to a directory of `.yaml` files and they load alongside the
built-ins. The schema is the same, with two restrictions:

- Every id must start with `custom.`. Rule ids key dismissals and run diffs, so
  namespacing keeps yours from ever colliding with a built-in one.
- `kind: python` is rejected. A rule file is data; it can't name code to import.

A file that fails to load is skipped and reported in the UI's "not checkable" list
with the parse error, rather than failing the scan. Check them up front with
`python -m app.rules.validate`.

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

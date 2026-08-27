# Writing diagnostic rules

Every check healing-hertz performs is declared in the **rule catalog** —
`backend/app/rules/catalog/`, one YAML file per area. The catalog owns each
check's identity, category, severity, thresholds and wording.

Most rules express their logic there too. A few, whose algorithm genuinely
isn't data, keep a Python class for the *logic* and still keep their *wording*
in the catalog.

You can add rules two ways:

- **Contributing them** — add an entry to `backend/app/rules/catalog/`.
- **Keeping them local** — point `RULES_DIR` at your own directory of YAML
  files. See [Your own rules](#your-own-rules).

---

## A complete rule

```yaml
- id: wired.poe_limited
  kind: declarative
  category: wired
  emits:
    - source: device_ports
      where: [poe_state, eq, LIMITED]
      severity: medium
      title: "PoE limited on {device_name} port {port_idx}"
      summary: >-
        Port {port_idx} on {device_name} is delivering limited PoE — the attached
        device may be underpowered, causing instability or reduced performance.
      recommendation: >-
        Check the switch's total PoE budget and the attached device's power class;
        move high-draw devices to a port/switch with headroom.
      evidence:
        device: {raw: device_name}
        port: {raw: port_idx}
        poeState: {raw: poe_state}
```

That is the whole rule. Add it, run `make validate-rules`, done.

---

## Entry fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Dotted lowercase, e.g. `wifi.dfs_channel`. **Never change it** — see [Rule ids are permanent](#rule-ids-are-permanent). |
| `kind` | yes | `declarative` or `python`. |
| `category` | yes | `device_health`, `wifi`, `wired`, `clients`, `firmware`, `capacity`. |
| `enabled` | no | Defaults `true`. Set `false` to retire a check without deleting its id. |
| `emits` | yes | One or more emit blocks. |

An entry has a **list** of emit blocks because one check can report more than
one thing under a single id — `device.pending_adoption` reports never-adopted
devices and adopted ones that fell back. They share an id, so they share a
dismissal.

---

## Sources

`source` names an iterable over the scan snapshot. A source does the joins,
lookups and null-guards in Python and yields a flat row of plain values, so YAML
only ever names a binding.

| Source | Yields | Bindings |
|---|---|---|
| `devices` | every adopted device | `device_id` `device_name` `device_model` `device_mac` `device_ip` `device_state` `device_supported` `device_firmware_version` `device_firmware_updatable` `is_access_point` `is_switch` `is_gateway` `name_or_model` `name_or_mac` |
| `pending_devices` | devices seen but not adopted | `pending_id` `pending_name` `pending_model` `pending_mac` `name_or_model_or_mac` |
| `device_stats` | per-device telemetry | `device_id` `device_name` `uptime_sec` `cpu_pct` `memory_pct` `load_1_min` `load_5_min` `load_15_min` `uplink_tx_bps` `uplink_rx_bps` |
| `device_ports` | every port on every device | `device_id` `device_name` `device_model` `port_idx` `port_state` `port_connector` `port_speed_mbps` `port_max_speed_mbps` `poe_state` `poe_standard` `poe_type` `poe_enabled` |
| `online_ap_radios` | broadcasting radios of online APs | `device_id` `device_name` `device_model` `radio_channel` `radio_width_mhz` `radio_frequency_ghz` `radio_band` `radio_standard` `radio_standard_normalized` |
| `ap_radio_stats` | per-radio counters for APs | `device_id` `device_name` `radio_frequency_ghz` `radio_band` `tx_retries_pct` |
| `clients` | connected clients | `client_id` `client_name` `client_mac` `client_ip` `client_type` `access_type` `access_authorized` |
| `rf_clients` | per-client RF detail | `client_mac` `client_name` `client_ssid` `client_ap_mac` `signal_dbm` `tx_rate_kbps` `rx_rate_kbps` `channel` `band_ghz` |
| `networks` | configured networks with their address space | `network_id` `network_name` `network_enabled` `network_vlan_id` `network_management` `network_is_default` `network_isolation_enabled` `network_internet_access_enabled` `network_dhcp_mode` `network_cidr` `network_prefix_length` `network_usable_hosts` `network_client_count` `network_pool_pressure` `network_trusted_dhcp_servers` |
| `wifi_broadcasts` | each broadcast WiFi network's settings | `wifi_id` `wifi_name` `wifi_enabled` `wifi_type` `wifi_security` `wifi_encryption` `wifi_pmf_mode` `wifi_fast_roaming_enabled` `wifi_hidden` `wifi_client_isolation_enabled` `wifi_band_steering_enabled` `wifi_bss_transition_enabled` `wifi_mlo_enabled` `wifi_uapsd_enabled` `wifi_bands` `wifi_band_count` `wifi_on_24` `wifi_on_5` `wifi_on_6` `wifi_basic_rate_24_kbps` `wifi_basic_rate_5_kbps` `wifi_mac_filter_action` `wifi_mac_filter_count` |
| `metric_trends` | robust statistics over each stored metric's history | `metric` `metric_label` `metric_watched` `subject_id` `subject_name` `sample_count` `latest` `median` `mad` `zscore` `zscore_abs` `ewma` `slope_per_day` `forecast_target` `days_to_target` `changepoint_at` `changepoint_direction` `changepoint_before` `changepoint_after` |
| `device_topology` | each device's place in the uplink tree | `device_id` `device_name` `device_model` `device_kind` `uplink_name` `uplink_depth` `is_root` `in_uplink_cycle` `downstream_devices` `downstream_aps` `downstream_names` |
| `switch_capacity` | link speed and PoE load per switching device | `device_id` `device_name` `device_model` `uplink_speed_mbps` `downstream_speed_mbps` `oversubscription_ratio` `active_ports` `poe_powered_ports` `poe_demand_w` `poe_budget_w` `poe_utilization` |
| `switch_stacks` | configured switch stacks and their roles | `stack_id` `stack_name` `stack_unit_count` `stack_active_controllers` `stack_backup_controllers` |

`app/rules/sources.py` is the authority; adding a binding there is cheap.

Two things sources do for you:

- **Missing integrations.** `rf_clients` yields nothing when the legacy
  controller API isn't configured, and `networks` / `wifi_broadcasts` yield
  nothing when the console is too old for the config endpoints, so a rule over
  either needs no guard of its own.
- **Display fallbacks.** `name_or_model` is `name or model`. Templates do no
  logic, so fallbacks are bindings.

---

## Predicates

`where` filters rows. The compact form is a three-element list:

```yaml
where: [poe_state, eq, LIMITED]
```

Nest with `all`, `any`, `not`:

```yaml
where:
  all:
    - [radio_frequency_ghz, eq, 5]
    - [radio_channel, in, $DFS_CHANNELS]
    - not: [device_name, eq, "Test AP"]
```

| Operator | Meaning |
|---|---|
| `eq` `ne` | equality |
| `lt` `lte` `gt` `gte` | ordered comparison |
| `in` `not_in` | membership in a list |
| `contains` | the value is contained *in* the binding |
| `is_null` `is_not_null` | presence (two-element form: `[binding, is_null]`) |

**A missing reading never matches an ordered comparison.** `[speed_mbps, lte, 100]`
is false when speed is null rather than an error, so you rarely need an explicit
null guard.

### Named constants

Values shared by more than one rule, or that deserve a name, live in
`catalog/_constants.yaml` and are referenced with `$`:

```yaml
where: [radio_channel, not_in, $GOOD_24_CHANNELS]
```

---

## Severity

A plain name, or graded by the value that matched:

```yaml
severity: medium
```

```yaml
severity:
  base: medium
  escalate:
    - {when: [cpu_pct, gte, 90], to: high}
```

Escalations are tried in order; first match wins, otherwise `base`.

Severity is a closed set — `critical` `high` `medium` `low` `info` — and it is
load-bearing for the health score: `critical` costs 25 points, `high` 10,
`medium` 4, `low` 1, `info` 0. A rule that fires constantly at `high` makes the
score useless.

---

## Computed values

Templates do no arithmetic. Anything derived is named first with `compute`,
which runs **before** `where`, so predicates can test it too:

```yaml
compute:
  uptime_days: {op: floordiv, of: uptime_sec, by: 86400}
  load_trend:  {op: ratio, of: load_5_min, per: load_15_min}
title: "{device_name} has been up for {uptime_days} days"
```

| Op | Fields | Result |
|---|---|---|
| `floordiv` | `of`, `by` | integer division |
| `ratio` | `of`, `per` | `of / per`; null if either is missing or `per <= 0` |
| `scale` | `of`, `by` | multiplication |
| `round` | `of`, `digits` | rounding (`digits` defaults 0) |

**This list is deliberately closed.** A rule that needs a fifth op is a rule
whose logic isn't data — write it in Python (see below). Adding one op per rule
is how a small set of names becomes an expression language.

---

## Aggregating

Some checks report a population rather than a device — "4 clients on legacy
rates" — where naming each one would be noise. `aggregate` folds every matching
row into one site-scoped finding:

```yaml
- source: rf_clients
  where:
    all:
      - [signal_dbm, is_not_null]
      - [signal_dbm, lte, -75]
  aggregate:
    into: site
    min_matches: 1        # stay quiet below this many matches
    compute:
      count: {op: count}
      worst: {op: min_of, of: signal_dbm}
  severity:
    base: medium
    escalate:
      - {when: [worst, lte, -85], to: high}
  title: "{count} client(s) with weak WiFi signal"
  evidence:
    clients:
      op: top
      sort_by: signal_dbm
      order: asc            # or desc
      limit: 10             # omit for all matches
      project: {name: client_name, signalDbm: signal_dbm, ssid: client_ssid}
```

Group ops are `count`, `min_of` and `max_of` (the latter two take `of`, and an
optional `null_as`).

**An aggregated block's prose sees only the group's computed values**, not any
row's bindings — the rows are gone by then, and naming one is a load error. The
exception is `evidence`, where `op: top` projects from the matched rows.

---

## Templates and evidence

Titles, summaries and recommendations are `str.format` over the bindings, so
format specs work as expected: `{cpu_pct:.0f}`, `{utilization:.0%}`.

**A replacement field must be a bare name.** No attribute access, no indexing,
no positional fields. That restriction is what makes rule files safe to accept
from operators, so it isn't negotiable — `{x.__class__}` and `{x[0]}` are
rejected at load.

`evidence` keys become the table the UI renders under a finding. Values are
bound with `{raw: binding}` so they keep their JSON type — a port index stays
an integer, not `"5"`.

> Two evidence conventions worth knowing: prefer `ap` over `name` for access
> point names (the LLM advisor's sanitiser pseudonymises `name`/`hostname` as
> client identity), and put the actual numbers in rather than asserting a
> conclusion — the operator judges for themselves.

---

## When your rule needs Python

Graph walks, medians over history, group-by, and anything cross-row can't be a
predicate over one row. Those keep a class — but only for the *logic*. The class
returns `Binding`s: it works out what is true and hands back the values.

```python
# app/rules/wifi.py
class MeshUplink:
    """An AP that reaches the network through another AP relays every frame.

    Not declarative: counting hops means walking the uplink chain, with a guard
    against a controller reporting a cycle.
    """

    id = "wifi.mesh_uplink"

    def evaluate(self, snapshot, history) -> list[Binding]:
        ...                                     # walk the chain
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
      evidence:
        wirelessHops: {raw: hops}
```

- **`provides`** lists the bindings the class guarantees. Templates, evidence
  and escalations are checked against it at load, so a rule and its wording
  can't drift apart without the catalog failing to load.
- **`impl`** may only name a class inside `app.rules`, and only in the built-in
  catalog.
- **Multiple shapes** under one id: give each emit block a `key` and return
  `Binding(key=...)` to match. `wan.latency_loss` reports loss and latency
  separately this way.

Add a docstring saying *why* the rule can't be data. Every `kind: python` rule
has one, so the boundary is documented where someone would question it.

Rules that need data the API doesn't expose at all belong in
`app/rules/unsupported.py`, which declares the check and why it can't run — the
UI shows these so coverage gaps are visible rather than silently absent.

---

## Your own rules

Set `RULES_DIR` to a directory of `.yaml` files and they load alongside the
built-ins. Empty by default, so a standard deployment gains no new surface.

```bash
RULES_DIR=/etc/healing-hertz/rules.d
```

**The Rules tab is the easiest way in.** It lists every check, and with
`RULES_DIR` set it will build a rule, check it, and save it for you — creating,
editing and deleting files in that directory. Content is validated before it is
written, so an invalid rule never lands on disk. Everything it writes is plain
YAML you can also edit by hand.

### Turning a built-in check off

You don't have to edit the shipped catalog — and in a container you can't, since
those files are inside the image. Press **Disable** on any built-in rule and it
is recorded in `RULES_DIR/_overrides.yaml`:

```yaml
disabled:
  - wifi.dfs_channel
  - device.recent_reboot
```

Your choice survives an upgrade, because the shipped catalog is untouched. Files
starting with `_` are settings rather than rules, so the loader never reads this
one looking for checks.

The schema is identical, with two restrictions:

- **Every id must start with `custom.`** Rule ids key dismissals and the
  new/resolved diff between runs, so namespacing keeps yours from ever
  colliding with a built-in one.
- **`kind: python` is rejected.** A rule file is data; it cannot name code to
  import.

A file that fails to load is **skipped, not fatal** — your scan still runs, and
the parse error appears in the UI's "not checkable" list. Check them up front:

```bash
make validate-rules
```

---

## Rule ids are permanent

A rule id is the key for dismissals, for the new/resolved/persisting diff
between runs, and for linking the LLM advisor's suggestions back to findings.

Renaming one orphans every dismissal against it, makes the finding reappear,
and drops the health score on every historical run. Use `enabled: false` to
retire a check instead of deleting or renaming it.

---

## Validating and testing

```bash
make validate-rules   # catalog loads; no scan, no network
make test             # full backend suite
```

Rule behaviour is pinned by a golden fixture: `backend/tests/golden/findings.json`
holds every field of every finding across a set of scenarios. It is what proves a
change to the engine — or a rule moving from Python to YAML — didn't quietly
reword a finding or stop one firing.

If you add or change a rule:

1. Add a scenario to `backend/tests/rule_scenarios.py`, including a negative
   case where the rule must stay quiet.
2. Regenerate: `cd backend && uv run python -m tests.golden.generate`
3. **Read the diff.** It is the only thing standing between a change and a
   silently reworded finding.

Tests run against the bundled demo fixtures — no network, no hardware. If your
rule needs telemetry the fixtures don't have, extend the JSON in
`backend/app/demo/fixtures/`; that improves demo mode at the same time.

---

## What makes a good rule

- **Evidence over assertion.** Put the actual numbers in `evidence`.
- **A recommendation someone can act on.** Name the settings page or the
  physical thing to check, not "investigate further".
- **Severity that matches consequence.** See the score costs above.
- **Set a subject** whenever the finding is about a specific device — it's the
  key for dismissals and run diffs.
- **Stay quiet when things are fine.** Rules that always fire train people to
  ignore them.

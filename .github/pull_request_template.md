## What this changes

<!-- One or two sentences. If it adds a diagnostic check, say what it catches. -->

## Why

<!-- The problem being solved, or the finding that was missed / wrongly raised. -->

## Checklist

- [ ] `make check` passes (lint, tests, dependency audit)
- [ ] New/changed behaviour is covered by a test
- [ ] No credentials, hostnames, MACs or other real network data in code, tests or fixtures

<!-- For a new rule, also: -->
- [ ] Roughly how often would this fire on a healthy network?
- [ ] Severity matches the real-world consequence (CRITICAL 25 pts … LOW 1 pt)

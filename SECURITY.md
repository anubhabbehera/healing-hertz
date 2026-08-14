# Security Policy

## Reporting a vulnerability

Please **don't open a public issue** for a security problem.

Use GitHub's [private vulnerability reporting](https://github.com/anubhabbehera/healing-hertz/security/advisories/new)
(the *Report a vulnerability* button under the Security tab). It creates a private
thread visible only to the maintainer.

Useful things to include: what an attacker can achieve, the steps to reproduce it, and
the versions of healing-hertz and UniFi Network involved. You'll get an initial reply
within a few days; please allow a reasonable window to fix before disclosing publicly.

## What this project is, security-wise

healing-hertz reads diagnostic data from a UniFi console on your own network and stores
it locally. Two properties are deliberate and worth understanding:

**It has no authentication.** Anyone who can reach the API can trigger scans and read
findings that describe your network's weak points. It is therefore configured to listen
on loopback only, in both `dev.sh` and `docker-compose.yml`. Exposing it to a network
without putting an authenticating proxy in front is a misconfiguration, not a
vulnerability in the app — but reports of anything that *bypasses* the loopback default
are very welcome.

**It is read-only against UniFi.** Only `GET` requests are issued. A change that
introduces a write path to your console, or a way to coerce one, is a security issue.

**It writes your own rule files, and nothing else.** The Rules tab creates, edits and
deletes diagnostic checks, because configuring them locally is the point of the tool.
That write is bounded on every axis: only inside `RULES_DIR`, only names matching a
strict pattern with no separators or traversal, only files ending `.yaml`, and only
content that validates as a declarative rule — an invalid file is never written. A rule
file cannot name Python to import, so it is data, not code. Nothing else on the
filesystem is writable through the API, and no endpoint accepts a path.

Because there is no authentication, that write is available to anyone who can reach the
port, which is why the loopback default matters. A report of anything that escapes
`RULES_DIR`, or turns rule content into code execution, is very welcome.

## Handling of credentials

Credentials live in `.env`, which is gitignored and never committed. The API exposes
only booleans (`unifi_api_key_set`), never values, and secrets are kept out of logs and
error messages.

Certificate verification against the UniFi console defaults to **off**, because
consoles ship self-signed certificates. This is a documented trade-off: the API key is
protected by trust in the local network rather than by certificate identity. Set
`UNIFI_TLS_VERIFY=true` where the console has a valid certificate.

## Data sent to third parties

When the optional AI advisor is enabled, findings and a telemetry summary are sent to
the configured LLM endpoint. That payload is sanitized first: MAC addresses, IP
addresses and serial numbers are stripped, client hostnames are pseudonymized, and
SSIDs are removed. Infrastructure device names are retained deliberately, since
remediation advice has to name them.

If you find a way to get unsanitized data into that payload, please report it — there
is a regression test (`test_payload_is_sanitized_and_compact`) guarding this, and a gap
in it is a real finding.

## Supported versions

This is a young project; fixes land on `main` and there are no maintained release
branches yet.

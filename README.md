<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/wordmark-dark.svg">
  <img src="docs/wordmark-light.svg" alt="healing hertz" width="276">
</picture>

[![CI](https://github.com/anubhabbehera/healing-hertz/actions/workflows/ci.yml/badge.svg)](https://github.com/anubhabbehera/healing-hertz/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/anubhabbehera/healing-hertz/branch/main/graph/badge.svg)](https://codecov.io/gh/anubhabbehera/healing-hertz)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

**A health check for your [UniFi](https://ui.com/) network.** Point it at your UniFi
console, press one button, and get a plain-language report of what's wrong and what to
do about it.

Most UniFi dashboards tell you *what* your network is doing. healing-hertz tells you
what's **wrong with it** — the access point on a bad channel, the cable that's quietly
running at a tenth of its speed, the device that keeps rebooting — and gives you the
steps to fix each one.

It never changes anything on your network. It only reads.

---

## What it checks

Every scan looks for problems across your whole site and rates each one from
**critical** down to **info**:

**Devices** — anything offline or in a degraded state, access points stuck waiting to
be adopted, high CPU or memory, devices that have rebooted recently, and devices that
keep rebooting over multiple scans (a pattern you'd rarely spot by eye). Also flags
pending firmware updates, access points running mismatched firmware versions, and
devices that haven't restarted in six months.

**WiFi** — 2.4 GHz radios on channels other than 1, 6 or 11; two access points fighting
over the same channel; channel widths that cause more interference than they cure;
radios parked on DFS channels, where a radar hit silences them for half an hour;
access points reaching the network over the air instead of over Ethernet; legacy
802.11b/g rate sets; and radios with high transmission retry rates, including ones
that are getting *worse* compared to your recent scans.

**Wiring** — the classic one: a port negotiating 100 Mbps on a gigabit-capable link,
which almost always means a damaged cable. Also PoE ports that can't deliver full
power, and a gateway running close to its uplink capacity.

**Clients** — guest devices stuck unauthorized, and (with an optional extra, see below)
clients with weak signal, clients bouncing constantly between access points, clients
negotiating legacy data rates that eat everyone else's airtime, strong-signal clients
stuck on 2.4 GHz when band steering should have moved them, and access points carrying
more clients than a radio handles well.

Settings the read-only Integration API doesn't expose — transmit power, band steering,
DTIM, minimum RSSI, automatic channel selection, firewall and VLAN isolation — are
listed as **not checkable**, together with what the community consensus recommends, so
you can audit them yourself instead of assuming they were checked.

**Internet & DNS** — connection latency and packet loss measured during each scan, plus
optional NextDNS analysis that surfaces malware and phishing domains your devices tried
to reach.

Each finding comes with the **evidence** behind it — the actual channel, the actual
CPU percentage — so you can judge it yourself rather than taking the tool's word.

## Health score, trends and history

Every scan produces a score out of 100. Findings cost points by severity, so the number
moves when your network actually improves or degrades.

Because every scan is saved, you get a **trend** of your network over time — health
score, device CPU, WiFi retries, internet latency — and you can **compare any two
scans** to see exactly what's new, what you fixed, and what's still outstanding.

This matters more than it sounds: UniFi itself doesn't keep this history, so the more
you scan, the more this becomes something you can't get anywhere else.

## AI-written fix plans (optional)

If you add an API key for Claude (or any compatible service), each scan also produces a
prioritized action plan written for *your* network — grouping related problems, spotting
common root causes, and giving concrete steps naming the actual UniFi settings pages.

This is entirely optional. Without a key you still get every finding and every built-in
recommendation; you just don't get the written plan on top.

**Your network details stay private.** Before anything is sent, MAC addresses, IP
addresses and serial numbers are stripped out, personal device names are replaced with
labels like `client-1`, and WiFi network names are removed. Only equipment names (your
access points and switches) are included, because the advice has to be able to name them.

---

## Try it first — no hardware needed

```bash
make setup    # one-time: installs everything
make demo     # opens with realistic sample data
```

Then open **http://localhost:5173** and press **Run scan**. You'll see the full
interface working against a made-up network with a handful of deliberate problems.

Press **Ctrl-C** in the terminal to stop it.

> Requires [uv](https://docs.astral.sh/uv/) (Python) and [Node.js](https://nodejs.org/)
> 20+. On macOS: `brew install uv node`.

## Connecting to your own network

### 1. Find your console's address

`UNIFI_HOST` is simply **the address you type into your browser to open UniFi**.

| What you have | What to use |
|---|---|
| A UniFi gateway or console — Dream Machine, UDM Pro/SE, UDR, Cloud Key, UCG | Your router's address, usually `192.168.1.1`. If `https://192.168.1.1` shows the UniFi login page, that's it. |
| UniFi Network Server you installed yourself (Docker, Linux, Windows) | That computer's address, plus `UNIFI_PORT=8443` and `UNIFI_API_PREFIX=/integration` |

Don't use `unifi.ui.com` — that's Ubiquiti's cloud portal, not the console on your own
network, and this app talks directly to your console.

### 2. Create an API key

In your UniFi console, go to **Settings → Control Plane → Integrations → Create API
Key**. (On some versions it's under **Settings → API**, or in your admin profile.
You'll need UniFi Network 9.0 or newer.) That key is for the
[UniFi Network Integration API](https://developer.ui.com/unifi-api/) — Ubiquiti's
supported, read-and-write-scoped interface, of which this app only ever reads.

**Recommended:** first create a separate admin account with the **View Only** role, log
in as that account, and create the key there. The app never writes to your network, and
a view-only key makes that a guarantee rather than a promise. Copy the key when it's
shown — you won't see it again.

### 3. Fill in your settings

```bash
cp .env.example .env
```

Open `.env` and set `UNIFI_HOST` and `UNIFI_API_KEY`. Then:

```bash
make dev
```

Open **http://localhost:5173**, go to **Settings → Test connection** to confirm it can
reach your console, then press **Run scan**.

### Everyday commands

| Command | What it does |
|---|---|
| `make dev` | Run against your real network |
| `make demo` | Run with sample data (uses a separate database — your real history is untouched) |
| `make stop` | Stop it if you left it running in another window |

Prefer containers? Fill in `.env`, then `make docker-up` (and `make docker-down` to
stop). Run `make` on its own to see everything available.

---

## Dismissing findings you can't fix

Sometimes a finding is correct but not something you're going to act on — an access
point you unplugged on purpose, a cable run you're not going to redo. Open the finding
and choose **Dismiss finding**, optionally with a note explaining why.

Dismissing is a lasting decision, not a temporary hide:

- The finding **stays visible**, marked as dismissed, so you don't lose the information.
- It **stops costing you points**, so your health score reflects the problems you
  actually intend to fix. Dismissing a critical finding gives you 25 points back.
- It applies to **future scans**, so the same known issue doesn't drag the score down
  every time.
- Past scans are re-scored too, so your trend line stays honest instead of showing a
  jump on the day you changed your mind.

You can review or undo dismissals any time under **Settings → Dismissed findings**.

## Optional extras

These fill gaps that UniFi's official API can't cover on its own. Each is off unless
you enable it, and if one fails your scan still completes without it. Until enabled,
the affected checks appear in the app marked "not checkable", with instructions —
nothing is silently missing.

| What you gain | How to turn it on |
|---|---|
| **Weak-signal clients** and **devices roaming between access points** | Add `UNIFI_USERNAME` / `UNIFI_PASSWORD` for a **View Only** local admin account. Uses an older UniFi interface that Ubiquiti doesn't officially document, so it may change with firmware updates — failures are skipped, never fatal. |
| **Internet latency and packet loss** | On by default. Measured from the computer running the app during each scan, and tracked over time. |
| **DNS problems, malware and phishing blocks** | Add `NEXTDNS_API_KEY` (from your [NextDNS](https://nextdns.io/) account settings) and `NEXTDNS_PROFILE_ID` (the short ID in your profile URL, e.g. `abc123`). Surfaces security blocks and unusual spikes in blocked traffic. See below for pointing your network at NextDNS in the first place. |

### Pointing UniFi at NextDNS over encrypted DNS

The NextDNS check reads your profile's analytics, so it only says something useful once
your network actually resolves through NextDNS. Doing that over **encrypted DNS** means
your queries can't be read or rewritten between your gateway and the resolver — worth
having regardless of this app.

Create a profile at [my.nextdns.io](https://my.nextdns.io/) and note its ID, then pick
whichever route matches your console:

**1. Native encrypted DNS (recent UniFi Network builds).** Under **Settings → Security**
you'll find UniFi's encrypted-DNS control (named *DNS Shield* on current builds). Set it
to the manual/custom option, choose **NextDNS** as the provider and paste your profile
ID. Your gateway then resolves over DoH for every client on the network.

**2. A DNSCrypt stamp (builds whose field rejects a plain URL).** Some versions accept
only an `sdns://` stamp and will refuse `https://dns.nextdns.io/abc123`. Generate one at
[dnscrypt.info/stamps](https://dnscrypt.info/stamps/) using host `dns.nextdns.io` and
path `/abc123`. Append a device name to the path — `/abc123/gateway` — if you want the
queries to show up under a name in NextDNS analytics rather than as *unidentified*.

**3. The NextDNS client on UniFi OS.** For full control, including per-VLAN profiles,
install NextDNS's own client on the gateway by following the
[NextDNS UnifiOS guide](https://github.com/nextdns/nextdns/wiki/UnifiOS). It survives
firmware updates less gracefully than the built-in options, so prefer 1 or 2 if they
work for you.

Two things to check afterwards: visit [test.nextdns.io](https://test.nextdns.io/) from a
client to confirm queries are arriving encrypted, and remember that anything setting its
own DNS (a device with hardcoded resolvers, or a browser doing its own DoH) bypasses the
gateway entirely. UniFi's exact menu wording moves between releases — if *DNS Shield*
isn't where this says, look for "encrypted DNS" or "secure DNS" under Security or the
Internet/WAN settings.

## Using a different AI provider

The AI advisor works with Anthropic directly, or with any service that speaks the same
API — OpenRouter, a self-hosted LiteLLM proxy, and similar. For OpenRouter:

```bash
ANTHROPIC_BASE_URL=https://openrouter.ai/api
ANTHROPIC_API_KEY=sk-or-...                  # your OpenRouter key
ADVISOR_MODEL=anthropic/claude-sonnet-4.5    # whatever model name they use
```

Leave `ANTHROPIC_BASE_URL` empty to use Anthropic directly. Other providers' models
work too — the app adapts automatically if the service doesn't support Anthropic's
newer response format.

---

## Things worth knowing

**Keep it on your own machine.** There's no login screen. The app is set up to be
reachable only from the computer it runs on, and it should stay that way — its reports
describe your network's weak points, which isn't something to leave open on a shared
network. If you want access from elsewhere, put it behind something that asks for a
password first.

**Your keys stay in `.env`** on your own machine and are never displayed in the app,
written to its logs, or included in error messages.

**A note on certificates.** UniFi consoles ship with self-signed certificates, so
certificate checking is off by default — otherwise nothing would connect. This is
normal for local network tools, but it does mean your API key is protected by trusting
your own network rather than by the certificate itself. If your console has a proper
certificate, set `UNIFI_TLS_VERIFY=true`.

**What it can't see.** The [Integration API](https://developer.ui.com/unifi-api/)
doesn't expose system logs, event history or alarms, so those aren't covered. Nor does
it expose wireless *configuration* — transmit power, band steering, DTIM, minimum RSSI,
firewall and VLAN isolation — so those checks are listed in-app as "not checkable" with
the recommended setting, rather than guessed at. Ubiquiti also doesn't provide
historical statistics, which is exactly why this app saves each scan itself.

## Troubleshooting

**"Test connection" fails.** Check you can open your console at the same address in a
browser. Self-hosted installs need `UNIFI_PORT=8443` and `UNIFI_API_PREFIX=/integration`.
Also confirm the key came from your console (Settings → Control Plane → Integrations),
not from Ubiquiti's cloud site.

**AI advice says it failed.** The message tells you why. A timeout usually means the
model you chose is slow — try a faster one via `ADVISOR_MODEL`. Everything else in the
scan still works regardless.

**"Address already in use".** A previous session is still running: `make stop`.

**Scans show no trends.** Trends need at least two scans — the chart fills in as you
build up history.

---

## Contributing

Bug reports, new diagnostic checks and improvements are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for how the project is put together and how to add
your own checks.

## Licence

MIT — see [LICENSE](LICENSE).

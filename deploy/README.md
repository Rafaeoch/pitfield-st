# Deploying Pitfield St

The laptop is a bad host for a daily archive. It sleeps, it travels, and macOS
puts scheduled agents behind TCC. This moves the two jobs to a box that stays
awake, and serves the site from the same box that computes it.

## What this costs

| | |
|---|---|
| Server (see below) | €3.79–7.55/mo |
| IPv4 address | ~€0.50/mo, billed separately |
| Domain | ~$12/yr |
| TLS | free, automatic, via Caddy |

Two workable choices. The server types are not offered everywhere: the Intel
`CX` line is EU-only, and the US locations sell the AMD `CPX` line instead, so
"CX22 in Ashburn" is not a thing you can select.

| | Type | Where | Specs | ~Price |
|---|---|---|---|---|
| Take this | **CX23** | Falkenstein / Nuremberg / Helsinki | 2 vCPU, 4GB | $6.49/mo |
| US-hosted | **CPX21** | Ashburn / Hillsboro | 3 vCPU, 4GB, 80GB | $37.49/mo |

The US premium is real and large -- roughly six times -- and buys nothing here.
The cheapest Ashburn option (CPX11) has 2GB, which is below the line.

Take CX22 in the EU unless you specifically want US hosting. Nothing here is
latency-sensitive: it is a once-a-day batch job against APIs, not a trading
system, and being 90ms further from Alpaca is invisible to a job that runs
twenty minutes after the close.

Avoid the 2GB tiers (CPX11, CAX11 at 4GB is fine). 4GB is the number that
matters, because the weekly study's bootstrap resampling is the only heavy job
and an earlier version of it was OOM-killed on a machine with more memory than
that. `bootstrap.sh` adds 2GB of swap as further insurance.

Prices and the type lineup change; confirm both in the console rather than
trusting this table.

## Order of operations

**1. Create the server.** Hetzner Cloud, **Debian 13**, CX23 in Helsinki
(or CPX21 in Ashburn for US hosting). Add your SSH key during creation, and do
not enable the root-password option. Debian 13 ships Python 3.13, inside the
range these tests run on. Ubuntu 24.04 (3.12) is equally fine where offered;
Hetzner may list only its newest Ubuntu, which ships 3.14 and is a bet on wheel
availability rather than on syntax.

**2. Provision it.** From this repo:

```bash
scp -r deploy root@SERVER_IP:/tmp/
ssh root@SERVER_IP 'bash /tmp/deploy/bootstrap.sh archive.example.com'
```

Installs Python, Node 22, Caddy; creates the `pitfield` service account; sets
the clock to America/New_York; opens 22/80/443 and closes everything else;
installs and starts both timers.

**3. Add credentials — by hand, on the box.**

```bash
ssh root@SERVER_IP
nano /etc/pitfield/env
```

Type the values in. Do not scp your local `.env`, do not paste secrets into a
chat window, and do not put them in this repository. The file is `0640
root:pitfield`, readable by the jobs and nobody else. With it left empty the
capture exits cleanly and says it had no keys, rather than half-writing a
session.

**4. Deploy the code.**

```bash
./deploy/deploy.sh root@SERVER_IP
```

Runs the tests locally first, rsyncs the tree, builds a Linux venv, fetches the
JPL kernel, installs Node deps from the lockfile, runs the tests again on the
server's own Python, seeds a first archive, and publishes.

**5. Point the domain.** An `A` record at the server IP. Caddy issues the
certificate on the first HTTPS request. Nothing to renew.

## What runs, and when

Both timers use market-local time, because the server clock is set to
America/New_York and tzdata handles the DST shift. A fixed UTC schedule drifts
an hour against the close twice a year.

| Timer | Fires | Does |
|---|---|---|
| `pitfield-capture` | Mon–Fri, hourly 16:25–23:25 ET | chain capture, ephemeris export, rebuild |
| `pitfield-weekly` | Sun 09:15 ET | reading list, paper notes, study, rebuild |

Hourly rather than once, because the job is idempotent per session and gated on
the market calendar: the repeats cost nothing on a normal day and are a free
retry when the venue times out on a bad one.

## The rebuild step is load-bearing

`scripts/publish_site.sh` runs after every data change, and it is not
optional. Pages read the archive at **build** time (`src/lib/archive.ts` uses
`fs.readFileSync`), while the three.js components fetch their JSON at **run**
time. `npm run dev` re-reads on every request, so on a laptop the distinction
never appears. Serving a stale `dist/` gives the worst available failure: the
moon globe and the volatility surface keep updating while the homepage
counters, the quality tables and the study results quietly freeze on build day.
Half the numbers wrong and no error anywhere.

Each build publishes to `/var/www/pitfield/releases/<timestamp>` and swaps a
symlink, so a reader never sees a half-copied site. Five releases are kept.

## Operating it

```bash
systemctl list-timers 'pitfield-*'            # when does it next fire
systemctl start pitfield-capture.service      # run a capture now
journalctl -u pitfield-capture.service -n 50  # what happened
cat /var/log/pitfield/failures.log            # what went wrong, durably
sudo -u pitfield tail -f /opt/pitfield/logs/capture-$(date +%F).log
```

Roll back to the previous build:

```bash
ls -1dt /var/www/pitfield/releases/*/         # pick the one before
ln -sfn /var/www/pitfield/releases/STAMP /var/www/pitfield/.current.tmp
mv -Tf /var/www/pitfield/.current.tmp /var/www/pitfield/current
```

## When a job fails

`pitfield-alert@.service` appends the failure and the last 25 journal lines to
`/var/log/pitfield/failures.log`, and POSTs to `PITFIELD_ALERT_WEBHOOK` if one
is set. Not email: a box with no MTA drops mail silently, and an alert you
believe is working but is not is worse than no alert.

A failed capture is a permanent hole. The quotes exist nowhere else, and they
cannot be reconstructed later from a fresh snapshot because that snapshot
carries the wrong timestamp. Record the gap; never backfill over it.

## What deploys never touch

`rsync --delete` would happily erase the archive, so these are excluded, and
rsync protects excluded paths from deletion:

- `data/` — the server's captures are the real ones
- `.env` — credentials live in `/etc/pitfield/env`
- `.venv/` — the laptop's is macOS; the box builds its own
- `logs/`, `node_modules/`, `site/dist/`

Never add `--delete-excluded`. That single flag deletes the archive.

## Known gap

The server will capture real chains into `data/` every session, and the vol
pages will keep showing synthetic surfaces, because `pipeline/run_day.py`
builds its archive from `synthetic_chain()` and nothing yet reads the captured
Parquet back out. `compute_day()` already accepts real rows — the missing piece
is the loader between them. Until that exists this deploy publishes a real
study, a real reading list and a real ephemeris, and an honestly-labelled
synthetic surface.

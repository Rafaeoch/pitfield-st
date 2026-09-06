#!/usr/bin/env bash
# One-time provisioning for a fresh Ubuntu 24.04 box. Run as root, once.
#   scp -r deploy root@SERVER:/tmp/ && ssh root@SERVER 'bash /tmp/deploy/bootstrap.sh archive.example.com'
#
# Ubuntu 24.04 specifically, because it ships Python 3.12 -- matching what this
# project is developed against. Debian 12 ships 3.11 and would be a gamble that
# nothing here uses 3.12 syntax.
#
# Idempotent: safe to re-run after changing the domain or the units.
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "usage: bootstrap.sh <domain>   (e.g. bootstrap.sh archive.pitfield.st)" >&2
  exit 64
fi
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Timezone -> America/New_York"
# The schedules are written in market-local time so tzdata handles DST. Change
# this and the capture window silently drifts off the close.
timedatectl set-timezone America/New_York

say "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-dev build-essential \
  git curl rsync ufw ca-certificates gnupg debian-keyring debian-archive-keyring \
  apt-transport-https unattended-upgrades
python3 --version

say "Node.js 22 (Astro needs 18+; the distro package lags)"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi
node --version

say "Caddy"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq
  apt-get install -y -qq caddy
fi
caddy version

say "Service account and directories"
id -u pitfield >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash pitfield
install -d -o pitfield -g pitfield /opt/pitfield
install -d -o pitfield -g pitfield /var/www/pitfield /var/www/pitfield/releases
install -d -o pitfield -g pitfield /var/log/pitfield
install -d -m 0750 -o root -g pitfield /etc/pitfield

say "Swap (insurance for the weekly bootstrap resampling on a 4GB box)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
free -h | head -3

say "Credentials file"
# Written empty. Real values are typed by the operator, never by tooling and
# never through a repository -- see deploy/README.md.
if [ ! -f /etc/pitfield/env ]; then
  cat > /etc/pitfield/env <<'ENVEOF'
# Pitfield St runtime credentials. Mode 0640, root:pitfield. Never committed.
# Fill these in by hand, then: systemctl start pitfield-capture.service
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
# Optional. Without it the paper summaries are a clean no-op.
ANTHROPIC_API_KEY=
# Optional. A Slack/Discord webhook that receives job failures.
PITFIELD_ALERT_WEBHOOK=
UNDERLYINGS=SPY
ENVEOF
  chmod 0640 /etc/pitfield/env
  chown root:pitfield /etc/pitfield/env
  echo "created /etc/pitfield/env (empty — fill it in)"
else
  echo "/etc/pitfield/env exists, left untouched"
fi

say "Firewall"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp  >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status verbose | head -8

say "Caddy site config for $DOMAIN"
sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/" "$(dirname "$0")/Caddyfile" > /etc/caddy/Caddyfile
# Serve something immediately so the TLS challenge has a docroot.
if [ ! -e /var/www/pitfield/current ]; then
  install -d -o pitfield -g pitfield /var/www/pitfield/releases/bootstrap
  echo '<!doctype html><title>Pitfield St</title><p>Provisioned. Awaiting first deploy.' \
    > /var/www/pitfield/releases/bootstrap/index.html
  ln -sfn /var/www/pitfield/releases/bootstrap /var/www/pitfield/current
  chown -h pitfield:pitfield /var/www/pitfield/current
fi
caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -2
systemctl reload caddy 2>/dev/null || systemctl restart caddy

say "systemd units"
install -m 0644 "$(dirname "$0")/systemd"/pitfield-*.service /etc/systemd/system/
install -m 0644 "$(dirname "$0")/systemd"/pitfield-*.timer   /etc/systemd/system/
systemctl daemon-reload
for u in pitfield-capture.service pitfield-weekly.service; do
  systemd-analyze verify "$u" && echo "  $u verified"
done
systemctl enable --now pitfield-capture.timer pitfield-weekly.timer
systemctl list-timers 'pitfield-*' --no-pager

say "Unattended security upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true

cat <<DONE

== Provisioned ==

Still to do, in order:
  1. Put the credentials in /etc/pitfield/env   (type them; do not paste a file)
  2. From your laptop:  ./deploy/deploy.sh root@$DOMAIN
  3. Point $DOMAIN's A record at this box, then Caddy issues TLS on first hit.

Timers are live. With an empty credentials file the capture exits cleanly and
records that it had no keys, rather than half-writing a session.
DONE

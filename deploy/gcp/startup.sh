#!/bin/bash
# Compute Engine metadata startup script; safe to rerun on every boot.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [[ ! -f /var/lib/xmr-bootstrap-complete ]]; then
  apt-get update
  apt-get install -y --no-install-recommends python3 python3-venv python3-pip nginx ca-certificates libgomp1
  touch /var/lib/xmr-bootstrap-complete
fi
id xmr >/dev/null 2>&1 || useradd --system --home-dir /var/lib/xmr --shell /usr/sbin/nologin xmr
install -d -o root -g root -m 0755 /opt/xmr /opt/xmr/releases /etc/xmr
install -d -o xmr -g xmr -m 0750 /var/lib/xmr
install -d -o xmr -g xmr -m 0755 /var/www/xmr
if [[ ! -x /opt/xmr/venv/bin/python ]]; then
  python3 -m venv /opt/xmr/venv
fi
systemctl enable nginx
echo 'XMR bootstrap ready; deploy an explicit source revision to start collection.'

#!/bin/sh
# Runs only as the forced command of the GitHub Actions trigger key (see
# /root/.ssh/authorized_keys), after the tests passed in CI. Pulls the
# repo (read-only deploy key), installs the fleet_ledger package with the
# dashboard's dependencies, syncs the dashboard, installs changed systemd units
# and restarts the service.
#
# The server runs its own copy, /opt/fleet-ledger/deploy.sh: a change here
# reaches the server only when that copy is replaced by hand.
#
# --exclude backend/.env: this file holds the real spreadsheet link and
# credentials path, lives only on the server, is not in git, and must
# never be touched by --delete (it was wiped once by an earlier version
# of this script — do not remove this line).
set -e
# Lock the whole update, including pip/rsync: a running recalculation may still
# import modules. Keep the same inode used by recalc.py.
LOCK=/opt/fleet-ledger/outputs/runs/recalc.lock
install -d -o dash -g dash /opt/fleet-ledger/outputs/runs
# Open without truncating/replacing an existing lock; fix owner on first deploy.
exec 9>>"$LOCK"
chown dash:dash "$LOCK"
flock -w 900 9 || { echo "recalc still running after 15 min, deploy postponed" >&2; exit 1; }
cd /opt/fleet-ledger/repo
git pull --ff-only
SRC=/opt/fleet-ledger/repo

# The calculation package, also imported by the dashboard. Installed as a copy,
# not linked, so the next git pull never changes running code.
/opt/fleet-ledger/venv/bin/pip install -q --no-cache-dir "$SRC[dashboard]"
rsync -a --delete \
  --exclude '__pycache__' --exclude '.playwright-mcp' --exclude '*.png' --exclude '.DS_Store' \
  --exclude 'backend/.env' \
  "$SRC/dashboard/" /opt/fleet-ledger/dashboard/
install -d -o dash -g dash /opt/fleet-ledger/outputs /opt/fleet-ledger/outputs/runs /opt/fleet-ledger/backups
chown -R dash:dash /opt/fleet-ledger/dashboard

# systemd units live in the repo: install the ones that changed, keep timers enabled.
# With Persistent=true, daemon-reload after a schedule change may fire a "missed" run at
# once, while this script holds the lock and restarts the dashboard: the recalc service
# retries until the dashboard answers, so that run waits for the deploy instead of failing.
reload=0
for unit in "$SRC"/deploy/*.service "$SRC"/deploy/*.timer; do
  target=/etc/systemd/system/$(basename "$unit")
  if ! cmp -s "$unit" "$target"; then
    install -m 644 "$unit" "$target"
    reload=1
  fi
done
if [ "$reload" = 1 ]; then
  systemctl daemon-reload
fi
for timer in "$SRC"/deploy/*.timer; do
  systemctl enable --quiet "$(basename "$timer")"
done

systemctl restart dashboard
sleep 5
curl -sf -m 5 http://127.0.0.1:8000/api/health >/dev/null || { echo "health check failed after deploy" >&2; exit 1; }
echo "deployed $(git rev-parse --short HEAD)"

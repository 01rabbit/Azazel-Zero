#!/bin/bash
# Re-exec under bash if not already
[ -n "${BASH_VERSION:-}" ] || exec /bin/bash "$0" "$@"
# Azazel-Zero: Suricata minimal rules updater (quiet profile)
# Goal: fetch tiny ruleset (DNS + SCAN only), avoid flowbit warnings and bulk distro rules.
# Usage: sudo bin/suricata_update.sh
set -euo pipefail

log() { printf "[suricata_update:min] %s\n" "$*"; }
warn() { printf "[suricata_update:min][WARN] %s\n" "$*" 1>&2; }

command -v suricata-update >/dev/null || { warn "suricata-update not found"; exit 1; }
command -v suricata >/dev/null || { warn "suricata not found"; exit 1; }

log "Stopping suricata (if running)"
sudo systemctl stop suricata 2>/dev/null || true

log "Cleaning previous rule cache"
sudo rm -f /var/lib/suricata/rules/suricata.rules || true
sudo rm -rf /var/lib/suricata/update/* || true

log "Refresh source index & disable ET/Open full set"
sudo suricata-update update-sources || true
sudo suricata-update disable-source et/open 2>/dev/null || true

SURI_VER_MM=$(suricata -V 2>&1 | sed -n 's/.* \([0-9]\+\.[0-9]\+\)\..*/\1/p' | head -n1)
SURI_VER_MM=${SURI_VER_MM:-6.0}
BASE="https://rules.emergingthreats.net/open/suricata-${SURI_VER_MM}/rules"

log "Fetching minimal ET Open ruleset (DNS + SCAN) for ${SURI_VER_MM}"
sudo suricata-update \
  --url "${BASE}/emerging-dns.rules" \
  --url "${BASE}/emerging-scan.rules"

# ensure rules produced
RULE_OUT="/var/lib/suricata/rules/suricata.rules"
if ! [ -s "$RULE_OUT" ]; then
  warn "No compiled rules produced. Check network or URLs."
  exit 2
fi

# link for YAMLs expecting /etc/suricata/rules
ALT_DIR="/etc/suricata/rules"
sudo install -d "$ALT_DIR"
sudo ln -sf "$RULE_OUT" "$ALT_DIR/suricata.rules"
log "Linked $RULE_OUT -> $ALT_DIR/suricata.rules"

warn "To keep output quiet, ensure YAML points to only 'suricata.rules'. Run: sudo bin/suricata_yaml_minify.sh"

log "Testing Suricata configuration"
if ! sudo suricata -T -c /etc/suricata/suricata.yaml -v; then
  warn "Test failed. Run 'sudo bin/suricata_yaml_minify.sh' to restrict rule-files, then retry."
  exit 3
fi

log "Starting Suricata"
sudo systemctl enable --now suricata
log "Done. Minimal rules active."

#!/bin/bash
# Normalize /etc/suricata/suricata.yaml to only load /var/lib/suricata/rules/suricata.rules
set -euo pipefail

YAML="/etc/suricata/suricata.yaml"
BACKUP="/etc/suricata/suricata.yaml.azazel.bak.$(date +%Y%m%d%H%M%S)"

sudo cp -a "$YAML" "$BACKUP"
echo "[yaml_minify] backup: $BACKUP"

# 1) set default-rule-path
sudo sed -i 's|^\s*default-rule-path:.*|default-rule-path: /var/lib/suricata/rules|' "$YAML"

# 2) replace rule-files block with single entry
#    from the line starting with 'rule-files:' up to the next top-level key (no indent), replace block
sudo awk '
BEGIN{inblock=0}
/^[[:space:]]*rule-files:[[:space:]]*$/ {print "rule-files:\n  - suricata.rules"; inblock=1; next}
/^[^[:space:]]/ { if(inblock){inblock=0} }
{ if(!inblock) print $0 }
' "$YAML" | sudo tee "$YAML.tmp" >/dev/null
sudo mv "$YAML.tmp" "$YAML"

# 3) sanity test and restart
sudo suricata -T -c "$YAML" -v
sudo systemctl restart suricata

echo "[yaml_minify] Applied. Suricata now loads only /var/lib/suricata/rules/suricata.rules"
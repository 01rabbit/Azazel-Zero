#!/bin/bash
# Normalize /etc/suricata/suricata.yaml to only load /var/lib/suricata/rules/suricata.rules
set -euo pipefail

YAML="/etc/suricata/suricata.yaml"
BACKUP="/etc/suricata/suricata.yaml.azazel.bak.$(date +%Y%m%d%H%M%S)"

sudo cp -a "$YAML" "$BACKUP"
echo "[yaml_minify] backup: $BACKUP"

# 1) set default-rule-path
sudo sed -i 's|^[[:space:]]*default-rule-path:.*|default-rule-path: /var/lib/suricata/rules|' "$YAML"

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
if ! command -v suricata >/dev/null; then
  echo "[yaml_minify][WARN] suricata not installed; skipping test/restart" >&2
  exit 0
fi
sudo suricata -T -c "$YAML"
sudo systemctl restart suricata

echo "[yaml_minify] Applied. Suricata now loads only /var/lib/suricata/rules/suricata.rules"
#!/bin/bash
# Re-exec under bash if not already
[ -n "${BASH_VERSION:-}" ] || exec /bin/bash "$0" "$@"

# -----------------------------------------------------------------------------
# Azazel‑Zero: Suricata YAML Minifier (cross-device, Pi Zero 2 W / Azazel‑Pi)
# Purpose:
#   Normalize `/etc/suricata/suricata.yaml` so Suricata loads **one rules file**
#   only (default: /var/lib/suricata/rules/suricata.rules). This reduces noise,
#   avoids flowbit warnings from mixed rule sets, and improves reproducibility.
#
# Options:
#   --yaml <path>                 # YAML path (default: /etc/suricata/suricata.yaml)
#   --default-rule-path <path>    # default-rule-path value (default: /var/lib/suricata/rules)
#   --rule-file <name>            # rule-files entry name (default: suricata.rules)
#   --backup-dir <dir>            # where to place backup (default: /etc)
#   --no-test                     # skip `suricata -T`
#   --no-restart                  # skip systemctl restart
#   -h | --help                   # show usage and exit
#
# Examples:
#   sudo bin/suricata_yaml_minify.sh
#   sudo bin/suricata_yaml_minify.sh --yaml=/etc/suricata/suricata.yaml
#   sudo bin/suricata_yaml_minify.sh --rule-file=suricata.rules --no-test
#
# See also:
#   bin/suricata_update.sh  # fetch minimal rules (profiles: minimal/standard/extended)
# -----------------------------------------------------------------------------

set -euo pipefail

# Defaults
YAML="/etc/suricata/suricata.yaml"
DEFAULT_RULE_PATH="/var/lib/suricata/rules"
RULE_FILE="suricata.rules"
BACKUP_DIR="/etc"
DO_TEST=1
DO_RESTART=1

log(){ printf "[yaml_minify] %s\n" "$*"; }
warn(){ printf "[yaml_minify][WARN] %s\n" "$*" 1>&2; }
die(){ printf "[yaml_minify][ERROR] %s\n" "$*" 1>&2; exit 1; }

usage(){ cat <<'USAGE'
Usage: suricata_yaml_minify.sh [--yaml PATH] [--default-rule-path PATH] [--rule-file NAME] [--backup-dir DIR] [--no-test] [--no-restart]

Normalize Suricata YAML to load a single rules file for reproducible, quiet operation.
USAGE
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yaml)                 shift; YAML="${1:-$YAML}" ;;
    --yaml=*)               YAML="${1#*=}" ;;
    --default-rule-path)    shift; DEFAULT_RULE_PATH="${1:-$DEFAULT_RULE_PATH}" ;;
    --default-rule-path=*)  DEFAULT_RULE_PATH="${1#*=}" ;;
    --rule-file)            shift; RULE_FILE="${1:-$RULE_FILE}" ;;
    --rule-file=*)          RULE_FILE="${1#*=}" ;;
    --backup-dir)           shift; BACKUP_DIR="${1:-$BACKUP_DIR}" ;;
    --backup-dir=*)         BACKUP_DIR="${1#*=}" ;;
    --no-test)              DO_TEST=0 ;;
    --no-restart)           DO_RESTART=0 ;;
    -h|--help)              usage; exit 0 ;;
    *)                      warn "Unknown option: $1" ;;
  esac
  shift
done

# Sanity
[[ -f "$YAML" ]] || die "YAML not found: $YAML"
sudo install -d "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/$(basename "$YAML").azazel.bak.$(date +%Y%m%d%H%M%S)"
sudo cp -a "$YAML" "$BACKUP"
log "backup: $BACKUP"

# 1) Set default-rule-path (insert if missing)
if grep -q '^[[:space:]]*default-rule-path:' "$YAML"; then
  sudo sed -i "s|^[[:space:]]*default-rule-path:.*|default-rule-path: ${DEFAULT_RULE_PATH}|" "$YAML"
else
  printf '\ndefault-rule-path: %s\n' "$DEFAULT_RULE_PATH" | sudo tee -a "$YAML" >/dev/null
fi

# 2) Replace or insert rule-files block with single entry
if grep -q '^[[:space:]]*rule-files:[[:space:]]*$' "$YAML"; then
  # Replace existing block
  sudo awk -v rf="$RULE_FILE" '
  BEGIN{inblock=0}
  /^[[:space:]]*rule-files:[[:space:]]*$/ {print "rule-files:\n  - " rf; inblock=1; next}
  /^[^[:space:]]/ { if(inblock){inblock=0} }
  { if(!inblock) print $0 }
  ' "$YAML" | sudo tee "$YAML.tmp" >/dev/null
  sudo mv "$YAML.tmp" "$YAML"
else
  # Insert new block at the end (top-level)
  printf '\nrule-files:\n  - %s\n' "$RULE_FILE" | sudo tee -a "$YAML" >/dev/null
fi

# 3) Sanity test (optional) and restart
if command -v suricata >/dev/null; then
  if [[ $DO_TEST -eq 1 ]]; then
    log "Testing Suricata configuration"
    if ! sudo suricata -T -c "$YAML"; then
      warn "suricata -T failed. Please inspect $YAML or run bin/suricata_update.sh to regenerate rules."
      exit 2
    fi
  else
    warn "Skipping suricata -T per --no-test"
  fi
else
  warn "suricata not installed; skipping test"
fi

if [[ $DO_RESTART -eq 1 ]] && command -v systemctl >/dev/null; then
  log "Restarting Suricata"
  sudo systemctl restart suricata || sudo systemctl try-restart suricata || true
else
  warn "Skipping restart per --no-restart or missing systemctl"
fi

log "Applied. Suricata now loads only ${DEFAULT_RULE_PATH}/${RULE_FILE}"
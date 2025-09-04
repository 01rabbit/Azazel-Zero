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
sudo suricata -T -c "$YAML" -v
sudo systemctl restart suricata

echo "[yaml_minify] Applied. Suricata now loads only /var/lib/suricata/rules/suricata.rules"
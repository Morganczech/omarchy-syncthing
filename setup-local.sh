#!/usr/bin/env bash
# Initialize a new user instance with local-only defaults.
set -euo pipefail
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/syncthing"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/syncthing"
for directory in "$state_dir" "$config_dir" "$HOME/.syncthing"; do
  if [[ -e "$directory/config.xml" ]]; then
    echo "Existing Syncthing configuration at $directory/config.xml. Review it in Syncthing." >&2
    exit 1
  fi
done

umask 077
STNODEFAULTFOLDER=1 syncthing generate --home="$state_dir"
python3 -I -B - "$state_dir/config.xml" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

path = Path(sys.argv[1])
tree = ET.parse(path)
root = tree.getroot()
options = root.find("options")
for tag, value in {
    "globalAnnounceEnabled": "false",
    "relaysEnabled": "false",
    "natEnabled": "false",
    "localAnnounceEnabled": "true",
    "limitBandwidthInLan": "true",
}.items():
    node = options.find(tag)
    if node is None:
        node = ET.SubElement(options, tag)
    node.text = value

for folder in root.findall("folder"):
    root.remove(folder)

defaults = root.find("./defaults/folder")
if defaults is not None:
    versioning = defaults.find("versioning")
    if versioning is None:
        versioning = ET.SubElement(defaults, "versioning")
    versioning.set("type", "staggered")

temporary = path.with_suffix(".tmp")
tree.write(temporary, encoding="utf-8", xml_declaration=True)
temporary.replace(path)
PY
systemctl --user enable --now syncthing.service

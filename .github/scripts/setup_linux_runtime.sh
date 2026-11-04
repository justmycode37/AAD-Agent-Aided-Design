#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y libosmesa6 bubblewrap apparmor-profiles

# Ubuntu mediates unprivileged user namespaces through this distribution
# profile. Load it for bwrap without disabling the host-wide AppArmor policy.
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict

# Fail setup immediately if the namespaces used by model workers cannot start.
bwrap --unshare-all --die-with-parent --new-session --cap-drop ALL \
  --ro-bind / / --proc /proc --dev /dev -- /usr/bin/true

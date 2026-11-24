#!/usr/bin/env bash
# install-pve-on-debian.sh — Converts Debian 13 (trixie) to Proxmox VE 9.x.
# Official procedure: https://pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie
# Idempotent: phase 1 = PVE kernel + reboot; phase 2 = PVE packages + bridge + final reboot.
set -euo pipefail

log() { printf '\e[1;34m[pve-install]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[error]\e[0m %s\n' "$*" >&2; exit 1; }

. /etc/os-release
[ "${VERSION_CODENAME:-}" = "trixie" ] || die "Debian 13 (trixie) required, found: ${VERSION_CODENAME:-?}"
[ "$(id -u)" -eq 0 ] || die "Must be run as root."

export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------- /etc/hosts ---
IP="$(hostname -I | awk '{print $1}')"
FQDN="$(hostname -f)"
sed -i '/^127\.0\.1\.1/d' /etc/hosts
if ! grep -qE "[[:space:]]${FQDN}" /etc/hosts; then
    log "Adding ${IP} ${FQDN} to /etc/hosts"
    echo "${IP} ${FQDN} $(hostname -s)" >> /etc/hosts
fi
[ "$(hostname --ip-address)" != "127.0.1.1" ] || die "The hostname does not resolve to a non-loopback IP."

# ----------------------------------------------------------------- repos ---
log "Adding the PVE repository (deb822, pve-no-subscription)"
cat > /etc/apt/sources.list.d/pve-install-repo.sources <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF

if [ ! -f /usr/share/keyrings/proxmox-archive-keyring.gpg ]; then
    log "Importing the Proxmox keyring (trixie)"
    wget -qO /usr/share/keyrings/proxmox-archive-keyring.gpg \
        https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg
fi

log "apt update + full-upgrade"
apt-get update -qq
apt-get -y -qq full-upgrade

# ----------------------------------------------------- phase 1: kernel ---
if ! uname -r | grep -q -- '-pve'; then
    log "Phase 1: installing the Proxmox kernel (proxmox-default-kernel)"
    apt-get -y -qq install proxmox-default-kernel
    log "Rebooting in 5 s — rerun the script afterwards (phase 2)."
    sleep 5
    systemctl reboot
    exit 0
fi
log "PVE kernel active: $(uname -r)"

# ----------------------------------------------------- phase 2: packages ---
log "Installing proxmox-ve (several minutes)"
apt-get -y -qq install proxmox-ve postfix open-iscsi chrony

log "Removing the Debian kernel (required) and os-prober"
apt-get -y -qq remove linux-image-amd64 'linux-image-6.12*' >/dev/null || true
apt-get -y -qq remove os-prober >/dev/null || true
update-grub >/dev/null 2>&1 || true

# enterprise repositories are useless in a lab (.list and .sources formats)
rm -f /etc/apt/sources.list.d/pve-enterprise.list \
      /etc/apt/sources.list.d/pve-enterprise.sources \
      /etc/apt/sources.list.d/ceph.list \
      /etc/apt/sources.list.d/ceph.sources
apt-get update -qq

# ---------------------------------------------------------- bridge vmbr0 ---
log "Configuring the vmbr0 bridge"
IFACE="$(ip -o route show default | awk '{print $5; exit}')"
[ -n "$IFACE" ] || die "Default interface not found."
cp -n /etc/network/interfaces /etc/network/interfaces.pre-pve 2>/dev/null || true

if grep -qE "iface ${IFACE} inet static" /etc/network/interfaces.pre-pve 2>/dev/null; then
    ADDR="$(ip -4 addr show dev "$IFACE" | awk '/inet /{print $2; exit}')"
    GW="$(ip -o route show default | awk '{print $3; exit}')"
    cat > /etc/network/interfaces <<EOF
auto lo
iface lo inet loopback

iface ${IFACE} inet manual

auto vmbr0
iface vmbr0 inet static
	address ${ADDR}
	gateway ${GW}
	bridge-ports ${IFACE}
	bridge-stp off
	bridge-fd 0
EOF
else
    cat > /etc/network/interfaces <<EOF
auto lo
iface lo inet loopback

iface ${IFACE} inet manual

auto vmbr0
iface vmbr0 inet dhcp
	bridge-ports ${IFACE}
	bridge-stp off
	bridge-fd 0
EOF
fi

ifreload -a 2>/dev/null || log "ifreload failed — the bridge will be active after reboot."

# ------------------------------------------------------------------ end ---
log "Installed version: $(pveversion)"
log "Reminder: change the root password and configure 2FA in the web UI."

log "Final reboot in 5 s (PVE kernel + bridge)..."
sleep 5
systemctl reboot

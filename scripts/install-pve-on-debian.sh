#!/usr/bin/env bash
# install-pve-on-debian.sh — Convertit une Debian 13 (trixie) en Proxmox VE 9.x.
# Procédure officielle : https://pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie
# Idempotent : phase 1 = kernel PVE + reboot ; phase 2 = paquets PVE + bridge + reboot final.
set -euo pipefail

log() { printf '\e[1;34m[pve-install]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[erreur]\e[0m %s\n' "$*" >&2; exit 1; }

. /etc/os-release
[ "${VERSION_CODENAME:-}" = "trixie" ] || die "Debian 13 (trixie) requis, trouvé : ${VERSION_CODENAME:-?}"
[ "$(id -u)" -eq 0 ] || die "À exécuter en root."

export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------- /etc/hosts ---
IP="$(hostname -I | awk '{print $1}')"
FQDN="$(hostname -f)"
sed -i '/^127\.0\.1\.1/d' /etc/hosts
if ! grep -qE "[[:space:]]${FQDN}" /etc/hosts; then
    log "Ajout de ${IP} ${FQDN} dans /etc/hosts"
    echo "${IP} ${FQDN} $(hostname -s)" >> /etc/hosts
fi
[ "$(hostname --ip-address)" != "127.0.1.1" ] || die "Le hostname ne résout pas vers une IP non-loopback."

# ----------------------------------------------------------------- repos ---
log "Ajout du dépôt PVE (deb822, pve-no-subscription)"
cat > /etc/apt/sources.list.d/pve-install-repo.sources <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF

if [ ! -f /usr/share/keyrings/proxmox-archive-keyring.gpg ]; then
    log "Import du keyring Proxmox (trixie)"
    wget -qO /usr/share/keyrings/proxmox-archive-keyring.gpg \
        https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg
fi

log "apt update + full-upgrade"
apt-get update -qq
apt-get -y -qq full-upgrade

# ----------------------------------------------------- phase 1 : kernel ---
if ! uname -r | grep -q -- '-pve'; then
    log "Phase 1 : installation du kernel Proxmox (proxmox-default-kernel)"
    apt-get -y -qq install proxmox-default-kernel
    log "Reboot dans 5 s — relancer le script ensuite (phase 2)."
    sleep 5
    systemctl reboot
    exit 0
fi
log "Kernel PVE actif : $(uname -r)"

# ----------------------------------------------------- phase 2 : paquets ---
log "Installation de proxmox-ve (plusieurs minutes)"
apt-get -y -qq install proxmox-ve postfix open-iscsi chrony

log "Retrait du kernel Debian (requis) et d'os-prober"
apt-get -y -qq remove linux-image-amd64 'linux-image-6.12*' >/dev/null || true
apt-get -y -qq remove os-prober >/dev/null || true
update-grub >/dev/null 2>&1 || true

# dépôts enterprise inutiles en lab (formats .list et .sources)
rm -f /etc/apt/sources.list.d/pve-enterprise.list \
      /etc/apt/sources.list.d/pve-enterprise.sources \
      /etc/apt/sources.list.d/ceph.list \
      /etc/apt/sources.list.d/ceph.sources
apt-get update -qq

# ---------------------------------------------------------- bridge vmbr0 ---
log "Configuration du bridge vmbr0"
IFACE="$(ip -o route show default | awk '{print $5; exit}')"
[ -n "$IFACE" ] || die "Interface par défaut introuvable."
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

ifreload -a 2>/dev/null || log "ifreload a échoué — le bridge sera actif au reboot."

# ------------------------------------------------------------------ fin ---
log "Version installée : $(pveversion)"
log "Rappel : changer le mot de passe root et configurer la 2FA dans l'UI web."

log "Reboot final dans 5 s (kernel PVE + bridge)..."
sleep 5
systemctl reboot

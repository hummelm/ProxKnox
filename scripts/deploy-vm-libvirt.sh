#!/usr/bin/env bash
# deploy-vm-libvirt.sh — Système automatique complet (machine Linux locale) :
#   1. VM KVM/libvirt via virt-install
#   2. Debian 13 préseedée, partitionnement conforme CIS 1.1
#   3. Conversion Debian 13 -> Proxmox VE 9.2 (install-pve-on-debian.sh, 2 phases)
#   4. Hardening CIS Level 2 via Ansible Lockdown DEBIAN13-CIS + audit syver
# Usage : adapter la config puis ./deploy-vm-libvirt.sh [--skip-ansible]
set -euo pipefail

# ------------------------------------------------------------------ config ---
VM_NAME="${VM_NAME:-proxknox-lab}"
VM_VCPUS="${VM_VCPUS:-4}"
VM_RAM="${VM_RAM:-8192}"
VM_DISK_GB="${VM_DISK_GB:-64}"
LIBVIRT_NET="${LIBVIRT_NET:-default}"      # virbr0 (NAT 192.168.122.0/24)
HTTP_PORT="${HTTP_PORT:-8090}"             # port servant le preseed sur virbr0

DEB_VERSION="${DEB_VERSION:-13.7.0}"       # netinst Debian 13 (trixie)
DEB_ISO_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-${DEB_VERSION}-amd64-netinst.iso"

ADMIN_USER="${ADMIN_USER:-adminlab}"
ADMIN_PW="${ADMIN_PW:-ChangeMeAdmin!}"
ROOT_PASSWORD="${ROOT_PASSWORD:-ChangeMeRoot!}"
SSH_PUBKEY_FILE="${SSH_PUBKEY_FILE:-${HOME}/.ssh/id_ed25519.pub}"
TIMEZONE="${TIMEZONE:-Europe/Paris}"
KEYB="${KEYB:-fr}"
WORKDIR="${WORKDIR:-${HOME}/.cache/proxknox}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PVE_CONVERT="${SCRIPT_DIR}/install-pve-on-debian.sh"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes"

# ---------------------------------------------------------------- helpers ---
log()  { printf '\e[1;34m[proxknox]\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m[proxknox]\e[0m %s\n' "$*"; }
die()  { printf '\e[1;31m[erreur]\e[0m %s\n' "$*" >&2; exit 1; }

SKIP_ANSIBLE=0
[ "${1:-}" = "--skip-ansible" ] && SKIP_ANSIBLE=1

[ -f "$SSH_PUBKEY_FILE" ] || die "Clé publique introuvable : $SSH_PUBKEY_FILE"
[ -f "$PVE_CONVERT" ] || die "Script de conversion introuvable : $PVE_CONVERT"
SSH_PUBKEY="$(cat "$SSH_PUBKEY_FILE")"

# ------------------------------------------------------- 0. prérequis hôte ---
log "Vérification des prérequis (libvirt, virt-install, KVM imbriqué)"
MISSING=""
for cmd in virsh virt-install python3 git ssh; do
    command -v "$cmd" >/dev/null || MISSING="$MISSING $cmd"
done
[ -z "$MISSING" ] || warn "Commandes manquantes :$MISSING — installer (ex. Debian/Ubuntu : apt install qemu-kvm libvirt-daemon-system libvirt-clients virtinst ovmf git)"
if [ -f /sys/module/kvm_intel/parameters/nested ]; then
    grep -q Y /sys/module/kvm_intel/parameters/nested || \
        warn "KVM imbriqué (Intel) désactivé — Proxmox imbriqué sera lent. Activer : echo 'options kvm-intel nested=Y' > /etc/modprobe.d/kvm-nested.conf && modprobe -r kvm_intel && modprobe kvm_intel"
elif [ -f /sys/module/kvm_amd/parameters/nested ]; then
    grep -q 1 /sys/module/kvm_amd/parameters/nested || \
        warn "KVM imbriqué (AMD) désactivé — voir la doc libvirt pour l'activer."
fi
virsh -c qemu:///system net-info "$LIBVIRT_NET" >/dev/null 2>&1 \
    || die "Réseau libvirt '${LIBVIRT_NET}' introuvable (virsh -c qemu:///system net-list --all)."

# ------------------------------------------------------- 1. ISO + preseed ---
mkdir -p "$WORKDIR"
ISO_PATH="${WORKDIR}/debian-${DEB_VERSION}-amd64-netinst.iso"
if [ ! -f "$ISO_PATH" ]; then
    log "Téléchargement de l'ISO Debian ${DEB_VERSION} netinst"
    wget -q -O "$ISO_PATH" "$DEB_ISO_URL"
fi

log "Génération du preseed (partitionnement CIS 1.1 + admin sudo + clé SSH)"
sed -e "s|__KEYB__|${KEYB}|g" \
    -e "s|__VMNAME__|${VM_NAME}|g" \
    -e "s|__TIMEZONE__|${TIMEZONE}|g" \
    -e "s|__ROOTPW__|${ROOT_PASSWORD}|g" \
    -e "s|__ADMIN_USER__|${ADMIN_USER}|g" \
    -e "s|__ADMIN_PW__|${ADMIN_PW}|g" \
    -e "s|__SSH_PUBKEY__|${SSH_PUBKEY}|g" \
    "${REPO_DIR}/preseed/debian13-cis.cfg" > "${WORKDIR}/debian13-cis.cfg"

log "Service HTTP du preseed sur virbr0 (port ${HTTP_PORT})"
HOST_IP_ON_NET="$(ip -4 -o addr show | awk -v n="virbr0" '$2==n{split($4,a,"/");print a[1];exit}')"
[ -n "$HOST_IP_ON_NET" ] || die "IP hôte sur virbr0 introuvable."
python3 -m http.server "$HTTP_PORT" --bind "$HOST_IP_ON_NET" --directory "$WORKDIR" >/dev/null 2>&1 &
HTTP_PID=$!
trap 'kill $HTTP_PID 2>/dev/null || true' EXIT
sleep 1

# ------------------------------------------------------------ 2. la VM ---
OS_VARIANT="$(osinfo-query os 2>/dev/null | awk -F- '/^ *debian-1[23]/{print $1; exit}' | tr -d ' ' || true)"
[ -n "$OS_VARIANT" ] || OS_VARIANT="debian12"
log "Création de la VM ${VM_NAME} (os-variant: ${OS_VARIANT}, UEFI, CPU host-passthrough)"

virsh -c qemu:///system destroy "$VM_NAME" >/dev/null 2>&1 || true
virsh -c qemu:///system undefine "$VM_NAME" --nvram --remove-all-storage >/dev/null 2>&1 || true

virt-install \
    --connect qemu:///system \
    --name "$VM_NAME" \
    --memory "$VM_RAM" \
    --vcpus "$VM_VCPUS" \
    --cpu host-passthrough \
    --machine q35 \
    --boot uefi \
    --disk "size=${VM_DISK_GB},bus=virtio,format=qcow2" \
    --network "network=${LIBVIRT_NET},model=virtio" \
    --graphics vnc,listen=127.0.0.1 \
    --video virtio \
    --os-variant "$OS_VARIANT" \
    --location "$ISO_PATH" \
    --extra-args "auto=true priority=critical preseed/url=http://${HOST_IP_ON_NET}:${HTTP_PORT}/debian13-cis.cfg" \
    --noautoconsole

# ------------------------------------------------- 3. attente IP + SSH ---
log "Installation Debian en cours (10-20 min). Attente de l'IP DHCP..."
VM_MAC="$(virsh -c qemu:///system domiflist "$VM_NAME" | awk '/ virtio /{print $5; exit}')"
[ -n "$VM_MAC" ] || die "MAC de la VM introuvable."

VM_IP=""
for _ in $(seq 1 80); do
    VM_IP="$(virsh -c qemu:///system net-dhcp-leases "$LIBVIRT_NET" 2>/dev/null \
        | awk -v m="$VM_MAC" 'tolower($1)==tolower(m){print $6; exit}')"
    [ -n "$VM_IP" ] && break
    sleep 30
done
[ -n "$VM_IP" ] || die "IP DHCP non obtenue après 40 min."

log "IP de la VM : ${VM_IP}. Attente de SSH..."
SSH_OK=0
for _ in $(seq 1 40); do
    if ssh $SSH_OPTS "root@${VM_IP}" true 2>/dev/null; then SSH_OK=1; break; fi
    sleep 30
done
[ "$SSH_OK" = 1 ] || die "SSH injoignable sur ${VM_IP} après l'installation."

# ------------------------------------------- 4. conversion Debian -> PVE ---
log "Conversion Debian 13 -> Proxmox VE 9.2 — phase 1 (kernel PVE + reboot)"
ssh $SSH_OPTS "root@${VM_IP}" 'bash -s' < "$PVE_CONVERT" \
    || die "Échec conversion phase 1 — console : virsh -c qemu:///system console ${VM_NAME}"

log "Attente du reboot sur le kernel PVE..."
sleep 45
SSH_OK=0
for _ in $(seq 1 20); do
    if ssh $SSH_OPTS "root@${VM_IP}" true 2>/dev/null; then SSH_OK=1; break; fi
    sleep 15
done
[ "$SSH_OK" = 1 ] || die "SSH injoignable après le reboot phase 1."

log "Conversion — phase 2 (proxmox-ve + bridge vmbr0 + reboot final, ~10-15 min)"
ssh $SSH_OPTS "root@${VM_IP}" 'bash -s' < "$PVE_CONVERT" \
    || die "Échec conversion phase 2."
sleep 45
SSH_OK=0
for _ in $(seq 1 20); do
    if ssh $SSH_OPTS "root@${VM_IP}" true 2>/dev/null; then SSH_OK=1; break; fi
    sleep 15
done
[ "$SSH_OK" = 1 ] || die "SSH injoignable après le reboot final."

# ----------------------------------------------- 5. inventaire + Ansible ---
log "Génération de l'inventaire Ansible (admin: ${ADMIN_USER})"
mkdir -p "${REPO_DIR}/inventory"
cat > "${REPO_DIR}/inventory/hosts.yml" <<EOF
all:
  children:
    pve_nodes:
      hosts:
        ${VM_NAME}:
          ansible_host: ${VM_IP}
          ansible_user: ${ADMIN_USER}
          ansible_become: true
EOF

if [ "$SKIP_ANSIBLE" = 1 ]; then
    log "--skip-ansible : lancer manuellement ensuite :"
    log "  ansible-galaxy install -r requirements.yml"
    log "  ansible-playbook -i inventory/hosts.yml site.yml"
    exit 0
fi

log "Installation des rôles Ansible Lockdown"
ansible-galaxy install -r "${REPO_DIR}/requirements.yml" --force

log "Hardening CIS Level 2 (Lockdown DEBIAN13-CIS) + audit syver — ~20-40 min"
cd "$REPO_DIR"
ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook -i inventory/hosts.yml site.yml \
    || die "Playbook Lockdown en échec — relancer avec --start-at-task ou vérifier les logs."

log "Terminé."
log "  UI Proxmox     : https://${VM_IP}:8006 (root / realm PAM)"
log "  SSH            : ssh ${ADMIN_USER}@${VM_IP} (root SSH désactivé par CIS)"
log "  Rapports audit : ${REPO_DIR}/audit-summaries/ et /opt sur la VM"

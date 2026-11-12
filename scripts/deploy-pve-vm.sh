#!/usr/bin/env bash
# deploy-pve-vm.sh — Voie B (rapide) : VM Proxmox VE 9.2 auto-installée (answer file).
# Prérequis : hôte PVE avec proxmox-auto-install-assistant (PVE 8.4+/9.x), accès SSH root.
# NOTE : le partitionneur PVE ne permet pas les partitions séparées CIS 1.1 —
# préférer deploy-debian-vm.sh (Debian 13 préseedé) pour une base conforme CIS L2.
# Usage : adapter les variables puis ./deploy-pve-vm.sh

set -euo pipefail

# ------------------------------------------------------------------ config ---
PVE_HOST="${PVE_HOST:-192.168.1.10}"          # hôte Proxmox existant
PVE_SSH_PORT="${PVE_SSH_PORT:-22}"
VMID="${VMID:-9001}"
VM_NAME="${VM_NAME:-pve-cis-lab}"
STORAGE="${STORAGE:-local-lvm}"               # stockage du disque
ISO_STORAGE="${ISO_STORAGE:-local}"           # stockage des ISO
BRIDGE="${BRIDGE:-vmbr0}"
VLAN="${VLAN:-}"                              # vide = pas de tag VLAN
DISK_GB="${DISK_GB:-64}"
RAM_MB="${RAM_MB:-8192}"
CORES="${CORES:-4}"
PVE_VERSION_ISO="${PVE_VERSION_ISO:-9.2-1}"   # ISO proxmox-ve_<version>.iso
ROOT_PASSWORD="${ROOT_PASSWORD:-ChangeMeCISLab!}"
SSH_PUBKEY_FILE="${SSH_PUBKEY_FILE:-${HOME}/.ssh/id_ed25519.pub}"
TIMEZONE="${TIMEZONE:-Europe/Paris}"
KEYB="${KEYB:-fr}"
FQDN="${FQDN:-${VM_NAME}.lab.local}"
WORKDIR="${WORKDIR:-/tmp/pve-autoinstall}"

# ---------------------------------------------------------------- helpers ---
log()  { printf '\e[1;34m[deploy]\e[0m %s\n' "$*"; }
die()  { printf '\e[1;31m[erreur]\e[0m %s\n' "$*" >&2; exit 1; }

pve() { ssh -p "$PVE_SSH_PORT" "root@${PVE_HOST}" "$*"; }

[ -f "$SSH_PUBKEY_FILE" ] || die "Clé publique introuvable : $SSH_PUBKEY_FILE"
SSH_PUBKEY="$(cat "$SSH_PUBKEY_FILE")"

# --------------------------------------------------------- 1. answer file ---
log "Génération de l'answer file sur ${PVE_HOST}"
pve "mkdir -p ${WORKDIR}"

pve "cat > ${WORKDIR}/answer.toml <<'EOF'
[global]
keyboard = \"${KEYB}\"
country = \"FR\"
fqdn = \"${FQDN}\"
mailto = \"root@localhost\"
timezone = \"${TIMEZONE}\"
root-password = \"${ROOT_PASSWORD}\"
root-ssh-keys = [\"${SSH_PUBKEY}\"]

[network]
source = \"dhcp\"

[disk-setup]
filesystem = \"ext4\"
disk = \"/dev/sda\"
EOF"

# ------------------------------------------------------- 2. ISO Proxmox ---
ISO_NAME="proxmox-ve_${PVE_VERSION_ISO}.iso"
ISO_PATH="/var/lib/vz/template/iso/${ISO_NAME}"

if ! pve "test -f ${ISO_PATH}"; then
    log "Téléchargement de l'ISO ${ISO_NAME} sur l'hôte (peut être long)"
    pve "wget -q -O ${ISO_PATH} https://download.proxmox.com/iso/${ISO_NAME} \
         || curl -fsSL -o ${ISO_PATH} https://download.proxmox.com/iso/${ISO_NAME}"
fi

# ------------------------------------------ 3. ISO auto-install (answer) ---
log "Construction de l'ISO auto-installée (answer file)"
pve "proxmox-auto-install-assistant prepare-iso ${ISO_PATH} --answerfile ${WORKDIR}/answer.toml \
     --output ${WORKDIR}/${VM_NAME}-auto.iso"
pve "test -f ${WORKDIR}/${VM_NAME}-auto.iso" || die "ISO auto-install absente"
pve "cp ${WORKDIR}/${VM_NAME}-auto.iso /var/lib/vz/template/iso/${VM_NAME}-auto.iso"

# ------------------------------------------------------------ 4. la VM ---
log "Création de la VM ${VMID} (${VM_NAME})"
pve "qm destroy ${VMID} --purge 2>/dev/null; true" >/dev/null 2>&1 || true

VLAN_ARG=""
[ -n "$VLAN" ] && VLAN_ARG=",tag=${VLAN}"

pve "qm create ${VMID} \
    --name ${VM_NAME} \
    --ostype l26 \
    --cores ${CORES} \
    --memory ${RAM_MB} \
    --cpu host,flags=+vmx \
    --bios ovmf \
    --machine q35 \
    --efidisk0 ${STORAGE}:1,format=raw,efitype=4m \
    --scsihw virtio-scsi-single \
    --scsi0 ${STORAGE}:${DISK_GB},format=raw,iothread=1,discard=on \
    --net0 virtio,bridge=${BRIDGE}${VLAN_ARG} \
    --agent enabled=1 \
    --serial0 socket \
    --vga std \
    --boot order=scsi0"

pve "qm set ${VMID} --ide2 ${ISO_STORAGE}:iso/${VM_NAME}-auto.iso,media=cdrom"
pve "qm set ${VMID} --onboot 1"

log "Démarrage de la VM"
pve "qm start ${VMID}"

log "VM ${VMID} démarrée. L'auto-install PVE dure ~5-10 min."
log "Récupérer l'IP (DHCP) : ssh root@${PVE_HOST} 'qm guest cmd ${VMID} network-get-interfaces'"
log "Puis renseigner inventory/hosts.yml et lancer :"
log "  ansible-galaxy collection install -r requirements.yml"
log "  ansible-playbook -i inventory/hosts.yml site.yml"

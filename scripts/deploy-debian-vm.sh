#!/usr/bin/env bash
# deploy-debian-vm.sh — Voie RECOMMANDÉE pour un partitionnement compatible CIS.
# Déploie une VM Debian 13 (trixie) préseedée (partitions séparées CIS 1.1) sur un hôte PVE,
# puis applique la conversion Debian -> Proxmox VE 9.x (install-pve-on-debian.sh, 2 phases).
set -euo pipefail

# ------------------------------------------------------------------ config ---
PVE_HOST="${PVE_HOST:-192.168.1.10}"
PVE_SSH_PORT="${PVE_SSH_PORT:-22}"
VMID="${VMID:-9001}"
VM_NAME="${VM_NAME:-pve-cis-lab}"
STORAGE="${STORAGE:-local-lvm}"
ISO_STORAGE="${ISO_STORAGE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
VLAN="${VLAN:-}"
DISK_GB="${DISK_GB:-64}"
RAM_MB="${RAM_MB:-8192}"
CORES="${CORES:-4}"

DEB_VERSION="${DEB_VERSION:-13.7.0}"        # ISO netinst Debian 13 (trixie)
DEB_IP="${DEB_IP:-}"                        # requis (mode 100% scripté)
DEB_NETMASK="${DEB_NETMASK:-255.255.255.0}"
DEB_GW="${DEB_GW:-}"
DEB_DNS="${DEB_DNS:-1.1.1.1}"
ROOT_PASSWORD="${ROOT_PASSWORD:-ChangeMeCISLab!}"
SSH_PUBKEY_FILE="${SSH_PUBKEY_FILE:-${HOME}/.ssh/id_ed25519.pub}"
TIMEZONE="${TIMEZONE:-Europe/Paris}"
KEYB="${KEYB:-fr}"
WORKDIR="${WORKDIR:-/tmp/deb-preseed}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONVERT_SCRIPT="${SCRIPT_DIR}/install-pve-on-debian.sh"

# ---------------------------------------------------------------- helpers ---
log() { printf '\e[1;34m[deploy]\e[0m %s\n' "$*"; }
die() { printf '\e[1;31m[erreur]\e[0m %s\n' "$*" >&2; exit 1; }
pve() { ssh -p "$PVE_SSH_PORT" "root@${PVE_HOST}" "$*"; }

[ -n "$DEB_IP" ] && [ -n "$DEB_GW" ] || die "Renseigner DEB_IP et DEB_GW (netcfg statique) pour un déploiement 100% scripté."
[ -f "$SSH_PUBKEY_FILE" ] || die "Clé publique introuvable : $SSH_PUBKEY_FILE"
[ -f "$CONVERT_SCRIPT" ] || die "Script de conversion introuvable : $CONVERT_SCRIPT"
SSH_PUBKEY="$(cat "$SSH_PUBKEY_FILE")"

DEB_ISO_NAME="debian-${DEB_VERSION}-amd64-netinst.iso"
DEB_ISO_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/${DEB_ISO_NAME}"
DEB_ISO_PATH="/var/lib/vz/template/iso/${DEB_ISO_NAME}"
OUT_ISO_NAME="${VM_NAME}-deb-cis-auto.iso"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# --------------------------------------------------- 1. ISO Debian + outils ---
log "Préparation de l'hôte (xorriso, isolinux)"
pve "apt-get update -qq && apt-get install -y -qq xorriso isolinux >/dev/null"

if ! pve "test -f ${DEB_ISO_PATH}"; then
    log "Téléchargement de l'ISO Debian netinst"
    pve "wget -q -O ${DEB_ISO_PATH} ${DEB_ISO_URL}"
fi

# ------------------------------------------------------- 2. remaster preseed ---
log "Extraction de l'ISO et génération du preseed (partitionnement CIS L2)"
pve "rm -rf ${WORKDIR} && mkdir -p ${WORKDIR}"
pve "xorriso -osirrox on -indev ${DEB_ISO_PATH} -extract / ${WORKDIR}/iso >/dev/null 2>&1"

pve "cat > ${WORKDIR}/preseed.cfg <<'PRESEED'
#### Debian 12 preseed — lab hardening CIS L2 (Proxmox VE)
d-i debian-installer/locale string fr_FR.UTF-8
d-i keyboard-configuration/xkb-keymap select __KEYB__
d-i console-setup/ask_detect boolean false

d-i netcfg/choose_interface select auto
d-i netcfg/disable_autoconfig boolean true
d-i netcfg/get_ipaddress string __IP__
d-i netcfg/get_netmask string __NETMASK__
d-i netcfg/get_gateway string __GW__
d-i netcfg/get_nameservers string __DNS__
d-i netcfg/confirm_static boolean true
d-i netcfg/get_hostname string __VMNAME__
d-i netcfg/get_domain string lab.local

d-i clock-setup/utc boolean true
d-i time/zone string __TIMEZONE__

d-i passwd/root-password password __ROOTPW__
d-i passwd/root-password-again password __ROOTPW__
d-i passwd/make-user boolean false

d-i apt-setup/services-select multiselect security, updates
d-i apt-setup/non-free-firmware boolean true
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string
d-i tasksel/tasksel/first multiselect standard, ssh-server
d-i popularity-contest/participate boolean false

#### Partitionnement CIS 1.1 : /tmp /var/tmp /var/log /var/log/audit /home séparés
d-i partman-auto/method string lvm
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-md/device_remove_md boolean true
d-i partman-lvm/confirm boolean true
d-i partman-lvm/confirm_nooverwrite boolean true
d-i partman-auto/disk string /dev/sda
d-i partman-auto/choose_recipe select cis_l2
d-i partman-auto/expert_recipe string \
    cis_l2 :: \
    512 512 512 vfat $primary{ } method{ efi } format{ } mountpoint{ /boot/efi } . \
    1024 1024 1024 ext4 $primary{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /boot } . \
    15360 15360 15360 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ / } . \
    2048 2048 2048 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /tmp } options/nodev{ nodev } options/nosuid{ nosuid } options/noexec{ noexec } . \
    10240 10240 10240 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /var } options/nodev{ nodev } options/nosuid{ nosuid } . \
    1024 1024 1024 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /var/tmp } options/nodev{ nodev } options/nosuid{ nosuid } options/noexec{ noexec } . \
    4096 4096 4096 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /var/log } options/nodev{ nodev } options/nosuid{ nosuid } options/noexec{ noexec } . \
    2048 2048 2048 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /var/log/audit } options/nodev{ nodev } options/nosuid{ nosuid } options/noexec{ noexec } . \
    2048 2048 2048 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /home } options/nodev{ nodev } options/nosuid{ nosuid } . \
    2048 2048 2048 swap $lvmok{ } method{ swap } format{ } . \
    10240 20480 -1 ext4 $lvmok{ } method{ format } format{ } use_filesystem{ } filesystem{ ext4 } mountpoint{ /var/lib/vz } options/nodev{ nodev } options/nosuid{ nosuid } .

d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string /dev/sda
d-i cdrom-detect/eject boolean true

d-i preseed/late_command string \
    in-target mkdir -p /etc/ssh/sshd_config.d /root/.ssh ; \
    echo 'PermitRootLogin prohibit-password' > /target/etc/ssh/sshd_config.d/50-root.conf ; \
    echo '__SSH_PUBKEY__' > /target/root/.ssh/authorized_keys ; \
    chmod 600 /target/root/.ssh/authorized_keys ; \
    cp /cdrom/install-pve-on-debian.sh /target/usr/local/sbin/ ; \
    chmod 700 /target/usr/local/sbin/install-pve-on-debian.sh
PRESEED"

pve "sed -i -e 's|__KEYB__|${KEYB}|g' -e 's|__IP__|${DEB_IP}|g' -e 's|__NETMASK__|${DEB_NETMASK}|g' \
     -e 's|__GW__|${DEB_GW}|g' -e 's|__DNS__|${DEB_DNS}|g' -e 's|__VMNAME__|${VM_NAME}|g' \
     -e 's|__TIMEZONE__|${TIMEZONE}|g' -e 's|__ROOTPW__|${ROOT_PASSWORD}|g' \
     -e 's|__SSH_PUBKEY__|${SSH_PUBKEY}|g' ${WORKDIR}/preseed.cfg"

log "Injection du preseed + script de conversion dans l'ISO"
scp -P "$PVE_SSH_PORT" -q $SSH_OPTS "$CONVERT_SCRIPT" "root@${PVE_HOST}:${WORKDIR}/iso/install-pve-on-debian.sh"
pve "cp ${WORKDIR}/preseed.cfg ${WORKDIR}/iso/preseed.cfg"

log "Patch des entrées de boot (isolinux + grub)"
pve "test -f ${WORKDIR}/iso/isolinux/txt.cfg && \
    sed -i 's|^append \(.*\)$|append auto=true priority=critical file=/cdrom/preseed.cfg \1|' ${WORKDIR}/iso/isolinux/txt.cfg || true"
pve "test -f ${WORKDIR}/iso/boot/grub/grub.cfg && \
    sed -i 's|^\(\s*linux\s.*\)$|\1 auto=true priority=critical file=/cdrom/preseed.cfg|' ${WORKDIR}/iso/boot/grub/grub.cfg || true"

log "Reconstruction de l'ISO"
pve "cd ${WORKDIR}/iso && xorriso -as mkisofs -r -J -joliet-long -l -iso-level 3 \
    -o ${WORKDIR}/${OUT_ISO_NAME} \
    -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin \
    -b isolinux/isolinux.bin -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot \
    -isohybrid-gpt-basdat . >/dev/null 2>&1 \
    && cp ${WORKDIR}/${OUT_ISO_NAME} /var/lib/vz/template/iso/${OUT_ISO_NAME}"

# --------------------------------------------------------------- 3. la VM ---
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
    --boot order=ide2"

pve "qm set ${VMID} --ide2 ${ISO_STORAGE}:iso/${OUT_ISO_NAME},media=cdrom"
pve "qm start ${VMID}"

# ------------------------------------------------- 4. attente SSH + PVE ---
log "Installation Debian en cours (10-20 min). Attente de SSH sur ${DEB_IP}..."
SSH_OK=0
for _ in $(seq 1 60); do
    if ssh -o BatchMode=yes -o ConnectTimeout=5 $SSH_OPTS "root@${DEB_IP}" true 2>/dev/null; then
        SSH_OK=1
        break
    fi
    sleep 30
done
[ "$SSH_OK" = 1 ] || die "SSH injoignable sur ${DEB_IP} après 30 min (voir console qm)."

log "Conversion Debian -> Proxmox VE 9.x — phase 1 (kernel PVE + reboot)"
ssh $SSH_OPTS "root@${DEB_IP}" 'bash -s' < "$CONVERT_SCRIPT" \
    || die "Échec de la conversion (phase 1) — vérifier les logs sur la VM."

log "Attente du reboot sur le kernel PVE..."
sleep 45
SSH_OK=0
for _ in $(seq 1 20); do
    if ssh -o BatchMode=yes -o ConnectTimeout=5 $SSH_OPTS "root@${DEB_IP}" true 2>/dev/null; then
        SSH_OK=1
        break
    fi
    sleep 15
done
[ "$SSH_OK" = 1 ] || die "SSH injoignable après le reboot phase 1."

log "Conversion — phase 2 (proxmox-ve + bridge + reboot final, ~10-15 min)"
ssh $SSH_OPTS "root@${DEB_IP}" 'bash -s' < "$CONVERT_SCRIPT" \
    || die "Échec de la conversion (phase 2) — vérifier les logs sur la VM."

pve "qm set ${VMID} --boot order=scsi0"
log "Terminé. VM ${VMID} = Proxmox VE avec partitionnement CIS."
log "Prochaine étape : ansible-playbook -i inventory/hosts.yml site.yml"

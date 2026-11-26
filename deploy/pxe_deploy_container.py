#!/usr/bin/env python3
# pxe_deploy_container.py — ProxKnox "fast" PXE: the PXE server runs
# in a Docker CONTAINER instead of a KVM VM.
#
# Ideal for iteration: no server VM to install (~5 min saved);
# the proxknox-pxe container (daemon-spawned, host netns) provides DHCP +
# TFTP + DNS (dnsmasq) on the isolated libvirt network "pxe-lab"
# (interface pxebr0, IP 192.168.200.2 added/removed by the container),
# serves the preseed and files over HTTP (:8000), NATs the segment to
# the Internet (iptables rules tagged proxknox-pxe, removed on shutdown)
# and receives the debian-installer syslog (UDP 10514).
#
# The pxe-client VM behaves exactly as in the KVM variant:
# PXE boot -> Debian 13 auto-installed (CIS preseed) -> conversion
# to Proxmox VE 9.2 in 2 phases. Orchestration (SSH gate, script
# transfer, phases, pveversion check) is done here, synchronously, via
# `docker exec` (the container is L2-adjacent to the client; the host
# does not need to reach the segment).
#
# "--iso" variant: installation from a FULL ISO (Debian DVD-1)
# attached to the client as a CD drive; Debian packages come from the
# ISO (no mirror, no upgrade during install — the upgrade happens
# during the Proxmox conversion). The client kernel/initrd are then
# extracted from the ISO (install.amd/) instead of the netboot tree.
#
# Usage: python3 deploy/pxe_deploy_container.py [--iso [PATH|URL]] [--encrypt]
# Env  : ROOT_PW, ADMIN_PW, SSH_PUBKEY_FILE, DISK_POOL, ISO_PATH (path or
#        URL), ISO_URL (default URL), DEB_VERSION, all overridable.
#        --encrypt / ENCRYPT_DISK=1: LUKS2 disk encryption with automatic
#        unlock by the VM's vTPM (clevis tpm2 pin, see LUKS_TPM_ENROLL);
#        passphrase LUKS_PW (kept as the manual fallback keyslot); swtpm
#        required on the host for the vTPM device.

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time

# ----------------------------------------------------------------- constants ---
PXE_NET = "pxe-lab"
MGMT_NET = "default"
PXE_BRIDGE = "pxebr0"                # interface of the pxe-lab libvirt network
CLIENT_NAME = "pxe-client"
CLIENT_MAC = "52:54:00:aa:00:10"
CONTAINER = "proxknox-pxe"
IMAGE = "proxknox-pxe:latest"
PXE_IP = "192.168.200.2"             # PXE server IP (on pxebr0, set by the container)
HTTP_PORT = "8000"                   # HTTP of the workdir (host netns)
CLIENT_VCPUS, CLIENT_RAM, CLIENT_DISK_GB = 4, 8192, 64

NETBOOT_URL = ("http://deb.debian.org/debian/dists/trixie/main/installer-amd64/"
               "current/images/netboot/netboot.tar.gz")
# Full ISO (DVD-1): contains everything needed to install Debian 13
# WITHOUT a mirror (includes "standard" + ssh-server tasks).
# cdimage.debian.org redirects to a mirror; deb.debian.org does not
# host the DVDs.
ISO_URL = ("https://cdimage.debian.org/debian-cd/current/amd64/iso-dvd/"
           "debian-13.7.0-amd64-DVD-1.iso")
ISO_SHA256 = ("347b6c67a3cc0b7ddb60b178f683470c4e2b7ac426c996d9337a2ff36c1"
              "a32d2")
# Script applied to the client before the PVE conversion when the install
# comes from the ISO: removes cdrom sources, writes the Debian deb822
# mirrors (the upgrade happens at that point: "we upgrade once switched
# over to Proxmox"), then apt update. Outside ISO mode: nothing to do.
ISO_APT_FIXUP = """#!/bin/bash
# switches the apt sources from the ISO (cdrom) to the official Debian mirrors
set -e
if [ -f /etc/apt/sources.list ]; then
    sed -i -E '/^[[:space:]]*(deb|Types:)[[:space:]].*(cdrom|file:)/Id' \\
        /etc/apt/sources.list
fi
grep -rl 'cdrom' /etc/apt/sources.list.d/ 2>/dev/null | xargs -r rm -f
printf 'Acquire::cdrom::AutoDetect \"\";\\n' > /etc/apt/apt.conf.d/99-no-cdrom
cat > /etc/apt/sources.list.d/debian-official.sources <<'EOF'
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
apt-get update -qq
echo "[iso-fixup] Debian sources in place (upgrade via PVE conversion)"
"""
PRESEED_URL = f"http://{PXE_IP}:{HTTP_PORT}/preseed/debian13-cis.cfg"
FILES_URL = f"http://{PXE_IP}:{HTTP_PORT}/files"
LEASE_FILE = "/srv/logs/dnsmasq.leases"     # in the container (mounted volume)
DI_SYSLOG = "logs/di-syslog.log"             # host side (workdir/logs/...)
IPT_TAG = "proxknox-pxe"

# vTPM device added to the client VM with --encrypt: emulator backend
# (swtpm on the host), TPM 2.0, CRB interface. The state (per-VM,
# /var/lib/libvirt/qemu/tpm/) holds the sealed key material; it is wiped
# on `virsh undefine --nvram` (cleanup of every run) — keep the LUKS
# passphrase fallback to decrypt a moved/recovered disk elsewhere.
TPM_XML = "<tpm model='tpm-crb'><backend type='emulator' version='2.0'/></tpm>"

# --encrypt: script run on the freshly installed node (over SSH, BEFORE the
# PVE conversion) to enroll the LUKS2 container in the vTPM. clevis (NOT
# systemd-cryptenroll) because Debian/Proxmox boot via initramfs-tools and
# clevis ships the initramfs unlocker + crypttab integration for it.
# Placeholders __LUKSPW__ / __PINCFG__ substituted by the orchestrator.
# The pin config is "{}" (no PCR binding) by default: this lab boots via
# SeaBIOS, whose firmware measures NOTHING into the PCRs — PCR binding
# would add no protection here and only risk a locked disk. LUKS_TPM_PCRS
# (e.g. "7" or "0,2,7") lets a UEFI/Secure Boot user bind anyway.
LUKS_TPM_ENROLL = r"""#!/bin/bash
# ProxKnox --encrypt: enroll the node's LUKS2 container in its vTPM
# (clevis tpm2 pin) so the disk unlocks automatically at boot.
# The passphrase keyslot 0 installed by d-i stays as the manual fallback.
set -euo pipefail
PASS="__LUKSPW__"
CFG="__PINCFG__"

DEV="$(lsblk -rnpo NAME,TYPE,FSTYPE | awk '$2=="part" && $3=="crypto_LUKS"{print $1; exit}')"
[ -n "$DEV" ] || { echo "[luks-tpm] no crypto_LUKS partition found (was --encrypt passed to the deploy?)"; exit 1; }
[ -e /dev/tpm0 ] || [ -e /dev/tpmrm0 ] || { echo "[luks-tpm] no TPM device visible (vTPM missing from the VM?)"; exit 1; }

if cryptsetup luksDump "$DEV" | grep -qi clevis; then
    echo "[luks-tpm] $DEV already enrolled in the TPM"
    BIND_NEEDED=0
else
    BIND_NEEDED=1
fi

echo "[luks-tpm] LUKS container: $DEV"
export DEBIAN_FRONTEND=noninteractive
echo "[luks-tpm] installing clevis-luks clevis-tpm2 clevis-initramfs tpm2-tools"
apt-get -y -qq install clevis-luks clevis-tpm2 clevis-initramfs tpm2-tools

if [ "$BIND_NEEDED" = 1 ]; then
    echo "[luks-tpm] binding the TPM2 pin (config: $CFG)"
    printf '%s\n' "$PASS" | clevis luks bind -f -d "$DEV" tpm2 "$CFG"
fi

# The first-boot bootstrap key (passphrase file in the initramfs, cf.
# ENCRYPT_LATE_CMD) is no longer needed: the vTPM does the unlocking now
# and the LUKS passphrase remains as the console fallback. Revert the
# crypttab entry and remove the key + hook BEFORE update-initramfs below
# (single rebuild: clevis + TPM modules + bootstrap key removal).
awk '$3=="/root/.luks-bootkey"{$3="none"; sub(/,keyscript=\/bin\/cat/,"",$4)} {print}' \
    /etc/crypttab > /etc/crypttab.new && mv /etc/crypttab.new /etc/crypttab
rm -f /root/.luks-bootkey /etc/initramfs-tools/hooks/proxknox-bootkey-hook \
      /usr/local/sbin/proxknox-luks-bootstrap

# TPM kernel modules in initramfs-tools (not pulled in automatically):
# required to reach the vTPM from the initramfs at unlock time
cat > /etc/initramfs-tools/hooks/proxknox-tpm <<'HOOK'
#!/bin/sh
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in
    prereqs) prereqs; exit 0;;
esac
. /usr/share/initramfs-tools/hook-functions
copy_modules_dir kernel/drivers/char/tpm
HOOK
chmod 755 /etc/initramfs-tools/hooks/proxknox-tpm
update-initramfs -u

echo "[luks-tpm] keyslots:"
cryptsetup luksDump "$DEV" | sed -n '/Key [Ss]lots/,$p' | grep -E '[Ss]lot|tlv|clevis' || true
echo "[luks-tpm] vTPM enrollment done"
"""

# --encrypt, d-i stage: the FIRST reboot after the installation lands on
# the cryptsetup passphrase prompt with nobody there — the very first
# unlock has no TPM binding yet. Bootstrap: d-i's late_command fetches the
# passphrase key + a bootstrap script over HTTP and runs the latter
# INSIDE the target. The script (LUKS_BOOTSTRAP below) does not rely on
# the state of /etc/crypttab at late_command time (d-i writes it at its
# own finish-install timing — the naive awk rewrite of run 1 emptied it):
# it finds the LUKS partition + UUID itself, rewrites or seeds the entry,
# installs the initramfs hook that embeds the key, rebuilds the initramfs.
# The enrollment script (LUKS_TPM_ENROLL) later reverts the crypttab entry
# and deletes key + hook: the vTPM then unlocks at every boot and the
# passphrase stays as the console fallback. __LURL__ = served-files URL.
ENCRYPT_LATE_CMD = """ ; \\
    in-target wget -q -O /root/.luks-bootkey __LURL__/proxknox-bootkey ; \\
    in-target chmod 600 /root/.luks-bootkey ; \\
    in-target wget -q -O /usr/local/sbin/proxknox-luks-bootstrap \\
        __LURL__/proxknox-luks-bootstrap ; \\
    in-target chmod 755 /usr/local/sbin/proxknox-luks-bootstrap ; \\
    in-target /usr/local/sbin/proxknox-luks-bootstrap"""

# Served over HTTP to the target during d-i (mknod-free, plain /bin/sh):
# no operator input, no quoting layers.
LUKS_BOOTSTRAP = """#!/bin/sh
# ProxKnox --encrypt: first-boot bootstrap. Point /etc/crypttab at the
# bootstrap key file (passphrase embedded in the initramfs via
# keyscript=/bin/cat) so the first reboot unlocks unattended; the vTPM
# enrollment later REMOVES all of this (keyslot passphrase remains).
set -e
KEY=/root/.luks-bootkey
[ -s "$KEY" ] || { echo "[luks-bootstrap] missing $KEY (not served?)"; exit 1; }
# cryptsetup reads the keyscript pipe RAW (--key-file=-): strip any
# trailing newline or the key will not match the passphrase
printf "%s" "$(cat "$KEY")" > "$KEY"
CRYPT=$(lsblk -rnpo NAME,FSTYPE | awk '$2=="crypto_LUKS"{print $1; exit}')
[ -n "$CRYPT" ] || { echo "[luks-bootstrap] no crypto_LUKS partition found"; exit 1; }
UUID=$(blkid -s UUID -o value "$CRYPT")
NAME=$(basename "$CRYPT")_crypt
echo "[luks-bootstrap] LUKS: $CRYPT (UUID=$UUID) -> $NAME"

# /etc/crypttab: d-i writes "<name> UUID=<uuid> none luks,discard,..." —
# rewrite the matching UUID line; seed our own if the crypttab is absent,
# empty or has no matching line (d-i timing).
if [ -s /etc/crypttab ]; then
    awk -v uuid="$UUID" -v key="$KEY" \\
        '$2=="UUID="uuid{$3=key; $4=$4 ",keyscript=/bin/cat"} {print}' \\
        /etc/crypttab > /etc/crypttab.proxknox
else
    : > /etc/crypttab.proxknox
fi
grep -q "UUID=$UUID" /etc/crypttab.proxknox ||
    echo "$NAME UUID=$UUID $KEY luks,keyscript=/bin/cat" >> /etc/crypttab.proxknox
mv /etc/crypttab.proxknox /etc/crypttab
chmod 600 /etc/crypttab
grep -q "UUID=$UUID .*keyscript" /etc/crypttab ||
    { echo "[luks-bootstrap] crypttab rewrite failed"; exit 1; }
echo "[luks-bootstrap] crypttab: $(grep "UUID=$UUID" /etc/crypttab)"

# initramfs hook: embed the key file in the initramfs
cat > /etc/initramfs-tools/hooks/proxknox-bootkey-hook <<'HOOK'
#!/bin/sh
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in
    prereqs) prereqs; exit 0;;
esac
. /usr/share/initramfs-tools/hook-functions
mkdir -p "$DESTDIR/root"
cp /root/.luks-bootkey "$DESTDIR/root/.luks-bootkey"
chmod 600 "$DESTDIR/root/.luks-bootkey"
HOOK
chmod 755 /etc/initramfs-tools/hooks/proxknox-bootkey-hook

update-initramfs -u
echo "[luks-bootstrap] done: first-boot unlock ready"
"""

SSH_OPTS = ["-p", "2222", "-i", "/srv/keys/deploy_key",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]

SUBPROCESS_ENV = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}


class DeployError(Exception):
    pass


def log(msg): print(f"\033[1;34m[proxknox-pxe]\033[0m {msg}", flush=True)
def warn(msg): print(f"\033[1;33m[proxknox-pxe]\033[0m {msg}", flush=True)
def die(msg): raise DeployError(msg)


def run(cmd, check=True, capture=True, timeout=None, input=None, env=None):
    proc = subprocess.run(cmd, env=env or SUBPROCESS_ENV, text=True,
                           capture_output=capture, timeout=timeout, input=input)
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"code {proc.returncode}"
        die(f"Failed: {' '.join(map(str, cmd[:6]))}... : {err[:300]}")
    return proc


def virsh(*args, check=True):
    return run(["virsh", "-c", "qemu:///system", *args], check=check)


def docker(*args, check=True, timeout=None, **kw):
    return run(["docker", *args], check=check, timeout=timeout, **kw)


def ssh_in_container(ip, cmd, check=False, timeout=None, input=None):
    """SSH to the client FROM the container (direct L2, mounted key).
    -i: stdin of docker exec (script transfer)."""
    return docker("exec", "-i", CONTAINER, "ssh", *SSH_OPTS, f"root@{ip}", cmd,
                  check=check, timeout=timeout, input=input)


# ---------------------------------------------------- XML surgery (pure) ---
def _insert_after_type(xml, boot_lines):
    out, done = [], False
    for line in xml.splitlines():
        out.append(line)
        if not done and "</type>" in line:
            out.extend(f"    {b}" for b in boot_lines)
            done = True
    if not done:
        die("</type> tag not found in the XML (<os> section).")
    return "\n".join(out) + "\n"


def force_boot_hd_then_network(xml):
    if "<boot dev='network'/>" in xml:
        xml = xml.replace("<boot dev='network'/>",
                          "<boot dev='hd'/><boot dev='network'/>", 1)
    elif "<boot dev='hd'/>" in xml:
        xml = xml.replace("<boot dev='hd'/>",
                          "<boot dev='hd'/><boot dev='network'/>", 1)
    else:
        xml = _insert_after_type(xml, ["<boot dev='hd'/>", "<boot dev='network'/>"])
    if "<boot dev='hd'/>" not in xml or "<boot dev='network'/>" not in xml:
        die("XML: hd/network boot not found after edit.")
    return xml


def add_tpm_device(xml):
    """--encrypt: adds the vTPM 2.0 (swtpm emulator, CRB) to the VM XML.
    libvirt launches swtpm itself for an emulator backend; the host
    packages are checked in step_prereqs."""
    if "<tpm " in xml:
        return xml
    if "</devices>" not in xml:
        die("XML: </devices> not found (cannot insert the vTPM).")
    return xml.replace("  </devices>", f"    {TPM_XML}\n  </devices>", 1)


# ------------------------------------------------------------------ steps ---
def step_prereqs(cfg):
    log(f"Prerequisites: docker, libvirt, isolated network {PXE_NET} — OK")
    cmds = ["virsh", "virt-install", "docker", "wget"]
    if cfg.iso_mode:
        cmds.append("xorriso")
    if cfg.encrypt:
        cmds += ["swtpm", "swtpm_setup"]
    for cmd in cmds:
        if not shutil.which(cmd):
            die(f"Missing command: {cmd}")
    if docker("info", timeout=10).returncode != 0:
        die("Docker daemon unreachable (docker info).")
    if virsh("net-info", MGMT_NET, check=False).returncode != 0:
        die(f"libvirt network '{MGMT_NET}' not found.")
    if virsh("net-info", PXE_NET, check=False).returncode != 0:
        log(f"Creating isolated network {PXE_NET} (bridge pxebr0)")
        virsh("net-define", os.path.join(cfg.pxe_dir, f"{PXE_NET}-network.xml"))
    virsh("net-start", PXE_NET, check=False)
    virsh("net-autostart", PXE_NET)
    info = virsh("net-info", PXE_NET).stdout
    if not re.search(r"Active:\s+yes", info):
        die(f"Network {PXE_NET} inactive.")

    pool = virsh("pool-info", cfg.disk_pool, check=False)
    if pool.returncode == 0 and re.search(r"State:\s+running", pool.stdout):
        cfg.disk_client = f"pool={cfg.disk_pool},size={cfg.node_disk_gb},bus=virtio,format=qcow2"
    else:
        warn(f"Pool '{cfg.disk_pool}' inactive — disk in the default pool.")
        cfg.disk_client = f"size={cfg.node_disk_gb},bus=virtio,format=qcow2"


def step_fetch_iso(cfg):
    """ISO mode: ensures a local full ISO (given path or download of the
    official DVD-1), checks its SHA-256 sum."""
    iso = cfg.iso_path
    if iso.startswith(("http://", "https://", "ftp://")):
        local = os.path.join(cfg.workdir, "iso", os.path.basename(iso))
        if os.path.isfile(local) and os.path.getsize(local) > 700 * 1024 * 1024:
            log(f"ISO already present: {local}")
        else:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            log(f"Downloading the ISO (~4.4 GB): {iso}")
            run(["wget", "-q", "-c", "-O", local, iso], timeout=7200)
        cfg.iso_local = local
    elif os.path.isfile(iso):
        cfg.iso_local = iso
    else:
        die(f"ISO not found: {iso} (provide an existing local path "
            "or a URL to download).")
    size_gib = os.path.getsize(cfg.iso_local) / 1024 ** 3
    if not 0.3 < size_gib < 20:
        die(f"Suspicious ISO size: {size_gib:.1f} GiB.")

    log("Verifying ISO SHA-256 (~30 s)...")
    h = hashlib.sha256()
    with open(cfg.iso_local, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if cfg.iso_sha256 and digest != cfg.iso_sha256:
        die(f"Unexpected ISO SHA-256 ({digest}).")
    log(f"ISO ready: {cfg.iso_local} ({size_gib:.1f} GiB, sha256 ok)")


def step_prepare_files(cfg):
    log(f"Files served (workdir: {cfg.workdir})")
    for d in ("preseed", "files", "tftp/pxelinux.cfg", "logs", "keys"):
        os.makedirs(os.path.join(cfg.workdir, d), exist_ok=True)

    # residual pxelinux lock from a previous run (LOCALBOOT): otherwise the
    # client boots "local" on its blank disk and never installs
    mac = cfg.node_mac.replace(":", "-").lower()
    lockfile = os.path.join(cfg.workdir, "tftp", "pxelinux.cfg", "01-" + mac)
    if os.path.exists(lockfile):
        log("Removing residual pxelinux lock (01-<mac> from a previous run)")
        os.remove(lockfile)

    if not os.path.isfile(cfg.ssh_pubkey_file):
        die(f"Public key not found: {cfg.ssh_pubkey_file}")
    host_pubkey = open(cfg.ssh_pubkey_file, encoding="utf-8").read().strip()

    # client preseed (placeholders identical to the KVM variant; the PXE
    # server URLs go through the container's port 8000)
    text = open(os.path.join(cfg.pxe_dir, "client-preseed.cfg"),
                encoding="utf-8").read()
    text = text.replace(f"http://{PXE_IP}/", f"http://{PXE_IP}:{HTTP_PORT}/")
    for k, v in {"__ROOTPW__": cfg.root_pw, "__ADMIN_PW__": cfg.admin_pw,
                 "__LUKSPW__": cfg.luks_pw,
                 "__HOST_PUBKEY__": host_pubkey,
                 "__NODEHOSTNAME__": cfg.node_hostname}.items():
        text = text.replace(k, v)
    if cfg.encrypt:
        # partman switches from plain LVM to "crypto": LUKS2 container
        # with the whole VG (and all $lvmok volumes) inside. The
        # ##ENCRYPT## lines of client-preseed.cfg (crypto passphrase +
        # confirmations) are activated.
        text = text.replace("d-i partman-auto/method string lvm",
                            "d-i partman-auto/method string crypto", 1)
        text = "\n".join(
            l[len("##ENCRYPT##"):] if l.startswith("##ENCRYPT##") else l
            for l in text.splitlines()) + "\n"
        # first-boot bootstrap (cf. ENCRYPT_LATE_CMD): the passphrase key
        # + initramfs hook are served over HTTP with the other files
        anc = "    in-target chmod 440 /etc/sudoers.d/90-admin-lab"
        if anc not in text:
            die("preseed: late_command anchor not found (bootstrap key).")
        text = text.replace(anc, anc + ENCRYPT_LATE_CMD.replace(
            "__LURL__", FILES_URL), 1)
        key_path = os.path.join(cfg.workdir, "files", "proxknox-bootkey")
        # exact passphrase bytes, NO trailing newline: the initramfs
        # keyscript pipe feeds cryptsetup raw (--key-file=-), where a
        # stray \n makes the key wrong (unlock failed: "No key available
        # with this passphrase", observed on the run of 25/11/2026)
        open(key_path, "w", encoding="utf-8").write(cfg.luks_pw)
        os.chmod(key_path, 0o600)
        open(os.path.join(cfg.workdir, "files", "proxknox-luks-bootstrap"),
             "w", encoding="utf-8").write(LUKS_BOOTSTRAP)
    else:
        # strip the inactive crypto lines: d-i would choke on them
        text = "\n".join(l for l in text.splitlines()
                         if not l.startswith("##ENCRYPT##")) + "\n"
        for leftover in ("proxknox-bootkey", "proxknox-luks-bootstrap"):
            p = os.path.join(cfg.workdir, "files", leftover)
            if os.path.exists(p):
                os.remove(p)
    if cfg.iso_mode:
        # Install from the ISO: NO network mirror, NO upgrade (upgrades
        # will happen during the Proxmox VE conversion). Packages come
        # from the "cdrom" (ISO attached to the client).
        text = text.replace(
            "d-i apt-setup/services-select multiselect security, updates",
            "d-i apt-setup/use_mirror boolean false\n"
            "d-i pkgsel/upgrade select none\n"
            # sudo is absent from the "standard, ssh-server" tasks and the
            # preseed writes /etc/sudoers.d: install it from the ISO (adminlab + Ansible)
            "d-i pkgsel/include string sudo")
        # The ISO initrd ignores log_host/log_port; end signal = HTTP
        # request issued by late_command. Marker UNIQUE per node
        # (otherwise the previous run's marker triggers the watchdog).
        marker_mac = cfg.node_mac.replace(":", "-").lower()
        last = "    in-target chmod 440 /etc/sudoers.d/90-admin-lab"
        if last in text:
            text = text.replace(
                last,
                last + " ; \\\n"
                "    in-target wget -q -O /dev/null "
                f"http://{PXE_IP}:{HTTP_PORT}/milestone/late-command-{marker_mac}")
    open(os.path.join(cfg.workdir, "preseed", "debian13-cis.cfg"), "w",
         encoding="utf-8").write(text)

    # deployment key: the container SSHes the client (docker exec); the
    # public half is served to the client's preseed (/files/deploy_key.pub)
    key = os.path.join(cfg.workdir, "keys", "deploy_key")
    if not os.path.isfile(key):
        run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "pxe-deploy-key",
             "-f", key], capture=False)
    shutil.copy(key + ".pub",
                os.path.join(cfg.workdir, "files", "deploy_key.pub"))
    os.chmod(key, 0o600)

    # client kernel/initrd
    tftp = os.path.join(cfg.workdir, "tftp")
    for f in ("linux", "initrd.gz"):
        dst = os.path.join(tftp, f)
        if os.path.exists(dst):
            os.remove(dst)
    if cfg.iso_mode:
        # install.amd/ from the ISO: initrd with the CDROM retriever
        # (required to install from the ISO; the netboot initrd, in
        # contrast, requires a mirror)
        log("Extracting install.amd/{vmlinuz,initrd.gz} from the ISO")
        isodir = os.path.join(cfg.workdir, "iso-amd")
        # the previous extraction leaves ISO directories/files as r-x/r--
        # -> make all directories (including the root) writable
        for root, _dirs, _files in os.walk(isodir):
            try:
                os.chmod(root, 0o700)
            except OSError:
                pass
        shutil.rmtree(isodir, ignore_errors=True)
        run(["xorriso", "-osirrox", "on", "-indev", cfg.iso_local,
             "-extract", "/install.amd", isodir])
        shutil.copy(os.path.join(isodir, "vmlinuz"), os.path.join(tftp, "linux"))
        shutil.copy(os.path.join(isodir, "initrd.gz"),
                    os.path.join(tftp, "initrd.gz"))
    else:
        # official Debian NETBOOT tree (required for pure-network PXE)
        tarball = os.path.join(cfg.workdir, "netboot", "netboot.tar.gz")
        netdir = os.path.dirname(tarball)
        if (not os.path.exists(tarball)) or os.path.getsize(tarball) == 0:
            os.makedirs(netdir, exist_ok=True)
            log("Downloading the Debian netboot tree (~55 MB)")
            run(["wget", "-q", "-O", tarball, NETBOOT_URL])
        with tarfile.open(tarball, "r:gz") as tar:
            try:
                tar.extractall(netdir, filter="data")
            except TypeError:
                tar.extractall(netdir)
        for f in ("linux", "initrd.gz"):
            shutil.copy(os.path.join(netdir, "debian-installer", "amd64", f),
                        os.path.join(tftp, f))
    log("kernel/initrd TFTP ready"
        + (" (extracted from the full ISO)" if cfg.iso_mode
           else " (Debian netboot tree)"))

    # pxelinux.cfg/default: same boot as the pxe-server VM
    with open(os.path.join(tftp, "pxelinux.cfg", "default"), "w") as f:
        f.write(
            "DEFAULT install\n"
            "PROMPT 0\n"
            "TIMEOUT 30\n"
            "\n"
            "LABEL install\n"
            "  KERNEL linux\n"
            f"  APPEND initrd=initrd.gz auto=true priority=critical "
            f"preseed/url={PRESEED_URL} log_host={PXE_IP} log_port=10514\n")

    shutil.copy(os.path.join(cfg.repo_dir, "deploy", "install-pve-on-debian.sh"),
                os.path.join(cfg.workdir, "files", "install-pve-on-debian.sh"))
    log(f"Preseed ({PRESEED_URL}), TFTP and deployment key ready")


def step_build_and_run_container(cfg):
    log(f"Building image {IMAGE} (cached if unchanged)")
    # The default docker bridge has no egress on this machine:
    # the build uses host networking (classic builder).
    env_build = dict(SUBPROCESS_ENV)
    env_build["DOCKER_BUILDKIT"] = "0"
    run(["docker", "build", "--network=host", "-t", IMAGE,
         os.path.join(cfg.pxe_dir, "container")],
         timeout=2400, env=env_build)

    # an existing container: docker stop (SIGTERM -> iptables/IP cleanup
    # by the entrypoint) THEN rm; rm -f (SIGKILL) would leave the host
    # network rules in place.
    docker("stop", "-t", "5", CONTAINER, check=False, timeout=30)
    docker("rm", CONTAINER, check=False, timeout=30)
    log(f"Starting container {CONTAINER} (host netns: {PXE_IP} on "
        f"{PXE_BRIDGE}, NAT upstream)")
    w = cfg.workdir
    # --network host: the container (root) sees pxebr0, sets the PXE
    # server IP on it and installs the tagged NAT rules (removed on
    # shutdown).
    docker("create", "--name", CONTAINER,
           "--network", "host",
           "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
           "-v", f"{w}/preseed:/srv/http/preseed:ro",
           "-v", f"{w}/files:/srv/http/files:ro",
           "-v", f"{w}/tftp:/srv/tftp",
           "-v", f"{w}/logs:/srv/logs",
           "-v", f"{w}/keys:/srv/keys:ro", IMAGE)
    docker("start", CONTAINER)

    # container ready? services in place
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        st = docker("exec", CONTAINER, "pgrep", "-x", "dnsmasq", check=False)
        if st.returncode == 0:
            break
        inspect = docker("inspect", "-f", "{{.State.Running}}", CONTAINER,
                         check=False)
        if inspect.stdout.strip() != "true":
            docker("logs", CONTAINER)
            die("Container proxknox-pxe stopped at startup.")
        time.sleep(2)
    else:
        docker("logs", CONTAINER)
        die("dnsmasq not started in the container after 60 s.")

    # self-diagnostic in the container: HTTP, TFTP, NAT, segment IP
    checks = {
        "HTTP preseed": ["wget", "-q", "-O", "-",
                         f"http://127.0.0.1:{HTTP_PORT}/preseed/debian13-cis.cfg"],
        "HTTP files": ["wget", "-q", "-O", "-",
                       f"http://127.0.0.1:{HTTP_PORT}/files/deploy_key.pub"],
        "TFTP": ["sh", "-c", "cd /tmp && tftp -4 -v {} -c get pxelinux.0 "
                 "&& test -s /tmp/pxelinux.0".format(PXE_IP)],
    }
    for name, cmdv in checks.items():
        r = docker("exec", CONTAINER, *cmdv, check=False, timeout=20)
        if r.returncode != 0 or not (r.stdout or "").strip():
            die(f"Self-check {name} failed in the container "
                f"(see docker logs {CONTAINER}).")
    nat = docker("exec", CONTAINER, "iptables", "-t", "nat", "-S", "POSTROUTING",
                 check=False)
    if IPT_TAG not in (nat.stdout or ""):
        die(f"NAT rule missing in the container (tag {IPT_TAG}).")
    addr = docker("exec", CONTAINER, "ip", "-o", "-4", "addr", "show",
                  "dev", PXE_BRIDGE, check=False)
    if f"{PXE_IP}/" not in (addr.stdout or ""):
        die(f"IP {PXE_IP} not set on {PXE_BRIDGE}.")
    log("PXE container operational: DHCP/TFTP/DNS/HTTP/NAT verified")


def step_create_client(cfg):
    # cleanup of any previous run
    virsh("destroy", cfg.node_name, check=False)
    virsh("undefine", cfg.node_name, "--nvram", check=False)
    virsh("vol-delete", "--pool", cfg.disk_pool, f"{cfg.node_name}.qcow2",
          check=False)
    virsh("vol-delete", "--pool", cfg.disk_pool, f"{cfg.node_name}-osd.qcow2",
          check=False)

    # additional OSD disk (Ceph): dedicated volume <node>-osd.qcow2
    osd_disk_arg = None
    if cfg.node_osd_gb:
        virsh("vol-create-as", cfg.disk_pool, f"{cfg.node_name}-osd.qcow2",
              f"{cfg.node_osd_gb}G", "--format", "qcow2")
        osd_path = virsh("vol-path", "--pool", cfg.disk_pool,
                         f"{cfg.node_name}-osd.qcow2").stdout.strip()
        osd_disk_arg = f"path={osd_path},bus=virtio,format=qcow2"
        log(f"OSD disk {cfg.node_osd_gb} GB for Ceph: {osd_path}")

    log(f"VM {cfg.node_name}: creation + PXE boot (hd then network)")
    cmdline = ["virt-install", "--connect", "qemu:///system",
               "--name", cfg.node_name,
               "--memory", str(cfg.node_ram), "--vcpus", str(cfg.node_vcpus),
               "--cpu", "host-passthrough", "--machine", "q35",
               "--disk", cfg.disk_client,
               "--network", f"network={PXE_NET},mac={cfg.node_mac},model=virtio",
               "--graphics", "vnc,listen=127.0.0.1", "--video", "virtio",
               "--pxe", "--osinfo", "detect=on,require=off", "--noautoconsole"]
    if osd_disk_arg:
        cmdline += ["--disk", osd_disk_arg]
    if cfg.iso_mode:
        # Full ISO attached as an optical drive: debian-installer
        # (ISO initrd) pulls the packages from it — install without a
        # mirror. Never in the boot order (hd then network): PXE boots
        # d-i, the ISO is only a package source.
        cmdline += ["--disk", (f"path={cfg.iso_local},device=cdrom,"
                              "perms=ro,bus=sata")]
    run(cmdline)
    # blank disk: we interrupt this boot and force the hd -> network
    # order (the PXE install will not retrigger after installation).
    virsh("destroy", cfg.node_name, check=False)
    xml_path = os.path.join(cfg.workdir, f"{cfg.node_name}.xml")
    xml = force_boot_hd_then_network(virsh("dumpxml", cfg.node_name).stdout)
    if cfg.encrypt:
        xml = add_tpm_device(xml)
        if "<tpm " not in xml:
            die("XML: vTPM device not found after edit.")
    open(xml_path, "w", encoding="utf-8").write(xml)
    virsh("define", xml_path)
    virsh("start", cfg.node_name)
    log("The client PXE boots: dnsmasq -> pxelinux -> kernel/initrd TFTP "
        "-> preseed HTTP")


def step_watch_install(cfg):
    """Follows the d-i installation: press Enter on the final screen +
    cold-reboot watchdog (the warm reboot from d-i sometimes hangs the
    VM; and a keystroke during early boot can hang the VM once d-i
    relaunches it — 3 min of silence afterwards). End marker:
    - netboot: "finish-install" in the relayed syslog (log_host);
    - ISO: the ISO initrd ignores log_host -> HTTP milestone issued by
      late_command ("milestone/late-command" logged in http.log)."""
    syslogfile = os.path.join(cfg.workdir, DI_SYSLOG)
    httplog = os.path.join(cfg.workdir, "logs", "http.log")
    if cfg.iso_mode:
        marker, marker_file = ("milestone/late-command-"
                               + cfg.node_mac.replace(":", "-").lower(),
                               httplog)
    else:
        marker, marker_file = "finish-install", syslogfile
        # start from a blank syslog: previous-run markers would trigger
        # spurious Enter presses/reboots
        try:
            open(syslogfile, "w").close()
        except OSError:
            pass  # created on the container side (root); read-only on the host
    stop = threading.Event()

    def seen(txt=marker, path=marker_file):
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return False
        return txt in content

    def loop():
        finish_at, restarts, quiet_until = None, 0, 0.0
        while not stop.is_set():
            now = time.monotonic()
            if seen() and now >= quiet_until:
                if finish_at is None:
                    finish_at = now - 30
                if now - finish_at > 8 * 60 and restarts < 2:
                    restarts += 1
                    warn("Client stuck after d-i installation finished "
                         "(warm reboot hung?) — cold restart")
                    virsh("destroy", cfg.node_name, check=False)
                    virsh("start", cfg.node_name)
                    finish_at = now
                    quiet_until = now + 180
                else:
                    virsh("qemu-monitor-command", cfg.node_name, "--hmp",
                          "sendkey ret", check=False)
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True, name="client-enter")
    t.start()
    return stop


def lease_ip(cfg):
    """Latest lease (most recent timestamp) for the client MAC —
    read in the container (dnsmasq writes to the mounted volume)."""
    r = docker("exec", CONTAINER, "awk",
               "-v", f"m={cfg.node_mac}",
               "BEGIN{max=0} tolower($2)==m && $1>max {max=$1; ip=$3} "
               "END{if (ip!=\"\") print ip}",
               LEASE_FILE, check=False)
    return (r.stdout or "").strip()


def step_wait_installed_client(cfg):
    """Waits for the Debian installation to finish + the SSH gate, then
    runs the Proxmox VE 9.2 conversion in 2 phases (install-pve-on-debian.sh)."""
    stop_watch = step_watch_install(cfg)

    log("Waiting for the client lease then SSH (:2222, deploy key)...")
    gate_cmd = "test -d /run/systemd/system && hostname -f"
    client_ip, gate_ok = "", False
    # wait window for the first SSH after the install: 60 min by default,
    # overridable (slow installs / resume after pause: WAIT_SSH_MAX_MIN)
    deadline = time.monotonic() + 60 * int(os.environ.get("WAIT_SSH_MAX_MIN", "60"))
    while time.monotonic() < deadline:
        ip = lease_ip(cfg)
        if ip and ip != client_ip:
            client_ip = ip
            log(f"Client {cfg.node_mac} -> {client_ip}")
        if client_ip and ssh_in_container(client_ip, gate_cmd).returncode == 0:
            gate_ok = True
            break
        time.sleep(10)
    if not gate_ok:
        stop_watch.set()
        die("Client never reachable over SSH (incomplete installation? "
            "see workdir/logs/di-syslog.log).")
    stop_watch.set()
    log(f"Debian installed and reachable: root@{client_ip}:2222")

    # lock PXE boot to the disk (prevents re-installation)
    mac = cfg.node_mac.replace(":", "-").lower()
    with open(os.path.join(cfg.workdir, "tftp", "pxelinux.cfg",
                           f"01-{mac}"), "w") as f:
        f.write("DEFAULT local\nLABEL local\n  LOCALBOOT 0\n")
    log("pxelinux: client PXE boot locked to its disk (LOCALBOOT)")

    # Debian 13 -> Proxmox VE 9.2 conversion (2 phases) via docker exec
    if cfg.iso_mode:
        # install came from the ISO: the system's apt sources point to
        # the cdrom. Switch to the official Debian mirrors BEFORE the
        # conversion: the upgrades (full-upgrade of install-pve) will
        # then happen through these repositories, i.e. once "switched
        # over to PVE".
        log("ISO: replacing cdrom sources with the Debian mirrors")
        r = ssh_in_container(client_ip,
                             "cat > /root/iso-apt-fixup.sh && "
                             "bash /root/iso-apt-fixup.sh",
                             check=False, timeout=600, input=ISO_APT_FIXUP)
        if r.returncode != 0:
            die("apt source switch (post-ISO) failed: "
                f"{(r.stdout or r.stderr or '').strip()[:300]}")

    # --encrypt: LUKS -> vTPM enrollment. Runs BEFORE the conversion: its
    # 2 unattended reboots need the automatic unlock (and, in ISO mode,
    # after the fixup above that restored the Debian mirrors, from which
    # clevis is installed). clevis instead of systemd-cryptenroll because
    # Debian/Proxmox unlock the root in an initramfs-tools environment.
    if cfg.encrypt:
        pcrs = cfg.luks_tpm_pcrs
        pin_cfg = ('{"pcr_bank":"sha256","pcr_ids":"%s"}' % pcrs
                   ) if pcrs else "{}"
        log(f"Encrypt: enrolling the LUKS2 container of {client_ip} in "
            f"the vTPM (clevis tpm2, "
            f"{'PCR ' + pcrs if pcrs else 'no PCR binding'})")
        r = ssh_in_container(client_ip,
                             "cat > /root/luks-tpm-enroll.sh && "
                             "chmod +x /root/luks-tpm-enroll.sh && "
                             "bash /root/luks-tpm-enroll.sh",
                             check=False, timeout=900,
                             input=LUKS_TPM_ENROLL
                                 .replace("__LUKSPW__", cfg.luks_pw)
                                 .replace("__PINCFG__", pin_cfg))
        if r.returncode != 0:
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            out = out.strip() or "(no output)"
            # keep the tail: enroll errors (dpkg/apt, clevis) surface at the
            # END of the output, a head slice hid the real cause once.
            die("LUKS->vTPM enrollment failed: " + out[-1500:])
        # verification = this reboot: nobody is there to type the LUKS
        # passphrase, so SSH coming back means the vTPM actually unlocked
        log("Encrypt: rebooting the node — proves the vTPM auto-unlock")
        ssh_in_container(client_ip, "systemctl reboot", check=False)
        # first wait for SSH to go DOWN (it answers briefly during the
        # shutdown), then for it to come back: nobody can type the
        # passphrase, so SSH returning means the vTPM actually unlocked
        down = time.monotonic() + 5 * 60
        while time.monotonic() < down:
            if ssh_in_container(client_ip, "true", check=False).returncode != 0:
                break
            time.sleep(5)
        ok, end = False, time.monotonic() + 15 * 60
        while time.monotonic() < end:
            if ssh_in_container(client_ip, "true", check=False).returncode == 0:
                ok = True
                break
            time.sleep(15)
        if not ok:
            die("Node unreachable after the enrollment reboot — the vTPM "
                "auto-unlock failed (node blocked on the LUKS passphrase "
                "prompt? console: `virsh console` / VNC).")
        log("Encrypt: vTPM auto-unlock verified (node rebooted unattended)")

    script = open(os.path.join(cfg.workdir, "files", "install-pve-on-debian.sh"),
                  encoding="utf-8").read()

    def push_and_run():
        r = ssh_in_container(client_ip,
                             "cat > /root/install-pve-on-debian.sh && "
                             "chmod +x /root/install-pve-on-debian.sh && "
                             "bash /root/install-pve-on-debian.sh",
                             check=False, timeout=1200, input=script)
        return r.returncode

    def wait_ssh(mins):
        end = time.monotonic() + mins * 60
        while time.monotonic() < end:
            r = ssh_in_container(client_ip, "true", check=False)
            if r.returncode == 0:
                return True
            time.sleep(15)
        return False

    log("Phase 1: Proxmox kernel + reboot (~2 min)")
    push_and_run()
    if not wait_ssh(12):
        die("SSH unreachable after phase 1 (hung reboot? rerun the deployment).")
    log("Phase 2: proxmox-ve + vmbr0 bridge + final reboot (~3-5 min)")
    push_and_run()
    if not wait_ssh(12):
        die("SSH unreachable after phase 2.")

    # tolerant final check: the phase 2 final reboot may still be in
    # progress (SSH still answers briefly during shutdown, and the
    # control pveversion may land inside the reboot) — retry for several
    # minutes before concluding failure.
    version, waited = "", False
    deadline = time.monotonic() + 10 * 60
    while time.monotonic() < deadline and not version:
        if waited:
            time.sleep(20)
        waited = True
        r = ssh_in_container(client_ip, "pveversion 2>/dev/null", check=False)
        version = (r.stdout or "").strip()
    if not version:
        die("pveversion unreachable after 10 min — is Proxmox VE installed? "
            "Check manually then rerun (the deployment is idempotent).")
    log(f"Proxmox VE installed: {version}")
    cfg.client_ip, cfg.pve_version = client_ip, version
    return client_ip


def step_inventory_and_report(cfg):
    inv_dir = os.path.join(cfg.repo_dir, "ansible", "inventory")
    os.makedirs(inv_dir, exist_ok=True)
    inventory = f"""all:
  children:
    pve_nodes:
      hosts:
        {cfg.node_name}:
          ansible_host: {cfg.client_ip}
          ansible_port: 2222
          ansible_user: adminlab
          ansible_become: true
"""
    # multi-node: keep the other hosts' blocks, update this one.
    # Line-based: split("\n        ") also matches INSIDE a host block (a
    # 10-space sub-line starts with the 8-space separator), which used to
    # leak the OLD keys of the re-deployed host into the inventory as
    # duplicate entries — YAML then kept the stale ansible_host.
    inv_path = os.path.join(inv_dir, "hosts.yml")
    if os.path.isfile(inv_path):
        kept = []      # blocks of other hosts: ["        name:\n", ...]
        cur = None
        for ln in open(inv_path, encoding="utf-8").read().splitlines():
            m = re.match(r"^        ([\w.-]+):\s*$", ln)
            if m:                        # host line (8 spaces)
                if m.group(1) != cfg.node_name:
                    cur = [ln]
                    kept.append(cur)
                else:
                    cur = None           # re-deployed host: replaced
            elif cur is not None and re.match(r"^          \S", ln):
                cur.append(ln)           # sub-key of a kept host
        if kept:
            inventory += "".join("\n".join(b) + "\n" for b in kept)
    # The container set {PXE_IP} on {PXE_BRIDGE}: the host routes to the
    # segment, direct SSH (port 2222) works from the controller.
    open(inv_path, "w", encoding="utf-8").write(inventory)
    log(f"Ansible inventory written: {inv_dir}/hosts.yml")

    print("\n".join([
        "",
        "=================================================",
        " PXE lab (container) deployed — pxe-client ready",
        "",
        f" pxe-server (docker): {CONTAINER} — {PXE_IP}/24 on pxebr0",
        f"   logs: docker logs {CONTAINER}",
        f"   leases: {cfg.workdir}/logs/dnsmasq.leases",
        f"   in the container: ssh -p 2222 -i /srv/keys/deploy_key root@{cfg.client_ip}",
        "",
        f" pxe-client: {cfg.client_ip} — {cfg.pve_version}",
        (f"   installed from the full ISO: {cfg.iso_local}"
         if cfg.iso_mode else "   installed from the Debian netboot"),
        ("   disk: LUKS2 encrypted — auto-unlock by the vTPM (clevis "
         "tpm2); manual fallback = the LUKS passphrase (LUKS_PW)"
         if cfg.encrypt else
         "   disk: unencrypted (deploy with --encrypt for LUKS2 + vTPM)"),
        f"   Proxmox UI: docker exec -it {CONTAINER} ssh -p 2222 "
        f"-i /srv/keys/deploy_key -L 8006:{cfg.client_ip}:8006 root@{cfg.client_ip}",
        "                (from another terminal) then http://localhost:8006",
        "",
        " CIS L2 hardening (optional):",
        f"   cd {cfg.repo_dir} && ansible-galaxy install -r ansible/requirements.yml --roles-path ansible/roles --force",
        "   ansible-playbook -i ansible/inventory/hosts.yml ansible/site.yml  (from the container,",
        f"   or adapt ansible_ssh_common_args with ssh -J via {CONTAINER})",
        "",
        " Lab shutdown/removal:",
        f"   virsh destroy {cfg.node_name} && virsh undefine {cfg.node_name}",
        f"   docker stop -t 5 {CONTAINER} && docker rm {CONTAINER}   "
        "(stop triggers the entrypoint's iptables/IP cleanup)",
        "=================================================",
    ]))


class Config:
    def __init__(self, args):
        self.root_pw = args.root_pw or os.environ.get("ROOT_PW", "ChangeMeRoot!")
        self.admin_pw = args.admin_pw or os.environ.get("ADMIN_PW", "ChangeMeAdmin!")
        self.ssh_pubkey_file = args.ssh_pubkey or os.environ.get(
            "SSH_PUBKEY_FILE", os.path.expanduser("~/.ssh/id_rsa.pub"))
        self.disk_pool = args.disk_pool or os.environ.get(
            "DISK_POOL", "libvirt-work")
        # target node (multi-node: pxe-client, pxe-node2, pxe-node3...)
        self.node_name = args.client or os.environ.get("CLIENT_NAME", CLIENT_NAME)
        self.node_mac = args.mac or os.environ.get("CLIENT_MAC", CLIENT_MAC)
        self.node_hostname = args.hostname or self.node_name.replace("-", "")
        self.node_vcpus = int(os.environ.get("CLIENT_VCPUS", args.vcpus or CLIENT_VCPUS))
        self.node_ram = int(os.environ.get("CLIENT_RAM", args.ram or CLIENT_RAM))
        self.node_disk_gb = int(os.environ.get("CLIENT_DISK_GB", args.disk_gb or CLIENT_DISK_GB))
        # additional OSD disk (0 = none): 2nd virtio disk for Ceph
        self.node_osd_gb = int(os.environ.get("CLIENT_OSD_GB", args.osd_disk_gb or 0))
        self.pxe_dir = os.path.dirname(os.path.abspath(__file__)) + "/pxe"
        self.repo_dir = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        self.workdir = args.workdir or os.environ.get(
            "WORKDIR", "/mnt/DATA/ProxKnox/libvirt-work/pxe-work")
        self.client_ip = ""
        self.pve_version = ""
        self.disk_client = ""
        # Disk encryption (LUKS2 + vTPM auto-unlock), opt-in: --encrypt or
        # ENCRYPT_DISK=1. LUKS_PW stays in keyslot 0 as manual fallback;
        # LUKS_TPM_PCRS: optional clevis PCR binding ("" = none, cf.
        # LUKS_TPM_ENROLL), e.g. "7" or "0,2,7".
        self.encrypt = bool(args.encrypt or os.environ.get(
            "ENCRYPT_DISK", "").strip().lower() in ("1", "true", "yes", "on"))
        self.luks_pw = args.luks_pw or os.environ.get("LUKS_PW", "ChangeMeLuks!")
        if self.encrypt and re.search(r'[\s"`\'\\$]', self.luks_pw):
            die("LUKS_PW contains characters that break the unattended "
                "enrollment/passphrase plumbing (space, quote, $, ` or \\) "
                "— pick a passphrase without them.")
        self.luks_tpm_pcrs = os.environ.get("LUKS_TPM_PCRS", "").strip()
        # ISO mode ("--iso" alone -> default ISO; "--iso path|URL" ->
        # provided ISO; ISO_PATH env var also accepted)
        iso_arg = args.iso
        if iso_arg is None and os.environ.get("ISO_PATH"):
            iso_arg = os.environ["ISO_PATH"]
        if iso_arg is not None and iso_arg == "":
            iso_arg = os.environ.get("ISO_URL", ISO_URL)
        self.iso_mode = iso_arg is not None
        self.iso_path = iso_arg or ""
        self.iso_local = ""
        self.iso_sha256 = (ISO_SHA256 if self.iso_mode
                           and "DVD-1" in self.iso_path else "")
        if self.iso_mode and not self.iso_path.startswith(
                ("http://", "https://", "ftp://")):
            self.iso_path = os.path.abspath(self.iso_path)


def main() -> int:
    p = argparse.ArgumentParser(
        description="ProxKnox PXE (container edition): docker PXE server "
                    "(DHCP/TFTP/DNS/HTTP/NAT) + pxe-client VM auto-installed "
                    "with Debian 13 then converted to Proxmox VE 9.2.")
    p.add_argument("--root-pw", default=None)
    p.add_argument("--admin-pw", default=None)
    p.add_argument("--ssh-pubkey", default=None)
    p.add_argument("--disk-pool", default=None)
    p.add_argument("--client", default=None,
                   help="client VM name (default: pxe-client)")
    p.add_argument("--mac", default=None,
                   help="client PXE MAC (default: 52:54:00:aa:00:10)")
    p.add_argument("--hostname", default=None,
                   help="client d-i hostname (default: <client> without dashes)")
    p.add_argument("--vcpus", type=int, default=None)
    p.add_argument("--ram", type=int, default=None, help="MiB (default: 8192)")
    p.add_argument("--disk-gb", type=int, default=None)
    p.add_argument("--osd-disk-gb", type=int, default=0,
                   help="size (GB) of an additional OSD disk (default: 0 = none)")
    p.add_argument("--encrypt", action="store_true",
                   help="LUKS2 disk encryption of the node's root disk with "
                        "automatic unlock by the VM's vTPM (clevis tpm2): "
                        "swtpm required on the host; the preseed installs "
                        "LVM-inside-LUKS, the passphrase (LUKS_PW, default "
                        "ChangeMeLuks!) stays as manual fallback; can also "
                        "be enabled with ENCRYPT_DISK=1")
    p.add_argument("--luks-pw", default=None,
                   help="LUKS passphrase of the encrypted container "
                        "(default: env LUKS_PW or ChangeMeLuks!)")
    p.add_argument("--workdir", default=None,
                   help="workdir of the served files (default: "
                        "/mnt/DATA/ProxKnox/libvirt-work/pxe-work)")
    p.add_argument("--iso", nargs="?", const="", default=None,
                   help="installs Debian from a FULL ISO (DVD-1): downloaded "
                        "locally if no path/URL given (default: "
                        "cdimage.debian.org current), packages pulled from "
                        "the ISO, NO upgrade during installation (upgrades "
                        "happen via the Proxmox conversion). Without this "
                        "flag: classic netboot + debian mirror.")
    args = p.parse_args()
    cfg = Config(args)

    steps = [
        ("prerequisites + pxe-lab network", lambda: step_prereqs(cfg)),
    ]
    if cfg.iso_mode:
        steps.append(("local full ISO (download)",
                      lambda: step_fetch_iso(cfg)))
    steps += [
        ("preseed + TFTP + keys", lambda: step_prepare_files(cfg)),
        ("PXE image + container", lambda: step_build_and_run_container(cfg)),
        ("pxe-client VM (PXE boot)", lambda: step_create_client(cfg)),
        ("Debian + Proxmox VE", lambda: step_wait_installed_client(cfg)),
        ("inventory + report", lambda: step_inventory_and_report(cfg)),
    ]
    for name, fn in steps:
        try:
            log(f"--- {name} ---")
            fn()
        except DeployError as e:
            print(f"\033[1;31m[error]\033[0m {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

#!/usr/bin/env bash
# e2e-cluster-test.sh — ProxKnox End-to-End cluster test procedure
#
# SCENARIO (everything automated, each phase resumable):
#   1. DEPLOYMENT   3 real PXE nodes (existing pxe-client + pxe-node2/3,
#                  Debian 13 from the DVD-1 ISO, dedicated 15 GB OSD disk)
#   2. NETWORK      static vmbr0 (192.168.200.11/12/13) + hostnames
#   3. CLUSTER      pvecm create + add — 3-vote quorum
#   4. HARDENING    harden-pve.yml (12 roles ON by default, cluster/Ceph
#                  rules in the PVE firewall)
#   5. CEPH         pveceph init + 1 OSD/node + vmstorage pool (3/2)
#   6. HARDENING    resumption: pve_ceph now applies to the deployed Ceph
#                  (cephx + msgr2 secure) + pve_audit (JSON)
#   7. TESTS        e2e_test role: mini Debian cloud VM (1 CPU/2 GB) on
#                  RBD, ping, 2 live-migrations with measured downtime
#   8. REPORT       e2e-cluster-test_<epoch>.json → md + html (single page)
#
# Usage:
#   ./tools/e2e-cluster-test.sh                  # everything (or auto-resume)
#   ./tools/e2e-cluster-test.sh --phase deploy   # a single phase
#                     (deploy|net|cluster|hardening|ceph|tests|report)
#   ./tools/e2e-cluster-test.sh --check           # prerequisites only
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
unset LD_LIBRARY_PATH   # GLIBCXX from the IDE breaks virsh (cf. docs § 8)
export ANSIBLE_CONFIG="$REPO/ansible/ansible.cfg"
export ANSIBLE_STDOUT_CALLBACK=default

INV="ansible/inventory/hosts.yml"
NODE2="pxe-node2"; NODE2_MAC="52:54:00:aa:00:12"
NODE3="pxe-node3"; NODE3_MAC="52:54:00:aa:00:13"
OSD_GB=15
WORKDIR_CLOUD="/mnt/DATA/ProxKnox/libvirt-work/pxe-work/iso/debian-13-generic-amd64.qcow2"
CLOUD_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2"

step() { printf '\e[1;34m[e2e]\e[0m %s\n' "$*"; }

check_prereqs() {
  step "0/8 Prerequisites (docker, libvirt, lab network, ISO, Ansible)"
  command -v docker >/dev/null || { echo "docker missing"; exit 1; }
  command -v virsh >/dev/null || { echo "virsh missing"; exit 1; }
  virsh net-info pxe-lab 2>/dev/null | grep -q 'Active:.*yes' \
    || { echo "pxe-lab network inactive"; exit 1; }
  test -f /mnt/DATA/ProxKnox/libvirt-work/pxe-work/iso/debian-13.7.0-amd64-DVD-1.iso \
    || { echo "DVD-1 ISO missing from cache (run the single-node chain once: ./tools/full-chain.sh --iso)"; exit 1; }
  python3 tools/audit-report.py --help >/dev/null 2>&1 || true
  step "Prerequisites OK"
}

deploy_node() { # $1 VM name, $2 MAC
  step "PXE deployment of $1 (DVD-1 ISO, + ${OSD_GB} GB OSD disk)"
  python3 deploy/pxe_deploy_container.py --iso \
    --client "$1" --mac "$2" --osd-disk-gb "$OSD_GB"
}

phase_deploy() {
  # node 1 = pxe-client: deployed/hardened by the standard chain (assumed)
  unset LD_LIBRARY_PATH
  virsh domstate pxe-client 2>/dev/null | grep -q '^running$' \
    || { virsh start pxe-client; sleep 60; }
  docker start proxknox-pxe 2>/dev/null >/dev/null || true  # segment NAT
  # pxe-client comes from the single-node chain: NO OSD disk — Ceph needs
  # one on every node: create + attach it (live + next boots) if missing.
  if ! virsh domblklist pxe-client 2>/dev/null | grep -q pxe-client-osd; then
    step "pxe-client: adding the ${OSD_GB} GB OSD disk"
    DISK_POOL="${DISK_POOL:-libvirt-work}"   # default of the deploy tool
    virsh vol-create-as "$DISK_POOL" pxe-client-osd.qcow2 \
      "${OSD_GB}G" --format qcow2
    osd_vol=$(virsh vol-path --pool "$DISK_POOL" pxe-client-osd.qcow2)
    # attach as an EXPLICIT qcow2 disk (plain attach-disk presents a raw
    # sparse file: the guest would see ~200 KB and OSD creation aborts)
    osd_xml=$(mktemp)
    printf "%s\n" \
      "<disk type='file' device='disk'>" \
      "  <driver name='qemu' type='qcow2' cache='none' discard='unmap'/>" \
      "  <source file='$osd_vol'/>" \
      "  <target dev='vdb' bus='virtio'/>" \
      "</disk>" > "$osd_xml"
    virsh attach-device pxe-client "$osd_xml" --config
    virsh attach-device pxe-client "$osd_xml" --live
    rm -f "$osd_xml"
  fi
  virsh dominfo "$NODE2" >/dev/null 2>&1 || deploy_node "$NODE2" "$NODE2_MAC"
  virsh dominfo "$NODE3" >/dev/null 2>&1 || deploy_node "$NODE3" "$NODE3_MAC"
  step "3 nodes deployed (pxe-client, $NODE2, $NODE3)"
}

fix_inventory_static() {
  step "Inventory → static IPs (.11/.12/.13)"
  python3 - <<'EOF'
import re
p = "ansible/inventory/hosts.yml"
src = open(p, encoding="utf-8").read()
ip = {"pxe-client": "192.168.200.11", "pxe-node2": "192.168.200.12",
      "pxe-node3": "192.168.200.13"}
for name, addr in ip.items():
    src = re.sub(rf"(\n        {re.escape(name)}:\n          ansible_host: )[0-9.]+",
                 rf"\g<1>{addr}", src)
open(p, "w", encoding="utf-8").write(src)
print("static inventory OK")
EOF
}

phase_net()      { step "Phase net: static vmbr0 + hostnames (+node reboot)"
                   ansible-playbook -i "$INV" ansible/e2e-cluster.yml --tags net
                   fix_inventory_static; sleep 30; }

phase_cluster()  { step "Phase cluster: pvecm create/add — 3-vote quorum"
                   ansible-playbook -i "$INV" ansible/e2e-cluster.yml --tags cluster; }

phase_hardening(){ step "Phase hardening: 12 roles ON + cluster/Ceph fw rules"
                   ansible-playbook -i "$INV" ansible/harden-pve.yml \
                     -e pve_firewall_cluster_services=true; }

phase_ceph()     { step "Phase ceph: pveceph init + OSD + pool"
                   ansible-playbook -i "$INV" ansible/e2e-cluster.yml --tags ceph
                   # Ceph hardening (pve_ceph) now applies:
                   ansible-playbook -i "$INV" ansible/harden-pve.yml \
                     -e pve_firewall_cluster_services=true -t hardening,ceph,audit; }

phase_tests()    { step "Phase tests: mini VM + live migrations"
                   if [ ! -s "$WORKDIR_CLOUD" ]; then
                     step "Downloading the Debian 13 cloud image (~350 MB)"
                     curl -fL --retry 3 -o "$WORKDIR_CLOUD" "$CLOUD_URL"
                   fi
                   ssh -p 2222 -o StrictHostKeyChecking=no adminlab@192.168.200.12 \
                     'true' 2>/dev/null || true
                   ansible-playbook -i "$INV" ansible/e2e-tests.yml; }

phase_report()   { step "Phase report: JSON → md + html"
                   python3 tools/e2e-report.py
                   ls -1 docs/runs/report-e2e-cluster_*.md 2>/dev/null | tail -1; }

PHASE=all
[ "${1:-}" = "--check" ] && { check_prereqs; exit 0; }
if [ "${1:-}" = "--phase" ]; then PHASE="${2:?phase missing}"; fi
case "$PHASE" in
  all)
    check_prereqs
    phase_deploy; phase_net; phase_cluster; phase_hardening
    phase_ceph; phase_tests; phase_report
    ;;
  deploy|net|cluster|hardening|ceph|tests|report)
    "phase_${PHASE}" ;;
  *) echo "Unknown phase: $PHASE (deploy|net|cluster|hardening|ceph|tests|report|all)"; exit 1 ;;
esac
step "Done ($PHASE)."

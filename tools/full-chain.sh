#!/usr/bin/env bash
# full-chain.sh — The complete ProxKnox chain, end to end, in one command:
#
#   1. DEPLOYMENT    : pxe_deploy_container.py [--iso] — ISO/netboot →
#                     pxe-client Debian 13 VM → Proxmox VE 9.2 conversion
#                     (writes ansible/inventory/hosts.yml)
#   2. HARDENING     : harden-pve.yml — PROXMOX hardening
#                     (HomeSecExplorer guide v0.9.2, toggleable roles)
#                     then site.yml — CIS Debian 13 L2 benchmark
#                     (ansible-lockdown.DEBIAN13-CIS + scoped PVE deviations)
#   3. AUDIT         : syver before/after remediation (scans: collected into
#                     ansible/audit-summaries/, cf. site.yml + group_vars)
#   4. REPORT        : tools/audit-report.py — consolidated md + html report
#                     in docs/runs/
#
# Usage:
#   ./tools/full-chain.sh                       # netboot + full hardening
#   ./tools/full-chain.sh --iso                 # from the DVD-1 ISO (no d-i updates)
#   ./tools/full-chain.sh --bootstrap           # (re)install galaxy first
#   ./tools/full-chain.sh --skip-deploy         # node already deployed
#   ./tools/full-chain.sh --skip-pve-hardening / --skip-cis
#   ./tools/full-chain.sh --reports-only        # just audit→report (node already hardened)
#   ./tools/full-chain.sh --check               # only check prerequisites
#
# Env    : ROOT_PW, ADMIN_PW, SSH_PUBKEY_FILE, DISK_POOL, WORKDIR, ISO_PATH,
#          ISO_URL (cf. deploy/pxe_deploy_container.py --help)
#
# The lab is left running at the end of the chain.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Reproducible Ansible environment on this machine:
#  - LD_LIBRARY_PATH (VS Code CLI) breaks virsh → removed for children
#  - `yaml` callback removed from community.general >= 12 → default forced
export ANSIBLE_CONFIG="$REPO/ansible/ansible.cfg"
export ANSIBLE_STDOUT_CALLBACK="${ANSIBLE_STDOUT_CALLBACK:-default}"
unset LD_LIBRARY_PATH

ISO_ARGS=()
RUN_DEPLOY=1 RUN_PVE=1 RUN_CIS=1 BOOTSTRAP=0 CHECK=0
for arg in "$@"; do
  case "$arg" in
    --iso)             ISO_ARGS+=(--iso);;
    --bootstrap)       BOOTSTRAP=1;;
    --skip-deploy)     RUN_DEPLOY=0;;
    --skip-pve-hardening) RUN_PVE=0;;
    --skip-cis)        RUN_CIS=0;;
    --reports-only)    RUN_DEPLOY=0; RUN_PVE=0; RUN_CIS=0;;
    --check)           CHECK=1;;
    *) echo "Unknown option: $arg" >&2; exit 2;;
  esac
done

step()  { printf '\033[1;34m[chain]\033[0m %s\n' "$*"; }
fatal() { printf '\033[1;31m[chain]\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------ 0. prerequisites ---
step "0/4 Prerequisites"
missing=()
for c in python3 ansible-playbook virsh virt-install docker wget; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
if [ ${#ISO_ARGS[@]} -gt 0 ]; then
  command -v xorriso >/dev/null 2>&1 || missing+=(xorriso)
fi
[ ${#missing[@]} -gt 0 ] && fatal "missing commands: ${missing[*]}"

env -u LD_LIBRARY_PATH virsh -c qemu:///system net-info default >/dev/null 2>&1 \
  || fatal "libvirt network 'default' unreachable (virsh)"
docker info >/dev/null 2>&1 || fatal "Docker daemon unreachable"
INV="ansible/inventory/hosts.yml"
if [ "$RUN_DEPLOY" -eq 0 ] && [ ! -f "$INV" ]; then
  fatal "inventory $INV missing -- run the deployment first (without --skip-deploy)"
fi
if [ "$BOOTSTRAP" = 1 ]; then
  step "0/4 Prerequisites OK (galaxy bootstrap requested)"
else
  step "0/4 Prerequisites OK"
fi

if [ "$CHECK" -eq 1 ]; then
  step "Check mode: prerequisites verified, nothing else executed."
  exit 0
fi

if [ "$BOOTSTRAP" -eq 1 ]; then
  step "Bootstrap: ansible-galaxy install -r ansible/requirements.yml --roles-path ansible/roles --force"
  ansible-galaxy install -r ansible/requirements.yml --roles-path ansible/roles --force
fi

# ------------------------------------------------------------- 1. deployment ---
if [ "$RUN_DEPLOY" -eq 1 ]; then
  step "1/4 Deployment: pxe_deploy_container.py ${ISO_ARGS[*]:-}"
  python3 deploy/pxe_deploy_container.py "${ISO_ARGS[@]}"
else
  step "1/4 Deployment: SKIPPED (--skip-deploy / --reports-only)"
fi

# ------------------------------------------------------------- 2. hardening ---
if [ "$RUN_PVE" -eq 1 ]; then
  step "2/4 Proxmox hardening (HomeSecExplorer guide): harden-pve.yml"
  ansible-playbook -i "$INV" ansible/harden-pve.yml
else
  step "2/4 Proxmox hardening: SKIPPED"
fi

if [ "$RUN_CIS" -eq 1 ]; then
  step "3/4 CIS Debian 13 L2 hardening (ansible-lockdown) + syver audit: site.yml"
  # a reboot may occur (change_requires_reboot) — reconnection is handled by the role
  ansible-playbook -i "$INV" ansible/site.yml
else
  step "3/4 CIS L2 hardening: SKIPPED"
fi

# ------------------------------------------------------------ 4. reports ---
step "4/4 Consolidated report (md + html)"
# PVE_VERSION can be set to override the version shown in the report
python3 tools/audit-report.py --audit-dir ansible/audit-summaries \
  --node "${NODE:-pxe-client}" \
  --pve "${PVE_VERSION:-pve-manager/9.x}" \
  --chain "deployment ${ISO_ARGS[*]:-netboot} → Proxmox VE 9.2 → harden-pve.yml (HSE + Ceph, guides ON by default) → site.yml (CIS L2) → syver + pve_audit"

step "Chain complete. Reports:"
ls -1 docs/runs/report-audit-cis-debian13-pve_*.md 2>/dev/null | tail -2 || true
IP=$(grep -m1 "ansible_host" "$INV" | awk '{print $2}')
step "Access: ssh -p 2222 adminlab@${IP:-<ip>}   (inventory: $INV)"
step "Remaining manual steps (cf. docs/full-chain.md): 2FA UI, certificates, backups."

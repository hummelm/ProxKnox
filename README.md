# ProxKnox

**Automated chain: Debian 13 → hardened Proxmox VE 9.2 → audit → cluster E2E test.**

French version: [README.fr.md](README.fr.md).

From a local Linux machine, with no server VM and no manual preparation:

- `./tools/full-chain.sh --iso` produces a **hardened Proxmox VE 9.2 node**
  (12 hardening roles per the HomeSecExplorer PVE9 guide + **CIS Debian 13
  Level 2** benchmark via `ansible-lockdown.DEBIAN13-CIS`, pinned 1.1.0)
  and a **consolidated audit report** (md + html);
- `./tools/e2e-cluster-test.sh` deploys a **3-node cluster** (real multi-node
  PXE), enables **Ceph** on it, runs a **test VM (1 vCPU/2 GB on RBD)**,
  **2 live migrations with ping-measured downtime**, and produces a
  **JSON test report** (ansible-lockdown style) rendered as a
  **single-page HTML** document.

| Reference run | Measured result |
|---|---|
| CIS L2 audit — 19/11/2026 | **304 failures → 14** out of 891 controls (**97.5 %** compliance); 8 controls manual by design |
| PVE hardening audit — 19/11/2026 | **0 deviations** (`pve_audit` role, 20 JSON controls) |
| Cluster E2E test — 24/11/2026 | **12/12 pass** — 2 live migrations (9-10 s), **0 lost ping responses** (44 samples at 200 ms), Ceph `HEALTH_OK` before/after |
| Encrypted chain (`--encrypt`) — 25/11/2026 | Same chain with **LUKS2 + vTPM auto-unlock** (HSE § 1.2.1): unattended boots verified at every reboot, audit unchanged — CIS 304 → 14 (**97.5 %**), 0 PVE deviation, 0 reachable gap closed: stolen/vanished disk no longer exposes data at rest |
| Encrypted cluster E2E (`ENCRYPT_DISK=1`) — 25/11/2026 | **12/12 pass on 3 encrypted nodes** — quorum 3/3, Ceph `HEALTH_OK` (3 OSDs, hardened), 2 online migrations (~9 s) with the VM pinging throughout, `vmstorage` on RBD |

Reports are versioned in [`docs/runs/`](docs/runs/) — source JSON (not
versioned) lives in `ansible/audit-summaries/`.

> Detailed reference: **[`docs/full-chain.md`](docs/full-chain.md)** —
> prerequisites, deployment modes, hardening, reports, troubleshooting
> (all observed on real runs). Appendix:
> [`docs/ansible-analysis.md`](docs/ansible-analysis.md) (feasibility of a
> redesign of the deployment in Ansible).

---

## The chain at a glance

```
  tools/full-chain.sh [--iso] [--encrypt] [--bootstrap]
  │
  ├─ 1. DEPLOYMENT   deploy/pxe_deploy_container.py [--iso] [--client …]
  │     libvirt network “pxe-lab” → Docker container proxknox-pxe
  │     (DHCP + TFTP + DNS + HTTP :8000 + NAT, host netns)
  │     → VM installed by Debian-installer (preseed, NO updates in --iso mode)
  │     → [--encrypt] LUKS2 disk + TPM auto-unlock (vTPM, clevis tpm2)
  │     → Proxmox VE 9.2 conversion (deploy/install-pve-on-debian.sh, 2 phases)
  │     → ansible/inventory/hosts.yml
  ├─ 2. PVE HARDENING ansible/harden-pve.yml — HomeSecExplorer guide v0.9.2
  │     12 roles ON by default (each toggled by a variable): microcode,
  │     unattended-upgrades, fail2ban (sshd + UI 8006), KSM off, RBAC,
  │     auditd /etc/pve, rkhunter, corosync secauth, PVE firewall,
  │     ssh-audit, Ceph hardening → pve_audit audit: JSON
  ├─ 3. CIS HARDENING ansible/site.yml — ansible-lockdown DEBIAN13-CIS L2
  │     remediation + documented Proxmox deviations → REBOOT → syver
  │     pre/post-remediation audits (JSON)
  └─ 4. REPORT       tools/audit-report.py → md + html (docs/runs/)
        single page, zero dependencies, high-level stats,
        per-control drill-down, source JSON embedded in the page
```

---

## Quick start — single hardened node

```bash
# 1. clone and check prerequisites (nested KVM, Docker, libvirt, Ansible)
git clone https://github.com/hummelm/ProxKnox && cd ProxKnox
./tools/full-chain.sh --check

# 2. run the full chain — first run: --bootstrap installs the roles
./tools/full-chain.sh --iso --bootstrap   # ~50-60 min with the ISO already cached
./tools/full-chain.sh --iso               # subsequent runs (~50 min)

# 2bis. encrypted node: LUKS2 disk, auto-unlocked at every reboot by the
#      VM's vTPM (needs swtpm + swtpm-tools on the host)
./tools/full-chain.sh --iso --encrypt

# 3. use the result (hardened node, SSH port 2222)
IP=$(grep -m1 ansible_host ansible/inventory/hosts.yml | awk '{print $2}')
ssh -p 2222 adminlab@$IP                        # adminlab, sudo NOPASSWD
ssh -p 2222 -L 8006:$IP:8006 adminlab@$IP       # UI: https://localhost:8006

# handy afterwards
./tools/full-chain.sh --reports-only       # regenerate the reports
./tools/full-chain.sh --skip-deploy        # re-hardening + audit only
```

### Chain variants

| Need | Command |
|---|---|
| Classic network deployment (Debian mirror) | `./tools/full-chain.sh` |
| Node with **encrypted disk** (LUKS2 + vTPM auto-unlock, HSE § 1.2.1) | `./tools/full-chain.sh --iso --encrypt` |
| Node already deployed: (re)hardening + audit | `./tools/full-chain.sh --skip-deploy` |
| PVE hardening only (no CIS Lockdown) | `./tools/full-chain.sh --skip-deploy --skip-cis` |
| CIS Lockdown only (no PVE hardening) | `./tools/full-chain.sh --skip-deploy --skip-pve-hardening` |
| Audit report only | `./tools/full-chain.sh --reports-only` |
| Check prerequisites | `./tools/full-chain.sh --check` |

---

## Quick start — 3-node cluster lab (Ceph + live migration)

Prerequisites: the single-node chain has already been run once (DVD-1 ISO in
cache).

```bash
./tools/e2e-cluster-test.sh --check            # prerequisites
./tools/e2e-cluster-test.sh                    # everything: 3 real PXE nodes →
                                               #   static vmbr0 → cluster (quorum 3)
                                               #   → 12-role hardening → Ceph
                                               #   (1 OSD/node, RBD pool 3/2)
                                               #   → minimal VM 1c/2GB → 2 live migrations
                                               #   → JSON + md + HTML report
./tools/e2e-cluster-test.sh --phase ceph       # resume a specific phase:
                     # deploy | net | cluster | hardening | ceph | tests | report
```

- **JSON report**: `ansible/audit-summaries/e2e-cluster-test_<epoch>.json` —
  in the same spirit as the ansible-lockdown audits: one result per test
  (id, phase, expected, observed), `pass`/`fail`/`notrun` statuses, summary.
- **Rendering**: `python3 tools/e2e-report.py` →
  `docs/runs/report-e2e-cluster_<date>.[md|html]` — **single-page,
  zero-dependency HTML**: summary cards (tests, migrations, lost pings),
  collapsible per-phase details, one badge per test.
- The test VM (deterministic IP: `dhcp-host` reservation by MAC **and**
  static cloud-init IP) stays in place for inspection.

---

## Hardening — what does the chain do?

### Proxmox VE (HomeSecExplorer guide v0.9.2) — `ansible/harden-pve.yml`

12 roles, **all ON by default**, each toggleable via
`ansible/group_vars/pve_nodes.yml` (`pve_hardening_<role>: true|false`):

`pve_microcode` · `pve_autoupdate` · `pve_fail2ban` (sshd + UI 8006) ·
`pve_ksm` (disabled, inter-VM side-channels) · `pve_users` (RBAC/2FA/tokens) ·
`pve_logging` (auditd `/etc/pve`) · `pve_rkhunter` · `pve_cluster`
(corosync `secauth`) · `pve_firewall` (allow-list 8006/SSH, INPUT DROP) ·
`pve_ssh_audit` (ssh-audit profile, `sshd -t` validated before reload) ·
`pve_ceph` (L2-style Ceph hardening: cephx, msgr2 secure, keyrings 0600;
automatic no-op without Ceph) · `pve_audit` (on-node audit → JSON
`pve-hardening-audit_<epoch>.json`, same format as the CIS scans).

### CIS Debian 13 Level 2 — `ansible/site.yml`

The `ansible-lockdown.DEBIAN13-CIS` role (pinned `1.1.0`) with framed and
documented Proxmox deviations in `group_vars/pve_nodes.yml`:

| Deviation | Variable | Justification |
|---|---|---|
| § 4 host firewall skipped | `deb13cis_section4: false` | the **PVE** firewall is authoritative (ufw + pve-firewall would neutralize each other) |
| § 5.1.8 `DisableForwarding` | `deb13cis_rule_5_1_8: false` | PVE live migration requires an SSH streamlocal port-forward (CIS 5.1.8 broke all incoming migration) |
| IPv4 forwarding / rp_filter | `deb13cis_is_router: true` | Proxmox routes/bridges for its guests |
| IPv6 kept | `deb13cis_ipv6_required: true` | PVE default |
| § 2.1.13 rsync masked | `deb13cis_rsync_mask: true` | dependency of the `proxmox-ve` meta-package |

### Audit and reports

| Source | Output |
|---|---|
| CIS Lockdown role: **syver** pre/post-remediation audits | `ansible/audit-summaries/debian-*-CIS-*.json` |
| `pve_audit` role (HSE + Ceph checks on the node) | `ansible/audit-summaries/pve-hardening-audit_<epoch>.json` |
| Cluster E2E tests (`e2e_test` role, 12 tests T010-T120) | `ansible/audit-summaries/e2e-cluster-test_<epoch>.json` |
| Audit consolidation | `python3 tools/audit-report.py` → `docs/runs/report-audit-cis-debian13-pve_<date>.[md\|html]` (drill-down, **source JSON embedded in the page**) |
| E2E rendering | `python3 tools/e2e-report.py` → `docs/runs/report-e2e-cluster_<date>.[md\|html]` |

---

## Useful variables (deployment)

| Variable | Default | Purpose |
|---|---|---|
| `ROOT_PW` / `ADMIN_PW` | lab values | root/adminlab passwords of the node |
| `SSH_PUBKEY_FILE` | `~/.ssh/id_rsa.pub` | key injected by the preseed |
| `DISK_POOL` | `libvirt-work` | disk pool of the VM |
| `WORKDIR` | `libvirt-work/pxe-work` | served files (TFTP, preseed, ISO, logs) |
| `ISO_PATH` / `ISO_URL` | DVD-1 cdimage (13.7.0) | full ISO (`--iso` mode), SHA-256 verified |
| `ENCRYPT_DISK` | empty | `1` = LUKS2 disk encryption with vTPM auto-unlock (idem `--encrypt`) |
| `LUKS_PW` | `ChangeMeLuks!` | LUKS passphrase (manual fallback keyslot; override before the lab leaves your machine) |
| `LUKS_TPM_PCRS` | empty | optional TPM PCR binding (see [`docs/full-chain.md` § 4.2](docs/full-chain.md#42-disk-encryption---encrypt-luks2--vtpm-auto-unlock)) |
| `WAIT_SSH_MAX_MIN` | `60` | max wait (min) for the client to come up on SSH |
| `CLIENT_NAME`, `CLIENT_MAC` | `pxe-client`, `52:54:00:aa:00:10` | identity of the deployed node (overridable via `--client` / `--mac`) |

Multi-node deployment (cluster lab): `--client`, `--mac`, `--hostname`,
`--vcpus`, `--ram`, `--disk-gb`, `--osd-disk-gb` — see
[`docs/full-chain.md` § 4.1](docs/full-chain.md#41-docker-container-the-one-used-by-the-chain).

---

## Directory layout

```text
ProxKnox/
├── README.md                      ← entry point (this file)
├── docs/
│   ├── full-chain.md              ← detailed reference (prerequisites → troubleshooting)
│   ├── ansible-analysis.md        ← appendix: redesign the deployment in Ansible?
│   └── runs/                      ← generated reports, versioned
├── deploy/                         ← host-side PXE deployment
│   ├── pxe_deploy_container.py     ← orchestrator [--iso] [--client …]: PXE container + VM(s)
│   ├── install-pve-on-debian.sh    ← Debian 13 → PVE 9.2 conversion (2 phases)
│   └── pxe/
│       ├── client-preseed.cfg      ← client preseed (CIS partitions)
│       ├── pxe-lab-network.xml     ← isolated libvirt network “pxe-lab” (no libvirt DHCP)
│       └── container/             ← Docker PXE server (Dockerfile, entrypoint, syslog)
├── ansible/                        ← self-contained Ansible project
│   ├── ansible.cfg · requirements.yml  ← required roles (CIS pinned 1.1.0)
│   ├── site.yml                    ← CIS L2 Lockdown + syver pre/post audits
│   ├── harden-pve.yml              ← PVE hardening: 12 roles ON by default
│   ├── e2e-cluster.yml · e2e-tests.yml ← cluster lab (net/cluster/ceph, E2E tests)
│   ├── group_vars/pve_nodes.yml    ← hardening toggles + CIS deviations
│   ├── inventory/hosts.yml(.example) ← inventory (hosts.yml NOT versioned, generated)
│   ├── audit-summaries/            ← unversioned JSON: syver scans, pve_audit, e2e
│   └── roles/                      ← 12 pve_* roles + e2e_test
│                                     (ansible-lockdown.DEBIAN13-CIS gitignored,
│                                     installed by --bootstrap)
└── tools/
    ├── full-chain.sh               ← full-chain orchestrator
    ├── e2e-cluster-test.sh         ← cluster E2E procedure (--phase …)
    ├── audit-report.py             ← consolidated audit report md + html
    └── e2e-report.py               ← E2E report md + html (single page)
```

Lab workdir (outside the repo, recreatable): `libvirt-work/pxe-work/` — VM
disks, TFTP, preseeds, cached ISO, logs (including `dnsmasq.leases`,
`dnsmasq-dhcp-hosts.conf`).

---

## What remains manual (by design)

| Area | Guide reference |
|---|---|
| UI 2FA (TOTP/YubiKey) + sealed root break-glass account | HSE § 2.1.3/2.1.4 |
| UI certificates (ACME/PKI, `pvenode cert set`) | HSE § 2.3.1/2.3.2 |
| 3-2-1 backups + restore testing | HSE § 4.x |
| Centralized monitoring (Prometheus/Grafana) | HSE § 5.2.x |
| Secure Boot (disk encryption itself: automated via `--encrypt`, [`docs` § 4.2](docs/full-chain.md#42-disk-encryption---encrypt-luks2--vtpm-auto-unlock)) | HSE § 1.1.5 |
| Review of the CIS report’s “Manual Check Required” items | report § residual failures |

## Jump to… (documentation index)

| Need | Where |
|---|---|
| Detailed prerequisites (nested virtualization, packages) | [`docs/full-chain.md` § 2](docs/full-chain.md#2-prerequisites-controller--local-linux-machine) |
| Netboot / ISO modes, options, environment variables | [`docs/full-chain.md` § 3-4](docs/full-chain.md#3-operating-mode) |
| Disk encryption — LUKS2 + vTPM auto-unlock (`--encrypt`) | [`docs/full-chain.md` § 4.2](docs/full-chain.md#42-disk-encryption---encrypt-luks2--vtpm-auto-unlock) |
| Hardening: 12 PVE roles, documented CIS deviations | [`docs/full-chain.md` § 5](docs/full-chain.md#5-hardening) |
| Audit → reports (md/html, embedded JSON) | [`docs/full-chain.md` § 6](docs/full-chain.md#6-audit--reports) |
| 3-node cluster E2E test (Ceph, migrations) | [`docs/full-chain.md` § 6bis](docs/full-chain.md#6bis-end-to-end-3-node-cluster-test-ceph--live-migration) |
| What remains manual | [`docs/full-chain.md` § 7](docs/full-chain.md#7-what-remains-manual-by-design) |
| Troubleshooting (16 incidents observed on real runs) | [`docs/full-chain.md` § 8](docs/full-chain.md#8-troubleshooting-all-observed-during-real-runs) |
| Shutting down / deleting the lab | [`docs/full-chain.md` § 3.5](docs/full-chain.md#35-shutting-down--deleting-the-lab) |
| Reports of validated runs | [`docs/runs/`](docs/runs/) |

## Warnings

- The hardening steps (CIS L2 + PVE hardening) **substantially modify the
  system**: intended for a lab — evaluate every control before production.
- `PermitRootLogin` restricted by CIS: administration via `adminlab`
  (sudo NOPASSWD) or SSH keys.
- Default lab passwords (`ChangeMeRoot!`/`ChangeMeAdmin!`/`ChangeMeLuks!`):
  override them (`ROOT_PW`/`ADMIN_PW`/`LUKS_PW`) as soon as the lab
  leaves your machine.
- Some controls remain manual by nature (2FA, BIOS/hardware, backups) —
  see § 7 above.

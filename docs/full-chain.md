# ProxKnox full chain (reference document)

> **Entry point: `README.md`** (overview + quick start). This document
> is the detailed reference: deployment, hardening, audit → report.
> Appendix: [`docs/ansible-analysis.md`](ansible-analysis.md) (feasibility of
> refactoring the deployment into Ansible).

## 1. Overview

```
            tools/full-chain.sh [--iso] [--bootstrap]
            │
 ┌──────────┴───────────────────────────────────────────────────────┐
 │ 1. DEPLOYMENT   pxe_deploy_container.py [--iso]                 │
 │    pxe-lab network → proxknox-pxe container (DHCP/TFTP/DNS/HTTP/ │
 │    NAT) → pxe-client VM: PXE boot → Debian 13 auto-installed     │
 │    → Proxmox VE 9.2 conversion (2 phases) → ansible/inventory/hosts.yml │
 ├──────────────────────────────────────────────────────────────────┤
 │ 2. PVE HARDENING harden-pve.yml (HomeSecExplorer guide v0.9.2)    │
 │    12 roles, all ON by default (toggled via variables):         │
 │    microcode · unattended-upgrades · fail2ban (sshd+8006) · KSM   │
 │    off · pveum/RBAC accounts · auditd /etc/pve · rkhunter ·      │
 │    corosync secauth · PVE firewall · ssh-audit · Ceph hardening   │
 │    → pve_audit: pve-hardening-audit_<epoch>.json                  │
 ├──────────────────────────────────────────────────────────────────┤
 │ 3. CIS HARDENING site.yml (ansible-lockdown DEBIAN13-CIS L2)     │
 │    CIS Debian 13 benchmark remediation + scoped PVE deviations  │
 │    (rsync masked, section 4 ufw skipped, is_router, ipv6)        │
 │    → REBOOT → pre/post-remediation syver audits                  │
 ├──────────────────────────────────────────────────────────────────┤
 │ 4. AUDIT→REPORT tools/audit-report.py                            │
 │    syver + pve_audit JSON (ansible/audit-summaries/, gitignored) │
 │    → consolidated md + html report in docs/runs/ (annotated     │
 │    failures, PVE deviations)                                     │
 └──────────────────────────────────────────────────────────────────┘
```

Reference runs (Nov. 2026):
- ISO-mode deployment: Debian 13 installed from DVD-1 **without any
  updates** (mirror sources written before the PVE conversion);
- PVE hardening: playbook green, 0 failures;
- CIS L2 audit: **304 failures → 14** (97.5% compliance across 891
  controls, 8 "manual by design") — reports in `docs/runs/`.

## 2. Prerequisites (controller = local Linux machine)

- Linux with **KVM** + **nested virtualization** (Proxmox runs INSIDE
  the VM):
  ```bash
  echo 'options kvm-intel nested=Y' | sudo tee /etc/modprobe.d/kvm-nested.conf
  sudo modprobe -r kvm_intel && sudo modprobe kvm_intel   # (kvm_amd for AMD)
  ```
- Packages: `qemu-kvm libvirt-daemon-system libvirt-clients virtinst ovmf`,
  `wget`, `xorriso` (`--iso` mode), Ansible ≥ 2.16, Docker
- Member of the `libvirt` and `docker` groups (no root needed)
- Active libvirt `default` network (virbr0, 192.168.122.0/24), SSH key
  pair (`~/.ssh/id_ed25519.pub`)

Check without launching anything:

```bash
`./tools/full-chain.sh --check`
```

## 3. Operating mode

### 3.1 A single command (full chain)

```bash
cd ProxKnox
`./tools/full-chain.sh --iso --bootstrap   # first run (installs galaxy)`
`./tools/full-chain.sh --iso               # subsequent runs (~50 min, cached)`
```

Observed duration: **~50-60 min** with the ISO (including ~25 min if
the DVD-1 is not cached in `libvirt-work/pxe-work/iso/`), ~35 min via
netboot. The run is idempotent: re-running it is safe.

### 3.2 Useful variants

| Need | Command |
|---|---|
| Classic network deployment (Debian mirror) | `./tools/full-chain.sh` |
| Node already deployed: (re)run hardening + audit | `./tools/full-chain.sh --skip-deploy` |
| PVE hardening only (without the CIS Lockdown) | `./tools/full-chain.sh --skip-deploy --skip-cis` |
| Regenerate the audit report only | `./tools/full-chain.sh --reports-only` |
| Check the prerequisites | `./tools/full-chain.sh --check` |
| A specific PVE hardening phase | `ansible-playbook -i ansible/inventory/hosts.yml ansible/harden-pve.yml --tags fail2ban` |
| CIS audit/role without touching the PVE | `ansible-playbook -i ansible/inventory/hosts.yml ansible/site.yml --tags lockdown` |

### 3.3 Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ROOT_PW` / `ADMIN_PW` | lab values | root/adminlab passwords of the node |
| `SSH_PUBKEY_FILE` | `~/.ssh/id_rsa.pub` | key injected by the preseed |
| `DISK_POOL` | `libvirt-work` | VM disk pool |
| `WORKDIR` | `libvirt-work/pxe-work` | files served (TFTP, preseed, ISO, logs) |
| `ISO_PATH` / `ISO_URL` | current DVD-1 cdimage | full ISO (`--iso` mode) |
| `PVE_VERSION`, `NODE` | display | audit report labels |
| `WAIT_SSH_MAX_MIN` | `60` | max wait (min) for the client's SSH |
| `CLIENT_NAME`, `CLIENT_MAC` | `pxe-client`, `52:54:00:aa:00:10` | identity of the deployed node (overridable via `--client` / `--mac`) |

### 3.4 Accessing the result

```bash
# SSH (port 2222, user adminlab, sudo NOPASSWD; root SSH = keys)
IP=$(grep -m1 ansible_host ansible/inventory/hosts.yml | awk '{print $2}')
ssh -p 2222 adminlab@$IP
# Proxmox UI (tunnel)
ssh -p 2222 -L 8006:$IP:8006 adminlab@$IP   # then https://localhost:8006
```

### 3.5 Shutting down / deleting the lab

```bash
virsh destroy pxe-client && virsh undefine pxe-client --nvram
docker stop -t 5 proxknox-pxe && docker rm proxknox-pxe
```

(Always `docker stop` before `rm`: the SIGTERM triggers the container
entrypoint's iptables/IP cleanup.)

## 4. Deployment mode (details)

The deployment produces a Debian 13 libvirt VM (CIS partitioning from
the preseed) converted to **Proxmox VE 9.2**, with the final boot from
disk (PXE boot is pinned to `LOCALBOOT` after installation:
`pxelinux.cfg/01-<mac>`), and writes the Ansible inventory
`ansible/inventory/hosts.yml`.

### 4.1 Docker container (the one used by the chain)

```
Container proxknox-pxe (--network host, cap NET_ADMIN/NET_RAW)
│  sets 192.168.200.2/24 on pxebr0 (removed on stop)
│  iptables rules tagged "proxknox-pxe" (MASQUERADE + FORWARD at the top, removed on stop)
│  dnsmasq  : DHCP (192.168.200.100-200) + TFTP + DNS on pxebr0 only
│  HTTP :8000 : preseed + /files (workdir mounted into /srv)
│  syslog  : UDP 10514 -> logs/di-syslog.log
└─ host orchestrator: everything is synchronous via docker exec
   (the container SSHes into the client — direct L2, key mounted in /srv/keys)
```

- The `pxe-lab` network has **no `<ip>`**: libvirt starts no dnsmasq on
  it — the container provides DHCP/TFTP/DNS/gateway, a truly
  self-contained PXE setup. No server VM needed, ideal for iteration
  (~14 min deployment).
- `--network host` is mandatory on this machine (the default docker
  bridge has no egress; a macvlan on pxebr0/virbr0 cannot reach the
  host's addresses).
- Cleanup: always `docker stop -t 5` then `docker rm`
  (`docker rm -f` = SIGKILL → leaves the iptables rules in place).
- Optional static reservations: the
  `logs/dnsmasq-dhcp-hosts.conf` file (mounted volume) is included by
  the entrypoint if it exists — one `dhcp-host=<MAC>,<IP>` line per VM
  with a fixed IP (the e2e_test role writes the test VM's entry
  there). Any other MAC stays dynamic (pool 192.168.200.100-200) and
  PXE-boots normally.

```bash
python3 deploy/pxe_deploy_container.py           # netboot (Debian mirror)
python3 deploy/pxe_deploy_container.py --iso     # from the full ISO → PVE switchover

# multi-node deployment (cluster lab): dedicated identity + OSD disk
python3 deploy/pxe_deploy_container.py --iso \
    --client pxe-node2 --mac 52:54:00:aa:00:12 --hostname proxknox-node2 \
    --osd-disk-gb 15        # same for pxe-node3 (cf. tools/e2e-cluster-test.sh)
```

#### `--iso` mode (install from a full ISO)

```bash
python3 deploy/pxe_deploy_container.py --iso                 # official DVD-1
python3 deploy/pxe_deploy_container.py --iso ~/isos/debian-…-DVD-1.iso
```

- the ISO is downloaded locally if missing (~4.4 GB, `wget -c` resumes
  interrupted downloads), **SHA-256 verified**;
- the client **always boots via PXE**, but: the kernel/initrd are
  **extracted from the ISO** (`install.amd/`, via xorriso — their
  initrd contains the cdrom retriever, which is required; the netboot
  initrd, by contrast, would require a mirror);
- the ISO is attached as an **optical drive** (`device=cdrom`), never
  in the boot order: PXE starts d-i, the ISO is merely a package source;
- preseed: `apt-setup/use_mirror=false`, `pkgsel/upgrade=none`,
  `pkgsel/include sudo` → **Debian performs no updates during
  installation**;
- before the PVE conversion: cdrom sources removed + official Debian
  mirrors written (deb822) → the upgrade (`full-upgrade` from
  install-pve) happens **once switched over to Proxmox**;
- ISO-specific behaviors: the ISO's initrd **ignores `log_host`**
  (syslog relay = netboot only) → the end of installation is signaled
  by an **HTTP milestone** (`milestone/late-command`, logged in
  `http.log`) that arms the Enter press + the reboot watchdog; the
  previous xorriso extraction leaves `r--`/`r-x` permissions → the
  directory is made writable before re-extraction.

## 5. Hardening

### 5.1 Proxmox VE — `harden-pve.yml` (HSE guide v0.9.2)

Each role can be **toggled** in `group_vars/pve_nodes.yml`
(`pve_hardening_<role>: true|false`) — **all ON by default**:

| Role | Guide controls | Default |
|---|---|---|
| `pve_microcode` | § 1.1.7 non-free-firmware repositories, § 1.1.8 CPU microcode | ON |
| `pve_autoupdate` | § 1.1.3 unattended-upgrades (security origin) | ON |
| `pve_fail2ban` | § 2.3.3 fail2ban sshd + UI 8006 (pvedaemon filter) | ON |
| `pve_ksm` | § 1.2.6 KSM disabled (inter-VM side channels) | ON |
| `pve_users` | § 2.1.x custom accounts + RBAC, 2FA (opt.), § 2.2.x API tokens | ON |
| `pve_logging` | § 5.1.2 auditd `/etc/pve`, § 5.1.1 pve* syslog export (opt.) | ON |
| `pve_rkhunter` | § 5.3.2 daily rkhunter | ON |
| `pve_cluster` | § 3.2 corosync `secauth` (no-op without a cluster) | ON |
| `pve_firewall` | § 1.2.4/1.2.5 PVE firewall + 8006/SSH allow-list, INPUT DROP | ON (allow-list tested; can be disabled in sensitive contexts) |
| `pve_ssh_audit` | § 1.1.4 ssh-audit profile (`sshd -t` validated before reload) | ON |
| `pve_ceph` | **§ 3.3-3.5 L2-style Ceph hardening**: cephx required, msgr2 "secure", keyrings 0600, `auth_allow_insecure_global_id_reclaim=false` + E-NO / Red Hat Ceph 8 guides; auto-noop without Ceph (checks that no leftover daemons remain) | ON |
| `pve_audit` | on-node audit of every control → **JSON** `pve-hardening-audit_<epoch>.json` (like the syver scans), consolidated into the md/html report | ON |

### 5.2 CIS Debian 13 L2 benchmark — `site.yml`

Role `ansible-lockdown.DEBIAN13-CIS` (pinned `1.1.0` in
`requirements.yml`, Level 2 benchmark) with the **scoped PVE
deviations** defined in `group_vars/pve_nodes.yml`:

| Deviation | Variable | Why |
|---|---|---|
| § 2.1.13 rsync masked (not purged) | `deb13cis_rsync_mask: true` | dependency of the `proxmox-ve` meta-package (the apt hook blocks the purge) |
| § 4 host firewall **skipped** | `deb13cis_section4: false` + ufw purge (post_task in `site.yml`) | the **PVE** firewall is authoritative (HSE guide § 1.1.1); ufw+pvefw neutralize each other (lockout observed) |
| § 5.1.8 `DisableForwarding` **disabled** | `deb13cis_rule_5_1_8: false` | PVE live-migration requires an SSH streamlocal port forward to `/run/qemu-server/<vmid>.migrate`; `DisableForwarding yes` on the destination breaks any incoming migration (`ram_save_setup failed: Input/output error`) — observed in the 24/11/2026 run; the `pve_ssh_audit` role removes the leftover line |
| IPv4 forwarding / rp_filter | `deb13cis_is_router: true` | Proxmox routes/bridges for its guests (SDN/NAT/firewall) |
| IPv6 kept | `deb13cis_ipv6_required: true` | PVE default |
| others | `deb13cis_level_1/2`, `disruption_high`, `section1-7`, `setup_audit`, `run_audit`, `fetch_audit_output` | CIS level, pre/post syver audits, reports in `docs/runs/` |

The two playbooks are **complementary**: `site.yml` covers the Debian
CIS benchmark, `harden-pve.yml` the Proxmox-specific controls
(deviations such as `PermitRootLogin`/`fuse`/`DisableForwarding`,
documented in § 1.1.1/1.1.2 of the HSE guide).

## 6. Audit → reports

- The Lockdown role runs the **syver** audit (downloaded binary,
  DEBIAN13-CIS-Audit content) **before** remediation, applies the
  remediation, **reboots** the node, then reruns the audit **after**;
  the 4 JSON files are pulled back into `ansible/audit-summaries/`.
- The **`pve_audit`** role does the same on the Proxmox hardening side:
  every HSE + Ceph control is checked on the node →
  `ansible/audit-summaries/pve-hardening-audit_<epoch>.json`
  (`results`/`summary`, pass/fail/skip statuses documented).
- `tools/audit-report.py` consolidates **all JSON sources** into a
  **md + html** report
  (`docs/runs/report-audit-cis-debian13-pve_<date>.[md|html]`):
  - **single-page, dependency-free HTML**: high-level stats cards
    (CIS before/after, CIS and PVE compliance), CIS breakdown by
    section, collapsible per-control drill-down (native `<details>`),
    source JSON embedded in the page;
  - argument status: `--node`, `--pve`, `--chain`, `--out-prefix`,
    `--audit-dir`, `--no-pve-audit` (skip the PVE audit).
- PVE hardening benchmark report (control by control from the HSE
  guide, evidence collected on the node):
  `docs/runs/hardening-benchmark-pve9-2026-11-18.md` (validated
  example).
- Comprehensive hardening report:
  `docs/runs/report-comprehensive-hardening_2026-11-19.md` (CIS +
  pve_* roles + Ceph + deviations + manual controls).

## 6bis. End-to-end 3-node cluster test (Ceph + live-migration)

A dedicated procedure — `tools/e2e-cluster-test.sh` — validates the
whole chain in cluster mode, with a JSON and HTML test report:

1. **Deployment**: 3 real PXE nodes (`pxe-client` acts as node 1;
   `pxe-node2/3` added by the parameterized deployment, 15 GB OSD disk
   per node).
2. **Network**: static `vmbr0` `192.168.200.11/12/13` + hostnames
   `proxknox-node{1,2,3}` (`ansible/e2e-cluster.yml --tags net`).
3. **Cluster**: `pvecm create/add` — 3-vote quorum (`--tags cluster`).
4. **Hardening**: `harden-pve.yml` with `-e
   pve_firewall_cluster_services=true` (corosync/Ceph rules added to
   the PVE firewall).
5. **Ceph**: `pveceph init` + 1 OSD/node + `vmstorage` pool (size 3,
   min_size 2, `pg_num 32` — HSE § 3.3; beyond that,
   `POOL_TOO_MANY_PGS` → permanent HEALTH_WARN on 3 OSDs) → the
   `pve_ceph` role applies to the real Ceph (`--tags ceph`).
6. **Tests** (`e2e_test` role): a real **Debian cloud** mini VM (1
   CPU/2 GB) deployed on the Ceph RBD, ping, **2 live-migrations**
   with **downtime measured by continuous pinging (200 ms)**, final
   Ceph HEALTH_OK.
   The VM's IP is **deterministic**: reserved by MAC in dnsmasq
   (`dhcp-host=...` written by the role into
   `logs/dnsmasq-dhcp-hosts.conf`, included by the container
   entrypoint) **and** set statically by cloud-init at creation
   (`ipconfig0 ip=192.168.200.192/24`) — no more DHCP lease reading.
7. **Report**: `e2e-cluster-test_<epoch>.json` (pass/fail status per
   test, ansible-lockdown style) → md + **single-page HTML**
   (`tools/e2e-report.py`), dependency-free, collapsible per-phase
   detail.

Individual phases can be re-run: `./tools/e2e-cluster-test.sh --phase
net|cluster|hardening|ceph|tests|report`; prerequisite check: `--check`.

Run of **24/11/2026**: **12/12 tests pass** — `docs/runs/`
`report-e2e-cluster_2026-11-24.{md,html}`. Live-migrations
node1→node2→node3 in 9 s each, **0 ping responses lost** (44 samples
at 200 ms), Ceph HEALTH_OK before and after, `minitest` VM left in
place for inspection.


## 7. What remains manual (by design)

| Area | Guide reference |
|---|---|
| UI 2FA (TOTP/YubiKey) + sealed root break-glass | HSE § 2.1.3/2.1.4 |
| UI certificates (ACME/PKI, `pvenode cert set`) | HSE § 2.3.1/2.3.2 |
| 3-2-1 backups + restore test | HSE § 4.x |
| Centralized monitoring (Prometheus/Grafana, alerting) | HSE § 5.2.x |
| Secure Boot + disk encryption (before any real use) | HSE § 1.1.5/1.2.1 (L3) |
| Review of the "Manual Check Required" controls in the CIS report | report, § residual failures |

## 8. Troubleshooting (all observed during real runs)

| Symptom | Cause | Fix (built into the chain) |
|---|---|---|
| `virsh` fails with `GLIBCXX…` | `LD_LIBRARY_PATH` inherited from an IDE | `full-chain.sh` runs `unset LD_LIBRARY_PATH` |
| The PXE client boots "local" on a blank disk, never installs | leftover `pxelinux.cfg/01-<mac>` lock from a previous run | removed by the orchestrator at startup |
| ISO mode: install never ends (no Enter press) | the ISO's initrd ignores `log_host` → no syslog | HTTP milestone `milestone/late-command` arming the watcher |
| `xorriso` failure on the 2nd ISO run | extracted ISO files/dirs left as `r--`/`r-x` | chmod the directories before re-extraction |
| Ansible: `yaml` callback removed | community.general ≥ 12 (installed via `--force` without a version) | pinned `<12` in `requirements.yml` + forced `ANSIBLE_STDOUT_CALLBACK=default` |
| Ansible: `sudo: not found` | sudo missing from the ISO-mode tasks (install without a mirror) | `pkgsel/include sudo` added to the ISO-mode preseed |
| CIS 2.1.13: `pve-apt-hook returned an error` | rsync purge blocked by Proxmox | `deb13cis_rsync_mask: true` |
| After Lockdown + reboot: SSH frozen | **section 4 ufw** incoming-deny (duplicates the PVE firewall) | `deb13cis_section4: false` + ufw purge in a post_task |
| `pveversion unreachable` at the end of deployment | `wait_ssh` catches the pre-reboot SSH window | tolerant pveversion check (10 min retry) |
| d-i warm reboot frozen (QEMU, intermittent) | reboot issued from d-i's RAM environment | watchdog: cold restart after 8 min (max 2), 3 min of qemu silence after each restart (a keypress during early boot can freeze the VM) |
| Client IP "changes" between d-i and the first boot | different DHCP client-id → multiple leases in dnsmasq.leases | the orchestrator keeps the lease with the most recent timestamp |
| Node SSH locked at a dead point (- needing rescue) | — | the VM is local: console Enter press via `virsh qemu-monitor-command pxe-client --hmp "sendkey ret"` (same approach as the orchestrator's d-i watcher) |
| Live-migration to a node: `ram_save_setup failed: Input/output error` | `DisableForwarding yes` (CIS 5.1.8) on the **destination** — sshd refuses the streamlocal forward to `/run/qemu-server/<vmid>.migrate` (log: `refused streamlocal port forward`) | PVE deviation `deb13cis_rule_5_1_8: false` + removal task in `pve_ssh_audit` |
| Live-migration: `Can't connect to destination address using public key` (ed25519) | `pve_ssh_audit` client block (HSE § 1.1.4) restricting `PubkeyAcceptedAlgorithms` to RSA → the cluster's ed25519 key (installed by `pvecm add --use_ssh`) is never offered | `PubkeyAcceptedAlgorithms ssh-ed25519,rsa-sha2-512,rsa-sha2-256` in the role's client block |
| Same while a manual `ssh -i` works | `/root/.ssh/id_ed25519` key copied via Ansible `copy: content:` → **trailing newline swallowed** → `error in libcrypto` | `411` bytes expected; `echo >>` as the fix |
| Ceph `HEALTH_WARN — POOL_TOO_MANY_PGS` | pool created with the default 128 PGs while 3 OSDs call for 32 | `--pg_num 32` at creation (`e2e-cluster.yml` § 9) or `ceph osd pool set vmstorage pg_num 32` (progressive merge) |

## 9. Directory layout

```
ProxKnox/
├── README.md                      ← ENTRY POINT (overview + quickstart + index)
├── docs/
│   ├── full-chain.md              ← THIS DOCUMENT (detailed reference)
│   ├── ansible-analysis.md        ← appendix: refactoring the deployment into Ansible
│   └── runs/                      ← generated audit reports, versioned
├── deploy/                        ← host-side PXE deployment
│   ├── pxe_deploy_container.py    ← container deployment orchestrator [--iso]
│   ├── install-pve-on-debian.sh   ← Debian 13 → PVE 9.2 conversion (2 phases)
│   └── pxe/
│       ├── client-preseed.cfg     ← client preseed (CIS partitions)
│       ├── pxe-lab-network.xml    ← isolated libvirt network "pxe-lab"
│       └── container/             ← Docker PXE server (Dockerfile, entrypoint, syslog)
├── ansible/                       ← self-contained Ansible project (play root)
│   ├── ansible.cfg · requirements.yml
│   ├── site.yml                   ← CIS L2 Lockdown + pre/post syver audits
│   ├── harden-pve.yml             ← PVE hardening: 12 roles, all ON by default
│   ├── e2e-cluster.yml · e2e-tests.yml  ← cluster lab (net/cluster/ceph, tests)
│   ├── group_vars/pve_nodes.yml   ← PVE hardening toggles + CIS deviations
│   ├── inventory/hosts.yml(.example)  ← inventory (hosts.yml NOT versioned,
│   │                                  generated/adjusted by the deployment)
│   ├── audit-summaries/           ← unversioned JSON: pre/post syver scans,
│   │                                 pve-hardening-audit_*, e2e-cluster-test_*
│   └── roles/                     ← 12 pve_* roles + e2e_test
│                                    (ansible-lockdown.DEBIAN13-CIS: gitignored,
│                                    installed via --bootstrap / requirements.yml)
└── tools/
    ├── full-chain.sh              ← full-chain orchestrator
    ├── e2e-cluster-test.sh        ← 3-node E2E cluster procedure (--phase …)
    ├── audit-report.py            ← consolidated md + html audit report
    └── e2e-report.py               ← E2E md + html report (single page)
```

Lab workdir (outside the repo): `libvirt-work/` — pxe-client VM disk,
`pxe-work/` (TFTP, preseed, key, cached ISO, logs). Everything is
recreatable: the deployment destroys and recreates its VM on every
run.

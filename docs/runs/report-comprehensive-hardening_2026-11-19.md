# Comprehensive hardening report — Proxmox VE 9.2 "pxe-client" (reference run 19/11/2026)

- **Target**: `pxe-client` — Debian 13 (DVD-1 13.7.0 ISO, install WITHOUT updates) → Proxmox VE **9.2.28** (kernel 7.0.14-29-pve)
- **Chain**: `deploy/pxe_deploy_container.py --iso` → `deploy/install-pve-on-debian.sh` → `ansible/harden-pve.yml` (HomeSecExplorer guide) → `ansible/site.yml` (ansible-lockdown **DEBIAN13-CIS v1.0.0, Level 2 server profile**) → `syver` pre/post audit → `tools/audit-report.py`
- **Benchmarks**: CIS Debian 13 v1.0.0 L2 · HomeSecExplorer Proxmox-Hardening-Guide v0.9.2
- **Attachments**: [`report-audit-cis-debian13-pve_2026-11-19.md`](report-audit-cis-debian13-pve_2026-11-19.md) (+ `.html`), [`hardening-benchmark-pve9-2026-11-18.md`](hardening-benchmark-pve9-2026-11-18.md)

## 1. Hardening summary

| Area | Benchmark | Result |
|---|---|---|
| CIS remediation (Lockdown role) | CIS Debian 13 v1.0.0, L2 server | failures **304 → 14** (-95,4 %), compliance **97,5 %** (891 final controls) |
| Proxmox hardening (10 `pve_*` roles) | HomeSecExplorer v0.9.2 | **0 failures** (roles ON by default + `firewall`/`ssh_audit` as targeted tests) |
| CIS partitioning | preseed (`deploy/pxe/client-preseed.cfg`) | `/tmp`, `/var`, `/var/tmp`, `/var/log`, `/var/log/audit`, `/home`, `/var/lib/vz` separate, `nodev/nosuid/noexec` |
| Remaining manual controls | HSE + CIS "Manual Check" | 6 documented areas (§ 6) |

## 2. CIS Debian 13 Level 2 — breakdown by section (syver pre/post scan)

| CIS section | Before (failures/controls) | After (failures/controls) |
|---|---|---|
| 1 — Initial Setup (partitions, repositories, kernel, modules) | 15/226 | 7/226 |
| 2 — Services | 19/89 | 3/89 |
| 3 — Network configuration | 37/88 | 1/88 |
| 4 — Host firewall (assumed PVE deviation: `deb13cis_section4=false`, duplicate PVE firewall) | 6/9 | 0/0 |
| 5 — Access, authentication, sudo, sshd | 63/199 | 2/199 |
| 6 — Logging & audit | 160/212 | 1/212 |
| 7 — System tools & integrity | 4/76 | 0/76 |

## 3. Residual CIS failures (14) and assessment

| CIS ID | Control | Assessment |
|---|---|---|
| 1.1.1.7 (×4 cases) | squashfs kernel module not available | hot blacklist, `lsmod` review / purge at reboot — manual by nature |
| 1.1.1.11 | unused filesystems kernel modules | "Manual Check": alert validation after reboot |
| 1.2.1.1 | sources.list Signed-By | sources in **deb822** (`.sources`) on this node; the scan targets classic `sources.list` — cosmetic |
| 2.1.13 | rsync masked not purged | **EXPECTED PVE DEVIATION**: purge blocked by `proxmox-ve` → `deb13cis_rsync_mask: true` |
| 2.1.23 | only approved services listening | "Manual Check": review of listening ports |
| 2.3.1.1 | single time daemon | **EXPECTED PVE DEVIATION**: timesyncd masked, PVE uses chrony |
| 3.3.1.12 | `rp_filter=1` | **EXPECTED PVE DEVIATION**: rp_filter kept for routing/SDN (`deb13cis_is_router`) |
| 4.1.4 | ufw outgoing default | "Manual"; CIS section 4 ufw is disabled (duplicate of the PVE firewall) |
| 5.2.4 | password required for sudo | assumed lab deviation: `adminlab NOPASSWD` (preseed) — to tighten in production |
| 5.4.1.2 | per-user `PASS_MIN_DAYS` | "Manual Check": account review |
| 6.1.1.1.2 | journald file permissions | "Manual Check" by design |
| 1.3.1.3 | AppArmor profiles enforcing | 22 profiles in complain mode (PVE packages) — manual review |

Bottom line: **7 failures = manual controls by design**, **3 = documented PVE deviations**, **4 = post-reboot follow-up** (squashfs). No failure of automatable remediation.

## 4. Proxmox hardening (HomeSecExplorer guide v0.9.2) — 10 `pve_*` roles

| Role | `pve_hardening_*` toggle (group_vars) | Controls covered (guide) | Run status 19/11 |
|---|---|---|---|
| `pve_autoupdate` | `true` | § 1.1.3 automatic security updates (unattended-upgrades, `Automatic-Reboot=false`) | APPLIED |
| `pve_microcode` | `true` | § 1.1.7/1.1.8 non-free-firmware repositories + CPU microcode (`intel-microcode 3.20251111.1`) | APPLIED |
| `pve_fail2ban` | `true` | § 2.3.3 fail2ban jails `sshd` (port 2222, systemd backend) + `proxmox` (8006, pvedaemon filter) ; maxretry=3, findtime=2h, bantime=1h | APPLIED |
| `pve_ksm` | `true` | § 1.2.6 KSM disabled (`/sys/kernel/mm/ksm/run=0`, ksmtuned absent, `--allow-ksm 0` available) | APPLIED |
| `pve_users` | `true` | § 2.1.1/2.1.2/2.2.x custom accounts + RBAC (`adminlab@pam` ; root@pam break-glass ; per-user role/path ; API tokens with expiration) | APPLIED (lab RBAC: Administrator on `/` — tighten in production) |
| `pve_logging` | `true` | § 5.1.1/5.1.2 auditd persistent rule `-w /etc/pve -p wa -k pve-config` + rsyslog export of `pve*` (disabled in lab: `pve_logging_remote_host: ""`) | APPLIED |
| `pve_rkhunter` | `true` | § 5.3.2 rkhunter 1.4.6 + `cron.daily`, `--update` best-effort | APPLIED |
| `pve_cluster` | `true` | § 3.x — auto-noop on a single node (would force `secauth on` if clustered) | VERIFIED NO-OP |
| `pve_firewall` | `true`¹ | § 1.2.4/1.2.5 PVE firewall: `enable=1`, `policy_in=DROP`, SSH 2222 / GUI 8006 rules from 192.168.122.0/24 and .200.0/24 | APPLIED (targeted run 18/11, **enabled by default** since 23/11) |
| `pve_ssh_audit` | `true`¹ | § 1.1.4 ssh-audit profile: post-quantum hybrid KEX (`mlkem768x25519+sntrup761x25519-sha512`), ciphers `chacha20-poly1305,aes256-gcm,aes128-gcm`, MACs `hmac-sha2-*-etm` ; drop-in validated with `sshd -t` before reload | APPLIED (targeted run 18/11, default since 23/11) |
| `pve_ceph` (new) | `true` | **§ 3.3-3.5 Ceph (CIS L2-type)**: cephx required everywhere, Messenger v2 « secure » (`ms_cluster/client/service/mon_*_mode=secure`, `ms_bind_msgr2`, `ms_encrypt`), keyrings `0600 root:root`, `auth_allow_insecure_global_id_reclaim=false` (E-NO/RH Ceph 8) ; auto-noop if Ceph absent (checks the absence of residual daemons) | N/A on this node (no Ceph) — guides pre-positioned |
| `pve_audit` (new) | `true` | On-node audit of each control above → **JSON** `pve-hardening-audit_<epoch>.json` (same spirit as the syver scans), consolidated into the md/html report | **13 pass / 0 fail / 7 N/A** (run 23/11) |

¹ Since the 23/11/2026 run, **all guides are applied by default**
("activatable guides" system: each role remains disableable via
`pve_hardening_<role>: false` in `ansible/group_vars/pve_nodes.yml`).
Allow-list rules 2222/8006 verified: SSH and UI remain accessible after
activation (`sshd -T`: KEX `mlkem768x25519-sha256,sntrup761x25519-sha512`;
`cluster.fw`: `enable: 1`, `policy_in: DROP`).

Partitions/FS (§ 1.1.6), repositories (§ 1.1.7), no LXC/OCI on the host (§ 1.2.7): covered by the preseed and lab policy — see the 18/11 benchmark (link at top).

## 5. Assumed PVE deviations (decision, documented)

| Deviation | Justification | Toggle |
|---|---|---|
| CIS section 4 ufw disabled | duplicate of the PVE firewall (lockout observed in a real run) | `deb13cis_section4: false` |
| `PermitRootLogin prohibit-password` | PVE requires root access for conversion/cluster | documented CIS deviation (§ 5 doc) |
| `deb13cis_is_router: true` | rp_filter kept for Proxmox routing/SDN | `group_vars/pve_nodes.yml` |
| `deb13cis_rsync_mask: true` | rsync purge blocked by PVE apt hooks | `group_vars/pve_nodes.yml` |
| sudo `adminlab NOPASSWD` | lab; fallback use via UI/console | preseed — tighten in production |

## 6. Controls remaining manual (by design)

| Area | Reference |
|---|---|
| UI 2FA (TOTP/YubiKey) + sealed root break-glass | HSE § 2.1.3/2.1.4 |
| UI certificates (ACME/PKI, `pvenode cert set`) | HSE § 2.3.1/2.3.2 |
| 3-2-1 backups + restore test | HSE § 4.x |
| Centralized monitoring (Prometheus/Grafana, alerting) | HSE § 5.2.x |
| Secure Boot + disk encryption (LUKS2, before any real use) | HSE § 1.1.5/1.2.1 (L3) |
| Review of CIS "Manual Check Required" (squashfs/lsmod post-reboot, listening ports, journald, accounts) | § 3 above |

## 7. Evidence

```
syver pre-audit  : 304 failures / 900 controls (2026-11-19, ansible/audit-summaries/*_pre_scan_1795075300.json)
syver post-audit :  14 failures / 891 controls (2026-11-19, *_post_scan_1795076362.json)
PVE audit (pve_audit role, 2026-11-23) : 13 pass / 0 fail / 7 N/A
                   (ansible/audit-summaries/pve-hardening-audit_1795455017.json)
harden-pve.yml    : 0 failures — runs of 19/11 (roles ON) and 23/11 (all guides ON, 68 ok / 5 changed)
benchmark evidence: docs/runs/hardening-benchmark-pve9-2026-11-18.md (pveversion, fw, fail2ban, auditd, ksm, sshd -T…)
detailed report   : docs/runs/report-audit-cis-debian13-pve_2026-11-19.md (+ html, drill-down)
```

## 8. Addendum — run of 23/11/2026: guides applied by default

The node (upgraded to 9.2.31 by the automatic security updates § 1.1.3) was
re-hardened with **all guides active by default**:

- **PVE firewall** (`pve_firewall: true`): `enable: 1`, `policy_in: DROP` —
  SSH 2222 and UI 8006 verified accessible after activation.
- **ssh_audit profile** (`pve_ssh_audit: true`): the updated OpenSSH now
  accepts ML-KEM — effective KEX `mlkem768x25519-sha256,sntrup761x25519-sha512`.
- **Ceph hardening** (`pve_ceph: true`, HSE § 3.3-3.5 type L2 + E-NO +
  Red Hat Ceph 8): as Ceph is not deployed on this node, the role locks down
  the absence of residual daemons and catalogs the controls (cephx, msgr2
  secure, keyrings 0600, `auth_allow_insecure_global_id_reclaim=false`,
  encrypted OSDs, pool size/min_size + CRUSH) as documented N/A — they
  will apply automatically if a Ceph is deployed.
- **PVE audit in JSON** (`pve_audit: true`): each control is verified on
  the node and consolidated into the overall report (md + html).

PVE audit of 23/11: **13 compliant, 0 deviations, 7 N/A** (100% compliance
excluding NA). HTML report: high-level stats + expandable per-control
detail, embedded source JSON, single dependency-free page.

> Report generated on 19/11/2026 (reference chain), consolidated on 23/11/2026.
> Regenerate the reports: `./tools/full-chain.sh --reports-only`.

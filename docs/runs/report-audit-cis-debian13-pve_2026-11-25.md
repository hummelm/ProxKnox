# Consolidated audit report — ProxKnox (CIS L2 + PVE/Ceph hardening)

- **Target**: pxe-client — pve-manager/9.x
- **Benchmarks**: CIS Debian 13 v1.0.0 (server, **Level 2**) via syver ·
  HomeSecExplorer PVE9 v0.9.2 · Ceph hardening (HSE § 3.3-3.5, E-NO, RH Ceph 8)
- **Tooling**: site.yml (ansible-lockdown DEBIAN13-CIS) + harden-pve.yml
  (11 toggleable roles) + pve_audit role (on-node checks)
- **Scan date**: 2026-11-25 12:13
- **Chain**: deployment --iso (disk: LUKS2 + vTPM) → Proxmox VE 9.2 → harden-pve.yml (HSE + Ceph, guides ON by default) → site.yml (CIS L2) → syver + pve_audit

## Overview

| Scan | Controls | Failures | Skipped |
|---|---|---|---|
| CIS — before remediation | 900 | 304 | 20 |
| CIS — final (after remediation) | 889 | **14** | 8 |

CIS failures: **304 → 14** (-95.4%).
Final CIS compliance: **97.5%** (passed controls / total).

## Proxmox VE hardening audit (HomeSecExplorer guide + Ceph)

| PVE audit | Value |
|---|---|
| Controls | 20 |
| Compliant | 13 |
| **Deviations** | **0** |
| Not applicable (documented skips) | 7 |
| Compliance (pass + N/A) | **100 %** |
### Audited controls (HSE PVE9 guide + Ceph hardening)| ID | Guide | Control | Status | Detail / reason ||---|---|---|---|---|| HSE-1.1.3 | HSE PVE9 § 1.1.3 | Automatic security updates | ✅ pass | unattended-upgrades enabled + 20auto-upgrades |
| HSE-1.1.4 | HSE PVE9 § 1.1.4 | ssh-audit profile (post-quantum hybrid algorithm) | ✅ pass | sntrup761x25519/mlkem768x25519 KEX in sshd -T |
| HSE-1.1.6 | HSE PVE9 § 1.1.6 | /var/lib/vz on a dedicated file system | ✅ pass | findmnt /var/lib/vz |
| HSE-1.1.7 | HSE PVE9 § 1.1.7 | non-free-firmware repositories | ✅ pass | sources.list.d contains non-free-firmware |
| HSE-1.1.8 | HSE PVE9 § 1.1.8 | CPU microcode installed | ✅ pass | intel-microcode or amd64-microcode |
| HSE-1.2.4 | HSE PVE9 § 1.2.4/1.2.5 | PVE firewall enabled, policy_in DROP | ✅ pass | /etc/pve/firewall/*.fw: enable 1 + policy_in DROP (+ rules 8006/2222) |
| HSE-1.2.6 | HSE PVE9 § 1.2.6 | KSM disabled | ✅ pass | /sys/kernel/mm/ksm/run = 0 |
| HSE-1.2.7 | HSE PVE9 § 1.2.7 | No Docker/OCI on the host | ✅ pass | docker absent from the PVE host |
| HSE-2.1.1 | HSE PVE9 § 2.1.1 | Custom administration account | ✅ pass | adminlab@pam active (user.cfg) |
| HSE-2.1.3 | HSE PVE9 § 2.1.3 | 2FA on the UI (TOTP/YubiKey) | ⏭️ skip | manual control by design (2FA enrollment done separately) |
| HSE-2.3.3 | HSE PVE9 § 2.3.3 | Fail2ban sshd + Proxmox UI | ✅ pass | jails sshd (2222) + proxmox (8006) active |
| HSE-3.2 | HSE PVE9 § 3.2 | corosync secauth | ⏭️ skip | standalone node — no corosync cluster (pve_cluster role no-op) |
| CEPH-0 | E-NO Ceph hardening | No leftover Ceph daemon without a configured cluster | ✅ pass | ceph-* daemons only if Ceph is configured |
| CEPH-2 | HSE PVE9 § 3.5 + RH Ceph 8 | cephx required everywhere (auth_*_required) | ⏭️ skip | Ceph not deployed on this node |
| CEPH-3 | HSE PVE9 § 3.5 (Level 2) | Messenger v2 "secure" mode, encrypted traffic | ⏭️ skip | Ceph not deployed on this node |
| CEPH-4 | E-NO Ceph hardening | Keyrings 0600 root:root | ⏭️ skip | Ceph not deployed — no keyring to protect on this node |
| CEPH-6 | HSE PVE9 § 3.4 (Level 2) | OSD encryption (LUKS) | ⏭️ skip | Ceph not deployed on this node |
| CEPH-7 | HSE PVE9 § 3.3 (Level 1) | Pools size/min_size + CRUSH failure domains | ⏭️ skip | Ceph not deployed on this node |
| HSE-5.1.2 | HSE PVE9 § 5.1.2 | auditd watches /etc/pve | ✅ pass | -w /etc/pve -p wa -k pve-config rule |
| HSE-5.3.2 | HSE PVE9 § 5.3.2 | daily rkhunter | ✅ pass | package + /etc/cron.daily/rkhunter |

## CIS breakdown by section

| Section | Before (failures/controls) | Final (failures/controls) |
|---|---|---|
| - —  | 0/1 | 0/1 |
| 1 — Initial Setup (partitions, repositories, kernel, modules) | 15/226 | 7/226 |
| 2 — Services | 19/89 | 3/89 |
| 3 — Network configuration | 37/88 | 1/88 |
| 4 — Host firewall (accepted PVE deviation) | 6/9 | 0/0 |
| 5 — Access, authentication, sudo, sshd | 63/199 | 2/197 |
| 6 — Logging & auditing | 160/212 | 1/212 |
| 7 — System tools & integrity | 4/76 | 0/76 |

## Residual CIS failures (14)

| CIS ID | Control | Finding (scan excerpt) | Interpretation |
|---|---|---|---|
| 1.1.1.7 | Ensure squashfs kernel module is not available | blacklist | Command: blacklist_squashfs: exit-status: Expected 1 to be numerically eq 0 | squashfs module — blacklisted at runtime, manual review (lsmod) / reboot |
| 1.1.1.7 | Ensure squashfs kernel module is not available | blacklist | Command: blacklist_squashfs: stdout: Expected "" to have patterns ["/^([^:]+:)?\\s*blacklist\\s+squashfs\… | squashfs module — blacklisted at runtime, manual review (lsmod) / reboot |
| 6.1.1.1.2 | Ensure journald log file access is configured | Manual Check Required | Command: /etc/tmpfiles.d/systemd.conf: stdout: Expected "Manual - Please check journald default permissio… | manually reviewed by design (journald permissions) |
| 1.1.1.7 | Ensure squashfs kernel module is not available | blacklist | Command: squashfs: exit-status: Expected 1 to be numerically eq 0 | squashfs module — blacklisted at runtime, manual review (lsmod) / reboot |
| 1.1.1.7 | Ensure squashfs kernel module is not available | blacklist | Command: squashfs: stdout: Expected "" to have patterns ["install /bin/true"] the missing elements were [… | squashfs module — blacklisted at runtime, manual review (lsmod) / reboot |
| 2.1.23 | Ensure only approved services are listening on a network interface | Manual Check required | Command: manual_listening_ports: stdout: Expected "Manual!! - Please check only approved services are lis… | manually reviewed by design (listening services) |
| 1.1.1.11 | Ensure unused filesystems kernel modules are not available | Manual Check will be needed to validate if alerts | Command: unused_filesystems: exit-status: Expected 1 to be numerically eq 0 | manually reviewed by design (alert validation) |
| 1.2.1.1 | Ensure the source.list and .source files use the Signed-By option | Command: sources_signed_by: stdout: Expected "/etc/apt/sources.list\n" to have patterns ["!/./"] the miss… | deb822 sources (.sources) on this node; the scan targets a classic sources.list — cosmetic |
| 5.4.1.2 | Ensure minimum password days is configured | check users | Command: check_users_MIN_DAYS: stdout: Expected "User: adminlab PASS_MIN_DAYS: 0\n" to have patterns ["!/… | manually reviewed by design (account review) |
| 5.2.4 | Ensure users must provide password for escalation | Command: nopasswd_sudoers_d: stdout: Expected "/etc/sudoers.d/90-admin-lab:adminlab ALL=(ALL) NOPASSWD:AL… | lab deviation: sudo NOPASSWD adminlab (preseed) — tighten in production |
| 1.3.1.3 | Ensure all AppArmor Profiles are enforcing | Command: apparmor_enf_only: stdout: Expected "profiles_loaded=118\nprofiles_complain=22\nprocesses_compla… | non-enforcing AppArmor profiles (PVE) — manual review |
| 2.1.13 | Ensure rsync services are not in use | masked | File: rsync_socket_masked: /etc/systemd/system/rsyncd.socket: exists: Expected false to equal true | EXPECTED PVE DEVIATION: rsync masked, not purged (proxmox-ve dependency) — deb13cis_rsync_mask |
| 2.3.1.1 | Ensure a single time synchronization daemon is in use | File: timesync_masked: /etc/systemd/system/systemd-timesyncd.service: exists: Expected false to equal tru… | EXPECTED PVE DEVIATION: timesyncd masked, PVE uses chrony |
| 3.3.1.12 | Ensure net.ipv4.conf.all.rp_filter is configured | live ipv4 all | KernelParam: net.ipv4.conf.all.rp_filter: value: Expected "2" to equal "1" | EXPECTED PVE DEVIATION: rp_filter kept for routing/SDN (deb13cis_is_router) |

## Skipped CIS controls (8)

| CIS ID | Control | Reason |
|---|---|---|
| 7.1.10 | Ensure access to /etc/security/opasswd is configured | old passwd | True |
| 7.1.10 | Ensure access to /etc/security/opasswd is configured | old passwd | True |
| 7.1.10 | Ensure access to /etc/security/opasswd is configured | old passwd | True |
| 1.5.7 | Ensure Automatic Error Reporting is configured | conf | True |
| 2.1.13 | Ensure rsync services are not in use | masked | True |
| 2.1.13 | Ensure rsync services are not in use | masked | True |
| 2.3.1.1 | Ensure a single time synchronization daemon is in use | True |
| 2.3.1.1 | Ensure a single time synchronization daemon is in use | True |

## Accepted PVE deviations (documented)

- **Section 4 (host firewall ufw) disabled**: the HomeSecExplorer PVE9 guide § 1.1.1 recommends skipping benchmark CIS § 4 when the PVE firewall is enabled (harden-pve.yml § 1.2.4: enable=1, INPUT DROP, allow-list 8006/2222). deb13cis_section4: false + ufw purge are in group_vars/pve_nodes.yml / site.yml.
- **2.1.13 rsync masked (not purged)**: dependency of the proxmox-ve meta-package (pve-apt-hook blocks its removal) — deb13cis_rsync_mask: true.
- **IPv4 forwarding / rp_filter kept**: Proxmox acts as a router for its guests (SDN/NAT/PVE firewall) — deb13cis_is_router: true.

## Source files (JSON)

- `debian-DEBIAN13-CIS-1.0.0_pre_scan_1795075300.json` — before CIS remediation (Count: 900, Failed: 304, Skipped: 20, Duration: 16.520s)
- `debian-DEBIAN13-CIS-1.0.0_post_scan_1795605217.json` — CIS final (Count: 889, Failed: 14, Skipped: 8, Duration: 17.035s)
- `pve-hardening-audit_1795605191.json` — PVE audit (Count: 20, Failed: 0, Skipped: 7)

Reports: `docs/runs/report-audit-cis-debian13-pve_2026-11-25.md` / `docs/runs/report-audit-cis-debian13-pve_2026-11-25.html`.

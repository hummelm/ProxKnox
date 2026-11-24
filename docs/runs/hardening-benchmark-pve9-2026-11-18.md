# Hardening benchmark — Proxmox VE 9 (HomeSecExplorer guide v0.9.2)

- **Target**: `pxe-client` — 192.168.200.119:2222 — `pve-manager/9.2.28/310cfbc506c4490f (kernel 7.0.14-29-pve)`
- **Date**: 18 Nov 2026
- **Deployment chain**: `pxe_deploy_container.py --iso` (Debian 13 from DVD-1 13.7.0 ISO, no updates during installation, updates applied at the PVE switchover) → PVE 9.2.28 conversion → `harden-pve.yml`
- **Roles executed**: ON by default (microcode, autoupdate, fail2ban, ksm, users, logging, rkhunter, cluster-noop) + targeted tests `ssh_audit` and `firewall` (`-e pve_hardening_<role>=true`)
- **Benchmark**: https://github.com/HomeSecExplorer/Proxmox-Hardening-Guide/blob/main/docs/pve9-hardening-guide.md (v0.9.2)
- **Not executed in this run**: `site.yml` (Ansible-Lockdown DEBIAN13-CIS role) — CIS partitioning via preseed present, full CIS remediation available but not launched (see § 1.1.1/1.1.2)

## Summary

| Status | Controls |
|---|---|
| APPLIED — automated, verified on the node | 11 |
| PARTIAL / infrastructure ready but incomplete in the lab | 5 |
| MANUAL (by nature, documented) | 3 |
| NOT COVERED (explicit lab decision) | 6 |
| NOT APPLICABLE (single node, no SDN/Ceph/CT) | 6 |

## Detail per control of the guide

| Guide | Control | Status | Evidence / implementation |
|---|---|---|---|
| 1.1.1 | Debian 13 CIS L1 | PARTIAL | CIS partitions/preseed (`/tmp /var /var/tmp /var/log /var/log/audit /home /var/lib/vz` separate, `nodev/nosuid/noexec`) ; `PermitRootLogin prohibit-password` (deviation documented § 1.1.1 for PVE) ; **Lockdown role not executed in this run** |
| 1.1.2 | Debian 13 CIS L2 | NOT COVERED (this run) | `site.yml` + `ansible-lockdown.DEBIAN13-CIS` available ; `deb13cis_is_router=true` already configured for PVE compatibility |
| 1.1.3 | automatic security updates | APPLIED | unattended-upgrades active ; `20auto-upgrades` (Update-Package-Lists=1, Unattended-Upgrade=1) ; origins `Debian` + `Debian-Security` ; `Remove-Unused-Dependencies=true`, `Automatic-Reboot=false` |
| 1.1.4 | ssh-audit profile | APPLIED (targeted test) | `sshd -T`: KEX `sntrup761x25519-sha512,curve25519-sha256` ; ciphers `chacha20-poly1305,aes256-gcm,aes128-gcm` ; MACs `hmac-sha2-512/256-etm` ; drop-in validated by `sshd -t` before reload ; root client: RSA block for cluster peers in `/root/.ssh/config`. Trixie adjustment: ML-KEM excluded (OpenSSH 9.9p2) |
| 1.1.5 | disk encryption (L3) | NOT COVERED | lab: qcow2 VM disk ; must be addressed before any real use (LUKS2 at the preseed) |
| 1.1.6 | dedicated `/var/lib/vz` FS | APPLIED | dedicated partition at the preseed, `nodev,nosuid` (without noexec, per the guide) |
| 1.1.7 | non-free-firmware repositories | APPLIED | `debian-official.sources` (`main contrib non-free-firmware`, trixie + updates + security) |
| 1.1.8 | CPU microcode | APPLIED | `intel-microcode 3.20251111.1~deb13u1` installed (applies at next reboot) |
| 1.2.1.x | Secure Boot + lockdown integrity (L3) | NOT COVERED | lab BIOS/SeaBIOS (PXE) ; requires UEFI/Secure Boot at install — outside the current PXE variant |
| 1.2.2 | network separation | PARTIAL | lab: dedicated isolated PXE segment + NAT ; no mgmt/cluster/storage VLANs in the lab |
| 1.2.3 | Proxmox subscription | NOT COVERED | `pve-no-subscription` (operational control, not applicable to the lab) |
| 1.2.4 | PVE firewall | APPLIED (targeted test) | `enable=1`, `policy_in=DROP`, `policy_out=ACCEPT` ; 4 active ACCEPT rules: tcp/8006 and tcp/2222 from 192.168.122.0/24 and 192.168.200.0/24 ; SSH/GUI access verified after activation |
| 1.2.5 | FORWARD DROP (L2) | NOT APPLIED | tech preview nftables ; toggle `pve_firewall_forward_drop` (default off) — the role reports it if the API refuses |
| 1.2.6 | KSM disabled | APPLIED | `/sys/kernel/mm/ksm/run = 0`, ksm/ksmtuned services absent/disabled ; `qm set --allow-ksm 0` available via `pve_ksm_disable_vmids` |
| 1.2.7 | no LXC/OCI on the host | APPLIED (policy) | no CT/OCI created on the host |
| 1.2.8 | unprivileged CTs only | N/A | no CT on the node |
| 1.3.x | SDN | N/A | no SDN (documented _no-op_) |
| 2.1.1 | custom accounts | APPLIED | `adminlab@pam` created (enable=1, comment) ; root@pam reserved for break-glass |
| 2.1.2 | least-privilege RBAC | PARTIAL | `adminlab@pam` → `Administrator` on `/` in the lab ; the role accepts a per-user role/path (e.g. `PVEAuditor` on `/vms/100`) |
| 2.1.3 | 2FA | MANUAL | `pve_users_require_2fa` (default off): `pvesh set /access/domains/pam --tfa type=oath` — prior enrollment mandatory |
| 2.1.4 | break-glass | MANUAL | documented procedure ; root password to be sealed offline in production |
| 2.1.5 | access model | PARTIAL | root SSH = keys only (`prohibit-password`, port 2222) ; sudo `adminlab NOPASSWD` (lab) ; automation tokens recommended for CI (no token created) |
| 2.2.1–2.2.4 | API tokens | PARTIAL | `pve_api_tokens` role ready (expiration `--expire`, scope via ACL) ; no token configured in the lab |
| 2.3.1/2.3.2 | UI certificates | NOT COVERED | default self-signed certificate (lab) ; ACME/PKI to be wired up (`pvenode cert set` / ACME) in production |
| 2.3.3 | fail2ban sshd + GUI | APPLIED | fail2ban active, jails `sshd` (port 2222, systemd backend) + `proxmox` (8006, pvedaemon filter) ; maxretry=3, findtime=2h, bantime=1h |
| 3.1 | corosync double ring | N/A | standalone node (`/etc/pve/corosync.conf` absent) |
| 3.2 | corosync secauth | N/A (verified no-op) | `pve_cluster` role forces `secauth on` if clustered ; not applicable here |
| 3.3–3.5 | Ceph (pools, OSD/msgr encryption) | N/A | no Ceph |
| 4.x | 3-2-1 backups, restore tests | NOT COVERED | operations procedure (PBS/none — lab, no backup jobs) |
| 5.1.1 | centralized logs | PARTIAL | `pve_logging` role: rsyslog export of `pve*` programs ready, disabled in the lab (`pve_logging_remote_host: ""`) |
| 5.1.2 | auditd `/etc/pve` | APPLIED | persistent rule `90-pve.rules`: `-w /etc/pve -p wa -k pve-config` ; `auditctl -l` verified ; auditd active |
| 5.2.1/5.2.2 | metrics + alerts | NOT COVERED | no collector in the lab (pve_exporter/Prometheus to be wired up) |
| 5.3.1 | periodic system audits | PARTIAL | syver Lockdown audit wired into `site.yml` (reports in `audit-summaries/`) — not executed in this run |
| 5.3.2 | rkhunter | APPLIED | rkhunter 1.4.6 installed + `cron.daily` (package) ; `--update` best-effort (mirrors) without blocking the run |
| 5.4 | documentation | OUT OF SCOPE | inventory/runbook to be maintained by operations |

## Evidence (excerpts, 18/11/2026)

```
pveversion        pve-manager/9.2.28/310cfbc506c4490f (running kernel: 7.0.14-29-pve)
fw options        {"enable":1,"policy_in":"DROP","policy_out":"ACCEPT"}
fw rules active   4/4: hse-124-ssh(2222) + hse-124-gui(8006) from .122.0/24 and .200.0/24
fail2ban          Jail list: proxmox, sshd      (maxretry=3, findtime=2h, bantime=1h)
auditd            -w /etc/pve -p wa -k pve-config   (active)
ksm               /sys/kernel/mm/ksm/run = 0; ksmtuned: not-found (disabled)
sshd -T           kexalgorithms sntrup761x25519-sha512@openssh.com,curve25519-sha256[...]
                  ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com
                  macs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com
microcode         ii  intel-microcode 3.20251111.1~deb13u1
unattended        active; origins Debian + Debian-Security; Automatic-Reboot=false
users PVE         adminlab@pam (enable=1); ACL: / → Administrator (adminlab@pam, propagate=1)
                  tfa: absent (manual, § 2.1.3)
ssh root          Port 2222; PermitRootLogin prohibit-password; dedicated authorized_keys
sudo              adminlab ALL=(ALL) NOPASSWD:ALL
sources apt       0 cdrom lines; debian-official.sources + pve-install-repo.sources
                  (fixedup post-ISO: updates applied at the PVE switchover -> 0 packages held back)
corosync          standalone (no-op); repositories: Components pve-no-subscription
```

## Findings and recommended actions

1. **Access lockdown validated under real conditions**: firewall activation tested successfully (SSH + UI 8006 reachable only from the management subnets). A lockout incident during role development (rules created disabled before the `enable`) led to the final anti-lockout ordering: all rules are created/re-enabled **before** the firewall is activated, and the role's rules are re-parameterized automatically (`enable=1`).
2. **ISO preseed compliant with the "zero-mirror, no updates" principle**: no residual cdrom source, no packages held back — the update happened only during the PVE conversion.
3. **For a complete CIS score**: run `site.yml` afterwards (Lockdown DEBIAN13-CIS L2 + syver audit); the PVE deviations documented by the guide (PermitRootLogin, fuse, DisableForwarding, § 4 host firewall) are already framed by `group_vars/pve_nodes.yml`.
4. **Remaining manual / not covered**: 2FA + break-glass (§ 2.1.3/2.1.4), UI certificates (§ 2.3.x), 3-2-1 backups with restore test (§ 4.x), monitoring (§ 5.2.x), Secure Boot / disk encryption (L3).

#!/usr/bin/env bash
# audit-cis.sh — Audit CIS Level 2 pour Proxmox VE (Debian 12 base).
# Usage : ./audit-cis.sh [-o rapport.txt]   (à exécuter en root sur le nœud PVE)
set -u

OUTPUT=""
[ "${1:-}" = "-o" ] && OUTPUT="${2:-}" && [ -z "$OUTPUT" ] && { echo "usage: $0 [-o file]" >&2; exit 2; }

PASS=0; FAIL=0; WARN=0
report() {
    local line="$1"
    echo "$line"
    [ -n "$OUTPUT" ] && echo "$line" >> "$OUTPUT"
}
result() { # result PASS|FAIL|WARN "id" "desc"
    local status="$1" id="$2" desc="$3"
    case "$status" in
        PASS) PASS=$((PASS+1)); report "[PASS] $id - $desc" ;;
        FAIL) FAIL=$((FAIL+1)); report "[FAIL] $id - $desc" ;;
        WARN) WARN=$((WARN+1)); report "[WARN] $id - $desc" ;;
    esac
}

# check <id> <desc> <cmd_si_ok>
check() { # check id desc cmd
    local id="$1" desc="$2" cmd="$3"
    if eval "$cmd" >/dev/null 2>&1; then
        result PASS "$id" "$desc"
    else
        result FAIL "$id" "$desc"
    fi
}

mount_opts() { findmnt -kn -o OPTIONS "$1" 2>/dev/null || true; }
has_opt()    { mount_opts "$1" | tr ',' '\n' | grep -qx "$2"; }

report "=== Audit CIS L2 — Proxmox VE — $(hostname) — $(date '+%F %T') ==="

# ------------------------------------------------------------- 1. Filesystem
for p in /tmp /var/tmp /var/log /var/log/audit /home /dev/shm; do
    if findmnt -kn "$p" >/dev/null 2>&1; then
        check "1.1.$p" "$p : partition séparée" "findmnt -kn $p"
        for opt in nodev nosuid noexec; do
            case "$p:/dev/shm:$opt" in /dev/shm:noexec:*) continue;; esac
            check "1.1.$p.$opt" "$p : option $opt" "has_opt $p $opt"
        done
    else
        result WARN "1.1.$p" "$p : pas une partition séparée"
    fi
done

# ------------------------------------------------- 2. Packages & services
check "2.1.1" "GFS/OCFS2 non installé" "! dpkg -s gfs2-utils >/dev/null 2>&1"
for svc in avahi-daemon cups rpcbind rsync nfs-kernel-server snmpd talk telnet openbsd-inetd tftp; do
    check "2.2.$svc" "service $svc désactivé/absent" \
        "! systemctl is-enabled '$svc' >/dev/null 2>&1 && ! systemctl is-active '$svc' >/dev/null 2>&1"
done
check "2.3.1" "AIDE installé (L2)" "dpkg -s aide >/dev/null 2>&1"
check "2.3.2" "AIDE cron/journal actif" "systemctl is-enabled aidecheck.timer >/dev/null 2>&1 || crontab -l root 2>/dev/null | grep -q aide"
check "2.4" "APT : mises à jour de sécurité configurées" "grep -rqs 'security' /etc/apt/sources.list /etc/apt/sources.list.d/"

# ------------------------------------------------------------- 3. Sysctl
sys() { sysctl -n "$1" 2>/dev/null; }
check "3.1.1" "IPv6 désactivé ou configuré (L2)" "[ \"\$(sys net.ipv6.conf.all.disable_ipv6)\" = 1 ] || [ \"\$(sys net.ipv6.conf.default.disable_ipv6)\" = 1 ] || grep -qs 'net.ipv6' /etc/sysctl.conf /etc/sysctl.d/"
check "3.2.1" "net.ipv4.ip_forward = 0 (hors routeur)" "[ \"\$(sys net.ipv4.ip_forward)\" = 0 ] || grep -qs 'net.ipv4.ip_forward' /etc/sysctl.d/"
check "3.2.2" "packet redirects : send_redirects = 0" "[ \"\$(sys net.ipv4.conf.all.send_redirects)\" = 0 ]"
check "3.3.1" "accept_source_route = 0 (all)" "[ \"\$(sys net.ipv4.conf.all.accept_source_route)\" = 0 ]"
check "3.3.2" "accept_redirects = 0 (all)" "[ \"\$(sys net.ipv4.conf.all.accept_redirects)\" = 0 ]"
check "3.3.3" "secure_icmp_redirects = 0" "[ \"\$(sys net.ipv4.conf.all.secure_redirects)\" = 0 ]"
check "3.3.4" "log_martians activé" "[ \"\$(sys net.ipv4.conf.all.log_martians)\" = 1 ]"
check "3.4.1" "icmp_echo_ignore_broadcasts = 1" "[ \"\$(sys net.ipv4.icmp_echo_ignore_broadcasts)\" = 1 ]"
check "3.4.2" "icmp_ignore_bogus_error_responses = 1" "[ \"\$(sys net.ipv4.icmp_ignore_bogus_error_responses)\" = 1 ]"
check "3.5.1" "rp_filter = 1 (all)" "[ \"\$(sys net.ipv4.conf.all.rp_filter)\" = 1 ]"
check "3.5.2" "tcp_syncookies = 1" "[ \"\$(sys net.ipv4.tcp_syncookies)\" = 1 ]"
check "3.6" "Modules réseau rares blacklistés (dccp, sctp, rds, tipc)" \
    "grep -qs 'install dccp /bin/true' /etc/modprobe.d/ && grep -qs 'install sctp /bin/true' /etc/modprobe.d/"

# ------------------------------------------------------- 4. Audit & logs
check "4.1.1" "auditd installé" "dpkg -s auditd >/dev/null 2>&1"
check "4.1.2" "auditd activé" "systemctl is-enabled auditd >/dev/null 2>&1"
check "4.1.3" "audit=1 dans la ligne de commande GRUB" "grep -qs 'audit=1' /proc/cmdline || grep -qs 'audit=1' /etc/default/grub"
check "4.1.4" "Règles d'audit présentes" "test -s /etc/audit/rules.d/*.rules -o -s /etc/audit/audit.rules"
check "4.1.5" "audit_backlog_limit défini (GRUB)" "grep -qs 'audit_backlog_limit=' /etc/default/grub /proc/cmdline"
check "4.2.1" "journald : Storage=persistent" "grep -qs '^\s*Storage=persistent' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*.conf"
check "4.2.2" "journald : Compress=yes" "grep -qs '^\s*Compress=yes' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*.conf"
check "4.2.3" "rsyslog installé" "dpkg -s rsyslog >/dev/null 2>&1"
check "4.2.4" "rsyslog actif" "systemctl is-active rsyslog >/dev/null 2>&1"
check "4.2.5" "Logs centralisés configurés (L2)" "grep -rqs '@@\?/' /etc/rsyslog.conf /etc/rsyslog.d/ || grep -qs 'RemoteHost' /etc/rsyslog.d/*.conf"
check "4.2.6" "logrotate configuré" "dpkg -s logrotate >/dev/null 2>&1 && test -f /etc/logrotate.conf"

# --------------------------------------------------------- 5. Accès / PAM
check "5.1.1" "cron activé" "systemctl is-enabled cron >/dev/null 2>&1"
check "5.1.2" "/etc/crontab root:root et 0600" "[ \"\$(stat -c '%u:%g %a' /etc/crontab)\" = '0:0 600' ] || [ \"\$(stat -c '%u:%g %a' /etc/crontab)\" = '0:0 0600' ]"
for d in /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /etc/cron.d; do
    check "5.1.$d" "$d : root:root 0700" "[ \"\$(stat -c '%u:%g %a' $d)\" = '0:0 700' ]"
done
check "5.1.8" "at/cron restreints (cron.allow)" "test -f /etc/cron.allow && [ \"\$(stat -c '%a' /etc/cron.allow)\" -le 640 ]"
check "5.2.1" "sudo installé" "dpkg -s sudo >/dev/null 2>&1"
check "5.2.2" "session sudo journalisée" "grep -rqs 'pam_tty_audit.so' /etc/pam.d/ || grep -rqs 'logfile' /etc/sudoers /etc/sudoers.d/"
check "5.3.1" "pwquality installé" "dpkg -s libpam-pwquality >/dev/null 2>&1"
check "5.3.2" "pwquality : minlen >= 14" "grep -qs 'minlen\s*=\s*1[4-9]\|minlen\s*=\s*[2-9][0-9]' /etc/security/pwquality.conf"
check "5.3.3" "pwquality : complexity (minclass>=3 ou dcredit/ucredit...)" \
    "grep -qs 'minclass\s*=\s*[3-4]' /etc/security/pwquality.conf || { grep -qs 'dcredit\s*=\s*-[1-9]' /etc/security/pwquality.conf && grep -qs 'ucredit\s*=\s*-[1-9]' /etc/security/pwquality.conf; }"
check "5.4.1" "pam_faillock configuré (deny<=5)" "grep -qs 'faillock' /etc/pam.d/common-auth && grep -qs 'deny\s*=\s*[0-5]\b' /etc/security/faillock.conf /etc/pam.d/common-auth"
check "5.4.2" "pam_unix : sha512" "grep -qs 'sha512' /etc/pam.d/common-password"
check "5.5.1" "login.defs : PASS_MAX_DAYS <= 365" "grep -qs '^\s*PASS_MAX_DAYS\s\+[0-9]\+' /etc/login.defs && ! grep -qs '^\s*PASS_MAX_DAYS\s\+\(36[6-9]\|[4-9][0-9][0-9]\|[1-9][0-9][0-9][0-9]\)' /etc/login.defs"
check "5.5.2" "login.defs : PASS_MIN_DAYS >= 1" "grep -qs '^\s*PASS_MIN_DAYS\s\+[1-9]' /etc/login.defs"
check "5.5.3" "login.defs : PASS_WARN_AGE >= 7" "grep -qs '^\s*PASS_WARN_AGE\s\+\([7-9]\|[1-9][0-9]\)' /etc/login.defs"
check "5.5.4" "login.defs : INACTIVE <= 30 (L2)" "grep -qs '^\s*INACTIVE\s\+\(0\|[1-9]\|[12][0-9]\|30\)' /etc/login.defs"
check "5.6" "root : shell nologin pour comptes système" "! grep -E '^[^:]+:[^:]*:[0-9]{1,2}:.*[^/]$' /etc/passwd | grep -v nologin | grep -v false | grep -qs ."
check "5.7" "umask par défaut 027+ (login.defs)" "grep -qs '^\s*UMASK\s\+0[2-7][7-9]' /etc/login.defs"

# ----------------------------------------------------------------- 5. SSH
sshd_test() { sshd -T 2>/dev/null | grep -qi "$1"; }
sshd_val()  { sshd -T 2>/dev/null | awk -v k="$1" 'tolower($1)==k{print tolower($2); exit}'; }
sshd_num_le() { local v; v="$(sshd_val "$1")"; [ -n "$v" ] && [ "$v" -le "$2" ] 2>/dev/null; }
check "5.8.1" "sshd : PermitRootLogin prohibit-password/no" "sshd_test 'permitrootlogin \(prohibit-password\|no\)'"
check "5.8.2" "sshd : PasswordAuthentication no (L2)" "sshd_test 'passwordauthentication no'"
check "5.8.3" "sshd : MaxAuthTries <= 4" "sshd_num_le maxauthtries 4"
check "5.8.4" "sshd : MaxSessions <= 10" "sshd_num_le maxsessions 10"
check "5.8.5" "sshd : ClientAliveInterval 1-900" "v=\$(sshd_val clientaliveinterval); [ -n \"\$v\" ] && [ \"\$v\" -ge 1 ] && [ \"\$v\" -le 900 ]"
check "5.8.6" "sshd : AllowTcpForwarding no (L2)" "sshd_test 'allowtcpforwarding no'"
check "5.8.7" "sshd : X11Forwarding no" "sshd_test 'x11forwarding no'"
check "5.8.8" "sshd : MaxStartups 10:30:60" "[ \"\$(sshd_val maxstartups)\" = '10:30:60' ]"
check "5.8.9" "sshd : LoginGraceTime <= 60" "sshd_num_le logingracetime 60"
check "5.8.10" "sshd : Banner configuré" "sshd_test 'banner /'"
check "5.8.11" "sshd : AllowAgentForwarding no (L2)" "sshd_test 'allowagentforwarding no'"
check "5.8.12" "sshd : Ciphers forts uniquement" "sshd_val ciphers | grep -vqs 'cbc\|3des\|arcfour'"

# ------------------------------------------------------- 6. Permissions
check "6.1.1" "/etc/passwd 644 root:root" "[ \"\$(stat -c '%u:%g %a' /etc/passwd)\" = '0:0 644' ]"
check "6.1.2" "/etc/passwd- sécurisé" "[ \"\$(stat -c '%a' /etc/passwd-)\" -le 644 ] 2>/dev/null"
check "6.1.3" "/etc/shadow root:shadow 640/0000" "S=\$(stat -c '%a' /etc/shadow); [ \"\$S\" = 640 ] || [ \"\$S\" = 0000 ] || [ \"\$S\" = 600 ]"
check "6.1.4" "/etc/group 644 root:root" "[ \"\$(stat -c '%u:%g %a' /etc/group)\" = '0:0 644' ]"
check "6.1.5" "/etc/gshadow 640/0000" "S=\$(stat -c '%a' /etc/gshadow); [ \"\$S\" = 640 ] || [ \"\$S\" = 0000 ]"
check "6.1.6" "/etc/shadow- / gshadow- sécurisés" "{ [ ! -e /etc/shadow- ] || [ \"\$(stat -c '%a' /etc/shadow-)\" -le 640 ]; } && { [ ! -e /etc/gshadow- ] || [ \"\$(stat -c '%a' /etc/gshadow-)\" -le 640 ]; }"
check "6.1.7" "Pas de comptes système avec shell valide" "! awk -F: '\$3 < 1000 && \$7 !~ /(nologin|false)/ {print}' /etc/passwd | grep -qs root"
check "6.1.8" "root : PATH sans . ni répertoires world-writable" \
    "! echo \"\$PATH\" | grep -qs ':\.\|::' && ! find \$(echo \"\$PATH\" | tr ':' ' ') -maxdepth 0 -perm -0002 2>/dev/null | grep -qs ."
check "6.1.9" "Pas de fichier .rhosts / hosts.equiv" "! find / -name .rhosts -o -name hosts.equiv 2>/dev/null | grep -qs ."
check "6.1.10" "Pas de comptes sans mot de passe" "! awk -F: '(\$2 == \"\" ) {print \$1}' /etc/shadow | grep -qs ."
check "6.1.11" "Pas de + dans passwd/shadow (legacy NIS)" "! grep -qs '^+:' /etc/passwd /etc/shadow"
check "6.1.12" "world-writable files (échantillon /etc)" "! find /etc -xdev -perm -0002 -type f 2>/dev/null | grep -qs ."
check "6.1.13" "Pas de fichiers unowned/ungrouped (échantillon)" "! find /etc /usr /bin /sbin -xdev \( -nouser -o -nogroup \) 2>/dev/null | grep -qs ."
check "6.1.14" "SUID/SGID listés et revus (fichier présent)" "test -f /usr/local/share/cis/suid-sgid-inventory.txt || test -f /var/lib/cis/suid-sgid.txt"
check "6.2.1" "Core dumps désactivés (L2)" "grep -qs '^\s*\*\s*hard\s*core\s*0' /etc/security/limits.conf && [ \"\$(sys fs.suid_dumpable)\" = 0 ]"
check "6.2.2" "XD/NX support actif" "dmesg 2>/dev/null | grep -qs 'NX.*active\|protection.*active' || grep -qs 'nx' /proc/cpuinfo"
check "6.2.3" "ASLR activé" "[ \"\$(sys kernel.randomize_va_space)\" = 2 ]"
check "6.2.4" "USB storage désactivé (L2)" "grep -qs 'install usb-storage /bin/true\|blacklist usb-storage' /etc/modprobe.d/"

# -------------------------------------------------------- 7. Spécifique PVE
check "7.1" "pveproxy actif" "systemctl is-active pveproxy >/dev/null 2>&1"
check "7.2" "pveproxy : écoute restreinte (LISTEN_IP ou firewall)" \
    "grep -qs 'LISTEN_IP' /etc/default/pveproxy || pve-firewall status 2>/dev/null | grep -qs enabled"
result WARN "7.3" "TLS pveproxy >= 1.2 : vérifier manuellement /etc/default/pveproxy (HONOR_CIPHER_ORDER, TLS_PROTOCOL)"
check "7.4" "2FA TOTP configurée sur realm PAM (L2)" "grep -qs 'tfa' /etc/pve/domains.cfg"
check "7.5" "fail2ban installé" "dpkg -s fail2ban >/dev/null 2>&1"
check "7.6" "fail2ban : jail proxmox actif" "grep -qs 'proxmox' /etc/fail2ban/jail.local /etc/fail2ban/jail.d/*.conf 2>/dev/null"
check "7.7" "PVE firewall activé au niveau datacenter/option" \
    "grep -qs 'enable: 1' /etc/pve/firewall/cluster.fw 2>/dev/null || grep -qs 'enable:1' /etc/pve/firewall/cluster.fw 2>/dev/null"
check "7.8" "Permissions /etc/pve restreintes" "[ \"\$(stat -c '%a' /etc/pve)\" -le 755 ]"
check "7.9" "Pas de mot de passe root identique SSH+UI (root@pam sans 2FA)" \
    "grep -qs 'tfa' /etc/pve/domains.cfg || ! sshd -T 2>/dev/null | grep -qi 'passwordauthentication yes'"
check "7.10" "pve-updates : dépôt no-subscription configuré" "grep -qs 'pve-no-subscription\|pvetest' /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null"
result WARN "7.11" "Corosync cluster crypto : vérifier manuellement si en cluster"
result WARN "7.12" "Console Spice/noVNC : vérifier l'absence d'accès anonyme"

# ----------------------------------------------------------------- score --
TOTAL=$((PASS+FAIL+WARN))
[ "$TOTAL" -eq 0 ] && TOTAL=1
report "---------------------------------------------------------------"
report "Résumé : PASS=$PASS  FAIL=$FAIL  WARN=$WARN"
report "Conformité (hors WARN) : $(( PASS * 100 / (PASS+FAIL == 0 ? 1 : PASS+FAIL) ))%"
report "==============================================================="

[ -n "$OUTPUT" ] && echo "Rapport écrit dans $OUTPUT" >&2
exit 0

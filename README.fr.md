# ProxKnox

**Chaîne automatisée : Debian 13 → Proxmox VE 9.2 durci → audit → test E2E cluster.**

Depuis une machine Linux locale, sans VM serveur ni préparation manuelle :

- `./tools/chaine-complete.sh --iso` produit un **nœud Proxmox VE 9.2 durci**
  (12 rôles de hardening selon le guide HomeSecExplorer PVE9 + benchmark
  **CIS Debian 13 Level 2** via `ansible-lockdown.DEBIAN13-CIS`, pinné 1.1.0)
  et un **rapport d'audit consolidé** (md + html) ;
- `./tools/e2e-cluster-test.sh` déploie un **cluster à 3 nœuds** (PXE réel
  multi-nœuds), y active **Ceph**, y exécute une **VM de test (1 vCPU/2 Go
  sur RBD)**, **2 live-migrations avec downtime mesuré au ping**, et produit
  un **rapport de test JSON** (façon ansible-lockdown) rendu en **HTML une
  page**.

| Run de référence | Résultat mesuré |
|---|---|
| Audit CIS L2 — 19/11/2026 | **304 échecs → 14** sur 891 contrôles (**97,5 %** de conformité) ; 8 contrôles manuels par conception |
| Audit hardening PVE — 19/11/2026 | **0 écart** (rôle `pve_audit`, 20 contrôles json) |
| Test E2E cluster — 24/11/2026 | **12/12 pass** — 2 live-migrations (9-10 s), **0 réponse ping perdue** (44 échantillons à 200 ms), Ceph `HEALTH_OK` avant/après |

Rapports versionnés dans [`docs/runs/`](docs/runs/) — sources JSON (non
versionnées) dans `ansible/audit-summaries/`.

> Référence détaillée : **[`docs/chaine-complete.md`](docs/chaine-complete.md)** —
> prérequis, modes de déploiement, hardening, rapports, dépannage (constatés sur
> runs réels). Annexe : [`docs/analyse-ansible.md`](docs/analyse-ansible.md)
> (faisabilité d'une refonte du déploiement en Ansible).

---

## La chaîne en un coup d'œil

```
  tools/chaine-complete.sh [--iso] [--bootstrap]
  │
  ├─ 1. DÉPLOIEMENT   deploy/pxe_deploy_container.py [--iso] [--client …]
  │     réseau libvirt « pxe-lab » → conteneur Docker proxknox-pxe
  │     (DHCP + TFTP + DNS + HTTP :8000 + NAT, netns hôte)
  │     → VM installée par Debian-installer (preseed, SANS màj en mode --iso)
  │     → conversion Proxmox VE 9.2 (deploy/install-pve-on-debian.sh, 2 phases)
  │     → ansible/inventory/hosts.yml
  ├─ 2. HARDENING PVE ansible/harden-pve.yml — guide HomeSecExplorer v0.9.2
  │     12 rôles ON par défaut (bascule par variable) : microcode,
  │     unattended-upgrades, fail2ban (sshd + UI 8006), KSM off, RBAC,
  │     auditd /etc/pve, rkhunter, corosync secauth, firewall PVE,
  │     ssh-audit, durcissement Ceph → audit pve_audit : JSON
  ├─ 3. HARDENING CIS ansible/site.yml — ansible-lockdown DEBIAN13-CIS L2
  │     remédiation + écarts Proxmox cadrés → REBOOT → audits syver
  │     pré/post remédiation (JSON)
  └─ 4. RAPPORT       tools/audit-report.py → md + html (docs/runs/)
        page unique, zéro dépendance, stats de haut niveau,
        drill-down par contrôle, JSON sources embarqués
```

---

## Démarrage rapide — nœud unique durci

```bash
# 1. cloner et vérifier les prérequis (KVM imbriqué, Docker, libvirt, Ansible)
git clone https://github.com/hummelm/ProxKnox && cd ProxKnox
./tools/chaine-complete.sh --check

# 2. lancer la chaîne complète — 1er run : --bootstrap installe les rôles
./tools/chaine-complete.sh --iso --bootstrap   # ~50-60 min avec l'ISO en cache
./tools/chaine-complete.sh --iso               # runs suivants (~50 min)

# 3. utiliser le résultat (nœud durci, SSH port 2222)
IP=$(grep -m1 ansible_host ansible/inventory/hosts.yml | awk '{print $2}')
ssh -p 2222 adminlab@$IP                        # adminlab, sudo NOPASSWD
ssh -p 2222 -L 8006:$IP:8006 adminlab@$IP       # UI : https://localhost:8006

# utiles ensuite
./tools/chaine-complete.sh --reports-only       # regénérer les rapports
./tools/chaine-complete.sh --skip-deploy        # re-hardening + audit seulement
```

### Variantes de la chaîne

| Besoin | Commande |
|---|---|
| Déploiement réseau classique (miroir Debian) | `./tools/chaine-complete.sh` |
| Nœud déjà déployé : (re)hardening + audit | `./tools/chaine-complete.sh --skip-deploy` |
| Hardening PVE seul (sans CIS Lockdown) | `./tools/chaine-complete.sh --skip-deploy --skip-cis` |
| CIS Lockdown seul (sans hardening PVE) | `./tools/chaine-complete.sh --skip-deploy --skip-pve-hardening` |
| Rapport d'audit uniquement | `./tools/chaine-complete.sh --reports-only` |
| Vérifier les prérequis | `./tools/chaine-complete.sh --check` |

---

## Démarrage rapide — lab cluster 3 nœuds (Ceph + live-migration)

Prérequis : la chaîne simple a déjà été jouée une fois (ISO DVD-1 en cache).

```bash
./tools/e2e-cluster-test.sh --check            # prérequis
./tools/e2e-cluster-test.sh                    # tout : 3 nœuds PXE réels → vmbr0
                                               #   statique → cluster (quorum 3)
                                               #   → durcissement 12 rôles → Ceph
                                               #   (1 OSD/nœud, pool RBD 3/2)
                                               #   → VM mini 1c/2Go → 2 live-migrations
                                               #   → rapport JSON + md + HTML
./tools/e2e-cluster-test.sh --phase ceph       # reprendre une phase :
                     # deploy | net | cluster | hardening | ceph | tests | report
```

- **Rapport JSON** : `ansible/audit-summaries/e2e-cluster-test_<epoch>.json` —
  même esprit que les audits ansible-lockdown : un résultat par test
  (id, phase, attendu, constat), statuts `pass`/`fail`/`notrun`, summary.
- **Rendu** : `python3 tools/e2e-report.py` → `docs/runs/
  rapport-e2e-cluster_<date>.[md|html]` — HTML **une page, zéro dépendance** :
  cartes de synthèse (tests, migrations, pings perdus), détail par phase
  dépliable, badge par test.
- La VM de test (IP déterministe : réservation `dhcp-host` par MAC **et**
  IP statique cloud-init) reste en place pour inspection.

---

## Hardening — que fait la chaîne ?

### Proxmox VE (guide HomeSecExplorer v0.9.2) — `ansible/harden-pve.yml`

12 rôles **tous ON par défaut**, activables un à un via
`ansible/group_vars/pve_nodes.yml` (`pve_hardening_<rôle>: true|false`) :

`pve_microcode` · `pve_autoupdate` · `pve_fail2ban` (sshd + UI 8006) ·
`pve_ksm` (désactivé, side-channels inter-VM) · `pve_users` (RBAC/2FA/tokens) ·
`pve_logging` (auditd `/etc/pve`) · `pve_rkhunter` · `pve_cluster`
(corosync `secauth`) · `pve_firewall` (allow-list 8006/SSH, INPUT DROP) ·
`pve_ssh_audit` (profil ssh-audit, `sshd -t` validé avant rechargement) ·
`pve_ceph` (durcissement Ceph type L2 : cephx, msgr2 secure, keyrings 0600 ;
auto-noop sans Ceph) · `pve_audit` (audit sur le nœud → JSON
`pve-hardening-audit_<epoch>.json`, même format que les scans CIS).

### CIS Debian 13 Level 2 — `ansible/site.yml`

Rôle `ansible-lockdown.DEBIAN13-CIS` (pinné `1.1.0`) avec écarts Proxmox
cadrés et documentés dans `group_vars/pve_nodes.yml` :

| Écart | Variable | Justification |
|---|---|---|
| § 4 pare-feu hôte sauté | `deb13cis_section4: false` | le firewall **PVE** fait autorité (ufw + pve-firewall se neutralisent) |
| § 5.1.8 `DisableForwarding` | `deb13cis_rule_5_1_8: false` | la live-migration PVE exige un port-forward streamlocal SSH (CIS 5.1.8 cassait toute migration entrante) |
| forwarding IPv4 / rp_filter | `deb13cis_is_router: true` | Proxmox route/ponte pour ses invités |
| IPv6 conservé | `deb13cis_ipv6_required: true` | défaut PVE |
| § 2.1.13 rsync masqué | `deb13cis_rsync_mask: true` | dépendance du méta-paquet `proxmox-ve` |

### Audit et rapports

| Source | Sortie |
|---|---|
| Rôle CIS Lockdown : audits **syver** pré/post remédiation | `ansible/audit-summaries/debian-*-CIS-*.json` |
| Rôle `pve_audit` (contrôles HSE + Ceph sur le nœud) | `ansible/audit-summaries/pve-hardening-audit_<epoch>.json` |
| Tests E2E cluster (`rôle e2e_test`, 12 tests T010-T120) | `ansible/audit-summaries/e2e-cluster-test_<epoch>.json` |
| Consolidation audit | `python3 tools/audit-report.py` → `docs/runs/rapport-audit-cis-debian13-pve_<date>.[md\|html]` (drill-down, **JSON sources embarqués dans la page**) |
| Rendu E2E | `python3 tools/e2e-report.py` → `docs/runs/rapport-e2e-cluster_<date>.[md\|html]` |

---

## Variables utiles (déploiement)

| Variable | Défaut | Rôle |
|---|---|---|
| `ROOT_PW` / `ADMIN_PW` | valeurs de lab | mots de passe root/adminlab du nœud |
| `SSH_PUBKEY_FILE` | `~/.ssh/id_rsa.pub` | clé injectée par le preseed |
| `DISK_POOL` | `libvirt-work` | pool disque de la VM |
| `WORKDIR` | `libvirt-work/pxe-work` | fichiers servis (TFTP, preseed, ISO, logs) |
| `ISO_PATH` / `ISO_URL` | DVD-1 cdimage (13.7.0) | ISO complète (mode `--iso`), SHA-256 vérifiée |
| `WAIT_SSH_MAX_MIN` | `60` | délai max (min) d'attente du SSH du client |
| `CLIENT_NAME`, `CLIENT_MAC` | `pxe-client`, `52:54:00:aa:00:10` | identité du nœud déployé (surclassable par `--client` / `--mac`) |

Déploiement multi-nœuds (lab cluster) : `--client`, `--mac`, `--hostname`,
`--vcpus`, `--ram`, `--disk-gb`, `--osd-disk-gb` — cf.
[`docs/chaine-complete.md` § 4.1](docs/chaine-complete.md#41-conteneur-docker-celle-utilisée-par-la-chaîne).

---

## Arborescence

```text
ProxKnox/
├── README.md                      ← point d'entrée (ce fichier)
├── docs/
│   ├── chaine-complete.md          ← référence détaillée (prérequis → dépannage)
│   ├── analyse-ansible.md         ← annexe : refonte du déploiement en Ansible ?
│   └── runs/                       ← rapports générés, versionnés
├── deploy/                         ← déploiement PXE côté hôte
│   ├── pxe_deploy_container.py     ← orchestrateur [--iso] [--client …] : conteneur PXE + VM(s)
│   ├── install-pve-on-debian.sh     ← conversion Debian 13 → PVE 9.2 (2 phases)
│   └── pxe/
│       ├── client-preseed.cfg       ← preseed client (partitions CIS)
│       ├── pxe-lab-network.xml      ← réseau libvirt isolé « pxe-lab » (sans DHCP libvirt)
│       └── container/               ← serveur PXE Docker (Dockerfile, entrypoint, syslog)
├── ansible/                        ← projet Ansible autonome
│   ├── ansible.cfg · requirements.yml  ← rôles requis (CIS pinné 1.1.0)
│   ├── site.yml                     ← CIS L2 Lockdown + audits syver pré/post
│   ├── harden-pve.yml               ← hardening PVE : 12 rôles ON par défaut
│   ├── e2e-cluster.yml · e2e-tests.yml ← lab cluster (net/cluster/ceph, tests E2E)
│   ├── group_vars/pve_nodes.yml     ← bascules hardening + écarts CIS
│   ├── inventory/hosts.yml(.example) ← inventaire (hosts.yml NON versionné, généré)
│   ├── audit-summaries/             ← JSON NON versionnés : scans syver, pve_audit, e2e
│   └── roles/                       ← 12 rôles pve_* + e2e_test
│                                      (ansible-lockdown.DEBIAN13-CIS gitignoré,
│                                      installé par --bootstrap)
└── tools/
    ├── chaine-complete.sh           ← orchestrateur de la chaîne complète
    ├── e2e-cluster-test.sh          ← procédure E2E cluster (--phase …)
    ├── audit-report.py               ← rapport d'audit consolidé md + html
    └── e2e-report.py                 ← rapport E2E md + html (page unique)
```

Workdir du lab (hors repo, recréable) : `libvirt-work/pxe-work/` — disques
des VM, TFTP, preseeds, ISO en cache, logs (dont `dnsmasq.leases`,
`dnsmasq-dhcp-hosts.conf`).

---

## Ce qui reste manuel (par conception)

| Domaine | Référence guide |
|---|---|
| 2FA UI (TOTP/YubiKey) + bris de glace root scellé | HSE § 2.1.3/2.1.4 |
| Certificats UI (ACME/PKI, `pvenode cert set`) | HSE § 2.3.1/2.3.2 |
| Sauvegardes 3-2-1 + test de restauration | HSE § 4.x |
| Supervision centralisée (Prometheus/Grafana) | HSE § 5.2.x |
| Secure Boot + chiffrement disque (avant usage réel) | HSE § 1.1.5/1.2.1 |
| Revue des « Manual Check Required » du rapport CIS | rapport § échecs résiduels |

## Sauter vers… (index de la documentation)

| Besoin | Où |
|---|---|
| Prérequis détaillés (virtualisation imbriquée, paquets) | [`docs/chaine-complete.md` § 2](docs/chaine-complete.md#2-prérequis-contrôleur--machine-linux-locale) |
| Modes netboot / ISO, options, variables d'env | [`docs/chaine-complete.md` § 3-4](docs/chaine-complete.md#3-mode-opératoire) |
| Hardening : 12 rôles PVE, écarts CIS documentés | [`docs/chaine-complete.md` § 5](docs/chaine-complete.md#5-hardening) |
| Audit → rapports (md/html, JSON embarqués) | [`docs/chaine-complete.md` § 6](docs/chaine-complete.md#6-audit--rapports) |
| Test E2E cluster 3 nœuds (Ceph, migrations) | [`docs/chaine-complete.md` § 6bis](docs/chaine-complete.md#6bis-test-end-to-end-cluster-3-nœuds-ceph--live-migration) |
| Ce qui reste manuel | [`docs/chaine-complete.md` § 7](docs/chaine-complete.md#7-ce-qui-reste-manuel-par-conception) |
| Dépannage (16 incidents constatés sur runs réels) | [`docs/chaine-complete.md` § 8](docs/chaine-complete.md#8-dépannage-tous-constatés-lors-des-runs-réels) |
| Éteindre / supprimer le lab | [`docs/chaine-complete.md` § 3.5](docs/chaine-complete.md#35-éteindre--supprimer-le-lab) |
| Rapports des runs validés | [`docs/runs/`](docs/runs/) |

## Avertissements

- Les durcissements (CIS L2 + hardening PVE) **modifient fortement le
  système** : prévu pour un lab — évaluer chaque contrôle avant production.
- `PermitRootLogin` restreint par CIS : administration via `adminlab`
  (sudo NOPASSWD) ou clés SSH.
- Mots de passe par défaut de lab (`ChangeMeRoot!`/`ChangeMeAdmin!`) :
  à surcharger (`ROOT_PW`/`ADMIN_PW`) dès que le lab sort de votre machine.
- Certains contrôles restent manuels par nature (2FA, BIOS/matériel,
  sauvegardes) — cf. § 7 ci-dessus.

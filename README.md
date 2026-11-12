# Proxmox VE 9.2 — Hardening CIS Level 2 (lab)

Kit de test du hardening CIS Level 2 sur une instance **Proxmox VE 9.2** (base
**Debian 13 « Trixie »**, kernel 7.0) déployée dans une VM.
Tout est scripté : **Bash** pour le déploiement et l'audit, **Ansible** pour l'application des contrôles.

## Arborescence

```
pve-cis-l2/
├── README.md
├── ansible.cfg
├── site.yml                     # playbook racine
├── requirements.yml             # collections requises
├── inventory/
│   └── hosts.yml.example        # inventaire exemple
├── scripts/
│   ├── deploy-pve-vm.sh         # déploie une VM PVE auto-installée (nested) sur un hôte Proxmox
│   └── audit-cis.sh             # audit CIS L2 (bash pur, exécutable sur le nœud PVE)
└── roles/pve_cis_l2/
    ├── defaults/main.yml        # toutes les variables de durcissement (toggles)
    ├── tasks/                   # 1 section CIS par fichier
    └── templates/               # configs déployées (sysctl, sshd, auditd, fail2ban…)
```

## 1. Prérequis

- Un hôte Proxmox VE **8.4+ / 9.x** pour héberger la VM de test (virtualisation imbriquée activable).
- Une machine de contrôle Ansible (≥ 2.14) avec `community.general`.
- Accès SSH root vers l'hôte PVE et vers la future VM.

## 2. Déployer la VM — deux voies

### Voie A (recommandée pour CIS) : Debian 13 préseedé -> conversion PVE 9.2

L'ISO d'installation PVE met tout dans une seule LVM (`pve/data`) : les contrôles
**CIS 1.1.x** (partitions séparées `/tmp`, `/var/tmp`, `/var/log`, `/var/log/audit`,
`/home` avec `nodev/nosuid/noexec`) sont alors impossibles. C'est la raison pour
laquelle Proxmox documente officiellement l'installation de PVE 9.2 sur Debian 13
« Trixie » pour une layout personnalisé :
https://pve.proxmox.com/wiki/Install_Proxmox_VE_on_Debian_13_Trixie

`scripts/deploy-debian-vm.sh` :
1. télécharge l'ISO **Debian 13.7 netinst** sur l'hôte PVE ;
2. génère un **preseed** avec le partitionnement CIS (ESP + /boot + LVM :
   `/`, `/tmp`, `/var`, `/var/tmp`, `/var/log`, `/var/log/audit`, `/home`,
   `/var/lib/vz`, swap) et remasterise l'ISO (`xorriso`) ;
3. crée et démarre la VM (nested), attend SSH ;
4. exécute `scripts/install-pve-on-debian.sh` **en 2 phases idempotentes**
   (procédure officielle Trixie) : dépôt deb822 + keyring, kernel
   `proxmox-default-kernel` + reboot, puis `proxmox-ve postfix open-iscsi chrony`,
   retrait du kernel Debian 6.12, bridge `vmbr0`, reboot final.

```bash
# adapter l'en-tête (PVE_HOST, DEB_IP, DEB_GW, ROOT_PASSWORD...)
./scripts/deploy-debian-vm.sh
```

### Voie B (rapide) : ISO PVE 9.2 auto-installée

Partitionnement PVE par défaut (non conforme CIS 1.1) — utile pour tester le
reste des contrôles sans refaire une install Debian :

```bash
./scripts/deploy-pve-vm.sh   # ISO proxmox-ve_9.2-1.iso + answer file
```

Dans les deux cas, récupérer l'IP de la VM et mettre à jour `inventory/hosts.yml`.

## 3. Appliquer le hardening CIS L2

```bash
cp inventory/hosts.yml.example inventory/hosts.yml   # adapter IP + user
ansible-galaxy collection install -r requirements.yml
ansible-vault encrypt_string 'S3cur3P@ss!' --name 'pve_cis_root_password'
ansible-playbook -i inventory/hosts.yml site.yml --ask-vault-pass
```

Sections activables via variables (voir `roles/pve_cis_l2/defaults/main.yml`) :
`filesystem`, `packages_services`, `network_sysctl`, `auditd`, `logging`,
`auth_pam`, `sshd`, `pve_specific` (2FA, fail2ban PVE, pveproxy, firewall, permissions /etc/pve).

> Avec la voie A, les partitions CIS 1.1 sont créées à l'installation ; le rôle
> Ansible ne fait alors que vérifier/appliquer les options de montage.

## 4. Auditer

Sur la VM cible :

```bash
./scripts/audit-cis.sh                 # rapport console
./scripts/audit-cis.sh -o report.txt   # rapport fichier
```

Ou via Ansible :

```bash
ansible-playbook -i inventory/hosts.yml site.yml --tags audit
```

Score final : `PASS / FAIL / WARN` par contrôle, résumé et pourcentage de conformité.

## Notes

- CIS Level 2 est **intrusif** : USB storage désactivé, `AllowTcpForwarding no`,
  `single-user-mode` auth, AIDE, etc. Ne pas appliquer sur un nœud de production
  sans relecture — ce kit est prévu pour un lab.
- Il n'existe pas de benchmark CIS officiel publié pour Proxmox VE ; les contrôles
  s'appuient sur les benchmarks CIS Debian 13 + recommandations spécifiques PVE
  (interface web, 2FA, fail2ban, permissions /etc/pve).

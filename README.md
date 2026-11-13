# ProxKnox — Lab automatisé : Debian 13 CIS L2 → Proxmox VE 9.2 (Ansible Lockdown)

Système 100 % scripté qui, depuis une **machine Linux locale** :

1. crée une **VM KVM/libvirt** avec `virt-install` (virtuclisation imbriquée) ;
2. installe **Debian 13 « Trixie »** via preseed avec un **partitionnement conforme CIS 1.1**
   (`/tmp`, `/var`, `/var/tmp`, `/var/log`, `/var/log/audit`, `/home`, `/var/lib/vz`
   séparés, `nodev/nosuid/noexec`) ;
3. applique la **conversion Debian 13 → Proxmox VE 9.2** (procédure officielle Proxmox,
   2 phases idempotentes) ;
4. applique le durcissement **CIS Level 2** avec le rôle officiel
   **[Ansible Lockdown DEBIAN13-CIS](https://github.com/ansible-lockdown/DEBIAN13-CIS)**
   (benchmark CIS Debian 13 v1.0.0) ;
5. **vérifie la conformité** avec l'audit automatisé Lockdown (`DEBIAN13-CIS-Audit`,
   binaire `syver`) — rapports pré/post remédiation récupérés dans `audit-summaries/`.

## Arborescence

```
ProxKnox/
├── README.md
├── ansible.cfg
├── site.yml                        # playbook Lockdown L2 (cible = VM Proxmox)
├── requirements.yml                # collections + rôle ansible-lockdown.DEBIAN13-CIS
├── group_vars/pve_nodes.yml        # vars Lockdown : level 2 + compat PVE + audit
├── inventory/hosts.yml.example
├── preseed/debian13-cis.cfg        # preseed Debian 13 (partitions CIS + admin sudo)
└── scripts/
    ├── deploy-vm-libvirt.sh        # ORCHESTRATEUR : virt-install → PVE 9.2 → Lockdown → audit
    └── install-pve-on-debian.sh    # conversion Debian 13 → PVE 9.2 (idempotent, 2 phases)
```

## Prérequis (machine locale)

- Linux avec **KVM** : `qemu-kvm libvirt-daemon-system libvirt-clients virtinst ovmf`
- **Virtualisation imbriquée** activée (Proxmox tourne dans la VM) :
  ```bash
  echo 'options kvm-intel nested=Y' | sudo tee /etc/modprobe.d/kvm-nested.conf
  sudo modprobe -r kvm_intel && sudo modprobe kvm_intel   # (kvm_amd pour AMD)
  ```
- Réseau libvirt `default` (virbr0, DHCP 192.168.122.0/24) actif
- Ansible ≥ 2.16 + `ansible-galaxy`
- Une paire de clés SSH (`~/.ssh/id_ed25519.pub`)

## Utilisation — une seule commande

```bash
git clone https://github.com/hummelm/ProxKnox && cd ProxKnox
# adapter si besoin l'en-tête de scripts/deploy-vm-libvirt.sh
./scripts/deploy-vm-libvirt.sh
```

Le script enchaîne tout : VM → Debian 13 (preseed HTTP) → conversion PVE 9.2
(kernel `proxmox-default-kernel` → reboot → `proxmox-ve` + bridge `vmbr0` → reboot)
→ `ansible-galaxy install` → `ansible-playbook site.yml` (Lockdown L2 + audit).

Mode pas-à-pas : `./scripts/deploy-vm-libvirt.sh --skip-ansible`, puis :

```bash
ansible-galaxy install -r requirements.yml
ansible-playbook -i inventory/hosts.yml site.yml
```

## Configuration Lockdown (group_vars/pve_nodes.yml)

- `deb13cis_level_1` / `deb13cis_level_2` : niveau CIS
- `deb13cis_disruption_high: true` : contrôles L2 à impact élevé (lab)
- `deb13cis_is_router: true` : **compatibilité PVE** — conserve le forwarding IPv4
  (SDN/NAT/firewall de Proxmox), évince les contrôles 3.3.1.x
- `setup_audit` / `run_audit` : audit syver pré/post remédiation automatique
- `fetch_audit_output` : rapports JSON ramenés dans `audit-summaries/`

## Résultat attendu

- VM accessible en SSH avec l'utilisateur `adminlab` (root SSH désactivé par CIS 5.1.21)
- **UI Proxmox** : `https://<IP-VM>:8006` (root, realm PAM — la 2FA TOTP peut être
  activée dans l'UI, non automatisable)
- Rapports d'audit : `audit-summaries/` côté contrôleur et `/opt` sur la VM
- Recap Ansible sans `failed` ; audit post-remédiation avec failures résiduelles
  minimales (certains contrôles nécessitent un reboot ou sont manuels)

## Avertissements

- Le rôle Lockdown **modifie fortement le système** : prévu ici pour un lab.
- `PermitRootLogin no` est appliqué par CIS : connecte-toi avec `adminlab` (sudo NOPASSWD).
- Certains contrôles CIS (2FA, BIOS, matériel) restent manuels par nature.

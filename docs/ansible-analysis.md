# Analysis — should `pxe_deploy_container.py` be rewritten entirely in Ansible?

Date: Nov. 2026 — scope: `deploy/pxe_deploy_container.py` (the "Docker
PXE server" variant, ~800 loc, multi-node since the cluster lab),
6 synchronous steps driven from the host (local Ansible controller).

## 1. Inventory of what the Python orchestrator does

| # | Current behavior (Python) | Native Ansible equivalent? | Difficulty |
|---|---|---|---|
| 1 | Prerequisites: `docker info`, libvirt `default`/`pxe-lab` networks (define/start/autostart), disk pool | `community.docker.docker_host_info`, `virt_infrastructure.libvirt.virt_net` modules (or `command: virsh`) | Low |
| 2 | Prepares the files: templated preseed (passwords, key), deployment key, `pxelinux.cfg/default`, ISO/netboot (get_url + xorriso) | `template`, `community.crypto.openssh_keypair`, `get_url`, `command: xorriso`, `get_url` with sha256 | Low — direct mapping |
| 3 | Builds and starts the proxknox-pxe container (`--network host`, cap NET_ADMIN, volumes, SIGTERM → entrypoint) | `community.docker.docker_image` + `docker_container` | Low |
| 4 | Creates the pxe-client VM (`virt-install --pxe`), forces the hd→network boot order through XML surgery, restarts it | **No `virt-install` module**: `command`/`script` + `virt_infrastructure.libvirt.virt_*` for the XML; idempotence must be hand-written (check "VM exists?") | Medium |
| 5 | d-i rollout: waits for the DHCP lease + reads the file (mounted), gates SSH via `docker exec ssh`, **2 continuously-running watcher threads** (Enter press via qemu-monitor + 8-min cold reboot watchdog) | `wait_for`, `until/retries`, `slurp` — but **nothing event-driven**; the 2 watchers require *uninterrupted polling in parallel* with another task | **High** (see §3) |
| 6 | 2-phase PVE conversion: pushes the script to the client via ssh (docker exec), reboots, waits, re-pushes, final reboot, `pveversion` | Ansible roles played **on the client** (target = VM) with native `reboot`: the only big piece that would *migrate better* than it is currently written; the "docker exec ssh" push can be replaced with `ansible_ssh_common_args: ProxyCommand="docker exec -i proxknox-pxe ssh -W %h:%p …"` | Low to medium |
| 7 | Writes `ansible/inventory/hosts.yml` + final report | `template` (localhost) or `add_host` + a later play | Low |

## 2. What translates cleanly

- **All of the file, network, and container preparation**: standard
  modules (`template`, `get_url`, `docker_image`, `docker_container`,
  `virt_net`). An `infra.yml` playbook could replace steps 1–3 with no
  loss.
- **Driving the client**: once the client has an IP and SSH (the
  inventory can be dynamic: an inventory script reading
  `logs/dnsmasq.leases`), everything done *on* the client runs through
  genuinely idempotent modules (apt, systemd, files) — the 2-phase PVE
  conversion even gains robustness thanks to `ansible.builtin.reboot`
  (a clean wait for SSH to come back, where the hybrid script counts
  delays).
- **Access to the isolated segment**: the current limitation ("the
  host does not route, we go through docker exec ssh") disappears:
  `ProxyCommand` via `docker exec` or `ssh -J` makes the client
  directly targetable by playbooks from the controller — we already
  use it for `site.yml` (Lockdown).

## 3. The three pain points

1. **The real-time watchers (step 5)** — the real friction point.
   - *Enter press on the d-i final screen*: must be sent at an
     indeterminate moment, **while the playbook is waiting for
     something else** (SSH). In Ansible, a "watcher role" has to run in
     `async` with polling, or via two concurrent playbooks (a looping
     `ansible.builtin.local_action` cannot run in parallel with
     another task within the same play). The realistic options: (a) an
     `async` task looping a `virsh qemu-monitor-command` script, (b)
     keep the Bash/Python loop launched by `ansible.builtin.script`
     running in the background. Each variant reintroduces, in the form
     of a script, the very logic we wanted to eliminate.
   - *Cold reboot watchdog*: same problem — it must act (destroy +
     start) **while** waiting. Sequential `until/retries` only covers
     linear scenarios; the non-linear recoveries (QEMU freeze at 8
     min, max 2 restarts, 3-minute silence window) call for a state
     machine, not a task list.
2. **No `virt-install` module**: creating the PXE VM with a blank disk +
   boot order and the XML surgery (`force_boot_hd_then_network`)
   remain `command` + `virt_*`. Doable, but the declarative aspect is
   lost; idempotence (existing VM? existing disk? leftover PXE
   config?) must be coded explicitly in `pre_tasks`.
3. **Ephemeral multi-VM sequence**: the lab performs a full "cold"
   install on every run (deliberate destruction). Ansible excels at
   *durable* targets; here the target (pxe-client) does not exist yet
   when the playbook starts → ad hoc dynamic inventory +
   `gather_facts: false`, then SSH bootstrap. It can all be done, but
   with delicate glue-code.

## 4. Verdict

| Approach | Feasibility | Effort | Net benefit |
|---|---|---|---|
| 100% playbook rewrite (watchers in `async`/scripts) | Possible | High (~2× the current code as YAML + helper scripts) | Low: the "imperative" part migrates into scripts, idempotence is only gained on the client |
| **Recommended hybrid**: Python orchestrator for the real-time mechanics (PXE boot, watchers, 2 phases), Ansible roles for everything touching the system (PVE conversion, CIS Lockdown, PVE hardening) — this is the repo's current architecture | Already in place | Minimal | Maximal: each building block is testable/idempotent where it matters |
| "Playbook + Bash sidecar watcher" rewrite | Possible | Medium | Moderate, but two runtimes to maintain |

**Conclusion.** Rewriting `pxe_deploy_container.py` *entirely* in
Ansible is technologically possible but economically poor: Ansible's
value (idempotence, modules, roles) concentrates on configuring the
already-installed client — something the repo already does better than
a rewrite would (the CIS Lockdown role, the 2-phase conversion, and
the new PVE hardening roles). The PXE mechanics (real-time DHCP/TFTP,
d-i final screens, QEMU freezes, supervised reboots) are *event-driven*
orchestration — the domain of scripts, not playbooks. We therefore
recommend: **keep the Python orchestrator as the "driver", and keep
externalizing into Ansible roles everything that is configuration**
(see `harden-pve.yml` and `roles/`).

## 5. If we decided to migrate anyway: plan

1. `infra.yml` playbook (localhost): pxe-lab network, files, image,
   container — replaces steps 1–3 (nearly 1:1, low risk).
2. `pxe_watch` role: local script launched in `async` — Enter press +
   reboot watchdog; burdened with the same constraints as today.
3. `dnsmasq.py` dynamic inventory: reads
   `pxe-work/logs/dnsmasq.leases`.
4. `pve_convert` role: replaces `install-pve-on-debian.sh` in 2 plays
   (native `reboot`), via docker ProxyCommand; idempotent.
5. `site.yml` unchanged, then `harden-pve.yml` (hardening roles).

Steps 1 and 4–5 are net gains; 2–3 are glue. This is the exact picture
of a boundary: **migrate the configuration, not the mechanics**.

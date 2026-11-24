# End-to-End test report — ProxKnox cluster (3 nodes)

- **Scenario**: 3-node Proxmox VE 9.2 lab cluster: PXE deployment, corosync cluster, Ceph (1 OSD/node), mini VM 1 vCPU/2 GB on RBD, live migrations with downtime measurement
- **Lab nodes**: proxknox-node1 (pxe-client), proxknox-node2 (pxe-node2), proxknox-node3 (pxe-node3)
- **Date**: 2026-11-24T13:17:23Z
- **Verdict**: ✅ PASS

## Overview

| Metric | Value |
|---|---|
| Tests | 12 |
| Passed | 12 |
| Not run | 0 |
| **Failures** | **0** |

### Cluster — 1/1 passed ✅

| Test | Expected | Result | Finding |
|---|---|---|---|
| T010-quorum-3 — 3-vote corosync quorum | pvecm status → Quorum: 3 votes | ✅ pass | Cluster information ------------------- Name:             proxknox-lab Config Version:   … |

### Ceph — 3/3 passed ✅

| Test | Expected | Result | Finding |
|---|---|---|---|
| T020-ceph-health — Ceph HEALTH_OK | ceph -s → health.status == HEALTH_OK | ✅ pass | HEALTH_OK |
| T030-ceph-osd — At least 3 OSDs (one per node) | len(ceph osd dump .osds) >= 3 | ✅ pass | 3 OSD |
| T040-ceph-hardened — Ceph hardening applied (msgr2 secure + cephx) | pve_ceph role: auth_*_required + ms_*_mode secure in ceph.conf | ✅ pass |  |

### Mini VM — 3/3 passed ✅

| Test | Expected | Result | Finding |
|---|---|---|---|
| T050-vm-deployed — Mini VM deployed (1 CPU / 2 GB / RBD disk) | qm create 100 + importdisk of the Debian 13 cloud image | ✅ pass | VM 100 already deployed (on proxknox-node3) |
| T060-vm-booted — Mini VM started (real OS boot) | qm status 100 → running | ✅ pass | status: running |
| T070-vm-ping — Mini VM reachable before migration | ping OK to 192.168.200.192 (cloud-init static IP) | ✅ pass | ping to 192.168.200.192 → OK |

### Live migration — 4/4 passed ✅

| Test | Expected | Result | Finding |
|---|---|---|---|
| T080-live-migration — Live migration node1 → proxknox-node2 + ping downtime | continuous ping during qm migrate --online: ~0 losses, VM running on the target | ✅ pass | PING sent=44 lost=0 MigDur=10s vm_ip=192.168.200.192 |
| T090-vm-running-target — VM running as seen from proxknox-node2 (pvesh API) | pvesh /nodes/proxknox-node2/qemu/100/status → running | ✅ pass | running |
| T100-second-migration — Second live migration → proxknox-node3 | qm migrate --online; VM running on target 2 | ✅ pass | MigDur=10s |
| T110-vm-healthy — VM still reachable after 2 migrations | ping OK after migrations | ✅ pass | ping → OK |

### Conclusion — 1/1 passed ✅

| Test | Expected | Result | Finding |
|---|---|---|---|
| T120-ceph-final — Ceph HEALTH_OK after all tests | ceph -s → HEALTH_OK | ✅ pass | HEALTH_OK |

Source: `e2e-cluster-test_1795526243.json`

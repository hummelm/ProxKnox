#!/usr/bin/env python3
"""e2e-report.py — ProxKnox End-to-End test report (3-node cluster)

Turns the E2E test JSON report (e2e_test role,
e2e-cluster-test_<epoch>.json) into:
  - Markdown: pipe tables
  - HTML    : SELF-CONTAINED page (no dependencies, no JS) — stat cards
              + per-test collapsible detail (native <details>), with the
              source JSON embedded.

Usage:
  python3 tools/e2e-report.py [--audit-dir ansible/audit-summaries] \
      [--out-prefix docs/runs/report-e2e-cluster] [--suite <json>]
"""

import argparse
import glob
import json
import html as _html
import os
import re
import sys
from datetime import datetime

CSS = """
:root{color-scheme:light dark}
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;margin:2rem auto;
max-width:1150px;color:#1a1a1a;line-height:1.45;background:#fafafa}
@media (prefers-color-scheme:dark){body{background:#141414;color:#ddd}
th{background:#242424!important}details summary:hover{background:#242424}}
h1{border-bottom:3px solid #1565c0;padding-bottom:.3rem}
h2{border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.88rem}
th,td{border:1px solid #ccc;padding:.45rem .6rem;text-align:left;vertical-align:top}
th{background:#f0f0f0} code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px}
.meta{color:#555;font-size:.9rem}.cards{display:flex;flex-wrap:wrap;gap:1rem;margin:1.5rem 0}
.card{flex:1 1 200px;background:#fff;border:1px solid #ddd;border-radius:8px;
padding:.9rem 1.1rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h3{margin:0 0 .4rem;font-size:.85rem;text-transform:uppercase;
letter-spacing:.04em;color:#666}.card .big{font-size:1.7rem;font-weight:700}
.card .sub{font-size:.8rem;color:#777}
.pass{color:#2e7d32;font-weight:600}.fail{color:#c62828;font-weight:600}
.badge{display:inline-block;border-radius:4px;padding:.1rem .5rem;
font-weight:600;color:#fff;font-size:.85rem}.b-pass{background:#2e7d32}
.b-fail{background:#c62828}.b-neutral{background:#616161}
details{border:1px solid #ddd;border-radius:6px;margin:.45rem 0;background:#fff}
details summary{cursor:pointer;padding:.55rem .8rem;font-weight:500;
list-style:none;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸";font-size:.9rem;color:#888}
details[open] summary::before{transform:rotate(90deg)}
details summary:hover{background:#f2f2f2}
details .body{padding:.6rem .9rem;border-top:1px solid #eee;font-size:.9rem}
.kv{font-size:.85rem;color:#555;margin:.15rem 0}
.tab{white-space:pre;overflow:auto;font-size:.78rem;background:#f5f5f5;
padding:.6rem;border-radius:4px;max-height:22rem}
.count{font-variant-numeric:tabular-nums;color:#888;font-size:.8rem}
footer{margin-top:2.5rem;padding-top:.8rem;border-top:1px solid #ddd;
color:#888;font-size:.8rem}
"""


def die(msg):
    print(f"[e2e-report] error: {msg}", file=sys.stderr)
    sys.exit(1)


def newest(pattern):
    best, best_ts = None, -1
    for p in glob.glob(pattern):
        m = re.search(r"_(\d+)\.json$", os.path.basename(p))
        if not m:
            continue
        ts = int(m.group(1))
        if ts > best_ts:
            best, best_ts = p, ts
    return best, best_ts


def esc(x):
    return _html.escape(str(x))


def generate_md(data, phases):
    n_p = sum(1 for r in data["results"] if r["status"] == "pass")
    n_f = sum(1 for r in data["results"] if r["status"] == "fail")
    n_nr = sum(1 for r in data["results"] if r["status"] == "notrun")
    lines = [f"""# End-to-End test report — ProxKnox cluster (3 nodes)

- **Scenario**: {data.get('scenario', '')}
- **Lab nodes**: {', '.join(data.get('lab-nodes', []))}
- **Date**: {data.get('date', '')}
- **Verdict**: {"✅ PASS" if n_f == 0 else "❌ FAILURES (" + str(n_f) + ")"}{" — " + str(n_nr) + " test(s) not run" if n_nr else ""}

## Overview

| Metric | Value |
|---|---|
| Tests | {len(data['results'])} |
| Passed | {n_p} |
| Not run | {n_nr} |
| **Failures** | **{n_f}** |
"""]
    for phase, rows in phases.items():
        np_, nf = sum(1 for r in rows if r["status"] == "pass"), \
                  sum(1 for r in rows if r["status"] == "fail")
        lines.append(f"\n### {phase} — {np_}/{len(rows)} passed"
                     f"{' ✅' if nf == 0 else f' (❌ {nf} failure(s))'}\n")
        lines.append("\n| Test | Expected | Result | Finding |\n|---|---|---|---|\n")
        for r in rows:
            st = {"pass": "✅", "fail": "❌"}.get(r["status"], "⏭")
            lines.append(f"| {r['id']} — {r['title']} | {r['expect']} "
                         f"| {st} {r['status']} | {str(r.get('got',''))[:90]} |\n")
    return "".join(lines) + f"\nSource: `{os.path.basename(data.get('_path',''))}`\n"


def generate_html(data, phases, out_html):
    n = len(data["results"])
    n_p = sum(1 for r in data["results"] if r["status"] == "pass")
    n_f = sum(1 for r in data["results"] if r["status"] == "fail")
    n_nr = sum(1 for r in data["results"] if r["status"] == "notrun")

    cards = [
        f'<div class="card"><h3>E2E tests</h3><div class="big">{n_p}/{n}</div>'
        f'<div class="sub">tests passed{"" if not n_nr else f" — {n_nr} not run"}</div></div>',
        f'<div class="card"><h3>Verdict</h3><div class="big '
        f'{"pass" if n_f == 0 else "fail"}">{"PASS" if n_f == 0 else "FAIL"}'
        f'</div><div class="sub">{n_f} failure(s)</div></div>',
        '<div class="card"><h3>Lab</h3><div class="big">3 nodes</div>'
        '<div class="sub">PXE → cluster → Ceph → migrations</div></div>',
    ]
    # migration durations if present in the findings
    durs = [m for r in data["results"] for m in re.findall(r"MigDur=(\d+)s", str(r.get("got", "")))]
    downs = [m for r in data["results"] for m in re.findall(r"lost=(\d+)", str(r.get("got", "")))]
    if durs:
        cards.append('<div class="card"><h3>Migrations</h3>'
                     f'<div class="big">{" + ".join(durs)} s</div>'
                     f'<div class="sub">live-migration '
                     f'duration{"s" if len(durs) > 1 else ""}</div></div>')
    if downs:
        cards.append('<div class="card"><h3>Ping loss</h3>'
                     f'<div class="big">{" + ".join(downs)}</div>'
                     '<div class="sub">missing ping responses during '
                     'the migration (200 ms/interval)</div></div>')

    phase_html = ""
    for phase, rows in phases.items():
        np_, nf = sum(1 for r in rows if r["status"] == "pass"), \
                  sum(1 for r in rows if r["status"] == "fail")
        phase_html += (f'<details open><summary><b>{esc(phase)}</b> '
                       f'<span class="count">{np_}/{len(rows)} passed'
                       f'{"" if nf == 0 else f" — {nf} failure(s)"}</span></summary>'
                       f'<div class="body">')
        for r in rows:
            cls = {"pass": "b-pass", "fail": "b-fail"}.get(r["status"], "b-neutral")
            phase_html += (
                f'<details><summary><span class="badge {cls}">'
                f'{esc(r["status"])}</span>'
                f'<b>{esc(r["id"])}</b> — {esc(r["title"])}'
                f'<span class="count">{esc(r.get("expect", ""))}</span>'
                f'</summary><div class="body">'
                f'<p class="kv"><b>Expected</b>: {esc(r.get("expect", ""))}</p>'
                f'<p class="kv"><b>Got</b>: <code>{esc(r.get("got", ""))}</code></p>'
                f'</div></details>')
        phase_html += "</div></details>"

    raw = json.dumps(data, indent=1, ensure_ascii=False)
    src = os.path.basename(data.get("_path", ""))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>E2E tests — ProxKnox cluster (3 nodes)</title>
<style>{CSS}</style></head><body>
<h1>End-to-End tests — ProxKnox cluster (3 nodes)</h1>
<p class="meta"><strong>Scenario</strong>: {esc(data.get('scenario', ''))}<br>
<strong>Nodes</strong>: {esc(' · '.join(data.get('lab-nodes', [])))} ·
<strong>Date</strong>: {esc(data.get('date', ''))}</p>
<div class="cards">{''.join(cards)}</div>
<h2>Per-phase detail (click to expand)</h2>
{phase_html}
<h2>Validated end-to-end scenario</h2>
<details><summary>The complete journey</summary><div class="body">
<ol>
<li><b>Deployment</b>: 3 Debian 13 nodes installed via real PXE
(Docker container providing DHCP/TFTP/HTTP/NAT), one dedicated OSD disk
per node, converted to Proxmox VE 9.2.</li>
<li><b>Network</b>: static vmbr0 (192.168.200.11/12/13), hostnames
proxknox-node1/2/3 — a stable base for corosync/Ceph.</li>
<li><b>Cluster</b>: <code>pvecm create/add</code> — 3-vote quorum.</li>
<li><b>Ceph</b>: <code>pveceph init</code> + 1 OSD/node + <code>vmstorage</code>
pool (size 3/min_size 2) — HEALTH_OK, "secure" msgr2 (cephx required
everywhere, encrypted traffic).</li>
<li><b>Hardening</b>: harden-pve.yml (12 roles ON, with cluster/Ceph rules
in the PVE firewall) on all 3 nodes.</li>
<li><b>Mini VM</b>: 1 vCPU / 2 GiB, disk on Ceph RBD, a real Debian 13
cloud image (lab-segment DHCP), started then pinged.</li>
<li><b>Live migrations</b>: node1→node2 then node2→node3, continuous ping
(200 ms) — the VM stays reachable, downtime measured.</li>
</ol></div></details>
<h2>Source data (embedded JSON)</h2>
<details><summary>Expand raw JSON {esc(src)}</summary>
<div class="body"><div class="tab">{esc(raw)}</div></div></details>
<footer>Generated by <code>tools/e2e-report.py</code> — single page, no
dependencies. Source: <code>{esc(src)}</code> → {esc(out_html)}</footer>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="E2E test report (md + html)")
    ap.add_argument("--audit-dir", default="ansible/audit-summaries")
    ap.add_argument("--out-prefix", default="docs/runs/report-e2e-cluster")
    ap.add_argument("--suite", default=None,
                    help="path to the e2e JSON (default: newest in --audit-dir)")
    args = ap.parse_args()

    path = args.suite
    if not path:
        path, ts = newest(os.path.join(args.audit_dir, "e2e-cluster-test_*.json"))
        if not path:
            die("no e2e-cluster-test_*.json in " + args.audit_dir)
    data = json.load(open(path, encoding="utf-8"))
    data["_path"] = path

    phases = {}
    for r in data["results"]:
        phases.setdefault(r["phase"], []).append(r)

    m = re.search(r"_(\d+)\.json$", os.path.basename(path))
    date = (datetime.fromtimestamp(int(m.group(1))).date()
            if m else datetime.now().date())
    out_md = f"{args.out_prefix}_{date}.md"
    out_html = f"{args.out_prefix}_{date}.html"
    os.makedirs(os.path.dirname(out_md) or ".", exist_ok=True)

    open(out_md, "w", encoding="utf-8").write(generate_md(data, phases))
    open(out_html, "w", encoding="utf-8").write(generate_html(data, phases, out_html))
    n_f = sum(1 for r in data["results"] if r["status"] == "fail")
    n = len(data["results"])
    print(f"[e2e-report] tests: {n - n_f}/{n} passed"
          f"{' ✅' if n_f == 0 else f' — {n_f} FAILURE(S) ❌'}")
    print(f"[e2e-report] report: {out_md}")
    print(f"[e2e-report] report: {out_html}")


if __name__ == "__main__":
    main()

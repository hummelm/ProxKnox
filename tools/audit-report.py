#!/usr/bin/env python3
"""audit-report.py — ProxKnox consolidated audit report (CIS + PVE + Ceph)

Consolidates the JSON audit reports into a human-readable report, in two
formats:

  - Markdown: identical content, pipe tables
  - HTML    : SELF-CONTAINED page (no dependencies, no JS required),
              high-level stats + per-control drill-down (<details>/native).

Sources (all JSON):
  1. `syver` scans from the ansible-lockdown DEBIAN13-CIS-Audit role
     (`debian-DEBIAN13-CIS-*_pre/post_scan_<epoch>.json`) — CIS Debian 13
     L2 benchmark. The most recent pair serves as the current run; the
     oldest available "pre" scan serves as the "before remediation"
     baseline.
  2. Proxmox hardening audit (`pve-hardening-audit_<epoch>.json`, pve_audit
     role) — HomeSecExplorer PVE9 guide (relevant sections) + Ceph hardening
     (HSE § 3.3-3.5, E-NO, Red Hat Ceph 8). Optional: the report is
     generated without this source if it is missing (--pve-audit none).

Output: `<out-prefix>_<date>.md` and `.html`
(default: docs/runs/report-audit-cis-debian13-pve_<date>)

Usage:
  python3 tools/audit-report.py [--audit-dir ansible/audit-summaries] \
      [--out-prefix docs/runs/report-audit-cis-debian13-pve] \
      [--node pxe-client] [--pve pve-manager/9.2.28] [--chain "..."] \
      [--no-pve-audit]
"""

import argparse
import collections
import glob
import json
import html as _html
import os
import re
import sys
from datetime import datetime

# ----------------------------------------------------------------- options ---
NOTES = {
    "1.1.1.7": "squashfs module — blacklisted at runtime, manual review (lsmod) / reboot",
    "1.2.1.1": "deb822 sources (.sources) on this node; the scan targets a classic sources.list — cosmetic",
    "2.1.13": "EXPECTED PVE DEVIATION: rsync masked, not purged (proxmox-ve dependency) — deb13cis_rsync_mask",
    "2.1.23": "manually reviewed by design (listening services)",
    "2.3.1.1": "EXPECTED PVE DEVIATION: timesyncd masked, PVE uses chrony",
    "3.3.1.12": "EXPECTED PVE DEVIATION: rp_filter kept for routing/SDN (deb13cis_is_router)",
    "5.2.4": "lab deviation: sudo NOPASSWD adminlab (preseed) — tighten in production",
    "5.4.1.2": "manually reviewed by design (account review)",
    "1.3.1.3": "non-enforcing AppArmor profiles (PVE) — manual review",
    "1.1.1.11": "manually reviewed by design (alert validation)",
    "6.1.1.1.2": "manually reviewed by design (journald permissions)",
}
SECNAME = {
    "1": "Initial Setup (partitions, repositories, kernel, modules)",
    "2": "Services",
    "3": "Network configuration",
    "4": "Host firewall (accepted PVE deviation)",
    "5": "Access, authentication, sudo, sshd",
    "6": "Logging & auditing",
    "7": "System tools & integrity",
}
ECARTS = [
    ("Section 4 (host firewall ufw) disabled",
     "the HomeSecExplorer PVE9 guide § 1.1.1 recommends skipping benchmark "
     "CIS § 4 when the PVE firewall is enabled (harden-pve.yml § 1.2.4: "
     "enable=1, INPUT DROP, allow-list 8006/2222). deb13cis_section4: false + "
     "ufw purge are in group_vars/pve_nodes.yml / site.yml."),
    ("2.1.13 rsync masked (not purged)",
     "dependency of the proxmox-ve meta-package (pve-apt-hook blocks its "
     "removal) — deb13cis_rsync_mask: true."),
    ("IPv4 forwarding / rp_filter kept",
     "Proxmox acts as a router for its guests (SDN/NAT/PVE firewall) — "
     "deb13cis_is_router: true."),
]


def die(msg):
    print(f"[audit-report] error: {msg}", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ parsing ---
def newest(pattern):
    """Most recent scan file according to the epoch in its name (_scan_<epoch>.json)."""
    best, best_ts = None, -1
    for p in glob.glob(pattern):
        m = re.search(r"_(?:scan_)?(\d+)\.json$", os.path.basename(p))
        if not m:
            continue
        ts = int(m.group(1))
        if ts > best_ts:
            best, best_ts = p, ts
    return best, best_ts


def oldest(pattern):
    best, best_ts = None, None
    for p in glob.glob(pattern):
        m = re.search(r"_(?:scan_)?(\d+)\.json$", os.path.basename(p))
        if not m:
            continue
        ts = int(m.group(1))
        if best_ts is None or ts < best_ts:
            best, best_ts = p, ts
    return best, (best_ts or 0)


def load(path):
    data = json.load(open(path))
    rows, titles = [], {}
    for r in data["results"]:
        ids = r.get("meta", {}).get("CIS_ID") or ["-"]
        cid = ids[0] if isinstance(ids, list) else str(ids)
        t = r.get("title", "")
        if "|" in t and cid not in titles and t.startswith(str(cid)):
            titles[cid] = t.split("|", 1)[1].strip()
        rows.append({
            "id": cid, "title": t,
            "skipped": bool(r.get("skipped")),
            "successful": bool(r.get("successful", True)),
            "skip_reason": r.get("skipped") or "",
            "compact": r.get("summary-line-compact") or "",
        })
    for r in rows:
        r["control"] = titles.get(r["id"],
                                  r["title"].split("|")[-1].strip()
                                  if "|" in r["title"] else r["title"])
    return rows, data["summary"]["summary-line"]


def load_pve_audit(path):
    """Load the PVE audit (pve_audit role) → list of normalized controls."""
    data = json.load(open(path))
    rows = []
    for r in data.get("results", []):
        status = r.get("status") or ("skip" if r.get("skipped")
                                     else "pass" if r.get("successful") else "fail")
        rows.append({
            "id": r.get("id", "-"),
            "section": r.get("section", "-"),
            "title": r.get("title", ""),
            "guide": r.get("guide", ""),
            "status": status,
            "successful": bool(r.get("successful")),
            "skipped": bool(r.get("skipped")),
            "reason": r.get("reason", ""),
            "detail": r.get("detail", ""),
        })
    s = data.get("summary", {})
    return rows, s, data


def klass(r):
    return "skip" if r["skipped"] else ("pass" if r["successful"] else "fail")


def section(r):
    return r["id"].split(".")[0] if r["id"] else "?"


def secstats(rows):
    c, f = collections.Counter(), collections.Counter()
    for r in rows:
        c[section(r)] += 1
        f[section(r)] += (klass(r) == "fail")
    return c, f


def short(s, n=105):
    s = (s or "").replace("\n", " ")
    return (s[:n] + "…") if len(s) > n else s


# --------------------------------------------------------------- generation ---
def pve_stats(rows):
    n_pass = sum(1 for r in rows if r["status"] == "pass")
    n_fail = sum(1 for r in rows if r["status"] == "fail")
    n_skip = sum(1 for r in rows if r["status"] == "skip")
    total = len(rows)
    pct = 100 * (n_pass + n_skip) / total if total else 0.0
    return n_pass, n_fail, n_skip, total, pct


def pve_md(rows):
    lines, cur_sec = ["", "### Audited controls (HSE PVE9 guide + Ceph hardening)",
                      "", "| ID | Guide | Control | Status | Detail / reason |",
                      "|---|---|---|---|---|"], None
    for r in rows:
        st = {"pass": "✅ pass", "fail": "❌ fail",
              "skip": "⏭️ skip"}[r["status"]]
        extra = r["reason"] or r["detail"]
        lines.append(f"| {r['id']} | {r['guide']} | {r['title']} | {st} "
                     f"| {short(extra, 90)} |\n")
    return "".join(lines)


def generate_md(ctx):
    c_av, f_av, c_fin, f_fin = ctx["c_av"], ctx["f_av"], ctx["c_fin"], ctx["f_fin"]
    secs = ctx["secs"]
    lines = [f"""# Consolidated audit report — ProxKnox (CIS L2 + PVE/Ceph hardening)

- **Target**: {ctx['node']} — {ctx['pve']}
- **Benchmarks**: CIS Debian 13 v1.0.0 (server, **Level 2**) via syver ·
  HomeSecExplorer PVE9 v0.9.2 · Ceph hardening (HSE § 3.3-3.5, E-NO, RH Ceph 8)
- **Tooling**: site.yml (ansible-lockdown DEBIAN13-CIS) + harden-pve.yml
  (11 toggleable roles) + pve_audit role (on-node checks)
- **Scan date**: {ctx['date']}
- **Chain**: {ctx['chain']}

## Overview

| Scan | Controls | Failures | Skipped |
|---|---|---|---|
| CIS — before remediation | {ctx['n_av']} | {ctx['n_av_f']} | {ctx['n_av_s']} |
| CIS — final (after remediation) | {ctx['n_fin']} | **{ctx['n_fin_f']}** | {ctx['n_fin_s']} |

CIS failures: **{ctx['n_av_f']} → {ctx['n_fin_f']}** ({ctx['delta']}).
Final CIS compliance: **{ctx["compliance"]}** (passed controls / total).
"""]
    if ctx.get("pve_rows"):
        np_, nf, ns, nt, pct = ctx["pve_counts"]
        lines.append(f"""
## Proxmox VE hardening audit (HomeSecExplorer guide + Ceph)

| PVE audit | Value |
|---|---|
| Controls | {nt} |
| Compliant | {np_} |
| **Deviations** | **{nf}** |
| Not applicable (documented skips) | {ns} |
| Compliance (pass + N/A) | **{pct:.0f} %** |
""")
        lines.append(pve_md(ctx["pve_rows"]))

    lines.append(f"""
## CIS breakdown by section

| Section | Before (failures/controls) | Final (failures/controls) |
|---|---|---|
""")
    for s in secs:
        lines.append(f"| {s} — {SECNAME.get(s, '')} | "
                     f"{f_av.get(s, 0)}/{c_av.get(s, 0)} | "
                     f"{f_fin.get(s, 0)}/{c_fin.get(s, 0)} |\n")

    lines.append(f"""
## Residual CIS failures ({ctx['n_fin_f']})

| CIS ID | Control | Finding (scan excerpt) | Interpretation |
|---|---|---|---|
""")
    for r in ctx["fails_fin"]:
        lines.append(f"| {r['id']} | {r['control']} | "
                     f"{short(r['compact'])} | {NOTES.get(r['id'], '')} |\n")

    lines.append(f"""
## Skipped CIS controls ({len(ctx['skips_fin'])})

| CIS ID | Control | Reason |
|---|---|---|
""")
    for r in ctx["skips_fin"]:
        lines.append(f"| {r['id']} | {r['control']} | "
                     f"{short(str(r['skip_reason']), 60)} |\n")

    lines.append("\n## Accepted PVE deviations (documented)\n\n")
    for title, detail in ECARTS:
        lines.append(f"- **{title}**: {detail}\n")

    lines.append(f"""
## Source files (JSON)

- `{ctx['path_avant']}` — before CIS remediation ({ctx['line_avant']})
- `{ctx['path_fin']}` — CIS final ({ctx['line_fin']})
""")
    if ctx.get("pve_src"):
        lines.append(f"- `{ctx['pve_src']}` — PVE audit "
                     f"({ctx['pve_line']})\n")
    lines.append(f"\nReports: `{ctx['out_md']}` / `{ctx['out_html']}`.\n")
    return "".join(lines)


# --------------------------------------------------------------------- HTML ---
CSS = """
:root{color-scheme:light dark}
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;margin:2rem auto;
max-width:1150px;color:#1a1a1a;line-height:1.45;background:#fafafa}
@media (prefers-color-scheme:dark){body{background:#141414;color:#ddd}
th{background:#242424!important}.card{background:#1e1e1e!important}
details summary:hover{background:#242424}}
h1{border-bottom:3px solid #c62828;padding-bottom:.3rem}
h2{border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}
table{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.88rem}
th,td{border:1px solid #ccc;padding:.45rem .6rem;text-align:left;vertical-align:top}
th{background:#f0f0f0} code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px}
.meta{color:#555;font-size:.9rem}
.cards{display:flex;flex-wrap:wrap;gap:1rem;margin:1.5rem 0}
.card{flex:1 1 210px;background:#fff;border:1px solid #ddd;border-radius:8px;
padding:.9rem 1.1rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h3{margin:0 0 .4rem;font-size:.85rem;text-transform:uppercase;
letter-spacing:.04em;color:#666}
.card .big{font-size:1.7rem;font-weight:700}
.card .sub{font-size:.8rem;color:#777}
.pass{color:#2e7d32;font-weight:600}.fail{color:#c62828;font-weight:600}
.skip{color:#b58a00;font-weight:600}
.badge{display:inline-block;border-radius:4px;padding:.1rem .5rem;
font-weight:600;color:#fff;font-size:.85rem}
.b-pass{background:#2e7d32}.b-fail{background:#c62828}.b-skip{background:#b58a00}
details{border:1px solid #ddd;border-radius:6px;margin:.45rem 0;background:#fff}
details summary{cursor:pointer;padding:.55rem .8rem;font-weight:500;
list-style:none;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸";font-size:.9rem;color:#888;transition:transform .1s}
details[open] summary::before{transform:rotate(90deg)}
details summary:hover{background:#f2f2f2}
details .body{padding:.6rem .9rem;border-top:1px solid #eee}
details .body p{margin:.4rem 0}
.kv{font-size:.85rem;color:#555;margin:.15rem 0}
.tab{white-space:pre;overflow:auto;font-size:.78rem;background:#f5f5f5;
padding:.6rem;border-radius:4px;max-height:22rem;overflow:auto}
.count{font-variant-numeric:tabular-nums;color:#888;font-size:.8rem}
footer{margin-top:2.5rem;padding-top:.8rem;border-top:1px solid #ddd;
color:#888;font-size:.8rem}
"""


def generate_html(ctx):
    def esc(x):
        return _html.escape(str(x))

    def card(h, big, sub, extra_cls=""):
        return (f'<div class="card"><h3>{h}</h3>'
                f'<div class="big {extra_cls}">{big}</div>'
                f'<div class="sub">{sub}</div></div>')

    # ---- high-level cards
    cards = [
        card("CIS Debian 13 L2", esc(ctx["n_fin_f"]),
             f"residual failures ({ctx['n_fin']} controls)"),
        card("CIS remediation", f"{ctx['n_av_f']} → {ctx['n_fin_f']}",
             esc(ctx["delta"]),
             "fail" if ctx["n_fin_f"] else "pass"),
        card("CIS compliance", esc(ctx["compliance"]),
             "passed controls / total", "pass"),
    ]
    if ctx.get("pve_rows"):
        np_, nf, ns, nt, pct = ctx["pve_counts"]
        cards.append(card("PVE Hardening (HSE)", esc(str(nf)),
                          f"deviations across {nt} controls ({np_} compliant, {ns} N/A)",
                          "fail" if nf else "pass"))
        cards.append(card("PVE compliance", f"{pct:.0f} %",
                          "pass + not applicable", "pass"))

    # ---- CIS: breakdown by section, collapsible with per-section failures
    sec_rows = []
    for s in ctx["secs"]:
        fails_s = [r for r in ctx["fails_fin"] if section(r) == s]
        if fails_s:
            inner = ("<table><tr><th>CIS</th><th>Control</th>"
                     "<th>Finding</th><th>Interpretation</th></tr>" +
                     "".join(
                         f"<tr><td>{esc(r['id'])}</td><td>{esc(r['control'])}</td>"
                         f"<td><code>{esc(short(r['compact'], 110))}</code></td>"
                         f"<td>{esc(NOTES.get(r['id'], ''))}</td></tr>"
                         for r in fails_s) + "</table>")
        else:
            inner = "<p>No residual failures in this section ✅</p>"
        sec_rows.append(
            f"<details><summary>"
            f"<b>Section {esc(s)} — {esc(SECNAME.get(s, ''))}</b> "
            f"<span class='count'>final: {f_fin_count(ctx, s)} failure(s) "
            f"/ {c_fin_count(ctx, s)} controls · "
            f"before: {f_av_count(ctx, s)}/{c_av_count(ctx, s)}</span>"
            f"</summary><div class='body'>{inner}</div></details>")

    # ---- PVE controls: collapsible, one per guide control
    pve_html = ""
    if ctx.get("pve_rows"):
        order = ["pass", "fail", "skip"]
        icon = {"pass": "b-pass", "fail": "b-fail", "skip": "b-skip"}
        labels = {"pass": "✅ pass", "fail": "❌ fail", "skip": "⏭️ N/A"}
        pve_html = "<h2>Proxmox VE hardening audit (per-control drill-down)</h2>\n"
        by_secs = collections.OrderedDict()
        for r in ctx["pve_rows"]:
            by_secs.setdefault(r["section"], []).append(r)
        for sec, rows in by_secs.items():
            n_ok = sum(1 for r in rows if r["status"] == "pass")
            n_ko = sum(1 for r in rows if r["status"] == "fail")
            pve_html += (f"<details open><summary><b>{esc(sec)}</b> "
                         f"<span class='count'>{n_ok} compliant, "
                         f"{n_ko} deviations, {len(rows) - n_ok - n_ko} N/A</span>"
                         f"</summary><div class='body'>")
            for r in sorted(rows, key=lambda x: order.index(x["status"])):
                extra = r["reason"] or r["detail"]
                pve_html += (
                    f"<details><summary>"
                    f"<span class='badge {icon[r['status']]}'>{labels[r['status']]}"
                    f"</span> <b>{esc(r['id'])}</b> — {esc(r['title'])} "
                    f"<span class='count'>{esc(r['guide'])}</span></summary>"
                    f"<div class='body'>"
                    f"<p class='kv'><b>Check</b>: {esc(r['detail'])}</p>")
                if r["reason"]:
                    pve_html += f"<p class='kv'><b>Reason</b>: {esc(r['reason'])}</p>"
                pve_html += "</div></details>"
            pve_html += "</div></details>"

    # ---- global-level CIS failures/skips
    fails_tbl = ("<table><tr><th>CIS ID</th><th>Control</th><th>Finding</th>"
                 "<th>Interpretation</th></tr>" +
                 "".join(
                     f"<tr><td>{esc(r['id'])}</td><td>{esc(r['control'])}</td>"
                     f"<td><code>{esc(short(r['compact'], 120))}</code></td>"
                     f"<td>{esc(NOTES.get(r['id'], ''))}</td></tr>"
                     for r in ctx["fails_fin"]) + "</table>")
    skips_tbl = ("<table><tr><th>CIS ID</th><th>Control</th><th>Reason</th></tr>" +
                 "".join(
                     f"<tr><td>{esc(r['id'])}</td><td>{esc(r['control'])}</td>"
                     f"<td>{esc(short(str(r['skip_reason']), 60))}</td></tr>"
                     for r in ctx["skips_fin"]) + "</table>")

    ecarts_html = "".join(f"<li><b>{esc(t)}</b>: {esc(d)}</li>" for t, d in ECARTS)

    # ---- embedded source JSON (the page "is based on the json")
    raw_blocks = [f"<h4>Final CIS scan — {esc(ctx['path_fin'])}</h4>"
                  f"<div class='tab'>{esc(ctx['raw_fin'])}</div>",
                  f"<h4>CIS scan before remediation — {esc(ctx['path_avant'])}</h4>"
                  f"<div class='tab'>{esc(ctx['raw_avant'])}</div>"]
    if ctx.get("pve_raw"):
        raw_blocks.append(f"<h4>PVE audit — {esc(ctx['pve_src'])}</h4>"
                          f"<div class='tab'>{esc(ctx['pve_raw'])}</div>")

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Consolidated audit report — ProxKnox (CIS + PVE + Ceph)</title>
<style>{CSS}</style></head><body>
<h1>Consolidated audit report — ProxKnox</h1>
<p class="meta"><strong>Target</strong>: {esc(ctx['node'])} — <code>{esc(ctx['pve'])}</code> ·
<strong>Benchmarks</strong>: CIS Debian 13 v1.0.0 L2 (syver) · HomeSecExplorer PVE9 v0.9.2 ·
Ceph hardening (HSE § 3.3-3.5, E-NO, RH Ceph 8) ·
<strong>Scan date</strong>: {esc(ctx['date'])} · <strong>Chain</strong>: {esc(ctx['chain'])}</p>

<h2>High-level overview</h2>
<div class="cards">{''.join(cards)}</div>

<p>CIS failures: <b>{ctx['n_av_f']} → {ctx['n_fin_f']}</b> ({esc(ctx['delta'])}) —
final compliance <b class="pass">{ctx["compliance"]}</b>.
Click / expand for the control-by-control breakdown.</p>

<h2>Proxmox VE hardening — by guide section</h2>
{''.join(sec_rows)}

{pve_html}

<h2>Residual CIS failures ({ctx['n_fin_f']})</h2>
{fails_tbl}

<h2>Skipped CIS controls ({len(ctx['skips_fin'])})</h2>
<details><summary>Show / hide ({len(ctx['skips_fin'])} controls)</summary>
<div class="body">{skips_tbl}</div></details>

<h2>Accepted PVE deviations</h2>
<ul>{ecarts_html}</ul>

<h2>Source data (embedded JSON)</h2>
<details><summary>Expand raw JSON (syver scans + PVE audit)</summary>
<div class="body">{''.join(raw_blocks)}</div></details>

<footer>Self-contained report generated by <code>tools/audit-report.py</code> — no
external dependencies; collapsible detail via native &lt;details&gt;. Sources:
<code>{esc(ctx['path_avant'])}</code>, <code>{esc(ctx['path_fin'])}</code>
{(' , <code>' + esc(ctx['pve_src']) + '</code>') if ctx.get('pve_src') else ''}.</footer>
</body></html>"""


# small helpers for the per-section breakdown (HTML)
def f_fin_count(ctx, s):
    return ctx["f_fin"].get(s, 0)


def c_fin_count(ctx, s):
    return ctx["c_fin"].get(s, 0)


def f_av_count(ctx, s):
    return ctx["f_av"].get(s, 0)


def c_av_count(ctx, s):
    return ctx["c_av"].get(s, 0)


# ---------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser(
        description="Consolidated audit report (CIS + PVE + Ceph): md + html")
    ap.add_argument("--audit-dir", default="ansible/audit-summaries")
    ap.add_argument("--out-prefix", default=None,
                    help="output prefix (default: docs/runs/"
                         "report-audit-cis-debian13-pve)")
    ap.add_argument("--node", default="pxe-client")
    ap.add_argument("--pve", default="pve-manager/9.x")
    ap.add_argument("--chain", default="deployment → PVE hardening (+Ceph) → "
                                       "CIS L2 remediation (site.yml) → audits")
    ap.add_argument("--no-pve-audit", action="store_true",
                    help="ignore the pve-hardening-audit_*.json source")
    args = ap.parse_args()

    adir = args.audit_dir
    if not os.path.isdir(adir):
        die(f"audit directory not found: {adir}")

    post_path, post_ts = newest(f"{adir}/*_post_scan_*.json")
    if not post_path:
        die("no *_post_scan_*.json scan in " + adir)
    pre_path, _ = newest(f"{adir}/*_pre_scan_*.json")
    if not pre_path:
        die("no *_pre_scan_*.json scan in " + adir)

    rows_fin, line_fin = load(post_path)

    # "before" baseline: the oldest available PRE scan (it coincides with
    # the current pre scan when only a single pair exists)
    pre_for_delta, _ = oldest(f"{adir}/*_pre_scan_*.json")
    rows_avant, line_avant = load(pre_for_delta)

    fails_fin = [r for r in rows_fin if klass(r) == "fail"]
    skips_fin = [r for r in rows_fin if klass(r) == "skip"]
    n_av = len(rows_avant)
    n_av_f = len([r for r in rows_avant if klass(r) == "fail"])
    n_av_s = len([r for r in rows_avant if klass(r) == "skip"])
    n_fin, n_fin_f = len(rows_fin), len(fails_fin)
    n_fin_s = len(skips_fin)
    c_av, f_av = secstats(rows_avant)
    c_fin, f_fin = secstats(rows_fin)
    secs = sorted(set(list(c_av) + list(c_fin)))
    delta = f"{100 * (n_fin_f - n_av_f) / n_av_f:+.1f}%" if n_av_f else "n/a"
    conforms = n_fin - n_fin_f - n_fin_s
    date = datetime.fromtimestamp(post_ts).strftime("%Y-%m-%d %H:%M")

    # ---- optional source: PVE audit (pve_audit role)
    pve_rows, pve_counts, pve_src, pve_line, pve_raw = [], None, "", "", ""
    pve_path, pve_ts = newest(f"{adir}/pve-hardening-audit_*.json")
    if pve_path and not args.no_pve_audit:
        pve_rows, pve_sum, pve_data = load_pve_audit(pve_path)
        pve_counts = pve_stats(pve_rows)
        pve_src = os.path.basename(pve_path)
        pve_line = pve_sum.get("summary-line", "")
        pve_raw = json.dumps(pve_data, indent=1, ensure_ascii=False)

    prefix = args.out_prefix or "docs/runs/report-audit-cis-debian13-pve"
    out_md = f"{prefix}_{datetime.fromtimestamp(post_ts).date()}.md"
    out_html = out_md[:-3] + ".html"

    ctx = dict(rows_fin=rows_fin, rows_avant=rows_avant, fails_fin=fails_fin,
               skips_fin=skips_fin, n_av=n_av, n_av_f=n_av_f, n_av_s=n_av_s,
               n_fin=n_fin, n_fin_f=n_fin_f, n_fin_s=n_fin_s,
               c_av=c_av, f_av=f_av, c_fin=c_fin, f_fin=f_fin, secs=secs,
               delta=delta, conforms=conforms,
               compliance=f"{100 * conforms / n_fin:.1f}%",
               date=date, node=args.node,
               pve=args.pve, chain=args.chain,
               path_avant=os.path.basename(pre_for_delta),
               path_fin=os.path.basename(post_path),
               line_avant=line_avant, line_fin=line_fin,
               pve_rows=pve_rows, pve_counts=pve_counts, pve_src=pve_src,
               pve_line=pve_line, pve_raw=pve_raw,
               raw_avant=open(pre_for_delta, encoding="utf-8").read(),
               raw_fin=open(post_path, encoding="utf-8").read(),
               out_md=out_md, out_html=out_html)

    open(out_md, "w", encoding="utf-8").write(generate_md(ctx))
    open(out_html, "w", encoding="utf-8").write(generate_html(ctx))
    print(f"[audit-report] CIS: failures {n_av_f} -> {n_fin_f} ({delta}) | "
          f"compliance {ctx["compliance"]}")
    if pve_counts:
        np_, nf, ns, nt, pct = pve_counts
        print(f"[audit-report] PVE (HSE + Ceph): {np_} compliant, {nf} deviations, "
              f"{ns} N/A ({pct:.0f} %)")
    print(f"[audit-report] report: {out_md}")
    print(f"[audit-report] report: {out_html}")


if __name__ == "__main__":
    main()

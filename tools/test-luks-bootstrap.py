#!/usr/bin/env python3
"""ProxKnox sandbox test: emulates `in-target <LUKS_BOOTSTRAP>` on a Debian
trixie root with three possible /etc/crypttab states at late_command time."""
import importlib.util
import os
import shutil
import subprocess
import sys

spec = importlib.util.spec_from_file_location(
    "pxe", os.path.join(os.path.dirname(__file__), "..", "deploy",
                        "pxe_deploy_container.py"))
pxe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pxe)

UUID = "ac7f9cfc-8fe1-4edb-a927-46d2b23a2b41"
fr = "/tmp/fakeroot"
shutil.rmtree(fr, ignore_errors=True)
os.makedirs(f"{fr}/etc/initramfs-tools/hooks", exist_ok=True)
os.makedirs(f"{fr}/root", exist_ok=True)
os.makedirs(f"{fr}/fakebin", exist_ok=True)
os.makedirs(f"{fr}/dev", exist_ok=True)

with open(f"{fr}/root/.luks-bootkey", "w") as f:
    f.write("ChangeMeLuks!\n")
os.chmod(f"{fr}/root/.luks-bootkey", 0o600)
with open(f"{fr}/fakebin/lsblk", "w") as f:
    f.write("#!/bin/sh\necho /dev/vda5 crypto_LUKS\n")
with open(f"{fr}/fakebin/blkid", "w") as f:
    f.write("#!/bin/sh\necho " + UUID + "\n")
with open(f"{fr}/fakebin/update-initramfs", "w") as f:
    f.write('#!/bin/sh\necho "[fake] update-initramfs $*"\n')
for n in ("lsblk", "blkid", "update-initramfs"):
    os.chmod(f"{fr}/fakebin/{n}", 0o755)

# make the chroot usable: bind the real system dirs (plus /proc, /dev)
# into the fake root; /etc and /root stay the fake ones (script targets).
# /etc/alternatives must be bound too: /usr/bin/awk is a symlink through it.
for d in ("usr", "bin", "sbin", "lib", "lib64", "proc", "dev"):
    if os.path.isdir("/" + d):
        os.makedirs(f"{fr}/{d}", exist_ok=True)
        subprocess.run(f"mount --rbind /{d} {fr}/{d}", shell=True, check=False)
os.makedirs(f"{fr}/etc/alternatives", exist_ok=True)
subprocess.run(f"mount --rbind /etc/alternatives {fr}/etc/alternatives",
              shell=True, check=False)

print("== LUKS_BOOTSTRAP script state-order test (chroot emulating in-target)")

ok = True
for state in ("di-line", "empty", "missing"):
    ct = f"{fr}/etc/crypttab"
    hook = f"{fr}/etc/initramfs-tools/hooks/proxknox-bootkey-hook"
    if os.path.exists(hook):
        os.remove(hook)
    if state == "di-line":
        with open(ct, "w") as f:
            f.write("vda5_crypt UUID=%s none luks,discard,x-initrd.attach\n" % UUID)
    elif state == "empty":
        open(ct, "w").close()
    elif state == "missing" and os.path.exists(ct):
        os.remove(ct)

    r = subprocess.run(
        ["chroot", fr, "/bin/sh", "-c",
         "PATH=/fakebin:/usr/sbin:/usr/bin:/sbin:/bin; exec /bin/sh -"],
        input=pxe.LUKS_BOOTSTRAP, text=True, capture_output=True, timeout=120)

    content = open(ct).read().strip() if os.path.exists(ct) else "<missing>"
    fields = content.split()
    good = (r.returncode == 0
            and len(fields) >= 4
            and f"UUID={UUID}" == fields[1]
            and "/root/.luks-bootkey" == fields[2]
            and "keyscript=/bin/cat" in fields[3]
            and os.path.exists(hook))
    ok = ok and good
    print(f"--- {state}: rc={r.returncode} -> {'PASS' if good else 'FAIL'}")
    print("    crypttab:", content if content else "<empty>")
    out = [x for x in r.stdout.strip().splitlines() if x]
    if out:
        print("    out:", out[-2:])
    if r.stderr.strip():
        print("    err:", r.stderr.strip()[:150])

# reverse transform (the enroll cleanup) on the bootstrapped line
rev = subprocess.run(
    ["awk", '$3=="/root/.luks-bootkey"{$3="none"; sub(/,keyscript=\\/bin\\/cat/,"",$4)} {print}'],
    input="vda5_crypt UUID=%s /root/.luks-bootkey luks,discard,x-initrd.attach,keyscript=/bin/cat\n" % UUID,
    text=True, capture_output=True)
good = rev.stdout.strip() == ("vda5_crypt UUID=%s none luks,discard,x-initrd.attach" % UUID)
ok = ok and good
print("reverse (enroll) transform:", "PASS" if good else "FAIL: " + rev.stdout)

print("OVERALL:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

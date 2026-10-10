#!/usr/bin/env python3
"""Static checks for a tch-gui-unhide tree and its generated installers.

Usage: ci/check.py TREE [--base BASE_TREE] [--installers]

  Lua      every *.lua and transformer *.map parses with `luac -p` (Lua 5.1)
  LP       every *.lp page parses with `luac -p` (as Lua when `--pretranslated`, else translated as the
           stock web/lp.lua does)
  shell    every file with an sh/ash shebang passes `sh -n` (and `busybox ash -n` if present)
  shellcheck (with --base) no new shellcheck *error* that BASE_TREE does not already have (BusyBox
           dialect); new warnings are listed but don't fail the run
  extras   (with --extras) extras/tch-gui-unhide-xtra.* equal what `extras/src/make -all` generates
  payloads (with --installers) every embedded base64 payload decodes, decompresses and lists,
           and every *.bz2 inside it decompresses (catches CRLF-damaged binaries)

Exit status 0 when every check passes, 1 otherwise. Tools: luac5.1 (or LUAC=...), sh, shellcheck,
base64, bzip2, tar; busybox is optional.
"""
import argparse
import base64
import bz2
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

LUAC = os.environ.get("LUAC") or shutil.which("luac5.1") or shutil.which("luac") or "luac"
SKIP_DIRS = {".git", "node_modules"}
SH_SHEBANG = re.compile(rb"^#!\s*(/usr)?/bin/(env\s+)?(ba)?sh\b|^#!\s*/bin/ash\b|^#!\s*/bin/busybox\s+sh\b")


def walk(tree):
    for root, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = os.path.join(root, f)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p


def luac_ok(src, name):
    r = subprocess.run([LUAC, "-p", "-"], input=src, capture_output=True)
    return r.returncode == 0, r.stderr.decode(errors="replace").strip().replace("stdin:", name + ":")


def lp_to_lua(text):
    """Translate a Lua page as the stock loader (usr/lib/lua/web/lp.lua) does: a page starting with
    `--pretranslated` is already Lua; otherwise text, <%= expr %> and <% code %> become Lua."""
    if text.startswith(b"--pretranslated"):
        return text
    text = re.sub(rb"<\?lua(=?)", lambda m: b"<%" + m.group(1), text).replace(b"?>", b"%>") if b"<?lua" in text else text
    out, pos = [], 0
    for m in re.finditer(rb"<%(=?)(.*?)%>", text, re.S):
        chunk = text[pos:m.start()]
        if chunk:
            level = 0
            while b"]" + b"=" * level + b"]" in chunk or chunk.endswith(b"]" + b"=" * level):
                level += 1
            eq = b"=" * level
            # keep line numbers: the long string holds the newlines itself
            out.append(b"ngx.print([" + eq + b"[" + chunk + b"]" + eq + b"])")
        if m.group(1):
            out.append(b"ngx.print(" + m.group(2) + b")")
        else:
            out.append(m.group(2) + b"\n")
        pos = m.end()
    tail = text[pos:]
    if tail:
        level = 0
        while b"]" + b"=" * level + b"]" in tail or tail.endswith(b"]" + b"=" * level):
            level += 1
        eq = b"=" * level
        out.append(b"ngx.print([" + eq + b"[" + tail + b"]" + eq + b"])")
    return b" ".join(out)


def rel(p, tree):
    return os.path.relpath(p, tree)


def check_lua(tree, fails):
    n = 0
    for p in walk(tree):
        if p.endswith((".lua", ".map")):
            n += 1
            ok, err = luac_ok(open(p, "rb").read(), rel(p, tree))
            if not ok:
                fails.append(("lua", err))
        elif p.endswith(".lp"):
            n += 1
            ok, err = luac_ok(lp_to_lua(open(p, "rb").read()), rel(p, tree))
            if not ok:
                fails.append(("lp", err))
    return n


def shell_files(tree):
    for p in walk(tree):
        with open(p, "rb") as f:
            head = f.read(64)
        if SH_SHEBANG.match(head):
            yield p


def check_shell(tree, fails):
    bb = shutil.which("busybox")
    n = 0
    for p in shell_files(tree):
        n += 1
        r = subprocess.run(["sh", "-n", p], capture_output=True)
        if r.returncode:
            fails.append(("sh -n", rel(p, tree) + ": " + r.stderr.decode(errors="replace").strip()))
        if bb:
            r = subprocess.run([bb, "ash", "-n", p], capture_output=True)
            if r.returncode:
                fails.append(("ash -n", rel(p, tree) + ": " + r.stderr.decode(errors="replace").strip()))
    return n


def shellcheck_findings(tree):
    """Findings keyed by (file, code, source line text), so moved lines don't count as new."""
    files = [p for p in shell_files(tree) if not os.path.basename(p).startswith("tch-gui-unhide")]
    found = set()
    for i in range(0, len(files), 50):
        # SC3036: ShellCheck's busybox dialect still rejects `echo -e`, which BusyBox ash's echo supports.
        r = subprocess.run(["shellcheck", "-s", "busybox", "-S", "warning", "-e", "SC3036", "-f", "json"]
                           + files[i:i + 50],
                           capture_output=True)
        for c in json.loads(r.stdout or b"[]"):
            try:
                line = open(c["file"], "rb").read().splitlines()[c["line"] - 1].strip().decode(errors="replace")
            except (IndexError, OSError):
                line = ""
            found.add((rel(c["file"], tree), c["level"], c["code"], line, c["message"]))
    return found


def check_payloads(tree, fails):
    n = 0
    for name in sorted(os.listdir(tree)):
        p = os.path.join(tree, name)
        if not (name.startswith("tch-gui-unhide-") and os.path.isfile(p)):
            continue
        for lineno, line in enumerate(open(p, "rb"), 1):
            m = re.match(rb"^\s*echo '([A-Za-z0-9+/=]{200,})' \| base64 -d \| (\w+)", line)
            if not m:
                continue
            n += 1
            where = "%s:%d" % (name, lineno)
            try:
                raw = base64.b64decode(m.group(1), validate=True)
                data = bz2.decompress(raw) if m.group(2) == b"bzcat" else raw
                with tarfile.open(fileobj=io.BytesIO(data)) as t:
                    for member in t.getmembers():
                        if member.isfile() and member.name.endswith(".bz2"):
                            try:
                                bz2.decompress(t.extractfile(member).read())
                            except OSError as e:
                                raise OSError("%s: %s" % (member.name, e)) from None
            except Exception as e:  # noqa: BLE001 - report any decode failure
                fails.append(("payload", "%s: %s: %s" % (where, type(e).__name__, e)))
    return n


def check_extras(tree, fails):
    """Regenerate every extra in a scratch copy and compare it with the committed file."""
    if not os.path.isfile(os.path.join(tree, "extras", "src", "make")):
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "extras")
        shutil.copytree(os.path.join(tree, "extras"), copy, symlinks=True)
        r = subprocess.run(["sh", os.path.join(copy, "src", "make"), "-all"], capture_output=True)
        if r.returncode:
            fails.append(("extras", "make -all failed: " + r.stderr.decode(errors="replace").strip()))
            return 0
        names = sorted(f for f in os.listdir(copy) if f.startswith("tch-gui-unhide-xtra."))
        for f in names:
            committed = os.path.join(tree, "extras", f)
            if not os.path.isfile(committed):
                fails.append(("extras", "extras/%s is generated but not committed" % f))
            elif open(committed, "rb").read() != open(os.path.join(copy, f), "rb").read():
                fails.append(("extras", "extras/%s differs from `extras/src/make -all` output" % f))
        return len(names)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree")
    ap.add_argument("--base")
    ap.add_argument("--installers", action="store_true")
    ap.add_argument("--extras", action="store_true")
    a = ap.parse_args()
    fails = []
    print("lua/lp files parsed: %d" % check_lua(a.tree, fails))
    print("shell files checked: %d" % check_shell(a.tree, fails))
    if a.base:
        new = shellcheck_findings(a.tree) - shellcheck_findings(a.base)
        for f, level, code, line, msg in sorted(new):
            text = "%s: SC%s %s | %s" % (f, code, msg, line)
            if level == "error":
                fails.append(("shellcheck", text))
            else:
                print("warning [shellcheck] " + text)
        print("new shellcheck findings: %d (%d errors)" % (len(new), sum(1 for n in new if n[1] == "error")))
    if a.extras:
        print("extras regenerated and compared: %d" % check_extras(a.tree, fails))
    if a.installers:
        print("installer payloads verified: %d" % check_payloads(a.tree, fails))
    for kind, msg in fails:
        print("FAIL [%s] %s" % (kind, msg))
    print("RESULT: %s" % ("FAIL (%d)" % len(fails) if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""S347 sentinel. Flags git push when HEAD is bard-eod/oracle-eod commit."""
import json, os, re, subprocess, sys
from datetime import datetime
R = os.getcwd()
P = re.compile(r"^(bard-eod|oracle-eod):")
LOG = R + "/.claude/hooks/persona-cross-push-log.jsonl"
TIER = os.environ.get("PERSONA_CROSS_PUSH_TIER", "judgment").lower()
def _in():
    try: return json.loads(sys.stdin.read() or "{}")
    except: return {}
def _head():
    try:
        return subprocess.check_output(["git","-C",R,"log","-1","--pretty=%s"],text=True,timeout=5).strip()
    except: return ""
def _unp():
    try:
        o = subprocess.check_output(["git","-C",R,"log","--pretty=%h %s","@{u}..HEAD"],text=True,timeout=5)
    except: return []
    out = []
    for line in o.strip().split("\n"):
        if not line.strip(): continue
        p = line.split(maxsplit=1)
        if len(p) > 1 and P.match(p[1]):
            out.append(line)
    return out
def _log(d):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f: f.write(json.dumps(d) + "\n")
    except: pass
def main():
    i = _in()
    if i.get("tool_name") != "Bash": sys.exit(0)
    c = i.get("tool_input", {}).get("command", "")
    if not re.search(r"git\s+push", c): sys.exit(0)
    h = _head()
    u = _unp()
    if not P.match(h) and not u: sys.exit(0)
    r = os.environ.get("PERSONA_EOD_PUSH_REASON", "").strip()
    d = {"ts": datetime.now().isoformat(), "head": h, "unp": u, "cmd": c, "tier": TIER}
    if r:
        d["action"] = "bypass"; d["reason"] = r; _log(d); sys.exit(0)
    d["action"] = "blocked" if TIER == "hard" else "warned"
    _log(d)
    m = f"[PERSONA-CROSS-PUSH] HEAD: {h} — persona-eod, see feedback memo"
    print(m, file=sys.stderr)
    sys.exit(2 if TIER == "hard" else 0)

if __name__ == "__main__":
    main()

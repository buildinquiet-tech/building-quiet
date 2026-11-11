#!/usr/bin/env python3
"""rules_audit.py: what does Claude Code load before you type a word?

Lists every instruction file a Claude Code session loads at launch for a project,
with its size, and flags the four things that make rules stop holding:

  LONG       a file over the docs' 200-line target
  CUT        MEMORY.md past its load limit (200 lines or 25 KB): the rest is never read
  DEAD       an @import that points at a file that is not there
  DUPLICATE  the same rule line in two or more loaded files (they drift apart)

and, for unattended runs, reads the permission settings (~/.claude/settings.json,
.claude/settings.json, .claude/settings.local.json) and flags:

  BROADALLOW an allow rule that lets every shell command run (Bash, Bash(*)), or a
             bypassPermissions default mode: the deny list is the only brake left
  NODENY     an allow list with no deny rules at all
  NOENVDENY  no deny rule that keeps Claude from reading .env or secrets files
  BADJSON    a settings file that does not parse, so none of its rules apply

Load rules follow https://code.claude.com/docs/en/memory (read 2026-11-11):
CLAUDE.md and CLAUDE.local.md from the project folder and every folder above it,
~/.claude/CLAUDE.md, every .claude/rules/*.md without a `paths:` field, and every
@import up to four hops, all at launch. Rules with `paths:` and CLAUDE.md files in
subfolders load only when Claude reads a matching file; they are listed, not counted.

Standard library only. Reads files, writes nothing.

  python3 rules_audit.py                 # audit the current folder
  python3 rules_audit.py path/to/repo    # audit another project
  python3 rules_audit.py --json          # machine output
  python3 rules_audit.py --strict        # exit 1 when anything is flagged
  python3 rules_audit.py --selfcheck     # run the built-in tests
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

LINE_TARGET = 200          # docs: "target under 200 lines per CLAUDE.md file"
MEMORY_LINES = 200         # docs: first 200 lines of MEMORY.md ...
MEMORY_BYTES = 25 * 1024   # ... or the first 25KB, whichever comes first
MAX_HOPS = 4               # docs: imports nest to a maximum depth of four hops
MIN_RULE_CHARS = 40        # shorter lines are headings and glue, not rules

FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
IMPORT = re.compile(r"(?<![\w@`])@((?:~|\.{1,2})?/?[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)")


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def frontmatter_has_paths(text: str) -> bool:
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    return re.search(r"^paths\s*:", text[3:end], re.M) is not None


def find_imports(text: str) -> list[str]:
    """@path tokens outside code fences and inline code. A token must look like a
    file path (contain a slash or end in a file extension) so @handles are skipped."""
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in IMPORT.finditer(INLINE_CODE.sub("", line)):
            token = m.group(1).rstrip(".,;:)")
            if "/" in token or re.search(r"\.[A-Za-z0-9]{1,5}$", token):
                out.append(token)
    return out


def resolve_import(token: str, base: Path, home: Path) -> Path:
    if token.startswith("~"):
        return (home / token[1:].lstrip("/")).resolve()
    p = Path(token)
    return p.resolve() if p.is_absolute() else (base.parent / p).resolve()


def rule_lines(text: str) -> set[str]:
    """Normalised lines long enough to be a rule. Code fences and tables are skipped."""
    out, in_fence = set(), False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        s = line.strip()
        if in_fence or not s or s.startswith(("#", "|", ">", "---")):
            continue
        s = re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", s)
        s = re.sub(r"\s+", " ", s).lower()
        if len(s) >= MIN_RULE_CHARS:
            out.add(s)
    return out


def permission_flags(project: Path, home: Path) -> list[dict]:
    """Read the permission rules in the settings files and flag the gaps that matter unattended."""
    files = [home / ".claude" / "settings.json", project / ".claude" / "settings.json",
             project / ".claude" / "settings.local.json"]
    allow: list[str] = []
    deny: list[str] = []
    out: list[dict] = []
    bypass = False
    for f in files:
        if not f.is_file():
            continue
        try:
            data = json.loads(read(f))
        except ValueError:
            out.append({"flag": "BADJSON", "file": str(f), "detail": "does not parse, none of its rules apply"})
            continue
        perms = data.get("permissions") if isinstance(data, dict) else None
        perms = perms if isinstance(perms, dict) else {}
        allow += [r for r in perms.get("allow", []) if isinstance(r, str)]
        deny += [r for r in perms.get("deny", []) if isinstance(r, str)]
        if perms.get("defaultMode") == "bypassPermissions" or data.get("skipDangerousModePermissionPrompt"):
            bypass = True
    broad = [r for r in allow if r.strip() in ("Bash", "Bash(*)", "Bash(**)")]
    if broad or bypass:
        out.append({"flag": "BROADALLOW", "file": "settings",
                    "detail": ("allow rule " + broad[0] if broad else "bypassPermissions mode")
                              + " lets every command run; the deny list is the only brake"})
    if (allow or bypass) and not deny:
        out.append({"flag": "NODENY", "file": "settings",
                    "detail": f"{len(allow)} allow rule(s) and no deny rules"})
    if (allow or bypass) and not any(re.search(r"\.env|secret|credential", r, re.I) and r.startswith(("Read", "Edit", "Write"))
                                      for r in deny):
        out.append({"flag": "NOENVDENY", "file": "settings",
                    "detail": "no deny rule for .env or secrets files, e.g. Read(./.env)"})
    return out


def audit(project: Path, home: Path, memory: Path | None = None) -> dict:
    project = project.resolve()
    loaded: dict[Path, dict] = {}
    on_demand: list[dict] = []
    flags: list[dict] = []

    def add(path: Path, why: str, hops: int = 0, shown: Path | None = None) -> None:
        real = path.resolve()
        if real in loaded or not real.is_file():
            return
        text = read(real)
        loaded[real] = {
            "path": str(shown or path),
            "why": why,
            "bytes": len(text.encode("utf-8")),
            "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "_text": text,
        }
        for token in find_imports(text):
            target = resolve_import(token, real, home)
            if not target.is_file():
                flags.append({"flag": "DEAD", "file": str(shown or path),
                              "detail": f"@{token} does not exist"})
            elif hops + 1 > MAX_HOPS:
                flags.append({"flag": "DEAD", "file": str(shown or path),
                              "detail": f"@{token} is past the {MAX_HOPS}-hop import limit and is not loaded"})
            else:
                add(target, f"imported by {Path(shown or path).name}", hops + 1)

    # 1. user file, then every folder from the top down to the project
    add(home / ".claude" / "CLAUDE.md", "user file")
    chain = [project, *project.parents]
    for folder in reversed(chain):
        if folder == home.resolve() / ".claude":
            continue
        for name in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"):
            why = "project file" if folder == project else "folder above the project"
            add(folder / name, why)

    # 2. rules: user level first, then project level
    for root, label in ((home / ".claude" / "rules", "user rule"),
                        (project / ".claude" / "rules", "project rule")):
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.md")):
            text = read(f)
            if frontmatter_has_paths(text):
                on_demand.append({"path": str(f), "why": f"{label} with paths:, loads on a matching file",
                                  "bytes": len(text.encode("utf-8"))})
            else:
                add(f, f"{label}, no paths: field")

    # 3. CLAUDE.md files in subfolders load on demand
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [d for d in dirnames if d not in skip]
        here = Path(dirpath)
        if here == project or here == project / ".claude":
            continue
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            if name in filenames:
                f = here / name
                on_demand.append({"path": str(f), "why": "subfolder file, loads when Claude reads there",
                                  "bytes": f.stat().st_size})

    # 4. memory index: only the head is loaded
    if memory and memory.is_file():
        text = read(memory)
        lines = text.splitlines(keepends=True)
        head = "".join(lines[:MEMORY_LINES]).encode("utf-8")[:MEMORY_BYTES]
        total_bytes = len(text.encode("utf-8"))
        loaded[memory.resolve()] = {"path": str(memory), "why": "memory index (head only)",
                                    "bytes": len(head), "lines": min(len(lines), MEMORY_LINES),
                                    "_text": head.decode("utf-8", errors="replace")}
        if len(lines) > MEMORY_LINES or total_bytes > MEMORY_BYTES:
            flags.append({"flag": "CUT", "file": str(memory),
                          "detail": f"{len(lines)} lines, {total_bytes / 1024:.1f} KB; only the first "
                                    f"{MEMORY_LINES} lines or {MEMORY_BYTES // 1024} KB load, "
                                    f"{total_bytes - len(head)} bytes are never read"})

    # flags on the loaded set
    for info in loaded.values():
        if info["lines"] > LINE_TARGET and "memory index" not in info["why"]:
            flags.append({"flag": "LONG", "file": info["path"],
                          "detail": f"{info['lines']} lines, target is under {LINE_TARGET}"})
    seen: dict[str, list[str]] = {}
    for info in loaded.values():
        for line in rule_lines(info["_text"]):
            seen.setdefault(line, []).append(info["path"])
    for line, files in sorted(seen.items()):
        if len(files) > 1:
            flags.append({"flag": "DUPLICATE", "file": " + ".join(sorted(files)),
                          "detail": line[:110]})

    flags += permission_flags(project, home)

    def short(text: str) -> str:
        """Show paths relative to the project where they sit inside it."""
        return text.replace(str(project) + os.sep, "")

    files = [{k: (short(v) if k == "path" else v) for k, v in info.items() if k != "_text"}
             for info in loaded.values()]
    for row in on_demand:
        row["path"] = short(row["path"])
    for row in flags:
        row["file"] = short(row["file"])
    total = sum(f["bytes"] for f in files)
    return {"project": str(project), "loaded": files, "on_demand": on_demand, "flags": flags,
            "total_bytes": total, "total_lines": sum(f["lines"] for f in files),
            "est_tokens": total // 4}


def render(result: dict) -> str:
    out = [f"Loaded at launch for {result['project']}", ""]
    width = max([len(f["path"]) for f in result["loaded"]] + [4])
    for f in sorted(result["loaded"], key=lambda f: -f["bytes"]):
        out.append(f"  {f['bytes'] / 1024:7.1f} KB  {f['lines']:5d} lines  {f['path']:<{width}}  {f['why']}")
    if not result["loaded"]:
        out.append("  nothing: no CLAUDE.md, rules or imports found for this folder")
    out += ["", f"  TOTAL {result['total_bytes'] / 1024:.1f} KB, {result['total_lines']} lines, "
                f"about {result['est_tokens']:,} tokens (bytes / 4) before your first message", ""]
    if result["on_demand"]:
        n = len(result["on_demand"])
        out.append(f"Loads only on demand ({n} file{'' if n == 1 else 's'}, not counted above)")
        for f in result["on_demand"]:
            out.append(f"  {f['bytes'] / 1024:7.1f} KB  {f['path']}  ({f['why']})")
        out.append("")
    if result["flags"]:
        out.append(f"Flags ({len(result['flags'])})")
        for fl in result["flags"]:
            out.append(f"  {fl['flag']:<9} {fl['file']}: {fl['detail']}")
    else:
        out.append("Flags (0): nothing to fix")
    return "\n".join(out)


def selfcheck() -> int:
    fails = []

    def check(name: str, cond: bool) -> None:
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        proj = root / "work" / "proj"
        (home / ".claude").mkdir(parents=True)
        (proj / ".claude" / "rules").mkdir(parents=True)
        (proj / "docs").mkdir()
        (proj / "sub").mkdir()
        rule = "Never push to the main branch without running the full test suite first.\n"
        (home / ".claude" / "CLAUDE.md").write_text("# me\n" + rule)
        (root / "work" / "CLAUDE.md").write_text("# parent\nUse tabs here.\n")
        (proj / "CLAUDE.md").write_text(
            "# proj\n" + rule + "See @docs/git.md and @docs/missing.md\n"
            "Email me @someone or run `@docs/skipped.md`\n```\n@docs/fenced.md\n```\n")
        (proj / "docs" / "git.md").write_text("git rules\n" + "x\n" * 250)
        (proj / "docs" / "skipped.md").write_text("no\n")
        (proj / "docs" / "fenced.md").write_text("no\n")
        (proj / ".claude" / "rules" / "always.md").write_text("always on\n")
        (proj / ".claude" / "rules" / "scoped.md").write_text("---\npaths:\n  - \"src/**\"\n---\nscoped\n")
        (proj / "sub" / "CLAUDE.md").write_text("sub\n")
        mem = root / "MEMORY.md"
        mem.write_text("".join(f"- note {i}\n" for i in range(300)))

        r = audit(proj, home, mem)
        names = {Path(f["path"]).name: f for f in r["loaded"]}
        paths = [f["path"] for f in r["loaded"]]
        flags = {(f["flag"], f["detail"][:20]) for f in r["flags"]}
        kinds = [f["flag"] for f in r["flags"]]

        check("user CLAUDE.md is loaded", any(p.endswith("home/.claude/CLAUDE.md") for p in paths))
        check("CLAUDE.md in the folder above is loaded", any(p.endswith("work/CLAUDE.md") for p in paths))
        check("project CLAUDE.md is loaded, shown relative to the project", "CLAUDE.md" in paths)
        check("rule without paths: is loaded", "always.md" in names)
        check("rule with paths: is on demand, not loaded",
              "scoped.md" not in names and any("scoped.md" in f["path"] for f in r["on_demand"]))
        check("subfolder CLAUDE.md is on demand", any(f["path"] == os.path.join("sub", "CLAUDE.md") for f in r["on_demand"]))
        check("@import is followed", "git.md" in names)
        check("@import in inline code is skipped", "skipped.md" not in names)
        check("@import in a code fence is skipped", "fenced.md" not in names)
        check("@handle is not read as an import", not any("someone" in f["detail"] for f in r["flags"]))
        check("missing import is flagged DEAD", ("DEAD", "@docs/missing.md doe") in flags)
        check("long file is flagged LONG", kinds.count("LONG") == 1)
        check("MEMORY.md past 200 lines is flagged CUT", kinds.count("CUT") == 1)
        check("MEMORY.md counts only its first 200 lines", names["MEMORY.md"]["lines"] == 200)
        check("same rule in two files is flagged DUPLICATE", kinds.count("DUPLICATE") == 1)
        check("total is the sum of the loaded files", r["total_bytes"] == sum(f["bytes"] for f in r["loaded"]))

        # five hops: a -> b -> c -> d -> e -> f, f must not load
        deep = root / "deep"
        deep.mkdir()
        (deep / "CLAUDE.md").write_text("@a.md\n")
        for cur, nxt in zip("abcde", "bcdef"):
            (deep / f"{cur}.md").write_text(f"@{nxt}.md\n")
        (deep / "f.md").write_text("too deep\n")
        r2 = audit(deep, home)
        n2 = {Path(f["path"]).name for f in r2["loaded"]}
        check("import four hops down is loaded", "d.md" in n2)
        check("import five hops down is not loaded and is flagged",
              "e.md" not in n2 and any("hop" in f["detail"] for f in r2["flags"]))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        (home / ".claude").mkdir(parents=True)
        proj = root / "p"
        (proj / ".claude").mkdir(parents=True)

        def kinds_for(settings, local=None) -> list[str]:
            (proj / ".claude" / "settings.json").write_text(settings)
            lf = proj / ".claude" / "settings.local.json"
            if local is None and lf.exists():
                lf.unlink()
            elif local is not None:
                lf.write_text(local)
            return [f["flag"] for f in audit(proj, home)["flags"]]

        check("no settings files: no permission flags", kinds_for("{}") == [])
        k = kinds_for('{"permissions":{"allow":["Bash(npm test)"]}}')
        check("allow list with no deny is flagged NODENY and NOENVDENY", "NODENY" in k and "NOENVDENY" in k)
        check("narrow allow is not BROADALLOW", "BROADALLOW" not in k)
        check("Bash(*) is flagged BROADALLOW", "BROADALLOW" in kinds_for('{"permissions":{"allow":["Bash(*)"]}}'))
        check("bypassPermissions is flagged BROADALLOW",
              "BROADALLOW" in kinds_for('{"permissions":{"defaultMode":"bypassPermissions","deny":["Read(./.env)"]}}'))
        check("deny for .env clears NOENVDENY and NODENY",
              kinds_for('{"permissions":{"allow":["Bash(npm test)"],"deny":["Read(./.env)"]}}') == [])
        check("deny in settings.local.json counts",
              kinds_for('{"permissions":{"allow":["Bash(ls)"]}}', '{"permissions":{"deny":["Read(./secrets/**)"]}}') == [])
        check("broken settings file is flagged BADJSON", "BADJSON" in kinds_for("{nope"))

    total = 26
    print(f"\n{'PASS' if not fails else 'FAIL'}: {total - len(fails)} of {total}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="List what Claude Code loads at launch, and flag what makes rules slip.")
    ap.add_argument("project", nargs="?", default=".", help="project folder (default: here)")
    ap.add_argument("--home", default=str(Path.home()), help="home folder holding .claude/ (default: yours)")
    ap.add_argument("--memory", help="path to a MEMORY.md index to include (first 200 lines or 25 KB)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is flagged")
    ap.add_argument("--selfcheck", action="store_true", help="run the built-in tests")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()
    project = Path(a.project)
    if not project.is_dir():
        print(f"not a folder: {project}", file=sys.stderr)
        return 2
    result = audit(project, Path(a.home), Path(a.memory) if a.memory else None)
    print(json.dumps(result, indent=2) if a.json else render(result))
    return 1 if (a.strict and result["flags"]) else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard Bash calls against recursive forced deletion and destructive Git commands.
Exit 2 blocks the call; exit 0 allows it.
Exit 1 means a crash, which blocks only because of onFailure: block.
"""
import io
import json
import os
import re
import shlex
import sys


def segments(command):
    """Split shell chains and lines without treating quoted separators as operators."""
    for line in command.splitlines():
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars="&|;")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            # An unmatched quote (an apostrophe in a heredoc line) must not crash the
            # guard: with onFailure "block" a crash would refuse the whole command.
            # ponytail: crude split here, so a quoted "rm -rf" on such a line can block.
            tokens = re.sub(r"([;&|]+)", r" \1 ", line).split()
        segment = []
        for token in tokens:
            if token and all(char in "&|;" for char in token):
                yield segment
                segment = []
            else:
                segment.append(token)
        yield segment


def decide(call: dict) -> tuple[int, str]:
    command = call.get("tool_input", {}).get("command", "")
    for tokens in segments(command):
        if not tokens:
            continue
        if tokens[0] == "sudo":
            # Locate the guarded command after sudo's flags and their values.
            start = next((i for i, token in enumerate(tokens)
                          if token in ("rm", "git")), len(tokens))
            tokens = tokens[start:]
        if not tokens:
            continue
        tokens = [os.path.basename(tokens[0])] + tokens[1:]  # /bin/rm is rm
        reason = ""
        if tokens[0] == "rm":
            args = tokens[1:]
            if "--" in args:
                args = args[:args.index("--")]
            flags = [arg for arg in args if arg.startswith("-")]
            short = "".join(arg[1:] for arg in flags if not arg.startswith("--"))
            recursive = "r" in short.lower() or "--recursive" in flags
            force = "f" in short or "--force" in flags
            if recursive and force:
                reason = "recursive forced rm"
        elif tokens[:2] == ["git", "push"]:
            if any(arg in ("--force", "-f") for arg in tokens[2:]):
                reason = "git push --force"
        elif tokens[:2] == ["git", "reset"] and "--hard" in tokens[2:]:
            reason = "git reset --hard"
        if reason:
            return 2, (f"Blocked by block-destructive.py: {reason}; "
                       "run it yourself in a terminal if you mean it")
    return 0, ""


def main():
    call = json.load(sys.stdin)
    code, reason = decide(call)
    if reason:
        print(reason, file=sys.stderr)
    sys.exit(code)


def selfcheck():
    blocked = [
        "rm -rf /tmp/x", "rm -fr /tmp/x", "rm -r -f /tmp/x",
        "rm --recursive --force /tmp/x", "rm -R --force /tmp/x",
        "sudo rm -rf /tmp/x", "sudo -u root rm -rf /tmp/x",
        "true && rm -rf /tmp/x", "false || rm -rf /tmp/x",
        "echo ready; rm -rf /tmp/x", "echo ready | rm -rf /tmp/x",
        "git push --force", "git push origin main -f",
        "git reset --hard HEAD~1",
        "git push --force-with-lease --force",
        "echo hi\nrm -rf /tmp/x", "/bin/rm -rf /tmp/x",
        "cat <<EOF\nit's here\nEOF\nrm -rf /tmp/x",
    ]
    allowed = [
        "rm file.txt", "rm -r build", "rm -f file.txt",
        "git push --force-with-lease", "git push --force-with-lease=main:abc",
        "git reset --soft HEAD~1", "git push origin main",
        "rm -- -rf", "echo 'rm -rf /tmp/x'", "true && rm -r build",
        "git commit -m \"$(cat <<'EOF'\nit's done\nEOF\n)\"",
        "cat <<EOF\ndon't panic\nEOF",
    ]
    for command in blocked:
        code, reason = decide({"tool_input": {"command": command}})
        assert code == 2 and "block-destructive.py" in reason, command
    for command in allowed:
        assert decide({"tool_input": {"command": command}}) == (0, ""), command
    original_decide, original_stdin = decide, sys.stdin

    def raising_decide(call):
        raise RuntimeError("intentional selfcheck crash")

    try:
        globals()["decide"] = raising_decide
        sys.stdin = io.StringIO('{"tool_input":{"command":"true"}}')
        try:
            main()
        except SystemExit as exc:
            assert exc.code not in (None, 0)
        except RuntimeError:
            pass  # Uncaught by main, so the process would exit nonzero.
        else:
            raise AssertionError("main swallowed the crash")
    finally:
        globals()["decide"] = original_decide
        sys.stdin = original_stdin
    print(f"selfcheck OK: {len(blocked) + len(allowed) + 1} cases")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        selfcheck()
    else:
        main()

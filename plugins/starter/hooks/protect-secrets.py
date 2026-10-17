#!/usr/bin/env python3
"""Keep secret files out of file and notebook tool calls.
Exit 2 blocks the call; exit 0 allows it.
Exit 1 means a crash, which blocks only because of onFailure: block.
"""
import io
import json
from pathlib import PurePosixPath
import sys


# ponytail: This hook does not see cat .env through Bash.
def decide(call: dict) -> tuple[int, str]:
    tool_input = call.get("tool_input", {})
    for field in ("file_path", "notebook_path"):
        filename = tool_input.get(field, "")
        path = PurePosixPath(filename.replace("\\", "/"))
        name = path.name
        secret_env = (name == ".env" or name.startswith(".env.")) and name not in (
            ".env.example", ".env.sample", ".env.template")
        if (secret_env or name.endswith((".pem", ".key", ".p12", ".pfx"))
                or name in ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")
                or any(part in (".ssh", ".aws") for part in path.parts)):
            return 2, (f"Blocked by protect-secrets.py: {filename}; "
                       "secrets stay out of the chat. You can open it yourself.")
    return 0, ""


def main():
    call = json.load(sys.stdin)
    code, reason = decide(call)
    if reason:
        print(reason, file=sys.stderr)
    sys.exit(code)


def selfcheck():
    blocked = [
        ".env", "app/.env.local", ".env.production", ".env.",
        "cert.pem", "private.key", "bundle.p12", "bundle.pfx",
        "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
        "/home/user/.ssh/config", "/home/user/.aws/config",
        ".env.example.local", "C:\\Users\\user\\.ssh\\config",
    ]
    allowed = [
        ".env.example", ".env.sample", ".env.template", "src/env.py",
        "keys.md", "src/main.py", "config/environment.json",
        "cert.pem.md", ".ssh-backup/config",
    ]
    for filename in blocked:
        code, reason = decide({"tool_input": {"file_path": filename}})
        assert code == 2 and filename in reason, filename
    for filename in allowed:
        assert decide({"tool_input": {"file_path": filename}}) == (0, ""), filename
    assert decide({"tool_input": {"notebook_path": ".env"}})[0] == 2
    assert decide({"tool_input": {"notebook_path": "analysis.ipynb"}}) == (0, "")
    assert decide({"tool_input": {"file_path": "safe.py", "notebook_path": ".env"}})[0] == 2
    original_decide, original_stdin = decide, sys.stdin

    def raising_decide(call):
        raise RuntimeError("intentional selfcheck crash")

    try:
        globals()["decide"] = raising_decide
        sys.stdin = io.StringIO('{"tool_input":{"file_path":"safe.py"}}')
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
    print(f"selfcheck OK: {len(blocked) + len(allowed) + 4} cases")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        selfcheck()
    else:
        main()

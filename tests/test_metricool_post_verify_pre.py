"""Tests for metricool-post-verify-pre PreToolUse hook.

Hook contract: stdin is JSON {"tool_name": "Bash", "tool_input": {"command": "..."}}.
Exit 0 = allow. Exit 2 + stderr = block. Exit other = unexpected.
"""

import json
import os
import subprocess

HOOK = os.path.join(os.path.dirname(__file__), "metricool-post-verify-pre.py")


def run_hook(command: str, tool: str = "Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    proc = subprocess.run(
        ["python3", HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=5,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_empty_media_reel_blocks():
    cmd = """curl -X POST -H 'X-Mc-Auth: tok' -d '{"text":"hi","media":[],"instagramData":{"type":"REEL"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, err = run_hook(cmd)
    assert code == 2, f"expected block (2), got {code}: stderr={err!r}"
    assert "empty media" in err.lower() or "empty" in err.lower() or "no media" in err.lower() or "blocked" in err.lower()


def test_missing_media_field_reel_blocks():
    cmd = """curl -X POST -d '{"text":"hi","instagramData":{"type":"REEL"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_populated_media_reel_allows():
    cmd = """curl -X POST -d '{"text":"hi","media":["https://litterbox.catbox.moe/abc.mp4"],"instagramData":{"type":"REEL"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 0


def test_carousel_with_media_allows():
    cmd = """curl -X POST -d '{"text":"hi","media":["a.jpg","b.jpg"],"instagramData":{"type":"POST"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 0


def test_carousel_empty_media_also_blocks():
    cmd = """curl -X POST -d '{"text":"hi","media":[],"instagramData":{"type":"POST"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_youtube_short_empty_media_blocks():
    cmd = """curl -X POST -d '{"text":"hi","media":[],"youtubeData":{"type":"SHORT"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_tiktok_video_empty_media_blocks():
    cmd = """curl -X POST -d '{"text":"hi","media":[],"providers":[{"network":"tiktok"}]}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_non_metricool_post_allows():
    cmd = """curl -X POST -d '{}' https://example.com/api/posts"""
    code, _, _ = run_hook(cmd)
    assert code == 0


def test_metricool_get_allows():
    cmd = """curl -X GET https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 0


def test_metricool_delete_allows():
    cmd = """curl -X DELETE https://app.metricool.com/api/v2/scheduler/posts/12345?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 0


def test_threads_text_post_allows():
    cmd = """curl -X POST -d '{"text":"thread post","providers":[{"network":"threads"}]}' https://app.metricool.com/api/v2/scheduler/posts?blogId=67890"""
    code, _, _ = run_hook(cmd)
    assert code == 0


def test_non_bash_tool_passes():
    code, _, _ = run_hook("anything", tool="Edit")
    assert code == 0


def test_empty_stdin_passes():
    proc = subprocess.run(["python3", HOOK], input="", capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0


# ---------- ADVERSARIAL CASES (Phase 1.5 Task 5) ----------

def test_media_empty_string_blocks():
    """media as empty string (not array) — should block."""
    cmd = """curl -X POST -H "X-Mc-Auth: tok" -d '{"text":"x","media":"","instagramData":{"type":"REEL"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_media_null_blocks():
    """media as null (JSON literal) — should block."""
    cmd = """curl -X POST -H "X-Mc-Auth: tok" -d '{"text":"x","media":null,"instagramData":{"type":"REEL"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_media_array_with_empty_string_blocks():
    """media as [""] — array with one empty string element."""
    cmd = """curl -X POST -H "X-Mc-Auth: tok" -d '{"text":"x","media":[""],"instagramData":{"type":"REEL"}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_multiline_heredoc_payload_blocks_when_empty_media():
    """Bash heredoc-style payload split across lines."""
    cmd = "curl -X POST -H 'X-Mc-Auth: tok' -d $'{\\n  \"text\":\"x\",\\n  \"media\":[],\\n  \"instagramData\":{\"type\":\"REEL\"}\\n}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_python_subprocess_curl_payload_blocks():
    """Bash command containing python subprocess that calls curl with empty-media payload."""
    cmd = """python3 -c "import subprocess; subprocess.run(['curl', '-X', 'POST', '-d', '{\\"text\\":\\"x\\",\\"media\\":[],\\"instagramData\\":{\\"type\\":\\"REEL\\"}}', 'https://app.metricool.com/api/v2/scheduler/posts?blogId=12345'])" """
    code, _, _ = run_hook(cmd)
    assert code == 2


def test_whitespace_variants_blocked():
    """Various whitespace patterns around the empty-media regex."""
    for media_repr in (
        '"media":[]',
        '"media": []',
        '"media" : []',
        '"media" :  [ ]',
    ):
        cmd = f"""curl -X POST -H "X-Mc-Auth: tok" -d '{{"text":"x",{media_repr},"instagramData":{{"type":"REEL"}}}}' https://app.metricool.com/api/v2/scheduler/posts?blogId=12345"""
        code, _, _ = run_hook(cmd)
        assert code == 2, f"failed for media_repr={media_repr!r}"

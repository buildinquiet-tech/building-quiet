#!/usr/bin/env python3
"""Build one product's shipped file from a tagged commit.

    python3 scripts/build_product.py <slug> [--tag TAG] [--repo PATH] [--check FILE]

Reads products/<slug>/ AS OF THE TAG (never the working tree), so what ships is
exactly what the tag says. The tag defaults to the newest `<slug>-v*` tag.
Writes dist/<artifact> and prints its sha256.

products/<slug>/product.json:
    kind "zip"  -> src/ is zipped under a single root folder named `zip_root`
    kind "file" -> src/<artifact> is shipped as-is

Zips are deterministic: sorted entries, timestamps pinned to the tag's commit
date, so the same tag always builds the same bytes.

--check FILE compares the build against a previously shipped file (member by
member for zips, bytes for files) and exits 1 on any difference.
"""
import argparse, hashlib, io, json, shutil, subprocess, sys, tarfile, time, zipfile
from pathlib import Path


def git(repo, *args, binary=False):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode().strip()


def newest_tag(repo, slug):
    tags = git(repo, "tag", "--list", f"{slug}-v*", "--sort=-v:refname").splitlines()
    if not tags:
        sys.exit(f"no tag matching {slug}-v* in {repo}. Tag a commit first: git tag {slug}-v1")
    return tags[0]


def tree_at(repo, tag, slug):
    """Return {relative path under src/: bytes} for products/<slug>/src at the tag."""
    raw = git(repo, "archive", "--format=tar", tag, f"products/{slug}/src", binary=True)
    prefix = f"products/{slug}/src/"
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for m in tf.getmembers():
            if m.isfile():
                files[m.name[len(prefix):]] = tf.extractfile(m).read()
    return files


def build_zip(files, root, stamp):
    buf = io.BytesIO()
    dt = time.gmtime(stamp)[:6]
    dirs = sorted({str(Path(root, p).parent) + "/" for p in files} | {root + "/"})
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in dirs:
            info = zipfile.ZipInfo(d, dt)
            info.external_attr = 0o40755 << 16
            z.writestr(info, b"")
        for p in sorted(files):
            info = zipfile.ZipInfo(f"{root}/{p}", dt)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[p])
    return buf.getvalue()


def zip_members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--tag")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--check", help="previously shipped file to compare against")
    a = ap.parse_args()

    repo = Path(a.repo).expanduser().resolve()
    tag = a.tag or newest_tag(repo, a.slug)
    spec = json.loads(git(repo, "show", f"{tag}:products/{a.slug}/product.json"))
    files = tree_at(repo, tag, a.slug)
    if not files:
        sys.exit(f"products/{a.slug}/src is empty at {tag}")

    if spec["kind"] == "zip":
        stamp = int(git(repo, "log", "-1", "--format=%ct", tag))
        data = build_zip(files, spec["zip_root"], stamp)
    elif spec["kind"] == "file":
        data = files[spec["artifact"]]
    else:
        sys.exit(f"unknown kind {spec['kind']!r}")

    out = repo / "dist" / spec["artifact"]
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(data)
    print(f"{a.slug} @ {tag} -> {out.relative_to(repo)}  sha256 {hashlib.sha256(data).hexdigest()[:16]}")

    if a.check:
        old = Path(a.check).expanduser().read_bytes()
        same = zip_members(old) == zip_members(data) if spec["kind"] == "zip" else old == data
        print("check: MATCHES shipped file" if same else "check: DIFFERS from shipped file")
        sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the deduplicated Wayback fetch-target list from the CDX manifests.

Input : manifests/phpBB2_manifest.json , manifests/forum_manifest.json
        (CDX JSON: header + [timestamp, original, statuscode, mimetype, digest, length])
Output: manifests/fetch_targets.tsv
        columns: board, slug, timestamp, digest, relpath, original_url

Dedup: one fetch per (normalized_url, digest).
  - normalized_url strips the volatile phpBB `sid=` session token.
  - `digest` is the Wayback content SHA1 -> exact content identity. Keeping every
    distinct digest per URL preserves genuinely different versions of a thread over
    time (gap-filling) while collapsing identical re-crawls. No content is dropped.
  - Only statuscode==200 rows are kept.
  - relpath embeds an 8-char url hash so distinct URLs never collide on disk.
"""
import json, re, os, hashlib

ALLOWED = ("viewtopic.php", "viewforum.php", "memberlist.php", "profile.php", "index.php")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def strip_sid(url):
    url = re.sub(r'([?&])sid=[0-9a-f]+(&|$)', lambda m: m.group(1) if m.group(2) == '&' else '', url)
    return url.rstrip('?&')

def pagetype(url):
    m = re.search(r'/(?:phpBB2|forum)/([a-z_]+\.php)', url)
    return m.group(1) if m else None

def board(url):
    if '/phpBB2/' in url: return 'phpBB2'
    if '/forum/' in url:  return 'forum'
    return None

def slug(url, pt):
    m = re.search(r'[?&](t|p|f|u)=(\d+)', url)
    base = f"{m.group(1)}{m.group(2)}" if m else pt.replace('.php', '')
    start = re.search(r'[?&]start=(\d+)', url)
    if start: base += f"_s{start.group(1)}"
    return base

def main():
    rows = []
    for fn in ("phpBB2_manifest.json", "forum_manifest.json"):
        rows.extend(json.load(open(os.path.join(HERE, "manifests", fn)))[1:])

    best = {}  # (norm_url, digest) -> tuple
    for ts, original, sc, mime, digest, length in rows:
        if sc != "200":
            continue
        pt = pagetype(original); bd = board(original)
        if not pt or not bd or pt not in ALLOWED:
            continue
        norm = strip_sid(original)
        key = (norm, digest)
        if key not in best or ts > best[key][2]:
            uh = hashlib.md5(norm.encode("utf-8", "replace")).hexdigest()[:8]
            sl = slug(norm, pt)
            relpath = f"{bd}/{sl}/{ts}_{uh}.html"
            best[key] = (bd, sl, ts, digest, relpath, original)

    out = sorted(best.values(), key=lambda r: (r[0], r[1], r[2]))
    path = os.path.join(HERE, "manifests", "fetch_targets.tsv")
    with open(path, "w") as f:
        f.write("board\tslug\ttimestamp\tdigest\trelpath\toriginal_url\n")
        for r in out:
            f.write("\t".join(str(x) for x in r) + "\n")

    from collections import Counter
    by_board = Counter(r[0] for r in out)
    print(f"wrote {path}: {len(out)} fetch targets")
    print("by board:", dict(by_board))

if __name__ == "__main__":
    main()

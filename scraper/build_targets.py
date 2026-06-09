#!/usr/bin/env python3
"""Build the deduplicated Wayback fetch-target list from the CDX manifests.

Input : manifests/phpBB2_manifest.json , manifests/forum_manifest.json
        (CDX JSON: header row + [timestamp, original, statuscode, mimetype, digest, length])
Output: manifests/fetch_targets.tsv
        columns: board, pagetype, slug, timestamp, length, original_url

Dedup strategy: one fetch per (normalized_url, length).
  - normalized_url strips the volatile phpBB `sid=` session token.
  - `length` (WARC record byte size) is a robust content-version proxy: identical
    pages under different session ids share the same length (sid is fixed-width),
    so this collapses session noise while KEEPING genuinely different versions of a
    thread over time (more posts -> different length) = content-distinct snapshots.
"""
import json, re, os, sys

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
    # stable, filesystem-safe identifier for the thread/page
    m = re.search(r'[?&](t|p|f|u)=(\d+)', url)
    if m: base = f"{m.group(1)}{m.group(2)}"
    else: base = pt.replace('.php', '')
    start = re.search(r'[?&]start=(\d+)', url)
    if start: base += f"_s{start.group(1)}"
    return base

def main():
    rows = []
    for fn, _ in (("phpBB2_manifest.json", 'phpBB2'), ("forum_manifest.json", 'forum')):
        data = json.load(open(os.path.join(HERE, "manifests", fn)))[1:]
        rows.extend(data)

    best = {}  # (norm_url, length) -> (board, pagetype, slug, timestamp, length, original)
    for ts, original, sc, mime, digest, length in rows:
        pt = pagetype(original)
        bd = board(original)
        if not pt or not bd or pt not in ALLOWED:
            continue
        norm = strip_sid(original)
        try: L = int(length)
        except: L = 0
        key = (norm, L)
        # keep the latest timestamp for each content-distinct version
        if key not in best or ts > best[key][3]:
            best[key] = (bd, pt, slug(norm, pt), ts, L, original)

    out = sorted(best.values(), key=lambda r: (r[0], r[1], r[2], r[3]))
    path = os.path.join(HERE, "manifests", "fetch_targets.tsv")
    with open(path, "w") as f:
        f.write("board\tpagetype\tslug\ttimestamp\tlength\toriginal_url\n")
        for r in out:
            f.write("\t".join(str(x) for x in r) + "\n")

    # stats
    from collections import Counter
    by_board = Counter(r[0] for r in out)
    by_type = Counter((r[0], r[1]) for r in out)
    print(f"wrote {path}: {len(out)} fetch targets")
    print("by board:", dict(by_board))
    for k, v in sorted(by_type.items()):
        print(f"  {k[0]:7s} {k[1]:16s} {v}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Resumable, polite Wayback harvester. Stdlib only (no pip install in CI).

Fetches each target's raw archived HTML via the `id_` (un-rewritten) endpoint and
saves it to archive/<board>/<slug>/<timestamp>.html as PLAIN HTML.

Resumable: any target whose output file already exists is skipped, so re-runs
(after throttling/timeouts) continue where they left off.

Usage:
  python3 scraper/harvest.py --shard 0 --num-shards 8 [--delay 1.7] [--limit N]
"""
import argparse, os, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(HERE, "manifests", "fetch_targets.tsv")
OUTROOT = os.path.join(HERE, "archive")
UA = "avalanches-forum-archive/1.0 (+https://github.com/; preservation of public fan-forum content)"

def load(shard, num_shards):
    rows = []
    with open(TARGETS) as f:
        next(f)  # header
        for i, line in enumerate(f):
            if i % num_shards != shard:
                continue
            board, pagetype, slug, ts, length, url = line.rstrip("\n").split("\t")
            rows.append((board, slug, ts, url))
    return rows

def fetch(ts, url, max_tries=5):
    wb = f"https://web.archive.org/web/{ts}id_/{url}"
    backoff = 5
    for attempt in range(1, max_tries + 1):
        try:
            req = urllib.request.Request(wb, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 502, 500):
                wait = backoff * attempt
                print(f"  {e.code} throttled, sleep {wait}s ({attempt}/{max_tries})", flush=True)
                time.sleep(wait)
                continue
            if e.code in (403, 404):
                print(f"  {e.code} skip {wb}", flush=True)
                return None
            time.sleep(backoff * attempt)
        except Exception as e:
            print(f"  err {e} ({attempt}/{max_tries})", flush=True)
            time.sleep(backoff * attempt)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--delay", type=float, default=1.7)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    rows = load(a.shard, a.num_shards)
    if a.limit:
        rows = rows[:a.limit]
    got = skip = fail = 0
    for n, (board, slug, ts, url) in enumerate(rows, 1):
        d = os.path.join(OUTROOT, board, slug)
        out = os.path.join(d, f"{ts}.html")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            skip += 1
            continue
        body = fetch(ts, url)
        if body is None:
            fail += 1
        else:
            os.makedirs(d, exist_ok=True)
            with open(out, "wb") as f:
                f.write(body)
            got += 1
            if got % 50 == 0:
                print(f"[shard {a.shard}] {n}/{len(rows)} got={got} skip={skip} fail={fail}", flush=True)
            time.sleep(a.delay)
    print(f"[shard {a.shard}] DONE got={got} skip={skip} fail={fail} of {len(rows)}", flush=True)

if __name__ == "__main__":
    main()

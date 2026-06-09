#!/usr/bin/env python3
"""Resumable, polite Wayback harvester. Stdlib only (no pip install in CI).

Fetches each target's raw archived HTML via the `id_` (un-rewritten) endpoint and
saves the original bytes to manifests-defined relpath under archive/ as PLAIN HTML
(no decode: the corpus is mixed iso-8859-1/utf-8; raw bytes preserve it exactly).

Rate discipline (archive.org blocks an IP ~1h after ~60 req/min):
  - SERIAL only (workflow max-parallel: 1); never burst.
  - PACING SLEEP AFTER EVERY NETWORK REQUEST regardless of outcome (200/404/fail).
  - Escalating backoff on connection refused/reset or 429/5xx.
  - Circuit breaker: after N consecutive failures, abort the shard (exit 1) so a
    later re-run resumes (already-saved files are skipped).

Usage: python3 scraper/harvest.py --shard 0 --num-shards 8 [--delay 2.5] [--limit N]
"""
import argparse, os, random, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(HERE, "manifests", "fetch_targets.tsv")
OUTROOT = os.path.join(HERE, "archive")
UA = ("avalanches-forum-archive/1.0 "
      "(+https://github.com/studioetc/theavalanches-forum-archive; "
      "public fan-forum preservation, read-only)")

def load(shard, num_shards):
    rows = []
    with open(TARGETS) as f:
        next(f)  # header: board slug timestamp digest relpath original_url
        for i, line in enumerate(f):
            if i % num_shards != shard:
                continue
            board, slug, ts, digest, relpath, url = line.rstrip("\n").split("\t")
            rows.append((ts, url, relpath))
    return rows

class Throttled(Exception):
    pass

def fetch_once(ts, url):
    """Return non-empty bytes; return None for permanent skip (403/404);
    raise Throttled for retryable conditions (429/5xx, conn reset, empty body)."""
    wb = f"https://web.archive.org/web/{ts}id_/{url}"
    try:
        req = urllib.request.Request(wb, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            final = r.geturl()
        if not body:
            raise Throttled("empty body")
        # id_ sometimes 302s to a different capture; content is still a real
        # archived page, but note when the served timestamp differs materially.
        if ts[:8] not in final:
            print(f"  note: {ts} redirected -> {final[:70]}", flush=True)
        return body
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise Throttled(f"HTTP {e.code}")
    except urllib.error.URLError as e:        # ECONNREFUSED/RESET, DNS, timeout
        raise Throttled(f"URLError {e.reason}")
    except Throttled:
        raise
    except Exception as e:
        raise Throttled(f"{type(e).__name__} {e}")

def atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--delay", type=float, default=2.5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--max-consecutive", type=int, default=8)
    a = ap.parse_args()

    rows = load(a.shard, a.num_shards)
    if a.limit:
        rows = rows[:a.limit]
    got = skip = miss = fail = 0
    consec = 0

    for n, (ts, url, relpath) in enumerate(rows, 1):
        out = os.path.join(OUTROOT, relpath)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            skip += 1
            continue                                  # no request -> no pacing sleep

        body = None
        failed = False
        for attempt in range(1, a.retries + 1):
            try:
                body = fetch_once(ts, url)
                break                                 # bytes (success) or None (permanent skip)
            except Throttled as e:
                if attempt == a.retries:
                    failed = True
                    break
                wait = min(4 * (2 ** (attempt - 1)), 90) + random.uniform(0, 3)
                print(f"  throttled ({e}) try {attempt}/{a.retries}, sleep {wait:.0f}s", flush=True)
                time.sleep(wait)

        if failed:
            fail += 1
            consec += 1
            if consec >= a.max_consecutive:
                print(f"[shard {a.shard}] ABORT: {consec} consecutive failures "
                      f"(likely IP-blocked). Re-run later to resume. "
                      f"PARTIAL got={got} skip={skip} miss={miss} fail={fail}", flush=True)
                sys.exit(1)
        elif body is None:
            miss += 1                                 # 403/404: not captured / excluded
            consec = 0
        else:
            atomic_write(out, body)
            got += 1
            consec = 0
            if got % 25 == 0:
                print(f"[shard {a.shard}] {n}/{len(rows)} got={got} skip={skip} miss={miss} fail={fail}", flush=True)

        time.sleep(a.delay + random.uniform(0, 1.0))  # pacing after EVERY request

    print(f"[shard {a.shard}] DONE got={got} skip={skip} miss={miss} fail={fail} of {len(rows)}", flush=True)

if __name__ == "__main__":
    main()

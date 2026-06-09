#!/usr/bin/env python3
"""Resumable, polite Wayback harvester. Stdlib only (no pip install in CI).

Fetches each target's raw archived HTML via the `id_` (un-rewritten) endpoint and
saves it to archive/<board>/<slug>/<timestamp>.html as PLAIN HTML.

Rate discipline (archive.org blocks an IP for ~1h after ~60 req/min):
  - SERIAL only. Run shards sequentially (workflow max-parallel: 1), never bursting.
  - ~1 request / DELAY seconds with jitter  (default 2.5s -> ~24/min, safe headroom).
  - On connection refused/reset (TCP-level throttle) OR 429: escalating backoff.
  - Circuit breaker: after many consecutive failures, long cooldown; if it persists,
    exit non-zero and let a later re-run resume (already-saved files are skipped).

Usage:
  python3 scraper/harvest.py --shard 0 --num-shards 8 [--delay 2.5] [--limit N]
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
        next(f)  # header
        for i, line in enumerate(f):
            if i % num_shards != shard:
                continue
            board, pagetype, slug, ts, length, url = line.rstrip("\n").split("\t")
            rows.append((board, slug, ts, url))
    return rows

class Throttled(Exception):
    pass

def fetch_once(ts, url):
    """Return bytes, or raise Throttled (retryable) / return None (permanent skip)."""
    wb = f"https://web.archive.org/web/{ts}id_/{url}"
    try:
        req = urllib.request.Request(wb, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (429, 500, 502, 503):
            raise Throttled(f"HTTP {e.code}")
        if e.code in (403, 404):
            return None                      # excluded / not captured -> skip
        raise Throttled(f"HTTP {e.code}")
    except urllib.error.URLError as e:       # ECONNREFUSED/RESET, DNS, timeout
        raise Throttled(f"URLError {e.reason}")
    except Exception as e:
        raise Throttled(f"{type(e).__name__} {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--delay", type=float, default=2.5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-consecutive", type=int, default=12,
                    help="consecutive throttles before aborting the shard for a later resume")
    a = ap.parse_args()

    rows = load(a.shard, a.num_shards)
    if a.limit:
        rows = rows[:a.limit]
    got = skip = fail = 0
    consec = 0

    for n, (board, slug, ts, url) in enumerate(rows, 1):
        out = os.path.join(OUTROOT, board, slug, f"{ts}.html")
        if os.path.exists(out) and os.path.getsize(out) > 0:
            skip += 1
            continue

        body = None
        for attempt in range(1, 7):                       # up to 6 tries per target
            try:
                body = fetch_once(ts, url)
                break                                     # success or permanent-skip(None)
            except Throttled as e:
                wait = min(4 * (2 ** (attempt - 1)), 300) + random.uniform(0, 3)
                print(f"  throttled ({e}) try {attempt}/6, sleep {wait:.0f}s", flush=True)
                time.sleep(wait)
        else:
            body = "FAILED"                                # exhausted retries

        if body == "FAILED":
            fail += 1
            consec += 1
            if consec >= a.max_consecutive:
                print(f"[shard {a.shard}] ABORT: {consec} consecutive failures "
                      f"(likely IP-blocked). Re-run later to resume.", flush=True)
                print(f"[shard {a.shard}] PARTIAL got={got} skip={skip} fail={fail}", flush=True)
                sys.exit(1)
            continue

        consec = 0
        if body is None:
            fail += 1
            continue

        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(body)
        got += 1
        if got % 25 == 0:
            print(f"[shard {a.shard}] {n}/{len(rows)} got={got} skip={skip} fail={fail}", flush=True)
        time.sleep(a.delay + random.uniform(0, 1.0))      # jittered pacing

    print(f"[shard {a.shard}] DONE got={got} skip={skip} fail={fail} of {len(rows)}", flush=True)

if __name__ == "__main__":
    main()

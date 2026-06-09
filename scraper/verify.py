#!/usr/bin/env python3
"""Coverage + health report over the harvested archive. Stdlib only.

Writes manifests/coverage_report.md:
  - fetched vs expected per board/pagetype
  - distinct threads recovered (t=/p= slugs)
  - flags likely-empty/stub pages (small size or phpBB 'no forums' / error markers)
"""
import os, re, collections

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(HERE, "manifests", "fetch_targets.tsv")
ARCHIVE = os.path.join(HERE, "archive")
REPORT = os.path.join(HERE, "manifests", "coverage_report.md")

STUB_MARKERS = (b"This board has no forums", b"The requested topic does not exist",
                b"Not Found", b"no posts", b"Information")

def expected():
    c = collections.Counter()
    slugs = collections.defaultdict(set)
    with open(TARGETS) as f:
        next(f)
        for line in f:
            board, pagetype, slug, ts, length, url = line.rstrip("\n").split("\t")
            c[(board, pagetype)] += 1
            slugs[board].add(slug)
    return c, slugs

def main():
    exp, exp_slugs = expected()
    got = collections.Counter()
    got_slugs = collections.defaultdict(set)
    stubs = 0
    total_bytes = 0
    files = 0
    for board in sorted(os.listdir(ARCHIVE)) if os.path.isdir(ARCHIVE) else []:
        bdir = os.path.join(ARCHIVE, board)
        if not os.path.isdir(bdir):
            continue
        for slug in os.listdir(bdir):
            sdir = os.path.join(bdir, slug)
            if not os.path.isdir(sdir):
                continue
            for fn in os.listdir(sdir):
                if not fn.endswith(".html"):
                    continue
                p = os.path.join(sdir, fn)
                sz = os.path.getsize(p)
                total_bytes += sz; files += 1
                got_slugs[board].add(slug)
                pt = "viewtopic.php" if re.match(r'[tp]\d', slug) else "other"
                got[(board, pt)] += 1
                if sz < 3000:
                    with open(p, "rb") as fh:
                        head = fh.read(4000)
                    if any(m in head for m in STUB_MARKERS):
                        stubs += 1

    lines = ["# Coverage report", ""]
    lines.append(f"- Files harvested: **{files}**  (~{total_bytes/1e6:.1f} MB)")
    lines.append(f"- Likely stub/empty pages flagged: **{stubs}**")
    lines.append("")
    lines.append("## Distinct threads/pages recovered (by board)")
    for board in sorted(exp_slugs):
        e = len(exp_slugs[board]); g = len(got_slugs.get(board, set()))
        pct = (100*g/e) if e else 0
        lines.append(f"- **{board}**: {g}/{e} slugs ({pct:.0f}%)")
    lines.append("")
    lines.append("## Captures fetched vs expected")
    for k in sorted(exp):
        lines.append(f"- {k[0]} {k[1]}: target {exp[k]}")
    with open(REPORT, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))

if __name__ == "__main__":
    main()

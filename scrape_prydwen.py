#!/usr/bin/env python3
"""
Scrapes ZZZ agent build data (stat priorities, target ranges, disc sets)
from Prydwen.gg and writes normalized JSON per agent.

Usage:
    python scrape_prydwen.py --slugs miyabi,yanagi,evelyn --out data/builds
    python scrape_prydwen.py --all --out data/builds          # scrape every agent
    python scrape_prydwen.py --slugs miyabi --dump-raw         # debug: dump raw HTML/JSON to inspect

Run with --dump-raw once against a character you know well (e.g. miyabi) to
confirm the extraction actually matches what's on the page before trusting
the Action to run unattended.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.prydwen.gg/zenless/characters/{slug}"
AGENTS_INDEX = "https://www.prydwen.gg/zenless/characters"
HEADERS = {
    # Identify yourself honestly and don't hide behind a browser UA -
    # polite scraping means the site owner can see who/what is hitting them.
    "User-Agent": "ZZZ-Companion-App build-data-sync (contact: <your github/email>)"
}
REQUEST_DELAY_SECONDS = 2.0  # be polite, this isn't a race


def get_all_slugs() -> list[str]:
    """Scrape the agent index page for character slugs."""
    r = requests.get(AGENTS_INDEX, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    slugs = set()
    for a in soup.find_all("a", href=re.compile(r"/zenless/characters/[^/]+$")):
        slug = a["href"].rstrip("/").split("/")[-1]
        if slug and slug != "characters":
            slugs.add(slug)
    return sorted(slugs)


def try_next_data(soup: BeautifulSoup) -> dict | None:
    """Look for the Next.js hydration payload - most stable extraction path."""
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def find_build_keys(obj, path="", found=None):
    """
    Recursively walk NEXT_DATA looking for keys that smell like build data.
    Prints paths so you can identify the real structure on first run.
    Only used in --dump-raw mode to help you locate the real field names.
    """
    if found is None:
        found = []
    target_hints = ("statpriority", "substat", "discset", "endgamestat", "mainstat")
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_l = str(k).lower()
            if any(h in key_l for h in target_hints):
                found.append((f"{path}.{k}", v if not isinstance(v, (dict, list)) else type(v).__name__))
            find_build_keys(v, f"{path}.{k}", found)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):  # cap to avoid runaway output on huge arrays
            find_build_keys(v, f"{path}[{i}]", found)
    return found


def extract_from_text(page_text: str) -> dict:
    """
    Fallback: parse the rendered text using the label patterns Prydwen
    consistently uses ("Disk 4", "Substats:", "Best Endgame Stats (Level 60)", etc).
    This is what to use if NEXT_DATA doesn't contain the build fields
    (e.g. if they're rendered server-side into plain markup with no JSON payload).
    """
    result = {}

    disk_pattern = re.compile(
        r"\*\*Disk (\d)\*\*\s*\n+\s*([^\n]+)", re.MULTILINE
    )
    main_stats = {}
    for slot, rule in disk_pattern.findall(page_text):
        main_stats[f"disk_{slot}"] = rule.strip()
    if main_stats:
        result["main_stat_priority"] = main_stats

    substats_match = re.search(r"Substats:\s*([^\n]+)", page_text)
    if substats_match:
        result["substat_priority"] = substats_match.group(1).strip()

    ranges_block = re.search(
        r"Best Endgame Stats \(Level 60\)\s*\n+(.+?)(?:\n\n[A-Z]|\Z)",
        page_text,
        re.DOTALL,
    )
    if ranges_block:
        stat_ranges = {}
        for line in ranges_block.group(1).splitlines():
            m = re.match(r"-\s*([A-Za-z ]+):\s*\*\*([^*]+)\*\*", line.strip())
            if m:
                stat_ranges[m.group(1).strip()] = m.group(2).strip()
        if stat_ranges:
            result["target_stat_ranges"] = stat_ranges

    # Disc sets: "Branch & Blade Song (4-PC)" preceded by a usage percentage
    set_pattern = re.compile(
        r"([\d.]+)%\s*\n+!\[[^\]]*\]\([^)]*\)\s*\n+([A-Za-z &]+)\s*\((\d)-PC\)"
    )
    sets = []
    for usage, name, pc in set_pattern.findall(page_text):
        sets.append({"name": name.strip(), "pieces": int(pc), "score_pct": float(usage)})
    if sets:
        result["disc_sets"] = sets

    return result


def scrape_agent(slug: str, dump_raw: bool = False) -> dict:
    url = BASE.format(slug=slug)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    if dump_raw:
        raw_path = Path(f"_debug_{slug}_raw.html")
        raw_path.write_text(r.text, encoding="utf-8")
        print(f"[debug] wrote raw HTML to {raw_path}")

    next_data = try_next_data(soup)
    if next_data:
        if dump_raw:
            hits = find_build_keys(next_data)
            print(f"[debug] {slug}: possible build-data keys in __NEXT_DATA__:")
            for path, val in hits:
                print(f"  {path} -> {val}")
        # TODO once you've identified the real key path from --dump-raw output,
        # extract it directly here instead of falling through to text parsing:
        # e.g. build = next_data["props"]["pageProps"]["character"]["build"]

    page_text = soup.get_text("\n")
    extracted = extract_from_text(page_text)

    if not extracted:
        raise ValueError(f"No build data extracted for '{slug}' - page structure may have changed")

    extracted["slug"] = slug
    extracted["source"] = url
    extracted["scraped_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return extracted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", help="comma-separated agent slugs")
    ap.add_argument("--all", action="store_true", help="scrape every agent from the index")
    ap.add_argument("--out", default="data/builds", help="output directory")
    ap.add_argument("--dump-raw", action="store_true", help="write debug HTML + key search output")
    args = ap.parse_args()

    if args.all:
        slugs = get_all_slugs()
    elif args.slugs:
        slugs = [s.strip() for s in args.slugs.split(",")]
    else:
        print("Pass --slugs a,b,c or --all", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    for slug in slugs:
        try:
            data = scrape_agent(slug, dump_raw=args.dump_raw)
            (out_dir / f"{slug}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"OK   {slug}")
        except Exception as e:
            print(f"FAIL {slug}: {e}", file=sys.stderr)
            failures.append(slug)
        time.sleep(REQUEST_DELAY_SECONDS)

    if failures:
        # Non-zero exit so the Action step fails and triggers the issue-creation step
        print(f"\n{len(failures)} agent(s) failed: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

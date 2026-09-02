#!/usr/bin/env python3
"""
Build one .ics feed per team from ESPN's public JSON endpoints.

Usage:
    python build_ics.py                 # writes docs/<team>.ics for each team
    python build_ics.py --outdir out    # custom output directory
    python build_ics.py --find texas    # resolve ESPN team ids by name
    python build_ics.py --dump cowboys  # print raw JSON for one configured team

No API key. ESPN's endpoints are undocumented and unsupported, so treat
schema drift as expected and keep the .get() chains defensive.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# --- config ---------------------------------------------------------------

# sport/league path segments come from ESPN's URL scheme:
#   site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{id}/schedule
TEAMS = [
    {
        "key": "cowboys",
        "label": "Cowboys",
        "sport": "football",
        "league": "nfl",
        "team_id": "6",          # Dallas Cowboys
        "duration_hours": 3.25,
        "season_types": [1, 2, 3],  # pre, regular, post
    },
    {
        "key": "spurs",
        "label": "Spurs",
        "sport": "basketball",
        "league": "nba",
        "team_id": "24",         # San Antonio Spurs
        "duration_hours": 2.5,
        "season_types": [1, 2, 3],  # pre, regular, post
    },
    {
        "key": "longhorns",
        "label": "Longhorns",
        "sport": "football",
        "league": "college-football",
        "team_id": "251",        # Texas Longhorns
        "duration_hours": 3.5,
        "season_types": [2, 3],
    },
    {
        "key": "utsa",
        "label": "UTSA",
        "sport": "football",
        "league": "college-football",
        "team_id": "2636",       # UTSA Roadrunners
        "duration_hours": 3.5,
        "season_types": [2, 3],
    },
]

REFRESH_INTERVAL = "PT6H"
USER_AGENT = "sports-cal/1.0 (personal calendar feed)"
# site.api.espn.com started 403ing non-browser clients (Akamai bot detection,
# verified Sep 2026); site.web.api.espn.com serves the identical API unblocked.
BASE = "https://site.web.api.espn.com/apis/site/v2/sports"

# --- http -----------------------------------------------------------------


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_events(team):
    """Return raw ESPN event dicts for one team, deduped across season types."""
    seen = {}
    for st in team["season_types"]:
        url = (
            f"{BASE}/{team['sport']}/{team['league']}"
            f"/teams/{team['team_id']}/schedule?seasontype={st}"
        )
        try:
            data = get_json(url)
        except Exception as exc:  # one bad season type shouldn't kill the run
            print(f"  warn: {team['key']} seasontype={st}: {exc}", file=sys.stderr)
            continue
        for ev in data.get("events", []) or []:
            if ev.get("id"):
                seen[ev["id"]] = ev
    return list(seen.values())


# --- parsing --------------------------------------------------------------


def parse_dt(value):
    """ESPN returns e.g. '2026-09-13T17:00Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def first_competition(ev):
    comps = ev.get("competitions") or []
    return comps[0] if comps else {}


def venue_string(comp):
    venue = comp.get("venue") or {}
    name = venue.get("fullName")
    addr = venue.get("address") or {}
    city = addr.get("city")
    state = addr.get("state")
    parts = [p for p in (name, city, state) if p]
    return ", ".join(parts)


def broadcast_string(comp):
    """Broadcast info lives in a few different shapes depending on league."""
    names = []
    for b in comp.get("broadcasts") or []:
        media = (b.get("media") or {}).get("shortName")
        if media:
            names.append(media)
        for n in b.get("names") or []:
            names.append(n)
    for b in comp.get("geoBroadcasts") or []:
        media = (b.get("media") or {}).get("shortName")
        if media:
            names.append(media)
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return ", ".join(out)


def matchup(ev, comp):
    """Prefer 'DAL @ NYG'-style short name, fall back to competitor lookup."""
    short = ev.get("shortName")
    if short:
        return short
    sides = {}
    for c in comp.get("competitors") or []:
        team = c.get("team") or {}
        sides[c.get("homeAway")] = team.get("abbreviation") or team.get("displayName")
    if sides.get("away") and sides.get("home"):
        return f"{sides['away']} @ {sides['home']}"
    return ev.get("name") or "Game"


def week_string(ev):
    week = ev.get("week") or {}
    if week.get("text"):
        return week["text"]
    if week.get("number"):
        return f"Week {week['number']}"
    return ""


# --- ics ------------------------------------------------------------------


def esc(text):
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line):
    """RFC 5545 wants lines <= 75 octets, continuations start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, start = [], 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # don't split a multi-byte char
        while end > start and (raw[end - 1] & 0xC0) == 0x80 and end < len(raw):
            end -= 1
        out.append(raw[start:end].decode("utf-8", errors="ignore"))
        start = end
        limit = 74  # continuation lines lose one octet to the leading space
    return "\r\n ".join(out)


def utc_stamp(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build_event(team, ev, now):
    comp = first_competition(ev)
    start = parse_dt(ev.get("date") or comp.get("date"))
    if not start:
        return None

    # No team prefix in the title — each feed is its own calendar, so the
    # matchup alone reads cleaner, especially on a phone's month view.
    title = matchup(ev, comp)
    uid = f"espn-{ev.get('id')}-{team['key']}@sports-cal"

    # DTSTAMP is stamped with the event's start, not build time, so rebuilding
    # an unchanged schedule produces a byte-identical file and the workflow's
    # commit-if-changed check actually skips no-op runs.
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{utc_stamp(start)}"]

    # ESPN sets timeValid=false when kickoff is still TBD — common for college
    # games until the networks pick windows 6-12 days out. All-day event avoids
    # a fake 3am placeholder sitting on the calendar.
    if ev.get("timeValid") is False:
        day = start.date()
        lines.append(f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}")
        title += " (time TBD)"
    else:
        end = start + timedelta(hours=team["duration_hours"])
        lines.append(f"DTSTART:{utc_stamp(start)}")
        lines.append(f"DTEND:{utc_stamp(end)}")

    lines.append(f"SUMMARY:{esc(title)}")

    where = venue_string(comp)
    if where:
        lines.append(f"LOCATION:{esc(where)}")

    desc_bits = []
    if ev.get("name"):
        desc_bits.append(ev["name"])
    wk = week_string(ev)
    if wk:
        desc_bits.append(wk)
    tv = broadcast_string(comp)
    if tv:
        desc_bits.append(f"TV: {tv}")
    if ev.get("id"):
        desc_bits.append(f"https://www.espn.com/{team['league']}/game/_/gameId/{ev['id']}")
    if desc_bits:
        lines.append(f"DESCRIPTION:{esc(chr(10).join(desc_bits))}")

    status = ((comp.get("status") or {}).get("type") or {}).get("state")
    if status == "post":
        lines.append("STATUS:CONFIRMED")

    lines.append("END:VEVENT")
    return lines


def build_calendar(all_events, now, cal_name):
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//sports-cal//ESPN feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(cal_name)}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH_INTERVAL}",
        f"X-PUBLISHED-TTL:{REFRESH_INTERVAL}",
    ]
    for lines in all_events:
        out.extend(lines)
    out.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in out) + "\r\n"


# --- helpers for setup ----------------------------------------------------


def find_teams(needle):
    """Print matching team ids across the leagues we care about."""
    leagues = [
        ("football", "nfl"),
        ("basketball", "nba"),
        ("football", "college-football"),
    ]
    needle = needle.lower()
    for sport, league in leagues:
        url = f"{BASE}/{sport}/{league}/teams?limit=1000"
        try:
            data = get_json(url)
        except Exception as exc:
            print(f"{league}: {exc}", file=sys.stderr)
            continue
        groups = ((data.get("sports") or [{}])[0].get("leagues") or [{}])[0]
        for entry in groups.get("teams") or []:
            team = entry.get("team") or {}
            name = team.get("displayName") or ""
            if needle in name.lower():
                print(f"{league:18} id={team.get('id'):>6}  {name}  "
                      f"[{team.get('abbreviation')}]")


# --- main -----------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="docs")
    ap.add_argument("--find", help="search ESPN team ids by name, then exit")
    ap.add_argument("--dump", help="print raw JSON for one configured team key")
    args = ap.parse_args()

    if args.find:
        find_teams(args.find)
        return

    if args.dump:
        team = next((t for t in TEAMS if t["key"] == args.dump), None)
        if not team:
            sys.exit(f"no team with key {args.dump!r}")
        url = (f"{BASE}/{team['sport']}/{team['league']}"
               f"/teams/{team['team_id']}/schedule")
        print(json.dumps(get_json(url), indent=2)[:20000])
        return

    import os
    now = datetime.now(timezone.utc)
    os.makedirs(args.outdir, exist_ok=True)

    written, empty = [], []
    for team in TEAMS:
        events = fetch_events(team)
        blocks = []
        for ev in sorted(events, key=lambda e: e.get("date") or ""):
            block = build_event(team, ev, now)
            if block:
                blocks.append(block)

        # An empty result usually means ESPN drift or an outage, not a real
        # empty schedule. Skip the write so the last good file stays published
        # rather than wiping subscribers' calendars.
        if not blocks:
            print(f"warn: {team['key']}: no events parsed, keeping existing "
                  f"file — check with --dump {team['key']}", file=sys.stderr)
            empty.append(team["key"])
            continue

        path = os.path.join(args.outdir, f"{team['key']}.ics")
        ics = build_calendar(blocks, now, team["label"])
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(ics)
        written.append(f"{team['label']} {len(blocks)}")

    if not written:
        sys.exit("no events parsed for any team — check the endpoint with --dump")
    print(f"wrote {len(written)} feeds to {args.outdir}/: {', '.join(written)}"
          + (f" (empty: {', '.join(empty)})" if empty else ""))


if __name__ == "__main__":
    main()

# sports-cal

Auto-updating `.ics` feeds for the Cowboys, Spurs, Longhorns, and UTSA —
one feed per team so each is a separate subscription you can toggle
independently — built from ESPN's public (undocumented) JSON endpoints.

## Layout

```
build_ics.py                      # the whole thing
.github/workflows/update-calendar.yml
docs/cowboys.ics                  # generated output, served by GitHub Pages
docs/spurs.ics
docs/longhorns.ics
docs/utsa.ics
```

## First run

```bash
python build_ics.py
```

Stdlib only, no dependencies.

### Verify the team ids before trusting the output

The ids in `TEAMS` are the ones ESPN uses today, but confirm rather than
assume — especially UTSA, where a wrong id fails silently as an empty schedule.

```bash
python build_ics.py --find texas
python build_ics.py --find "san antonio"
```

If a team comes back with zero events, dump the raw response and check whether
the field names moved:

```bash
python build_ics.py --dump utsa | head -60
```

Note: ESPN's `site.api.espn.com` host started rejecting non-browser clients
(Akamai bot detection, seen Sep 2026). The script uses `site.web.api.espn.com`,
which serves the identical API. If every request starts 403ing, that block has
probably spread — check whether another `*.espn.com` API host still answers.

## Publishing

Settings → Pages → deploy from branch `main`, folder `/docs`. The feeds land at:

```
https://<user>.github.io/sports-cal/cowboys.ics
https://<user>.github.io/sports-cal/spurs.ics
https://<user>.github.io/sports-cal/longhorns.ics
https://<user>.github.io/sports-cal/utsa.ics
```

Subscribe to each one separately (File → New Calendar Subscription in Apple
Calendar, four times). Each shows up as its own calendar named after the team,
so you can hide the Spurs during football season with one checkbox and re-show
them in April — unsubscribing is never necessary.

Pages serves `.ics` as `text/calendar`, which every calendar client accepts.
`raw.githubusercontent.com` serves it as `text/plain` — usually fine, but Apple
Calendar is pickier about it, so Pages is the safer host.

Add to Google Calendar: Other calendars → From URL. Apple Calendar: File → New
Calendar Subscription.

## Things that will bite you

**Refresh lag.** The Action rebuilds every 6 hours, but the client controls how
often it re-reads. Google Calendar refreshes external feeds on its own schedule,
often 12–24 hours and sometimes longer, with no way to force it. Apple Calendar
lets you set the interval per subscription — set it to hourly. This matters for
NFL flex scheduling, where a Sunday game can move to Sunday night about a week
out. If you want faster propagation, subscribe in Apple Calendar and let it sync
to your phone rather than subscribing in Google.

**College kickoff times.** Longhorns and UTSA games frequently show as TBD until
the networks pick windows 6–12 days ahead. ESPN flags these with
`timeValid: false`; the script renders them as all-day events with "(time TBD)"
in the title instead of parking a fake 3am block on your calendar. They convert
to timed events automatically once ESPN fills in the kickoff.

**Season boundaries.** The schedule endpoint returns the current season. Once
the NBA season rolls over or the CFP bracket sets, the feed picks it up on the
next run. Add `?season=2027` to backfill or look ahead.

**Volume.** Four teams is roughly 20 + 87 + 12 + 12 ≈ 130 events a year, and
the Spurs are most of that — which is why each team is its own feed: toggle
the Spurs calendar off during football season instead of unsubscribing.

**NBA Cup slots.** ESPN lists 80 of 82 Spurs regular-season games until the
NBA Cup knockout rounds are set in December; the last two appear automatically
once scheduled.

**Undocumented API.** ESPN can change or break these endpoints without notice.
The parsing is defensive (`.get()` chains, per-team failure isolation), so drift
should degrade to missing fields rather than a crash — but if the feed suddenly
empties, that's the first thing to check.

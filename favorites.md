# Favorite Teams

Visitors now pick their own teams in the dashboard's **"My Teams"** bar — those
picks are stored per browser (localStorage), so each person sees their own set.

This file no longer controls *which* teams show. It only provides optional
**styling overrides** for specific teams: a custom flag, display name, card
colour or note. Any team not listed here still works in the picker — it just
uses its default flag (from `groups_data.json`) and the default card colour.

- Add an override by adding a `## Team Name` section.
- The team name **must match** the name used in `groups_data.json`
  (e.g. `United States`, `England`, `Brazil`).
- The card's **status** and **match history** are filled in automatically from
  the live bracket on each refresh — you only set the fields below.

Fields (all optional except the team heading):

- `flag:`  emoji shown before the name
- `label:` display name (defaults to the team name if omitted)
- `color:` one or two hex colours for the card gradient, comma-separated
- `note:`  free-text blurb shown under the stats

## United States
- flag: 🇺🇸
- label: USA
- color: #1d2d50, #3c3b6e
- note: Through to the knockouts — chasing a deep run.

## England
- flag: 🏴󠁧󠁢󠁥󠁮󠁧󠁿
- label: England
- color: #7a0c0c, #a11d1d
- note: Into the knockout rounds — every game is now win-or-go-home.

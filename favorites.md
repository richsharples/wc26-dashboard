# Favorite Teams

Edit this file to control the highlighted cards at the top of the dashboard.

- Add or remove a team by adding/removing a `## Team Name` section.
- The team name **must match** the name used in `groups_data.json`
  (e.g. `United States`, `England`, `Brazil`) so its group and bracket run can be found.
- The card's **status** (alive / out / next match / result / champions) is filled
  in automatically from the live bracket on each refresh — you only set the
  fields below. During the group stage it falls back to the team's group position.

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

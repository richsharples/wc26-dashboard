# Favorite Teams

Edit this file to control the highlighted cards at the top of the dashboard.

- Add or remove a team by adding/removing a `## Team Name` section.
- The team name **must match** the name used in `groups_data.json`
  (e.g. `United States`, `England`, `Brazil`) so its group and stats can be found.
- `Played`, `Pts`, `GD` and `Rank` are filled in automatically from the live
  standings on each refresh — you only set the fields below.

Fields (all optional except the team heading):

- `flag:`  emoji shown before the name
- `label:` display name (defaults to the team name if omitted)
- `color:` one or two hex colours for the card gradient, comma-separated
- `note:`  free-text blurb shown under the stats

## United States
- flag: 🇺🇸
- label: USA
- color: #1d2d50, #3c3b6e
- note: Already through to Round of 32. A win secures Group D top spot outright.

## England
- flag: 🏴󠁧󠁢󠁥󠁮󠁧󠁿
- label: England
- color: #7a0c0c, #a11d1d
- note: Level on points with Ghana. Win or draw vs Panama keeps England top of Group L.

# NBA Pulse

NBA Pulse is a public NBA odds website that shows:
- a live daily NBA odds board
- live moneyline and spread data from DraftKings via The Odds API
- three daily data-driven betting recommendations

## Live Site

Public site:
[https://yuangfu66.github.io/NBA_Pulse/](https://yuangfu66.github.io/NBA_Pulse/)

GitHub Pages should be configured to deploy from the `main` branch using the `/docs` folder.

The embedded odds board loads live data from The Odds API widget whenever someone opens the page. The daily recommendations can now be refreshed automatically by GitHub Actions once `ODDS_API_KEY` is added as a repository secret.

## Project Structure

- `docs/index.html` - public website UI served by GitHub Pages
- `docs/daily-data.json` - generated recommendation data consumed by the site
- `scripts/refresh_nba_data.py` - fetches live NBA odds and generates the daily recommendations

## How It Works

The site uses two data paths:

1. Live odds board
- Embedded with The Odds API widget
- Shows live and upcoming NBA games and spreads

2. Daily recommendations
- Generated from The Odds API JSON endpoint
- Written into `docs/daily-data.json`
- Loaded by the website each time the page opens


## Notes

- Recommendations are data-driven picks based on current odds and basic model rules.
- They are not guarantees or financial advice.
- The scheduled workflow runs daily at 14:05 UTC, which is 7:05 AM PST or 8:05 AM PDT.

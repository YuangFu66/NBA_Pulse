# NBA Pulse

NBA Pulse is a public NBA odds website that shows:
- a live daily NBA odds board
- live moneyline and spread data from DraftKings via The Odds API
- three daily data-driven betting recommendations

## Live Site

Public site:
[https://raw.githack.com/YuangFu66/Project_test/664419ac67c360a41958311f8db9656d3404c1d9/site/index.html](https://raw.githack.com/YuangFu66/Project_test/664419ac67c360a41958311f8db9656d3404c1d9/site/index.html)

## Project Structure

- `site/index.html` - public website UI
- `site/daily-data.json` - generated recommendation data consumed by the site
- `scripts/refresh_nba_data.py` - fetches live NBA odds and generates the daily recommendations
- `.github/workflows/refresh-nba-data.yml` - scheduled workflow that refreshes the data automatically

## How It Works

The site uses two data paths:

1. Live odds board
- Embedded with The Odds API widget
- Shows live and upcoming NBA games and spreads

2. Daily recommendations
- Generated from The Odds API JSON endpoint
- Written into `site/daily-data.json`
- Loaded by the website each time the page opens


## Notes

- Recommendations are data-driven picks based on current odds and basic model rules.
- They are not guarantees or financial advice.


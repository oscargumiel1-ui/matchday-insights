# Matchday Insights

A free website with home and away stats for upcoming matches in La Liga, the Premier League, Serie A, the Bundesliga, Ligue 1, the Eredivisie, the Primeira Liga, the Championship, the Champions League and Brazil's Série A: goals, both-teams-to-score, over 2.5, clean sheets, form, league tables (overall, home and away) and head-to-head. Shots, corners, cards and xG appear when the data source provides them.

Everything runs for free on GitHub. Once a day, a script fetches the latest results from a football data API, works out the averages, and saves them to `data/matches.json`. The website just reads that file, so visitors never use up your API requests and your API key stays private.

The project ships with demo data, so the page works as soon as it's online. The demo numbers are invented and the page labels them as such.

## What's in here

| File | What it does |
|---|---|
| `index.html` | The website |
| `data/matches.json` | The stats the website shows (rewritten every day) |
| `data/cache.json` | Match stats already downloaded, so they're never fetched twice (created on the first real run) |
| `scripts/update.py` | The daily update script. Plain Python, nothing to install |
| `.github/workflows/update-data.yml` | Runs the script every morning on GitHub |

## Setup (about 15 minutes, all free)

### 1. Get the free API keys

1. **API-Football** (main source, includes shots, corners and xG): sign up at <https://dashboard.api-football.com/register>. No credit card needed. Copy your API key from the dashboard.
2. **football-data.org** (backup source, goals only): register at <https://www.football-data.org/client/register>. The key arrives by email.

Getting both is best. If the API-Football free plan doesn't cover the current season, the script switches to football-data.org on its own and the page shows goal-based stats only.

### 2. Put the project on GitHub

1. Create a free account at <https://github.com> if you don't have one.
2. Click **New repository**, name it (for example `matchday-insights`), make it **Public**, and create it.
3. On the new repository page, click **uploading an existing file**, drag in all the files and folders from this project, and click **Commit changes**.
   - The `.github` folder can be hidden on Mac and Windows. On Mac press `Cmd + Shift + .` in Finder to show it. If drag-and-drop skips it, create the file by hand: **Add file → Create new file**, type `.github/workflows/update-data.yml` as the name, and paste in the contents.

### 3. Add your keys as secrets

In the repository: **Settings → Secrets and variables → Actions → New repository secret**. Add:

- Name `APIFOOTBALL_KEY`, value: your API-Football key
- Name `FOOTBALL_DATA_KEY`, value: your football-data.org key

Secrets are hidden. They never appear on the website or in the code.

### 4. Turn on the website

**Settings → Pages**. Under *Build and deployment*, choose **Deploy from a branch**, branch **main**, folder **/ (root)**, then **Save**. After a minute your site is live at `https://YOUR-USERNAME.github.io/matchday-insights/`.

### 5. Load real data

Go to the **Actions** tab, click **Update match data**, then **Run workflow**. It takes up to 15 minutes because it waits between requests to stay inside the free limits. When it finishes, reload your site: the demo banner is gone and real fixtures appear.

From then on it updates by itself every morning.

## How it stays inside the free limits

- **API-Football, 100 requests/day.** One request per league loads every fixture and result of the season (4 in total). Head-to-heads for the next 3 days come next, then shots and corners for finished matches, newest first, up to 95 requests a day. Downloaded stats are saved in `data/cache.json` and never fetched again.
  - On the first days the script is catching up on the whole season, so shot and corner averages are based on fewer matches. The page says so. With roughly 40 matches a week across the four leagues, it catches up within a few days and then stays current.
- **football-data.org, 10 requests/minute.** The script waits 6.5 seconds between calls. One call per league for fixtures, one per league for tables, and one head-to-head per upcoming match (cached for a week). The first run with all ten leagues takes about 15 to 20 minutes; later runs are faster.

## Settings you can change

Set these as environment variables in the workflow file if you want:

| Variable | Default | Meaning |
|---|---|---|
| `PROVIDER` | `auto` | `auto`, `api-football` or `football-data` |
| `SEASON` | current | Season start year, e.g. `2026` for 2026/27 |
| `UPCOMING_DAYS` | `7` | How many days ahead to list fixtures |
| `APIF_DAILY_BUDGET` | `95` | Max API-Football requests per run |

## Try it on your computer

```bash
python3 scripts/update.py --demo     # invented data, no key needed
APIFOOTBALL_KEY=yourkey python3 scripts/update.py   # real data
python3 -m http.server 8000          # then open http://localhost:8000
```

Opening `index.html` by double-clicking won't load the data, because browsers block reading local files. Use the small server above.

## Adding a league

Add a line to `LEAGUES` in `scripts/update.py` with a short code, the name, the API-Football league ID and the football-data.org code. For example Serie B: `"sb": ("Serie B", 136, "SB")`. A league the free plan doesn't cover is skipped with a warning; the rest still update. If its season runs within one calendar year, add its code to `CALENDAR_YEAR_LEAGUES`.

## Responsible use

The page shows past performance only. It includes an 18+ notice and a link to responsible gambling help. Keep it if you publish the site.

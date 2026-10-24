#!/usr/bin/env python3
"""
Matchday Insights - daily data update.

Pulls fixtures, results and match stats for La Liga, Premier League, Serie A
and Bundesliga, then writes data/matches.json for the website.

Data sources (both free):
  * API-Football (api-football.com)  - goals + shots, corners, cards, xG.
    Free plan: 100 requests/day. Set APIFOOTBALL_KEY.
  * football-data.org                - goals only (no shots/corners on free).
    Free plan: 10 requests/minute. Set FOOTBALL_DATA_KEY.

PROVIDER=auto (default) uses API-Football if its key works for the current
season, otherwise falls back to football-data.org.

Only the Python standard library is used, so no installs are needed.

Usage:
  python scripts/update.py            # real run (needs a key)
  python scripts/update.py --demo     # invented data, no key, for testing
"""
import json, os, random, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_FILE = os.path.join(DATA_DIR, "matches.json")
CACHE_FILE = os.path.join(DATA_DIR, "cache.json")

# code: (name, API-Football league id, football-data.org code)
LEAGUES = {
    "ll": ("La Liga", 140, "PD"),
    "pl": ("Premier League", 39, "PL"),
    "sa": ("Serie A", 135, "SA"),
    "bl": ("Bundesliga", 78, "BL1"),
}
UPCOMING_DAYS = int(os.environ.get("UPCOMING_DAYS", "7"))
APIF_DAILY_BUDGET = int(os.environ.get("APIF_DAILY_BUDGET", "95"))  # keep 5 spare of the 100
CALL_GAP_SECONDS = float(os.environ.get("CALL_GAP_SECONDS", "6.5"))  # stays under 10 calls/minute
H2H_REFRESH_DAYS = 7
FINISHED_APIF = {"FT", "AET", "PEN"}
NOW = datetime.now(timezone.utc)


def log(*a):
    print(*a, flush=True)


def gh_note(level, msg):
    """Show a message on the GitHub Actions run page (level: notice, warning, error)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level}::{msg}", flush=True)


def stop(msg):
    gh_note("error", msg)
    sys.exit(msg)


def current_season():
    """Season label as both APIs use it: the year the season starts (2026 for 2026/27)."""
    env = os.environ.get("SEASON")
    if env:
        return int(env)
    return NOW.year if NOW.month >= 7 else NOW.year - 1


# ---------------------------------------------------------------- HTTP

class PlanError(Exception):
    """The API answered, but the free plan doesn't cover what we asked for."""


class Http:
    def __init__(self, base, headers, budget=None):
        self.base, self.headers, self.budget = base, headers, budget
        self.calls, self._last = 0, 0.0

    def left(self):
        return None if self.budget is None else self.budget - self.calls

    def get(self, path, params=None):
        if self.budget is not None and self.calls >= self.budget:
            raise RuntimeError("daily request budget used up")
        wait = CALL_GAP_SECONDS - (time.time() - self._last)
        if wait > 0 and self.calls:
            time.sleep(wait)
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers=self.headers)
        for attempt in range(3):
            try:
                self._last = time.time()
                self.calls += 1
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")[:300]
                if e.code == 429 and attempt < 2:
                    log("  rate limited, waiting 65s")
                    time.sleep(65)
                    continue
                if e.code in (401, 403):
                    raise PlanError(f"HTTP {e.code}: {body}")
                raise RuntimeError(f"HTTP {e.code} for {path}: {body}")
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(10)
                    continue
                raise RuntimeError(f"network error for {path}: {e}")


# ---------------------------------------------------------------- cache

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            c = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        c = {}
    c.setdefault("stats", {})
    c.setdefault("h2h", {})
    return c


def save_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
        else:
            json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


# Every provider returns matches in this common shape:
#   {id, league, date (ISO), status: "finished"|"upcoming"|"other",
#    home, away, hid, aid, hg, ag}
# Match stats (shots etc.) live in cache["stats"][id] = {"home": {...}, "away": {...}}


# ---------------------------------------------------------------- API-Football

class ApiFootball:
    name = "API-Football"
    has_stats = True

    def __init__(self, key, season):
        self.http = Http("https://v3.football.api-sports.io",
                         {"x-apisports-key": key}, budget=APIF_DAILY_BUDGET)
        self.season = season

    def _get(self, path, params):
        data = self.http.get(path, params)
        errs = data.get("errors")
        if errs:
            text = json.dumps(errs)
            if any(w in text.lower() for w in ("plan", "season", "subscription", "token", "key")):
                raise PlanError(text)
            raise RuntimeError(f"API-Football error on {path}: {text}")
        return data.get("response", [])

    def fixtures(self):
        out = []
        for code, (_, lid, _) in LEAGUES.items():
            log(f"  fixtures: {LEAGUES[code][0]}")
            for it in self._get("/fixtures", {"league": lid, "season": self.season}):
                st = it["fixture"]["status"]["short"]
                out.append({
                    "id": f"af{it['fixture']['id']}", "raw_id": it["fixture"]["id"], "league": code,
                    "date": it["fixture"]["date"],
                    "status": "finished" if st in FINISHED_APIF else "upcoming" if st in ("NS", "TBD") else "other",
                    "home": it["teams"]["home"]["name"], "away": it["teams"]["away"]["name"],
                    "hid": it["teams"]["home"]["id"], "aid": it["teams"]["away"]["id"],
                    "hg": it["goals"]["home"], "ag": it["goals"]["away"],
                })
        return out

    def match_stats(self, m):
        resp = self._get("/fixtures/statistics", {"fixture": m["raw_id"]})
        res = {}
        for side in resp:
            s = {x["type"]: x["value"] for x in side.get("statistics", [])}
            key = "home" if side["team"]["id"] == m["hid"] else "away"
            res[key] = {
                "shots": num(s.get("Total Shots")),
                "sot": num(s.get("Shots on Goal")),
                "corners": num(s.get("Corner Kicks")),
                "cards": add(num(s.get("Yellow Cards"), zero=True), num(s.get("Red Cards"), zero=True)),
                "xg": num(s.get("expected_goals")),
            }
        return res if len(res) == 2 else None

    def h2h(self, m):
        resp = self._get("/fixtures/headtohead", {"h2h": f"{m['hid']}-{m['aid']}"})
        items = [{"date": r["fixture"]["date"][:10], "home": r["teams"]["home"]["name"],
                  "away": r["teams"]["away"]["name"], "hg": r["goals"]["home"], "ag": r["goals"]["away"]}
                 for r in resp if r["goals"]["home"] is not None]
        return sorted(items, key=lambda x: x["date"], reverse=True)[:5]


# ---------------------------------------------------------------- football-data.org

class FootballData:
    name = "football-data.org"
    has_stats = False

    def __init__(self, key, season):
        self.http = Http("https://api.football-data.org/v4", {"X-Auth-Token": key})
        self.season = season

    def fixtures(self):
        out = []
        for code, (_, _, fd) in LEAGUES.items():
            log(f"  fixtures: {LEAGUES[code][0]}")
            data = self.http.get(f"/competitions/{fd}/matches", {"season": self.season})
            for it in data.get("matches", []):
                st = it["status"]
                ft = it["score"]["fullTime"]
                out.append({
                    "id": f"fd{it['id']}", "raw_id": it["id"], "league": code, "date": it["utcDate"],
                    "status": "finished" if st == "FINISHED" else "upcoming" if st in ("SCHEDULED", "TIMED") else "other",
                    "home": it["homeTeam"].get("shortName") or it["homeTeam"]["name"],
                    "away": it["awayTeam"].get("shortName") or it["awayTeam"]["name"],
                    "hid": it["homeTeam"]["id"], "aid": it["awayTeam"]["id"],
                    "hg": ft["home"], "ag": ft["away"],
                })
        return out

    def match_stats(self, m):
        return None

    def h2h(self, m):
        data = self.http.get(f"/matches/{m['raw_id']}/head2head", {"limit": 5})
        items = []
        for r in data.get("matches", []):
            ft = r["score"]["fullTime"]
            if r["status"] != "FINISHED" or ft["home"] is None:
                continue
            items.append({"date": r["utcDate"][:10],
                          "home": r["homeTeam"].get("shortName") or r["homeTeam"]["name"],
                          "away": r["awayTeam"].get("shortName") or r["awayTeam"]["name"],
                          "hg": ft["home"], "ag": ft["away"]})
        return sorted(items, key=lambda x: x["date"], reverse=True)[:5]


# ---------------------------------------------------------------- demo provider

class Demo:
    """Invented data that runs through the same pipeline. For testing only."""
    name = "Demo data (invented)"
    has_stats = True
    TEAMS = {
        "ll": ["Real Madrid", "Barcelona", "Atlético Madrid", "Villarreal", "Real Sociedad", "Sevilla"],
        "pl": ["Arsenal", "Liverpool", "Newcastle", "Aston Villa", "Brighton", "Brentford"],
        "sa": ["Inter", "Napoli", "Atalanta", "Roma", "Juventus", "Lazio"],
        "bl": ["Bayern Munich", "Leverkusen", "Dortmund", "Freiburg", "Leipzig", "Stuttgart"],
    }

    def __init__(self, season):
        self.r = random.Random(7)
        self.season = season
        self.str = {t: 0.95 - i * 0.08 for teams in self.TEAMS.values() for i, t in enumerate(teams)}

    def fixtures(self):
        out, n = [], 0
        start = NOW - timedelta(days=70)
        for code, teams in self.TEAMS.items():
            rounds = [(h, a) for h in teams for a in teams if h != a]
            self.r.shuffle(rounds)
            for i, (h, a) in enumerate(rounds[:24]):
                d = (start + timedelta(days=i * 3)).replace(hour=self.r.choice([13, 16, 19]), minute=0, second=0, microsecond=0)
                fin = d < NOW - timedelta(hours=3)
                sh, sa = self.str[h] + 0.15, self.str[a]
                hg = sum(self.r.random() < sh * 0.42 for _ in range(5)) if fin else None
                ag = sum(self.r.random() < sa * 0.36 for _ in range(5)) if fin else None
                n += 1
                out.append({"id": f"demo{n}", "raw_id": n, "league": code, "date": d.isoformat(),
                            "status": "finished" if fin else "upcoming", "home": h, "away": a,
                            "hid": h, "aid": a, "hg": hg, "ag": ag})
            # make sure every league has fixtures in the coming days
            for k in range(2):
                h, a = teams[k * 2], teams[k * 2 + 3]
                n += 1
                d = (NOW + timedelta(days=1 + k)).replace(hour=18 + k, minute=30, second=0, microsecond=0)
                out.append({"id": f"demo{n}", "raw_id": n, "league": code, "date": d.isoformat(),
                            "status": "upcoming", "home": h, "away": a, "hid": h, "aid": a, "hg": None, "ag": None})
        return out

    def match_stats(self, m):
        def side(t, g, bonus):
            s = self.str[t] + bonus
            shots = round(7 + s * 10 + self.r.random() * 5)
            return {"shots": shots, "sot": max(g, round(shots * (0.28 + self.r.random() * 0.12))),
                    "corners": round(2 + s * 5 + self.r.random() * 3), "cards": self.r.randint(0, 4),
                    "xg": round(max(0.1, g * 0.7 + s * 0.8 + self.r.random() * 0.4), 2)}
        return {"home": side(m["home"], m["hg"], 0.1), "away": side(m["away"], m["ag"], 0)}

    def h2h(self, m):
        out = []
        for i in range(5):
            swap = i % 2 == 1
            out.append({"date": f"{self.season - 1 - i // 2}-{3 + i:02d}-14",
                        "home": m["away"] if swap else m["home"], "away": m["home"] if swap else m["away"],
                        "hg": self.r.randint(0, 3), "ag": self.r.randint(0, 2)})
        return sorted(out, key=lambda x: x["date"], reverse=True)


# ---------------------------------------------------------------- helpers

def num(v, zero=False):
    if v is None:
        return 0 if zero else None
    if isinstance(v, str):
        v = v.strip().rstrip("%")
        try:
            v = float(v)
        except ValueError:
            return 0 if zero else None
    return v


def add(a, b):
    return None if a is None or b is None else a + b


def pair_key(prov, m):
    a, b = sorted([str(m["hid"]), str(m["aid"])])
    return f"{type(prov).__name__}:{a}-{b}"


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def pct(flags):
    return round(100 * sum(flags) / len(flags)) if flags else None


def team_stats(team, finished, stats_cache, venue):
    """Per-game averages for `team`. venue: 'home', 'away' or 'all'."""
    rows = []
    for m in finished:
        if venue in ("home", "all") and m["home"] == team:
            rows.append((m, "home", m["hg"], m["ag"]))
        elif venue in ("away", "all") and m["away"] == team:
            rows.append((m, "away", m["ag"], m["hg"]))
    if not rows:
        return None
    mine, theirs = [], []
    for m, side, _, _ in rows:
        s = stats_cache.get(m["id"])
        if s:
            mine.append(s[side])
            theirs.append(s["away" if side == "home" else "home"])
    gf = [r[2] for r in rows]
    ga = [r[3] for r in rows]
    return {
        "gp": len(rows),
        "gf": avg(gf), "ga": avg(ga),
        "xg": avg([s.get("xg") for s in mine]), "xga": avg([s.get("xg") for s in theirs]),
        "shots": avg([s.get("shots") for s in mine]), "sot": avg([s.get("sot") for s in mine]),
        "corners": avg([s.get("corners") for s in mine]), "cards": avg([s.get("cards") for s in mine]),
        "statsGp": len(mine),
        "btts": pct([f > 0 and a > 0 for f, a in zip(gf, ga)]),
        "o25": pct([f + a > 2 for f, a in zip(gf, ga)]),
        "cs": pct([a == 0 for a in ga]),
        "fts": pct([f == 0 for f in gf]),
    }


def form(team, finished):
    games = [m for m in finished if team in (m["home"], m["away"])]
    out = []
    for m in games[-5:]:
        f, a = (m["hg"], m["ag"]) if m["home"] == team else (m["ag"], m["hg"])
        out.append("W" if f > a else "D" if f == a else "L")
    return out


# ---------------------------------------------------------------- main

def pick_provider(season, demo):
    if demo:
        p = Demo(season)
        return p, p.fixtures()
    want = os.environ.get("PROVIDER", "auto")
    afk, fdk = os.environ.get("APIFOOTBALL_KEY"), os.environ.get("FOOTBALL_DATA_KEY")
    if want in ("auto", "api-football") and afk:
        p = ApiFootball(afk, season)
        try:
            return p, p.fixtures()
        except PlanError as e:
            log(f"API-Football can't serve season {season} on this plan: {e}")
            if want == "api-football" or not fdk:
                stop(f"API-Football refused season {season} ({e}). Add a FOOTBALL_DATA_KEY secret to fall back to football-data.org.")
            gh_note("warning", f"API-Football refused season {season}: {e}. Using football-data.org instead.")
            log("Falling back to football-data.org (goals-based stats only).")
    if fdk:
        p = FootballData(fdk, season)
        return p, p.fixtures()
    stop("No API key found. Add a repository secret named exactly APIFOOTBALL_KEY (or FOOTBALL_DATA_KEY).")


def main():
    demo = "--demo" in sys.argv
    season = current_season()
    prov, matches = pick_provider(season, demo)
    log(f"Source: {prov.name} | season {season}/{str(season + 1)[2:]}")

    cache = {"stats": {}, "h2h": {}} if demo else load_cache()
    matches.sort(key=lambda m: m["date"])
    finished = [m for m in matches if m["status"] == "finished" and m["hg"] is not None]
    horizon = NOW + timedelta(days=UPCOMING_DAYS)
    upcoming = [m for m in matches if m["status"] == "upcoming"
                and NOW - timedelta(hours=2) <= datetime.fromisoformat(m["date"].replace("Z", "+00:00")) <= horizon]
    log(f"{len(finished)} finished matches, {len(upcoming)} upcoming in the next {UPCOMING_DAYS} days")

    def budget_left():
        left = prov.http.left() if hasattr(prov, "http") else None
        return 10**9 if left is None else left

    def fetch_h2h(ms, reserve=0):
        for m in ms:
            key = pair_key(prov, m)
            hit = cache["h2h"].get(key)
            if hit and (NOW - datetime.fromisoformat(hit["fetched"])).days < H2H_REFRESH_DAYS:
                continue
            if budget_left() <= reserve:
                return
            try:
                cache["h2h"][key] = {"fetched": NOW.isoformat(), "items": prov.h2h(m)}
            except Exception as e:
                log(f"  h2h failed for {m['home']} v {m['away']}: {e}")

    # 1) head-to-head for the next 3 days first, keeping a reserve for match stats
    soon = [m for m in upcoming if datetime.fromisoformat(m["date"].replace("Z", "+00:00")) <= NOW + timedelta(days=3)]
    fetch_h2h(soon, reserve=30 if prov.has_stats else 0)

    # 2) match stats for finished games we haven't stored yet (newest first)
    if prov.has_stats:
        todo = [m for m in reversed(finished) if m["id"] not in cache["stats"]]
        done = 0
        for m in todo:
            if budget_left() <= 0:
                break
            try:
                s = prov.match_stats(m)
                cache["stats"][m["id"]] = s or {"home": {}, "away": {}}
                done += 1
            except Exception as e:
                log(f"  stats failed for {m['home']} v {m['away']}: {e}")
                if "budget" in str(e):
                    break
        log(f"Match stats: fetched {done}, {len(todo) - done} still to backfill")

    # 3) remaining head-to-heads if budget allows
    fetch_h2h(upcoming)

    # Build output
    stats_cache = {k: v for k, v in cache["stats"].items() if v.get("home")}
    out_matches = []
    for m in upcoming:
        key = pair_key(prov, m)
        lg = [x for x in finished if x["league"] == m["league"]]
        out_matches.append({
            "id": m["id"], "league": m["league"], "kickoff": m["date"],
            "home": {"name": m["home"], "venue": team_stats(m["home"], lg, stats_cache, "home"),
                     "overall": team_stats(m["home"], lg, stats_cache, "all"), "form": form(m["home"], lg)},
            "away": {"name": m["away"], "venue": team_stats(m["away"], lg, stats_cache, "away"),
                     "overall": team_stats(m["away"], lg, stats_cache, "all"), "form": form(m["away"], lg)},
            "h2h": cache["h2h"].get(key, {}).get("items", []),
        })

    pending = len([m for m in finished if m["id"] not in cache["stats"]]) if prov.has_stats else 0
    result = {
        "updated": NOW.isoformat(timespec="minutes"),
        "source": prov.name, "demo": demo,
        "season": f"{season}/{str(season + 1)[2:]}",
        "hasMatchStats": prov.has_stats, "statsBackfillPending": pending,
        "leagues": {k: v[0] for k, v in LEAGUES.items()},
        "matches": out_matches,
    }
    save_json(OUT_FILE, result)
    # Same data as a script, so the page also works when opened straight from a folder
    with open(os.path.join(DATA_DIR, "matches.js"), "w") as f:
        f.write("window.MATCHDAY_DATA=" + json.dumps(result, separators=(",", ":"), ensure_ascii=False) + ";\n")
    if not demo:
        save_json(CACHE_FILE, cache, compact=True)
    calls = prov.http.calls if hasattr(prov, "http") else 0
    log(f"Wrote {len(out_matches)} matches to data/matches.json using {calls} API calls")
    gh_note("notice", f"{prov.name}: {len(out_matches)} upcoming matches, {len(finished)} finished, "
                      f"{pending} still waiting for shot stats, {calls} API calls used.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        stop(f"Update failed: {type(e).__name__}: {e}")

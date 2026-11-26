#!/usr/bin/env python3
"""
deal_alerts.py -- a short push to the owner's phone when Blizzard's hourly auction snapshot shows a listing that is genuinely worth buying.

In plain words (the owner, 24 Nov 2026: "that hourly data, can we have it sent to our phone if on the hour there's something particularly
worthwhile buying?"):
After each new hourly snapshot of the owner's auction house (blizzard_prices.py), this looks at the cheapest listing of every item and keeps only
the few that pass every proven rule below. At most 3 go out per snapshot, as ONE push to the free ntfy app on the owner's phone. Nothing is ever
bought: the push only says what to look at next time he is in the game. The snapshot can be up to an hour old, so a listing may be gone.

The rules (each one is evidence or the owner's own policy, not a guess):
  1. Price is 0.5x to 0.8x TSM's market value for the owner's auction house. The tip grader's only PROVEN WINNER (23 Nov 2026: 160 tips, 71% sold
     within 24 h; 1.5x TSM or more sold 32%). Under 0.5x is left out on purpose: not proven, and where troll listings and wrong TSM figures sit.
  2. It really is cheap: the next other listing is at least 10% dearer (the sniper's "market moved" rule, pricing rulebook 2.5).
  3. Sell price = never undercut, match the wall (pricing rulebook P1/P2): the cheapest price held by 2 or more of the OTHER listings (stacks: a
     tenth of the units); no wall = the cheapest other listing; nobody else listed = TSM's market value. Then held at 1.25x TSM (2x for
     random-suffix gear), the same TSM hold Hoard uses (SPEC 231).
  4. Profit after the 5% auction cut: at least 1 gold and at least 15% on what it costs (0.8x TSM sold at TSM makes 18.75%).
  5. Sells within 1 day (the owner, 23 Nov: Sells within default 1d): Blizzard's own sales estimate for this item (or roll) is at least the quantity
     bought a day. Until Blizzard has 12 hours of snapshots: plain items use TSM's EU-wide "sold per day" (3x the quantity, since that figure
     covers every Forever auction house); random-suffix gear waits for Blizzard's figures.
  6. Gear under required level 15 never alerts (rulebook P8); level 25+ ranks ahead of 15-24 (the owner, 22 Nov: "level 25+ first").
  7. Ranked by profit per sale (rulebook P5). At most 3 a snapshot, at most 24 a day, one item at most every 12 hours, a listing never twice.
  8. Quiet 23:00 to 07:00 UK time (deals found then are not saved up: by morning they are stale).

Delivery: ntfy (https://ntfy.sh, free, no account). The PC posts to a private topic name made once at random on this PC
(tools\\data\\alerts\\settings.json, git-ignored); the phone's ntfy app subscribes to that name. Anyone who knows the name could read the
pushes, so it is long and random; the pushes hold only item names and prices.
Since 24 Nov 2026 the same rules also run in the cloud, every hour, on GitHub (blizzard_cloud.py), so alerts keep coming while the PC is
off. While the cloud's alerts are live (tools\\data\\alerts\\cloud.json, written by the PC's sync), the PC sends none, and it has already
learnt which listings the cloud pushed; when the cloud stops for 2.5 hours, the PC takes over again by itself.

BIG TICKETS (the owner, 25 Nov 2026, "sure all" to: "a phone ping when a level 40+ BoE item is listed at under half its real price". His end goal:
buy expensive bind-on-equip gear when it is posted cheap, relist it high, wait a week). Its own push, its own rules, checked before the deals above:
  B1. Bind-on-equip weapon or armour (tools\\recipes\\raw_nd\\gear_items.csv "bond" 2) that needs level 40 or more (Hoard's item table, else
      item level - 5, as rule 6). One item, not a stack.
  B2. The real price is the freshest one that is not one seller's word:
        a. the price wall on the auction house now: the cheapest price held by 2+ OTHER listings that were posted at different times (auction
           ids more than BURST_IDS apart, about 5 minutes of the house's postings, or first seen in different snapshots). A pile posted
           together (the owner's 11 helms at 20g) is one seller flooding: it is neither a price nor a crowd in front of one. When lone listings
           under the wall are as many as the wall itself, there is no clear wall;
        b. else what it really went for: the middle price of the listings of this item (this roll) that vanished early in the last 7 days
           (Blizzard's sold log, 2 or more);
        c. else TSM's price for your auction house (plain items only: TSM mixes every roll of suffix gear into one figure).
      Then held at Hoard's TSM hold (1.25x TSM, 2x for suffix gear, SPEC 231), as every other sell price. No figure = no alert (never invented).
  B3. The listing costs at most half that real price, and makes at least BIG_MIN_PROFIT after the 5% cut.
  B4. It sells within a week (the owner: "wait a week"): Blizzard's sales count for the item (any roll) is at least 1 a week, or 2+ went in the
      sold log, or (before Blizzard has watched 12 hours) TSM's EU-wide sold per day / 3 is at least 1 a week.
  B5. At most 2 a snapshot and 10 a day, one item at most every 12 hours, a listing never twice (shared with the deals above), same quiet hours.
  Settings (settings.json): "big_on" (false = off), "big_min_level" (40), "big_min_profit" (copper).

Rules for the code (tests: tools/tests/test_deal_alerts.py): never raises and never fails or marks as stuck an auto-update run; never buys;
only acts on a snapshot it has not looked at, under 2 hours old; settings "on": false turns it off.

By hand (prints what it would send, sends nothing):  python tools\\deal_alerts.py --show
"""
import csv
import gzip
import json
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
DATA_DIR = THIS_DIR / "data"
GEAR_CSV = THIS_DIR / "recipes" / "raw_nd" / "gear_items.csv"       # every green-or-better weapon/armour piece of WoW: Forever (wago.tools DB2, 22 Nov)
NTFY = "https://ntfy.sh"
USER_AGENT = "HoardDealAlerts/1"
TIMEOUT = 20

CUT = 0.05                              # the auction house's cut of every sale
TSM_LO, TSM_HI = 0.5, 0.8               # rule 1: the proven-winner band
NEXT_GAP = 1.10                         # rule 2
HOLD_PLAIN, HOLD_SUFFIX = 1.25, 2.0     # rule 3: Hoard's TSM hold (SPEC 231)
MIN_PROFIT = 10000                      # rule 4: 1 gold, copper
MIN_RETURN = 0.15
REGION_FACTOR = 3                       # rule 5: TSM's region "sold per day" covers every EU Forever house
MIN_REQ, GOOD_REQ = 15, 25              # rule 6
LOW_LEVEL_WEIGHT = 0.5
MAX_PER_SNAPSHOT, MAX_PER_DAY = 3, 24   # rule 7
KEY_COOLDOWN = 12 * 3600
SENT_KEEP = 3 * 86400
QUIET = (23, 7)                         # rule 8: UK hours
MAX_AGE = 2 * 3600                      # a snapshot older than this is not alerted on

BIG_MIN_REQ = 40                        # big tickets B1
BIND_ON_EQUIP = 2                       # gear_items.csv "bond" (Enum.ItemBind)
BURST_IDS = 60000                       # B2a: Blizzard hands out about 11,000 auction ids a minute (sold log, 24 Nov 2026): 60,000 = about 5 minutes
BIG_SOLD_DAYS, BIG_SOLD_MIN = 7, 2      # B2b
BIG_RATIO = 0.5                         # B3
BIG_MIN_PROFIT = 20000                  # B3: 2 gold, copper (24 Nov 18:23 snapshot: 16 listings passed at 2g, 7 of them gone within a day; 9% of all 40+ BoE listings went)
BIG_WEEK = 7.0                          # B4
BIG_MAX_PER_SNAPSHOT, BIG_MAX_PER_DAY = 2, 10   # B5

DEFAULTS = {"on": True, "max_per_snapshot": MAX_PER_SNAPSHOT, "max_per_day": MAX_PER_DAY, "min_profit": MIN_PROFIT,
            "quiet_from": QUIET[0], "quiet_to": QUIET[1], "max_price": 0,
            "big_on": True, "big_min_level": BIG_MIN_REQ, "big_min_profit": BIG_MIN_PROFIT}


# ---------------------------------------------------------------------------
# small helpers

def _read_json(path, default=None):
    try:
        p = Path(path)
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return default


def _write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _int0(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def money(c):
    """copper -> "12g 50s" (copper shown only under 1 gold)."""
    c = int(round(c))
    g, s, cc = c // 10000, (c // 100) % 100, c % 100
    if g:
        return f"{g}g {s:02d}s"
    if s:
        return f"{s}s {cc:02d}c"
    return f"{cc}c"


def _last_sunday(year, month, hour_utc):
    import calendar
    day = max(d for d in range(1, calendar.monthrange(year, month)[1] + 1) if calendar.weekday(year, month, d) == 6)
    return calendar.timegm((year, month, day, hour_utc, 0, 0))


def uk_time(t):
    """(hour, "HH:MM") in UK time: GMT, or BST from the last Sunday of March to the last Sunday of October (01:00 UTC both). No tz database
    needed (Windows Python has none without the tzdata package)."""
    y = time.gmtime(t).tm_year
    bst = _last_sunday(y, 3, 1) <= t < _last_sunday(y, 10, 1)
    lt = time.gmtime(t + (3600 if bst else 0))
    return lt.tm_hour, f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


def quiet_now(t, settings):
    h, _ = uk_time(t)
    a, b = int(settings.get("quiet_from", QUIET[0])), int(settings.get("quiet_to", QUIET[1]))
    if a == b:
        return False
    return (a <= h or h < b) if a > b else (a <= h < b)


# ---------------------------------------------------------------------------
# what is known about items

def load_gear(path=None):
    """{itemID: (name, ilvl)} for every green-or-better weapon/armour piece (the committed Forever item table)."""
    out = {}
    try:
        with open(path or GEAR_CSV, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    out[int(r["id"])] = (r.get("name") or "", int(r["ilvl"]))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return out


def load_boe(path=None):
    """{itemID} of every bind-on-equip weapon or armour piece in the committed Forever item table ("bond" 2)."""
    out = set()
    try:
        with open(path or GEAR_CSV, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    if int(r["bond"]) == BIND_ON_EQUIP:
                        out.add(int(r["id"]))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return out


def load_sold_prices(data_dir, now, want=None, days=BIG_SOLD_DAYS):
    """{itemKey: [unit price of each listing that vanished early]} from Blizzard's sold log (data\\blizzard\\sold\\<day>.json.gz,
    rows [auctionId, key, qty, unit, firstSeen, goneBy, low]) over the last `days` days. want(key) -> bool keeps the reading small."""
    out = {}
    folder = Path(data_dir) / "blizzard" / "sold"
    for i in range(days):
        day = time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
        doc = _read_json(folder / f"{day}.json.gz", None)
        rows = doc.get("s") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            continue
        for r in rows:
            try:
                key, qty, unit, gone = str(r[1]), int(r[2]), int(r[3]), int(r[5])
            except (TypeError, ValueError, IndexError):
                continue
            if unit <= 0 or qty != 1 or gone > now or (want and not want(key)):
                continue
            out.setdefault(key, []).append(unit)
    return out


def load_req(hoard_dir):
    """{itemID: required level} for random-suffix gear, from Hoard's own ItemFacts_Data.lua (exact, researched 6 Oct)."""
    out = {}
    try:
        text = (Path(hoard_dir) / "ItemFacts_Data.lua").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for m in re.finditer(r"^(\d+) \d+ \d+ \d+ (-?\d+)\s*$", text, re.M):
        if int(m.group(2)) >= 0:
            out[int(m.group(1))] = int(m.group(2))
    return out


def load_suffix_names(hoard_dir):
    """{number: "of the Eagle"} from Hoard's own tables (SuffixValue.lua names, SuffixStats_Data.lua names, and each bonus list's suffix), so
    a roll can be named whichever of the game's numberings Blizzard's "rand" turns out to use. Unknown = the push says "(random roll)"."""
    names, bonus = {}, {}
    for fn in ("SuffixValue.lua", "SuffixStats_Data.lua"):
        try:
            text = (Path(hoard_dir) / fn).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"\[(\d+)\]\s*=\s*\"(of [^\"]{2,40})\"", text):
            names.setdefault(int(m.group(1)), m.group(2))
        for m in re.finditer(r"\[(\d+)\]\s*=\s*\{?\s*(\d{4,6})\b", text):
            bonus.setdefault(int(m.group(1)), int(m.group(2)))
    for b, sid in bonus.items():
        if sid in names:
            names.setdefault(b, names[sid])
    return names


def suffix_name(key, names):
    try:
        rand = int(str(key).split(":")[1])
    except (IndexError, ValueError):
        return None
    return names.get(rand) or names.get(abs(rand))


def required_level(iid, gear, req):
    """(required level or None, exact?) for gear; None for anything that is not gear. Not in Hoard's table: item level - 5, which is the
    exact answer for 95% of the 1,704 suffix items both tables know (checked 24 Nov 2026)."""
    if iid in req:
        return req[iid], True
    if iid in gear:
        return max(1, gear[iid][1] - 5), False
    return None, False


def load_tsm(data_dir):
    """({itemID: marketValue}, {itemID: name}, {itemID: region soldPerDay}) from the TSM files the updater already keeps."""
    mv, names, sold = {}, {}, {}
    d = Path(data_dir) / "tsm"
    try:
        with open(d / "realm-eu-normal-horde.csv", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    iid, v = int(r["itemId"]), float(r["marketValue"])
                except (KeyError, TypeError, ValueError):
                    continue
                if v > 0:
                    mv[iid] = int(v)
                if r.get("name"):
                    names[iid] = r["name"]
    except OSError:
        pass
    try:
        with open(d / "region-eu.csv", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    sold[int(r["itemId"])] = float(r["soldPerDay"])
                except (KeyError, TypeError, ValueError):
                    continue
                if r.get("name") and int(r["itemId"]) not in names:
                    names[int(r["itemId"])] = r["name"]
    except OSError:
        pass
    return mv, names, sold


# ---------------------------------------------------------------------------
# the rules

def _wall(others):
    """Rulebook 2.6: the cheapest unit price held by 2+ listings (single items) or a tenth of the units (stacks); else the cheapest; else None.
    others = [(unit, qty)] sorted by unit."""
    if not others:
        return None
    stacks = any(q > 1 for _u, q in others)
    total = sum(q for _u, q in others)
    at = {}
    for u, q in others:
        n = at.setdefault(u, [0, 0])
        n[0] += 1
        n[1] += q
    below_n = below_units = 0
    for u in sorted(at):
        cnt, units = at[u]
        if (units >= max(2, total * 0.10)) if stacks else (cnt >= 2):
            # the lone cheap ones in front are ignored only while they are FEWER than the wall; otherwise the market really is cheaper
            if (below_units >= units) if stacks else (below_n >= cnt):
                return others[0][0]
            return u
        below_n += cnt
        below_units += units
    return others[0][0]


def find_deals(listings, tsm_mv, rates, hours, region_sold, gear, req, names=None, settings=None):
    """listings = {auctionId: [key, qty, unitBuyout, band]} (blizzard_prices' snapshot index). Returns every deal that passes rules 1-6, best
    first. Each deal is a dict: aid, key, iid, suffixed, qty, unit, sell, profit, ratio, req, req_exact, rate, rate_from, wall_n, name."""
    settings = settings or DEFAULTS
    names = names or {}
    by_key = {}
    for aid, row in listings.items():
        try:
            key, qty, unit = str(row[0]), int(row[1]), int(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        if unit > 0 and qty > 0:
            by_key.setdefault(key, []).append((unit, qty, str(aid)))
    out = []
    min_profit = int(settings.get("min_profit", MIN_PROFIT))
    max_price = int(settings.get("max_price", 0) or 0)
    for key, rows in by_key.items():
        rows.sort()
        unit, qty, aid = rows[0]
        try:
            iid = int(key.split(":")[0])
        except ValueError:
            continue
        suffixed = ":" in key
        mv = tsm_mv.get(iid)
        if not mv:
            continue
        ratio = unit / mv
        if not (TSM_LO <= ratio <= TSM_HI):                                     # rule 1
            continue
        others = [(u, q) for u, q, _a in rows[1:]]
        if others and others[0][0] < unit * NEXT_GAP:                            # rule 2
            continue
        wall = _wall(others)                                                     # rule 3
        sell = wall if wall is not None else mv
        sell = min(sell, int(mv * (HOLD_SUFFIX if suffixed else HOLD_PLAIN)))
        per = sell * (1 - CUT) - unit                                            # rule 4
        profit = per * qty
        if profit < min_profit or per < unit * MIN_RETURN:
            continue
        if max_price and unit * qty > max_price:
            continue
        lvl, exact = required_level(iid, gear, req)                              # rule 6
        if lvl is not None and lvl < MIN_REQ:
            continue
        rate, rate_from = None, ""                                               # rule 5
        if hours >= 12:
            rate = float(rates.get(key, 0.0))
            rate_from = "blizzard"
        elif not suffixed and iid in region_sold:
            rate = float(region_sold[iid]) / REGION_FACTOR
            rate_from = "tsm"
        if rate is None or rate < qty:
            continue
        wall_n = sum(1 for u, _q in others if wall is not None and u == wall)
        name = names.get(iid) or (gear.get(iid, ("",))[0]) or f"item {iid}"
        weight = 1.0 if (lvl is None or lvl >= GOOD_REQ) else LOW_LEVEL_WEIGHT
        out.append({"aid": aid, "key": key, "iid": iid, "suffixed": suffixed, "qty": qty, "unit": unit, "sell": int(sell),
                    "profit": int(profit), "ratio": round(ratio, 2), "req": lvl, "req_exact": exact, "rate": round(rate, 1),
                    "rate_from": rate_from, "wall_n": wall_n, "name": name, "score": profit * weight})
    out.sort(key=lambda d: (-d["score"], d["key"]))
    return out


def sessions(listed):
    """How many separate postings a group of listings came from: listed = [(auctionId, firstSeen)]. Ids more than BURST_IDS apart, or
    first seen in different snapshots, are separate postings (Blizzard's snapshot does not say who the seller is)."""
    ids = sorted(int(a) for a, _f in listed)
    by_id = 1 + sum(1 for a, b in zip(ids, ids[1:]) if b - a > BURST_IDS)
    by_seen = len({int(f) for _a, f in listed if f})
    return max(by_id, by_seen)


def real_wall(others):
    """B2a. others = [(unit, auctionId, firstSeen)] of the OTHER single listings. Returns (wall price or None, listings at it, flood) where
    flood = (price, listings) of the cheapest pile posted together (one seller), or None."""
    at = {}
    for u, aid, first in others:
        at.setdefault(u, []).append((aid, first))
    lone, flood = 0, None
    for u in sorted(at):
        listed = at[u]
        if len(listed) >= 2:
            if sessions(listed) < 2:
                if flood is None:
                    flood = (u, len(listed))
                continue
            if lone >= len(listed):
                return None, 0, flood
            return u, len(listed), flood
        lone += 1
    return None, 0, flood


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def find_big_tickets(listings, tsm_mv, rates, hours, region_sold, gear, boe, req, sold, names=None, settings=None):
    """Rules B1-B4. listings = the snapshot index {auctionId: [key, qty, unit, band, firstSeen]}; sold = load_sold_prices(). Returns every
    big ticket, biggest profit first: dicts aid, key, iid, suffixed, unit, real, source, wall_n, sold_n, flood, profit, ratio, req, req_exact,
    week, name."""
    settings = settings or DEFAULTS
    names = names or {}
    min_req = int(settings.get("big_min_level", BIG_MIN_REQ))
    min_profit = int(settings.get("big_min_profit", BIG_MIN_PROFIT))
    by_key = {}
    for aid, row in listings.items():
        try:
            key, qty, unit = str(row[0]), int(row[1]), int(row[2])
            first = int(row[4]) if len(row) > 4 else 0
            iid = int(key.split(":")[0])
        except (TypeError, ValueError, IndexError):
            continue
        if unit > 0 and qty == 1 and iid in boe:
            by_key.setdefault(key, []).append((unit, str(aid), first))
    out = []
    for key, rows in by_key.items():
        iid = int(key.split(":")[0])
        lvl, exact = required_level(iid, gear, req)                              # B1
        if lvl is None or lvl < min_req:
            continue
        rows.sort()
        unit, aid, _first = rows[0]
        suffixed = ":" in key
        wall, wall_n, flood = real_wall(rows[1:])                                # B2
        past = sold.get(key) or []
        mv = tsm_mv.get(iid)
        if wall is not None:
            real, source = wall, "wall"
        elif len(past) >= BIG_SOLD_MIN:
            real, source = _median(past), "sold"
        elif mv and not suffixed:
            real, source = mv, "tsm"
        else:
            continue
        found, held = real, False
        if mv and real > mv * (HOLD_SUFFIX if suffixed else HOLD_PLAIN):
            real, held = mv * (HOLD_SUFFIX if suffixed else HOLD_PLAIN), True
        profit = real * (1 - CUT) - unit                                         # B3
        if unit > real * BIG_RATIO or profit < min_profit:
            continue
        week = 0.0                                                               # B4
        if hours >= 12:
            week = max(float(rates.get(key, 0.0)), float(rates.get(str(iid), 0.0))) * BIG_WEEK
        elif not suffixed and iid in region_sold:
            week = float(region_sold[iid]) / REGION_FACTOR * BIG_WEEK
        if week < 1 and len(past) < BIG_SOLD_MIN:
            continue
        name = names.get(iid) or (gear.get(iid, ("",))[0]) or f"item {iid}"
        out.append({"aid": aid, "key": key, "iid": iid, "suffixed": suffixed, "qty": 1, "unit": unit, "real": int(real), "source": source, "found": int(found), "held": held,
                    "wall_n": wall_n, "sold_n": len(past), "flood": flood, "profit": int(profit), "ratio": round(unit / real, 2),
                    "req": lvl, "req_exact": exact, "week": round(week, 1), "name": name})
    out.sort(key=lambda d: (-d["profit"], d["key"]))
    return out


def choose_big(bigs, state, now, settings=None):
    """B5: like choose(), with the big tickets' own caps (2 a snapshot, 10 a day)."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    used = int(state.get("big_today", 0)) if state.get("big_day") == day else 0
    return choose(bigs, state, now, {"max_per_snapshot": BIG_MAX_PER_SNAPSHOT, "max_per_day": BIG_MAX_PER_DAY}, used=used)


def remember_big(state, picked, now):
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    today = int(state.get("big_today", 0)) if state.get("big_day") == day else 0
    n = int(state.get("today", 0)) if state.get("day") == day else 0
    remember(state, picked, now)
    state.update(big_day=day, big_today=today + len(picked), today=n)        # the deals' own daily count is not used up by big tickets
    return state


def big_message(picked, snap):
    """(title, body, click URL) for one big-ticket push."""
    _h, hm = uk_time(snap)
    first = picked[0]
    title = f"Hoard BIG TICKET: {first['name']} {money(first['unit'])}, worth about {money(first['real'])}"
    if len(picked) > 1:
        title += f" (+{len(picked) - 1} more)"
    lines = []
    for d in picked:
        what = d["name"] + ((" " + d["suffix"] if d.get("suffix") else " (random roll)") if d["suffixed"] else "")
        what += f", lvl {d['req']}" + ("" if d["req_exact"] else "ish") + ", BoE"
        lines.append(what)
        lines.append(f"Buy {money(d['unit'])}, real price about {money(d['real'])}, profit about {money(d['profit'])} after the cut")
        found = d.get("found", d["real"])
        if d["source"] == "wall":
            why = f"{d['wall_n']} others listed at {money(found)} now"
        elif d["source"] == "sold":
            why = f"middle of {d['sold_n']} that went in the last {BIG_SOLD_DAYS} days, {money(found)}"
        else:
            why = "TSM price (nothing else listed or sold)"
        if d.get("held"):
            why += f"; held at {'2x' if d['suffixed'] else '1.25x'} TSM"
        if d.get("flood"):
            why += f"; ignored a pile of {d['flood'][1]} at {money(d['flood'][0])} posted together (one seller)"
        lines.append(why)
        sells = (f"about {d['week']:g} of this item" + (" (any roll)" if d["suffixed"] else "") + " sell a week") if d["week"] >= 1 \
            else f"{d['sold_n']} went in the last {BIG_SOLD_DAYS} days"
        lines.append(f"{sells}. Relist at the real price and give it a week.")
        lines.append("")
    lines.append(f"From the {hm} snapshot, so it may be gone. Never bought automatically.")
    click = f"https://www.wowhead.com/classic/item={first['iid']}"
    return title, "\n".join(lines).strip(), click


def choose(deals, state, now, settings=None, used=None):
    """Rule 7: drop listings already sent and items sent in the last 12 hours, then the best few within the hour's and the day's caps.
    used = how many of the day's cap are gone (default: the deals' own count)."""
    settings = settings or DEFAULTS
    sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
    keys = state.get("keys") if isinstance(state.get("keys"), dict) else {}
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    today = used if used is not None else (int(state.get("today", 0)) if state.get("day") == day else 0)
    room = min(int(settings.get("max_per_snapshot", MAX_PER_SNAPSHOT)), int(settings.get("max_per_day", MAX_PER_DAY)) - today)
    picked = []
    for d in deals:
        if len(picked) >= room:
            break
        if d["aid"] in sent or now - float(keys.get(d["key"], 0)) < KEY_COOLDOWN:
            continue
        if any(p["key"] == d["key"] for p in picked):
            continue
        picked.append(d)
    return picked


def remember(state, picked, now):
    sent = {k: v for k, v in (state.get("sent") or {}).items() if now - float(v) < SENT_KEEP}
    keys = {k: v for k, v in (state.get("keys") or {}).items() if now - float(v) < KEY_COOLDOWN}
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    today = int(state.get("today", 0)) if state.get("day") == day else 0
    for d in picked:
        sent[d["aid"]] = int(now)
        keys[d["key"]] = int(now)
    state.update(sent=sent, keys=keys, day=day, today=today + len(picked))
    return state


def message(picked, snap):
    """(title, body, click URL) for one push."""
    _h, hm = uk_time(snap)
    n = len(picked)
    title = f"Hoard: {n} deal{'s' if n != 1 else ''} worth a look ({hm} snapshot)"
    lines = []
    for d in picked:
        what = d["name"] + ((" " + d["suffix"] if d.get("suffix") else " (random roll)") if d["suffixed"] else "")
        if d["req"] is not None:
            what += f", lvl {d['req']}" + ("" if d["req_exact"] else "ish")
        qty = f"{d['qty']} x " if d["qty"] > 1 else ""
        lines.append(f"{what}\nBuy {qty}{money(d['unit'])}, sell at {money(d['sell'])}, profit about {money(d['profit'])} after the cut")
        why = f"{d['ratio']:.2f}x TSM"
        if d["wall_n"] >= 2:
            why += f", matches a wall of {d['wall_n']}"
        why += (f", sells about {d['rate']:g} a day" + (" here" if d["rate_from"] == "blizzard" else " (EU-wide / 3)"))
        lines.append(why)
        lines.append("")
    lines.append("Up to an hour old, so it may be gone. Never bought automatically.")
    click = f"https://www.wowhead.com/classic/item={picked[0]['iid']}" if picked else ""
    return title, "\n".join(lines).strip(), click


def send_ntfy(topic, title, body, click="", http=None, priority=4, tags=("moneybag",)):
    """One push. JSON publish so the title can hold any text. Raises on failure (run() catches)."""
    doc = {"topic": topic, "title": title[:250], "message": body[:3500], "tags": list(tags), "priority": priority}
    if click:
        doc["click"] = click
    data = json.dumps(doc).encode("utf-8")
    if http:
        return http(NTFY, data)
    req = urllib.request.Request(NTFY, data=data, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status


# ---------------------------------------------------------------------------
# settings and the run

def load_settings(alerts_dir):
    """settings.json, made on first use with a long random topic name. Returns the settings (defaults filled in)."""
    path = Path(alerts_dir) / "settings.json"
    doc = _read_json(path, None)
    if not isinstance(doc, dict):
        doc = {}
    changed = False
    topic = doc.get("topic")
    if not (isinstance(topic, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", topic)):
        doc["topic"] = "hoard-" + secrets.token_hex(8)
        changed = True
    for k, v in DEFAULTS.items():
        if k not in doc:
            doc[k] = v
            changed = True
    if changed:
        _write_json(path, doc)
    return doc


def run(now, data_dir=None, hoard_dir=None, http=None, gear_csv=None, dry=False):
    """After blizzard_prices.refresh: alert on the newest snapshot once. Returns a short log text ("" when there was nothing to do).
    Never raises."""
    try:
        data = Path(data_dir or DATA_DIR)
        bz = data / "blizzard"
        if not (bz / "credentials.json").is_file():
            return ""                                                   # no Blizzard data on this PC
        alerts = data / "alerts"
        settings = load_settings(alerts)
        if not settings.get("on", True):
            return ""
        cloud = _read_json(alerts / "cloud.json", {})       # blizzard_cloud.py: while the cloud sends the alerts, the PC stays quiet (one push per deal)
        if isinstance(cloud, dict) and isinstance(cloud.get("until"), (int, float)) and now < cloud["until"] and not dry:
            return "Deal alerts: sent from the cloud (the PC stays quiet)"
        snapshot = _read_json(bz / "prev.json.gz", {})
        snap = int(snapshot.get("snap") or 0) if isinstance(snapshot, dict) else 0
        listings = snapshot.get("ids") if isinstance(snapshot, dict) else None
        state_path = alerts / "state.json"
        state = {} if dry else _read_json(state_path, {})          # --show looks at the snapshot afresh
        state = state if isinstance(state, dict) else {}
        if not snap or not isinstance(listings, dict) or snap <= int(state.get("snap") or 0):
            return ""
        state["snap"] = snap
        if now - snap > MAX_AGE:
            if not dry:
                _write_json(state_path, state)
            return ""
        if quiet_now(now, settings) and not dry:
            _write_json(state_path, state)
            return ""
        hello = ""
        if not state.get("welcomed") and not dry:                       # one first push, so the phone shows it works before any deal
            send_ntfy(settings["topic"], "Hoard deal alerts are on",
                      "Your PC will push here when Blizzard's hourly auction data shows a strong deal (a few a day at most, never 23:00-07:00). "
                      "Nothing is ever bought for you.", http=http)
            state["welcomed"] = int(now)
            hello = "Deal alerts: first push sent (alerts are on)"
        import blizzard_prices
        days = _read_json(bz / "sales.json", {})
        rates, hours = blizzard_prices.per_day(days if isinstance(days, dict) else {}, snap)
        mv, names, sold = load_tsm(data)
        hoard = Path(hoard_dir) if hoard_dir else THIS_DIR.parent / "Hoard"
        gear, req = load_gear(gear_csv), load_req(hoard)
        sfx = {}

        def name_rolls(rows):
            if any(d["suffixed"] for d in rows) and not sfx:
                sfx.update(load_suffix_names(hoard))
            for d in rows:
                if d["suffixed"]:
                    d["suffix"] = suffix_name(d["key"], sfx)

        said, shown = [], []
        big_picked = []
        if settings.get("big_on", True):                                 # big tickets first: their own push (B1-B5)
            boe = load_boe(gear_csv)
            bigs = find_big_tickets(listings, mv, rates, hours, sold, gear, boe, req,
                                    load_sold_prices(data, snap, want=lambda k: _int0(k.split(":")[0]) in boe), names, settings)
            big_picked = choose_big(bigs, state, now, settings)
            name_rolls(big_picked)
            if big_picked:
                title, body, click = big_message(big_picked, snap)
                if dry:
                    shown.append(title + "\n" + body)
                else:
                    send_ntfy(settings["topic"], title, body, click, http=http, priority=5, tags=("rotating_light", "moneybag"))
                    remember_big(state, big_picked, now)
                    _write_json(state_path, state)
                    said.append(f"Big ticket alerts: sent {len(big_picked)} to the phone ("
                                + "; ".join(f"{d['name']} {money(d['unit'])}" for d in big_picked)[:200] + ")")
        deals = find_deals(listings, mv, rates, hours, sold, gear, req, names, settings)
        deals = [d for d in deals if all(d["aid"] != b["aid"] for b in big_picked)]
        picked = choose(deals, state, now, settings)
        name_rolls(picked)
        if not picked:
            if not dry:
                _write_json(state_path, state)
            if dry and shown:
                return "\n\n".join(shown)
            if said:
                return " | ".join(said)
            if deals:
                return f"Deal alerts: none worth a push in this snapshot ({len(deals)} passed the price rules, all sent before or capped)"
            return hello
        title, body, click = message(picked, snap)
        if dry:
            return "\n\n".join(shown + [title + "\n" + body])
        send_ntfy(settings["topic"], title, body, click, http=http)
        remember(state, picked, now)
        _write_json(state_path, state)
        said.append(f"Deal alerts: sent {len(picked)} to the phone (" + "; ".join(f"{d['name']} {money(d['unit'])}" for d in picked)[:200] + ")")
        return " | ".join(said)
    except Exception as exc:  # noqa: BLE001 - a courtesy: never fails a run
        return f"Deal alerts: skipped ({type(exc).__name__}: {str(exc)[:120]})"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--show" in argv:
        print(run(time.time(), dry=True) or "No deal passes the rules in the newest snapshot.")
        return 0
    if "--test" in argv:
        s = load_settings(DATA_DIR / "alerts")
        send_ntfy(s["topic"], "Hoard deal alerts are on", "This is a test. Real alerts come at most a few times an hour, only for strong deals.")
        print("sent a test push to topic " + s["topic"])
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

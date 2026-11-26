#!/usr/bin/env python3
"""
blizzard_prices.py -- Blizzard's own hourly auction-house snapshot for YOUR auction house, handed to Hoard next to the TSM price.

In plain words (the owner, 23 Nov 2026, picked "Yes, Blizzard data" on the card "Add Blizzard's hourly auction data next to TSM?"):
Since 6 Nov 2026 Blizzard's Game Data API publishes every WoW Forever auction house about once an hour: every listing, with its item, roll,
quantity, buyout and time left. Every Forever price site (wowfah, forevermastery, AHledger, ...) is built on it. the owner's PC now reads it too:
the auto-updater (every 15 minutes) asks for the newest snapshot of the owner's auction house (EU Normal Horde), works out per item

    lowest buyout now . usual price (the cheapest 15% of units on sale) . units listed . about how many sell a day

and hands that to Hoard inside Hoard\\CrowdPrices.lua (as Hoard.CrowdPrices.blizzard, read by Hoard/Crowd.lua, Hoard SPEC.md section 230).
Random-suffix gear ("of the Bandit") gets its own row per roll: on Forever Blizzard sends the roll as a bonus list id
(item "bonus_lists"), named with Hoard's own table Hoard\\SuffixBonus_Data.lua; a bonus that table cannot name stays a plain listing.

What the owner does once: make a free Battle.net developer "client" and paste its two codes into the "Hoard Blizzard Sign-in" window
(tools\\blizzard_signin.py; the updater makes its desktop shortcut and opens it once). The codes stay in tools\\data\\blizzard\\credentials.json
on his PC (git-ignored; never committed, never sent anywhere but Blizzard's own sign-in address). No codes = this step does nothing.

How "sells a day" is estimated (no API gives sales): each listing has its own id. A listing seen with "2 hours or more left" (LONG or
VERY_LONG) that is gone from the next snapshot, under 2.5 hours later, cannot have run out: it was bought or cancelled. A gone listing whose
seller put the same item and quantity back up at the same or a lower price in the same snapshot is counted as a repost (cancelled to
undercut), not a sale. The units left are summed per item over the last 7 days of snapshots and divided by the hours watched. Cancels the
repost rule cannot see still count, so it is "about"; quiet items show nothing until there are 12 hours of snapshots.

Which auction house is the owner's? Blizzard's API does not name the ruleset (Forever has no realms; each ruleset + faction is one auction
house). Each candidate house's lowest prices are compared with TSM's file for EU Normal Horde (tools\\data\\tsm\\realm-eu-normal-horde.csv,
already on this PC): the house whose lowest buyouts agree with TSM's on the most items is the owner's. It is re-checked every 7 days. Until one
house clearly wins, nothing is handed to Hoard (the log says the scores).

Rules (tests: tools/tests/test_blizzard_prices.py):
  * never raises and never fails or marks as stuck an auto-update run; a broken download changes nothing (the last good figures stay);
  * asks "only if changed" (If-Modified-Since) and not before about an hour after the last snapshot's own time; at most MATCH_PER_RUN
    houses are downloaded in one run while finding the house;
  * only numbers are written for Hoard, so whatever Blizzard sends, the Lua loads;
  * personal use on the owner's PC only: snapshots and figures stay in tools\\data\\blizzard (git-ignored);
  * every listing that vanished that way is also kept with its own price, by day, in tools\\data\\blizzard\\sold (5 weeks), so the
    sniper tip grader (backtest/tipgrade.py --blizzard) can check what buyers really paid (the owner, 24 Nov 2026: "hourly data, that must be
    absolutely epic for us");
  * on Forever the house answer's "commodities" is only a link: that second download (cloth, ore, herbs, potions...) is fetched too and
    counted like any listing, in the figures, the sales, the sold log and the kept snapshot (24 Nov 2026); a failure keeps the rest;
  * every hourly snapshot is also kept whole, gzip, in tools\\data\\blizzard\\snapshots\\<day>\\<HHMM>Z.json.gz (~1.5 MB each, ~40 MB a day):
    price history for the backtest and the tip grader (the owner, 24 Nov 2026: "Do you have all the price history from today? Like each
    hourly?"). None while the drive has under 20 GB free; over 30 GB the oldest days go first.

By hand (for a look, writes nothing to the game):  python tools\\blizzard_prices.py --show
"""
import base64
import gzip
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

REGION = "eu"
RULESET = 2                                            # Forever rulesets (community-read, Nov 2026): 1 PvP, 2 Normal, 3 Roleplay, 15 Roleplay PvP
FACTION = "horde"
API = "https://{region}.api.blizzard.com"
TOKEN_URL = "https://oauth.battle.net/token"
NAMESPACE = "dynamic-forever-{region}"
LOCALE = "en_GB"
INDEX_PATH = "/data/wow/ruleset/{ruleset}/auctions/"   # the trailing slash is needed (community note, 6 Nov 2026)
HOUSE_PATH = "/data/wow/ruleset/{ruleset}/auctions/{house}"
COMMODITIES_PATH = "/data/wow/ruleset/{ruleset}/auctions/{house}/commodities"   # Forever: stackables are a second download (24 Nov 2026)
DEV_SITE = "https://develop.battle.net/access/clients"

THIS_DIR = Path(__file__).resolve().parent
DATA_DIR = THIS_DIR / "data" / "blizzard"
TSM_REALM_CSV = THIS_DIR / "data" / "tsm" / "realm-eu-normal-horde.csv"
CREDS_FILE = "credentials.json"
BLOCK_FILE = "block.lua.txt"
SUFFIX_BONUS_FILE = THIS_DIR.parent / "Hoard" / "SuffixBonus_Data.lua"  # Hoard's own bonus list id -> suffix id table (Hoard SPEC.md section 114)                           # the Lua text crowd_prices.py appends to Hoard\\CrowdPrices.lua

USER_AGENT = "HoardBlizzardPrices/1"
TIMEOUT = 60
MAX_BYTES = 96 * 1024 * 1024                           # the busiest house is about 9 MB (Nov 2026)
SNAPSHOT_EVERY = 55 * 60                               # Blizzard publishes about hourly: no question before 55 min after the last snapshot
FAIL_RETRY = 30 * 60
MATCH_EVERY = 7 * 86400
MATCH_RETRY = 6 * 3600                                 # no clear winner: look again later
MATCH_PER_RUN = 3
MATCH_MIN_COMMON = 100                                 # items both sides price before a score counts
MATCH_NEAR = 0.05                                      # lowest buyouts within 5% "agree"
MATCH_MIN_SCORE = 0.5
MATCH_MIN_MARGIN = 0.15
MAX_GAP = int(2.5 * 3600)                              # snapshots further apart than this are not compared for sales
KEEP_DAYS = 8
SOLD_DIR = "sold"                                      # data\blizzard\sold\YYYY-MM-DD.json.gz: every listing that vanished, with its price
SOLD_KEEP_DAYS = 35
SNAP_DIR = "snapshots"                                 # data\blizzard\snapshots\YYYY-MM-DD\HHMMZ.json.gz: every hourly snapshot, whole, as Blizzard sent it
SNAP_MIN_FREE = 20 * 1024 ** 3                         # no snapshot kept while the drive has under 20 GB free (Hoard's figures go on regardless)
SNAP_MAX_BYTES = 30 * 1024 ** 3                        # over 30 GB of snapshots (about 2 years at ~40 MB a day): the oldest days go first
SALES_DAYS = 7
SALES_MIN_HOURS = 12
MARKET_SHARE = 0.15                                    # "usual price" = average of the cheapest 15% of units on sale
MAX_COPPER = 100000000000
MAX_AUCTIONS = 1000000
LONG_BANDS = (3, 4)                                    # LONG, VERY_LONG: 2 hours or more left
BANDS = {"SHORT": 1, "MEDIUM": 2, "LONG": 3, "VERY_LONG": 4}


# ---------------------------------------------------------------------------
# small helpers

def _int(v, lo=0, hi=MAX_COPPER):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f < lo or f > hi:
        return None
    return int(f)


def _read_json(path, default=None):
    try:
        p = Path(path)
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        doc = json.loads(raw.decode("utf-8"))
        return doc
    except (OSError, ValueError, EOFError):
        return default


def _write_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(data, str):
        data = data.encode("utf-8")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path, doc):
    raw = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
    _write_atomic(path, gzip.compress(raw, 6) if str(path).endswith(".gz") else raw)


def _http_time(text):
    try:
        dt = parsedate_to_datetime(str(text))
        return int(dt.timestamp())
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0


def _why(exc):
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:160]


# ---------------------------------------------------------------------------
# credentials and the sign-in token

def read_credentials(data_dir=None):
    """(client_id, client_secret) from tools\\data\\blizzard\\credentials.json, or None."""
    doc = _read_json(Path(data_dir or DATA_DIR) / CREDS_FILE, {})
    if not isinstance(doc, dict):
        return None
    cid, sec = str(doc.get("client_id") or "").strip(), str(doc.get("client_secret") or "").strip()
    if not cid or not sec or len(cid) > 200 or len(sec) > 200:
        return None
    return cid, sec


def save_credentials(client_id, client_secret, data_dir=None):
    _write_json(Path(data_dir or DATA_DIR) / CREDS_FILE, {"client_id": client_id.strip(), "client_secret": client_secret.strip()})


def http_request(url, headers=None, data=None, timeout=TIMEOUT):
    """(status, headers dict lower-cased, body bytes). 304 is returned, not raised; other HTTP errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 - fixed https addresses
            body = resp.read(MAX_BYTES + 1)
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, {k.lower(): v for k, v in (exc.headers or {}).items()}, b""
        raise
    if len(body) > MAX_BYTES:
        raise ValueError("answer far bigger than expected")
    if hdrs.get("content-encoding", "").lower() == "gzip":
        body = gzip.decompress(body)
    return status, hdrs, body


def get_token(creds, now, http=None, data_dir=None):
    """A bearer token (cached in data\\blizzard\\token.json until 10 minutes before it runs out). Raises when Blizzard refuses the codes."""
    http = http or http_request
    path = Path(data_dir or DATA_DIR) / "token.json"
    cached = _read_json(path, {})
    who = hashlib.sha256(creds[0].encode("utf-8")).hexdigest()[:12]
    if isinstance(cached, dict) and cached.get("who") == who and _int(cached.get("until"), 0, 1e12) and cached["until"] > now + 600:
        return str(cached.get("token"))
    basic = base64.b64encode(f"{creds[0]}:{creds[1]}".encode("utf-8")).decode("ascii")
    status, _h, body = http(TOKEN_URL, {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"},
                            b"grant_type=client_credentials")
    doc = json.loads(body.decode("utf-8"))
    token = doc.get("access_token")
    if status != 200 or not token:
        raise ValueError("Blizzard gave no sign-in token")
    life = _int(doc.get("expires_in"), 0, 1e7) or 3600
    try:
        _write_json(path, {"token": token, "until": int(now) + life, "who": who})
    except OSError:
        pass
    return str(token)


def api_url(path, region=REGION):
    q = urllib.parse.urlencode({"namespace": NAMESPACE.format(region=region), "locale": LOCALE})
    return API.format(region=region) + path + "?" + q


def check_credentials(client_id, client_secret, now, http=None, data_dir=None, region=REGION):
    """For the sign-in window: (True, how many auction houses Blizzard lists) or (False, a plain reason). Never raises."""
    try:
        token = get_token((client_id.strip(), client_secret.strip()), now, http=http, data_dir=data_dir)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            return False, "Blizzard did not accept those two codes. Copy them again from the Blizzard page (no spaces) and try once more."
        return False, f"Blizzard's sign-in answered with error {exc.code}. Try again in a few minutes."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not reach Blizzard ({_why(exc)}). Check the internet connection and try again."
    try:
        status, _h, body = (http or http_request)(api_url(INDEX_PATH.format(ruleset=RULESET), region), {"Authorization": "Bearer " + token})
        houses = list_houses(json.loads(body.decode("utf-8")))
        return True, len(houses)
    except Exception as exc:  # noqa: BLE001
        return False, f"The codes work, but Blizzard's auction list did not answer ({_why(exc)}). Your PC will keep trying by itself."


# ---------------------------------------------------------------------------
# reading Blizzard's answers

def list_houses(doc):
    """The index answer -> [{"id": int, "name": str}] (any shape that lists auction houses with an id)."""
    out, seen = [], set()
    items = doc.get("auctions") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        items = doc.get("auction_houses") if isinstance(doc, dict) else None
    for h in items if isinstance(items, list) else []:
        if not isinstance(h, dict):
            continue
        hid = _int(h.get("id"), 1, 1e9)
        if hid is None:
            href = str((h.get("key") or {}).get("href") or "") if isinstance(h.get("key"), dict) else ""
            tail = href.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            hid = _int(tail, 1, 1e9) if tail.isdigit() else None
        if hid is None or hid in seen:
            continue
        seen.add(hid)
        name = h.get("name")
        if isinstance(name, dict):
            name = name.get("en_GB") or name.get("en_US") or next(iter(name.values()), "")
        out.append({"id": hid, "name": str(name or "")[:80]})
    return out


def faction_houses(houses, faction=FACTION):
    """The houses whose name says the faction; every house when no name does (Blizzard may not name them)."""
    named = [h for h in houses if faction in h["name"].lower()]
    return named if named else list(houses)


_BONUS_CACHE = {}


def read_bonus_map(path=None):
    """{bonus list id: suffix id} from Hoard's SuffixBonus_Data.lua (its `map` block only; the `unresolved` ones are never guessed).
    On WoW Forever the roll of random-suffix gear rides in a bonus list id (Blizzard's answer: "item":{"bonus_lists":[12718],"id":7443},
    no "rand"), and Hoard keys a roll by its suffix id, so the two are joined with Hoard's own table. Missing or unreadable file = {}."""
    p = Path(path or SUFFIX_BONUS_FILE)
    try:
        st = p.stat()
        hit = _BONUS_CACHE.get(str(p))
        if hit and hit[0] == (st.st_mtime, st.st_size):
            return dict(hit[1])
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out, inside = {}, False
    for line in text.splitlines():
        t = line.strip()
        if not inside:
            inside = t.startswith("map") and t.rstrip().endswith("{")
            continue
        if t.startswith("}"):
            break
        if t.startswith("[") and "]=" in t:
            k, _, v = t[1:].partition("]=")
            b, sfx = _int(k.strip(), 1, 1e9), _int(v.strip().rstrip(","), 1, 1e9)
            if b is not None and sfx is not None:
                out[b] = sfx
    _BONUS_CACHE[str(p)] = ((st.st_mtime, st.st_size), dict(out))
    return out


def _roll_of(item, bonus_map):
    """The roll Hoard knows this listing by: the classic "rand" when Blizzard sends one, else the suffix id of the first bonus list id
    Hoard's table names, else 0 (plain: a bonus Hoard cannot name is never guessed)."""
    rand = _int(item.get("rand", 0), -1e9, 1e9) or 0
    if rand:
        return rand
    bl = item.get("bonus_lists")
    if isinstance(bl, list) and bonus_map:
        for b in bl:
            sfx = bonus_map.get(_int(b, 1, 1e9))
            if sfx:
                return sfx
    return 0


def parse_auctions(doc, bonus_map=None):
    """A house's answer -> list of (auctionId, itemId, rand, unitBuyout or 0, quantity, band 1-4 or 0).
    rand = the roll's suffix id (see _roll_of); bonus_map defaults to Hoard's own file next to tools. Reads "auctions" and, when
    Blizzard sends any, "commodities" (stackables priced per unit)."""
    rows = []
    if bonus_map is None:
        bonus_map = read_bonus_map()
    lists = [doc.get(k) for k in ("auctions", "commodities")] if isinstance(doc, dict) else []
    for a in [x for lst in lists if isinstance(lst, list) for x in lst]:
        if len(rows) >= MAX_AUCTIONS:
            break
        if not isinstance(a, dict):
            continue
        item = a.get("item") if isinstance(a.get("item"), dict) else {}
        aid, iid = _int(a.get("id"), 1, 1e15), _int(item.get("id"), 1, 1e7)
        qty = _int(a.get("quantity", 1), 1, 1e6)
        if aid is None or iid is None or qty is None:
            continue
        rand = _roll_of(item, bonus_map)
        unit = _int(a.get("unit_price"), 0) or 0
        if not unit:
            buy = _int(a.get("buyout"), 0) or 0
            unit = int(round(buy / qty)) if buy else 0
        tl = a.get("time_left")
        band = BANDS.get(str(tl).upper(), 0) if not isinstance(tl, (int, float)) else (_int(tl, 1, 4) or 0)
        rows.append((aid, iid, rand, unit, qty, band))
    return rows


def _key(iid, rand):
    return f"{iid}:{rand}" if rand else str(iid)


def summarize(rows):
    """Per item id ("123", every roll together) and per roll ("123:-19"): [lowest unit buyout, usual price, units listed]."""
    units = {}
    for _aid, iid, rand, unit, qty, _band in rows:
        keys = (str(iid),) if not rand else (str(iid), _key(iid, rand))
        for k in keys:
            u = units.setdefault(k, [0, []])
            u[0] += qty
            if unit > 0:
                u[1].append((unit, qty))
    out = {}
    for k, (qty, priced) in units.items():
        if not priced:
            out[k] = [0, 0, qty]
            continue
        priced.sort()
        want = max(1, int(sum(q for _u, q in priced) * MARKET_SHARE + 0.999))
        got, total = 0, 0
        for unit, q in priced:
            take = min(q, want - got)
            got += take
            total += take * unit
            if got >= want:
                break
        out[k] = [priced[0][0], int(round(total / got)), qty]
    return out


# Hoard SPEC 240 (the owner, 25 Nov 2026, "sure all" to the flood spotter): ONE SELLER'S PILE. Blizzard names no seller, but listing ids count up as listings are
# posted (about 100 a second across the region in the 24 Nov snapshot). Copies of one item at one price whose ids follow each other closely were posted together:
# one seller's pile. Same item + price gaps in the 24 Nov snapshot: 116 at the 10th percentile, 173 at the 25th, 15,915 at the median (other sellers).
PILE_MIN = 5                                           # copies posted together that make a pile (the owner's example: 11 helms at 20g)
PILE_GAP = 2000                                        # listing ids at most this far apart are "posted together" (about 20 seconds)


def piles(rows):
    """{itemId: [copies, unit price]}: the CHEAPEST price at which one seller posted PILE_MIN or more single copies together.
    Single copies only (quantity 1: gear is always one; nine stacks of cloth at one price is how everyone posts, not a dump). Every roll of an item together."""
    by = {}
    for aid, iid, _rand, unit, qty, _band in rows:
        if qty != 1 or unit <= 0:
            continue
        by.setdefault(iid, {}).setdefault(unit, []).append(aid)
    out = {}
    for iid, prices in by.items():
        for unit in sorted(prices):
            ids = sorted(prices[unit])
            if len(ids) < PILE_MIN:
                continue
            run = best = 1
            for i in range(1, len(ids)):
                run = run + 1 if ids[i] - ids[i - 1] <= PILE_GAP else 1
                best = max(best, run)
            if best >= PILE_MIN:
                out[iid] = [best, unit]
                break
    return out


def lowest_prices(rows):
    """{itemId: lowest unit buyout} for plain items (rand 0): what the house match compares with TSM's minBuyout."""
    low = {}
    for _aid, iid, rand, unit, _q, _b in rows:
        if rand or unit <= 0:
            continue
        if iid not in low or unit < low[iid]:
            low[iid] = unit
    return low


def match_score(low, tsm_min):
    """(share of common items whose lowest buyouts agree within MATCH_NEAR, common items)."""
    common = agree = 0
    for iid, mine in low.items():
        theirs = tsm_min.get(iid)
        if not theirs or theirs <= 0:
            continue
        common += 1
        if abs(mine / theirs - 1) <= MATCH_NEAR:
            agree += 1
    return (agree / common if common else 0.0), common


def pick_house(scores):
    """scores {houseId: (score, common)} -> houseId when one house clearly agrees with TSM's file for the owner's auction house, else None."""
    good = sorted(((s, hid) for hid, (s, c) in scores.items() if c >= MATCH_MIN_COMMON), reverse=True)
    if not good or good[0][0] < MATCH_MIN_SCORE:
        return None
    if len(good) > 1 and good[0][0] - good[1][0] < MATCH_MIN_MARGIN:
        return None
    return good[0][1]


def tsm_min_buyouts(path=None):
    """{itemId: minBuyout} from TSM's file for the owner's auction house (already downloaded by crowd_prices.py), or {}."""
    try:
        import crowd_prices
        text = Path(path or TSM_REALM_CSV).read_text(encoding="utf-8")
        rows, _upd = crowd_prices.parse_realm_csv(text)
        return {iid: r[1] for iid, r in rows.items() if r[1] > 0}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# sales from one snapshot to the next

def vanished_listings(prev, cur_rows, gap):
    """prev = {auctionId: [itemKey, qty, unit, band, firstSeen]} from the last snapshot (firstSeen 0 = was there before we looked; the
    23 Nov format has no firstSeen); cur_rows = this one's parse_auctions rows; gap = seconds between.
    Returns [[auctionId, itemKey, qty, unit, firstSeen, low], ...]: listings that had 2 hours or more left and are gone, minus reposts (same
    item key and quantity newly listed at the same or a lower unit price). low = the cheapest unit buyout of that item key in prev: a dear
    listing that went while a cheaper one stayed is more likely a cancel than a buyer (the tip grader only counts the cheapest, like Hoard).
    [] when the gap is too long to tell expiry from a sale."""
    if not prev or gap <= 0 or gap > MAX_GAP:
        return []
    now_ids = set()
    new_by_key = {}
    for aid, iid, rand, unit, qty, _band in cur_rows:
        now_ids.add(aid)
        if str(aid) not in prev:
            new_by_key.setdefault(_key(iid, rand), []).append([qty, unit, False])
    low = {}
    for rec in prev.values():
        if isinstance(rec, list) and len(rec) >= 4 and rec[2] and (rec[0] not in low or rec[2] < low[rec[0]]):
            low[rec[0]] = rec[2]
    out = []
    for aid, rec in prev.items():
        if not isinstance(rec, list) or len(rec) < 4:
            continue
        key, qty, unit, band = rec[0], rec[1], rec[2], rec[3]
        first = rec[4] if len(rec) > 4 else 0
        if band not in LONG_BANDS or int(aid) in now_ids:
            continue
        repost = None
        for cand in new_by_key.get(key, ()):
            if not cand[2] and cand[0] == qty and (not unit or (cand[1] and cand[1] <= unit)):
                repost = cand
                break
        if repost is not None:
            repost[2] = True
            continue
        out.append([int(aid), key, qty, unit, first, low.get(key, 0)])
    return out


def vanished_sales(prev, cur_rows, gap):
    """{itemKey: units bought (about)} from vanished_listings; a roll's units count for the roll and for the item. {} when the gap is too long."""
    sold = {}
    for _aid, key, qty, _unit, _first, _low in vanished_listings(prev, cur_rows, gap):
        sold[key] = sold.get(key, 0) + qty
        base = key.split(":")[0]
        if base != key:
            sold[base] = sold.get(base, 0) + qty
    return sold


def snapshot_index(rows, prev=None, snap=0):
    """{auctionId: [itemKey, qty, unit, band, firstSeen]}. firstSeen = the snapshot a listing was first seen in, carried over from prev;
    0 when it was already there in the first snapshot we looked at (or prev is in the old 4-field format)."""
    out = {}
    for aid, iid, rand, unit, qty, band in rows:
        old = prev.get(str(aid)) if prev else None
        if not prev:
            first = 0
        elif isinstance(old, list):
            first = old[4] if len(old) > 4 else 0
        else:
            first = int(snap)                          # new since prev (0 when the gap was too long to say when it came)
        out[str(aid)] = [_key(iid, rand), qty, unit, band, first]
    return out


def log_sold(data_dir, prev_snap, new_snap, listings, keep_days=SOLD_KEEP_DAYS):
    """Adds the hour watched ([prev_snap, new_snap] under "w") and each vanished listing ([aid, key, qty, unit, firstSeen, goneBy, low] under "s")
    to data\\blizzard\\sold\\<day of new_snap>.json.gz, and drops day files older than keep_days. For the sniper tip grader and the backtest
    (hoard-tools backtest/tipgrade.py): the price each listing went at, which sales.json does not keep."""
    import time as _t
    folder = Path(data_dir) / SOLD_DIR
    day = _t.strftime("%Y-%m-%d", _t.gmtime(new_snap))
    path = folder / f"{day}.json.gz"
    doc = _read_json(path, {})
    doc = doc if isinstance(doc, dict) else {}
    w = doc.get("w") if isinstance(doc.get("w"), list) else []
    s = doc.get("s") if isinstance(doc.get("s"), list) else []
    win = [int(prev_snap), int(new_snap)]
    if win not in w:
        w.append(win)
        s.extend([aid, key, qty, unit, first, int(new_snap), low] for aid, key, qty, unit, first, low in listings)
    _write_json(path, {"w": w, "s": s})
    cutoff = _t.strftime("%Y-%m-%d", _t.gmtime(new_snap - keep_days * 86400))
    for p in folder.glob("*.json.gz"):
        if p.name[:10] < cutoff:
            try:
                p.unlink()
            except OSError:
                pass


def _free_bytes(path):
    """Free bytes on the drive that holds path (None when unknown)."""
    import shutil
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None


def _mb(n):
    return f"{n / 1024 / 1024:.1f} MB"


def keep_snapshot(data_dir, snap, body):
    """Keeps one hourly snapshot whole (Blizzard's own answer, gzip) as data\\blizzard\\snapshots\\<day>\\<HHMM>Z.json.gz, for the backtest,
    the tip grader and any later look at price history (the owner, 24 Nov 2026: "Do you have all the price history from today? Like each hourly?").
    Not while the drive has under SNAP_MIN_FREE free; over SNAP_MAX_BYTES the oldest days go first (never today's). Returns a short note."""
    import shutil
    import time as _t
    root = Path(data_dir) / SNAP_DIR
    free = _free_bytes(Path(data_dir))
    if free is not None and free < SNAP_MIN_FREE:
        return f"snapshot not kept: the drive has under {SNAP_MIN_FREE // 1024 ** 3} GB free"
    day = _t.strftime("%Y-%m-%d", _t.gmtime(snap))
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (_t.strftime("%H%M", _t.gmtime(snap)) + "Z.json.gz")
    tmp = path.with_suffix(".part")
    tmp.write_bytes(gzip.compress(bytes(body), 6))
    os.replace(tmp, path)
    days = sorted(p for p in root.iterdir() if p.is_dir() and len(p.name) == 10)
    sizes = {p: sum(f.stat().st_size for f in p.glob("*.json.gz")) for p in days}
    total = sum(sizes.values())
    for p in days:
        if total <= SNAP_MAX_BYTES or p.name >= day:
            break
        shutil.rmtree(p, ignore_errors=True)
        total -= sizes[p]
    n = sum(1 for p in root.iterdir() if p.is_dir() for _f in p.glob("*.json.gz"))
    return f"snapshot kept ({_mb(path.stat().st_size)}; {n} kept, {_mb(total)})"


def add_sales(days, when, hours, sold):
    """days = {"YYYY-MM-DD": {"h": hours watched, "s": {key: units}}}; keeps KEEP_DAYS days."""
    import time as _t
    day = _t.strftime("%Y-%m-%d", _t.gmtime(when))
    d = days.setdefault(day, {"h": 0.0, "s": {}})
    d["h"] = round(float(d.get("h", 0)) + hours, 3)
    s = d.setdefault("s", {})
    for k, n in sold.items():
        s[k] = s.get(k, 0) + n
    for old in sorted(days)[:-KEEP_DAYS]:
        days.pop(old, None)
    return days


def per_day(days, when):
    """({key: units a day}, hours watched) over the last SALES_DAYS days; {} until SALES_MIN_HOURS hours have been watched."""
    import time as _t
    recent = [_t.strftime("%Y-%m-%d", _t.gmtime(when - i * 86400)) for i in range(SALES_DAYS)]
    hours, total = 0.0, {}
    for day in recent:
        d = days.get(day)
        if not isinstance(d, dict):
            continue
        hours += float(d.get("h", 0))
        for k, n in (d.get("s") or {}).items():
            total[k] = total.get(k, 0) + n
    if hours < SALES_MIN_HOURS:
        return {}, hours
    return {k: n * 24.0 / hours for k, n in total.items()}, hours


# ---------------------------------------------------------------------------
# the Lua text for Hoard

def _num(f):
    s = f"{f:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


BLOCK_CHUNK = 4000          # rows per little Lua function (see block_lua)


def block_lua(house, name, updated, summary, rates, hours, piles=None):
    """The text crowd_prices.py appends to Hoard\\CrowdPrices.lua (numbers and fixed words only):
    Hoard.CrowdPrices.blizzard = { house, name, updated, hours, count, items = { [itemID] = { lowest, usual, listed, perDay } },
                                   rolls = { ["itemID:suffix"] = { lowest, usual, listed, perDay } },
                                   piles = { [itemID] = { copies, unit price } } }   (copper; perDay -1 = not known yet; piles: Hoard SPEC 240, only when given)
    24 Nov 2026: the rows are NOT inside an "if ... then ... end" (Lua 5.1 cannot jump over a block that big: "control structure too long", and
    then the game reads NONE of CrowdPrices.lua, TSM's prices included). They are built in a local table, BLOCK_CHUNK rows per little function
    (each Lua 5.1 function holds at most 262,143 different numbers and words), and handed to Hoard by one short "if" at the end."""
    def row(k):
        low, usual, qty = summary[k]
        rate = rates.get(k)
        return f"{{{int(low)},{int(usual)},{int(qty)},{_num(rate) if rate is not None else '-1'}}}"
    items = sorted((int(k), k) for k in summary if ":" not in k)
    rolls = sorted(((int(k.split(":")[0]), int(k.split(":")[1])), k) for k in summary if ":" in k)
    safe_name = "".join(ch for ch in str(name) if ch.isalnum() or ch in " -")[:40] or "your auction house"
    out = ["-- Blizzard's own hourly auction-house snapshot for your auction house (tools\\blizzard_prices.py, Hoard SPEC.md section 230)",
           "do",
           f'local B = {{ v = 1, house = {int(house)}, name = "{safe_name}", updated = {int(updated)}, '
           f"hours = {_num(hours)}, count = {len(items)}, items = {{}}, rolls = {{}}{', piles = {}' if piles else ''} }}",
           "local function put(into, rows) for k, v in pairs(rows) do into[k] = v end end"]

    def chunks(where, lines):
        for i in range(0, len(lines), BLOCK_CHUNK):
            out.append(f"put(B.{where}, (function() return {{")
            out.extend(lines[i:i + BLOCK_CHUNK])
            out.append("} end)())")
    chunks("items", [f"[{iid}]={row(k)}," for iid, k in items])
    chunks("rolls", [f'["{iid}:{rand}"]={row(k)},' for (iid, rand), k in rolls])
    if piles:
        chunks("piles", [f"[{int(iid)}]={{{int(n)},{int(unit)}}}," for iid, (n, unit) in sorted(piles.items())])
    out.append('if type(Hoard) == "table" and type(Hoard.CrowdPrices) == "table" then Hoard.CrowdPrices.blizzard = B end')
    out.append("end")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# one auto-update step

def _house_name(region, ruleset=RULESET, faction=FACTION):
    rules = {1: "PvP", 2: "Normal", 3: "Roleplay", 15: "Roleplay PvP"}.get(ruleset, "")
    return f"{region.upper()} {rules} {faction.title()}".strip()


def _stamp(t):
    import time as _t
    return _t.strftime("%d %b %H:%M", _t.gmtime(t)) if t else "?"


def refresh(now, last=None, http=None, data_dir=None, tsm_csv=None, region=REGION):
    """Keep data\\blizzard\\block.lua.txt fresh. last = what the previous call returned. Returns (record to remember, log text). Never raises.
    No credentials: ({...}, "") and nothing happens."""
    last = dict(last) if isinstance(last, dict) else {}
    rec = dict(last)
    notes = []
    try:
        data = Path(data_dir or DATA_DIR)
        creds = read_credentials(data)
        if not creds:
            return last, ""
        http = http or http_request
        tried, ok = _int(last.get("tried"), 0, 1e12) or 0, bool(last.get("ok", True))
        if tried and 0 <= now - tried < (14 * 60 if ok else FAIL_RETRY):
            return last, ""
        snap = _int(last.get("snap"), 0, 1e12) or 0
        house = _int(last.get("house"), 1, 1e9)
        matched = _int(last.get("matched"), 0, 1e12) or 0
        need_match = house is None or now - matched >= MATCH_EVERY
        if house is not None and not need_match and snap and 0 <= now - snap < SNAPSHOT_EVERY:
            return last, ""
        rec["tried"] = int(now)
        token = get_token(creds, now, http=http, data_dir=data)
        auth = {"Authorization": "Bearer " + token}

        # 1. which house is the owner's (compared with TSM's file for EU Normal Horde)
        if need_match:
            m = dict(last.get("match")) if isinstance(last.get("match"), dict) and now - (_int(last["match"].get("since"), 0, 1e12) or 0) < MATCH_EVERY else {}
            if m.get("wait") and now < m["wait"]:
                rec["ok"] = True
                return rec, ""
            if not m.get("todo"):
                _st, _h, body = http(api_url(INDEX_PATH.format(ruleset=RULESET), region), auth)
                index = json.loads(body.decode("utf-8"))
                try:
                    _write_json(data / "index.json", index)
                except OSError:
                    pass
                houses = list_houses(index)
                if not houses:
                    raise ValueError("Blizzard listed no auction houses")
                m = {"since": int(now), "todo": [h["id"] for h in faction_houses(houses)], "scores": {}}
            tsm_min = tsm_min_buyouts(tsm_csv)
            if len(tsm_min) < MATCH_MIN_COMMON:
                rec.update(ok=True, match=m)
                notes.append("Blizzard prices: waiting for TSM's file for your auction house to tell which Blizzard house is yours")
                return rec, " | ".join(notes)
            for hid in list(m["todo"])[:MATCH_PER_RUN]:
                _st, _h, body = http(api_url(HOUSE_PATH.format(ruleset=RULESET, house=hid), region), auth)
                rows = parse_auctions(json.loads(body.decode("utf-8")))
                score, common = match_score(lowest_prices(rows), tsm_min)
                m["scores"][str(hid)] = [round(score, 3), common]
                m["todo"].remove(hid)
            if m["todo"]:
                rec.update(ok=True, match=m)
                return rec, ""
            won = pick_house({int(k): tuple(v) for k, v in m["scores"].items()})
            said = ", ".join(f"{k}: {v[0]:.0%} of {v[1]}" for k, v in sorted(m["scores"].items()))
            if won is None:
                m.update(todo=[], wait=int(now) + MATCH_RETRY)
                rec.update(ok=True, match=m)
                notes.append(f"Blizzard prices: no house clearly matches your auction house yet ({said}); looking again in 6 hours")
                return rec, " | ".join(notes)
            if won != house:
                rec.pop("snap", None)
                rec.pop("lastmod", None)
                snap = 0
                for p in ("prev.json.gz", "sales.json"):
                    try:
                        (data / p).unlink()
                    except OSError:
                        pass
            house = won
            rec.update(house=won, matched=int(now), match={})
            notes.append(f"Blizzard prices: your auction house is Blizzard house {won} ({said})")

        # 2. the newest snapshot of the owner's house
        hdrs = dict(auth)
        if rec.get("lastmod") and (data / "prev.json.gz").is_file():
            hdrs["If-Modified-Since"] = str(rec["lastmod"])[:100]
        status, rh, body = http(api_url(HOUSE_PATH.format(ruleset=RULESET, house=house), region), hdrs)
        rec["ok"] = True
        if status == 304:
            return rec, " | ".join(notes)
        doc = json.loads(body.decode("utf-8"))
        com_note = ""
        if isinstance(doc, dict) and isinstance(doc.get("commodities"), dict):
            # 24 Nov 2026 (the gold study): "commodities" is only a LINK on Forever; cloth, ore, herbs and potions sit behind it. Our own
            # address is asked (never the link's), with the same sign-in; a failure keeps the rest of the snapshot.
            try:
                _cs, _ch, cbody = http(api_url(COMMODITIES_PATH.format(ruleset=RULESET, house=house), region), auth)
                cdoc = json.loads(cbody.decode("utf-8"))
                lst = cdoc.get("auctions") if isinstance(cdoc, dict) else None
                if not isinstance(lst, list):
                    lst = cdoc.get("commodities") if isinstance(cdoc, dict) else None
                doc["commodities"] = lst if isinstance(lst, list) else []
                com_note = f"; {len(doc['commodities'])} commodity listings"
            except Exception as exc:  # noqa: BLE001
                doc["commodities"] = []
                com_note = f"; commodities not read ({_why(exc)})"
            body = json.dumps(doc, separators=(",", ":")).encode("utf-8")      # the kept snapshot holds both downloads
        rows = parse_auctions(doc)
        if not rows:
            raise ValueError("the snapshot lists no auctions")
        new_snap = _http_time(rh.get("last-modified")) or int(now)
        if snap and new_snap <= snap:
            rec["snap"] = snap
            return rec, " | ".join(notes)
        try:
            smp = _read_json(data / "sample.json", {})
            if not isinstance(smp, dict) or smp.get("v") != 2 or now - (data / "sample.json").stat().st_mtime > 86400:
                aucs = doc.get("auctions") if isinstance(doc.get("auctions"), list) else []
                com = doc.get("commodities") if isinstance(doc.get("commodities"), list) else []
                with_bonus = [a for a in aucs if isinstance(a, dict) and isinstance(a.get("item"), dict) and a["item"].get("bonus_lists")]
                _write_json(data / "sample.json", {
                    "v": 2, "keys": sorted(doc.keys()), "auctions": aucs[:25], "commodities": com[:10], "counts": {
                        "auctions": len(aucs), "commodities": len(com), "with_bonus_lists": len(with_bonus),
                        "rolls_named": sum(1 for r in rows if r[2]), "bonus_map": len(read_bonus_map())},
                    "with_bonus_lists": with_bonus[:15]})
        except (OSError, AttributeError):
            pass
        prev = _read_json(data / "prev.json.gz", {})
        prev_ids = prev.get("ids") if isinstance(prev, dict) else None
        prev_snap = _int(prev.get("snap"), 0, 1e12) if isinstance(prev, dict) else 0
        days = _read_json(data / "sales.json", {})
        days = days if isinstance(days, dict) else {}
        gap = new_snap - (prev_snap or 0)
        if isinstance(prev_ids, dict) and prev_snap and 0 < gap <= MAX_GAP:
            add_sales(days, new_snap, gap / 3600.0, vanished_sales(prev_ids, rows, gap))
            try:
                log_sold(data, prev_snap, new_snap, vanished_listings(prev_ids, rows, gap))
            except (OSError, ValueError, TypeError):
                pass                                   # the sold log is for the grader only: it never stops Hoard's figures
        _write_json(data / "prev.json.gz", {"snap": new_snap, "ids": snapshot_index(rows, prev_ids if isinstance(prev_ids, dict) else None,
                                                                              new_snap if 0 < gap <= MAX_GAP else 0)})
        _write_json(data / "sales.json", days)
        rates, hours = per_day(days, new_snap)
        summary = summarize(rows)
        _write_atomic(data / BLOCK_FILE, block_lua(house, _house_name(region), new_snap, summary, rates, hours, piles=piles(rows)))
        rec.update(snap=new_snap, lastmod=rh.get("last-modified") or "", count=len(rows))
        try:
            kept = "; " + keep_snapshot(data, new_snap, body)
        except (OSError, ValueError, TypeError) as exc:
            kept = f"; snapshot not kept ({_why(exc)})"   # the history is for the backtest only: it never stops Hoard's figures
        notes.append(f"Blizzard prices: {len([k for k in summary if ':' not in k])} items, {len(rows)} listings "
                     f"(snapshot {_stamp(new_snap)} UTC, sales from {hours:.0f} h of snapshots{com_note}{kept})")
        return rec, " | ".join(notes)
    except Exception as exc:  # noqa: BLE001 - a courtesy: never fails a run
        rec["ok"] = False
        notes.append(f"Blizzard prices: skipped, the old ones stay ({_why(exc)})")
        return rec, " | ".join(notes)


def block_text(data_dir=None):
    """The Lua block for Hoard\\CrowdPrices.lua, or "" when there is none yet."""
    try:
        return (Path(data_dir or DATA_DIR) / BLOCK_FILE).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return ""


def main(argv=None):
    import time
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--show" not in argv:
        print(__doc__)
        return 0
    creds = read_credentials()
    if not creds:
        print("No Blizzard codes yet: open 'Hoard Blizzard Sign-in' (tools\\blizzard_signin.py).")
        return 1
    ok, said = check_credentials(creds[0], creds[1], time.time())
    print(f"Blizzard lists {said} auction houses for {_house_name(REGION)}'s ruleset." if ok else said)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

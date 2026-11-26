#!/usr/bin/env python3
"""
crowd_prices.py -- TradeSkillMaster's free crowd prices for WoW Forever, handed to Hoard in the game folder.

In plain words: TradeSkillMaster (TSM) pools the auction-house prices its many users see and publishes, for each region, one free public
file of them (no account, no key, no TSM addon or app): https://public-data.tradeskillmaster.com/forever/eu/region/items.csv
Every few hours the auto-updater on the owner's PC downloads that file and turns it into <AddOns>\\Hoard\\CrowdPrices.lua, which Hoard reads at
login / /reload (Hoard/Crowd.lua). Hoard shows the crowd price and how many sell a day across the region next to its own price, and uses the
crowd price only where it has no price of its own; the sniper never buys on it (Hoard SPEC.md section 199).

the owner's realm is on the EU region (CAMWOW EU5 Realm 3 PvE), hence REGION = "eu". Personal use on the owner's own PC only: the data is never
committed to a repo or put in a release (the copy of CrowdPrices.lua in the Hoard repo is an empty placeholder, like UpdateStatus.lua).

YOUR OWN AUCTION HOUSE TOO (Hoard SPEC.md section 226, the owner 22 Nov 2026: "Can we get more accurate TSM data then?"): WoW Forever has no
named realms; each ruleset is one mega-server per region, and its auction house is shared by every player of that ruleset AND FACTION in
the region. TSM publishes that auction house's own prices as a second free file ("realm" in TSM's words; checked 22 Nov 2026, no account):
    https://public-data.tradeskillmaster.com/forever/eu/realm/normal-horde/items.csv   (itemId,name,marketValue,minBuyout,recent,historical,updatedAt)
the owner plays Horde on Normal (PvE), so REALM = "normal-horde". It is the same market Hoard scans, so it is far closer than the EU-wide figure
(which mixes PvE, PvP, RP and both factions). TSM says realm files change "every few hours"; the region file "daily". The realm file has no
sale rate, so "sells N a day" still comes from the region file. Both files are kept: Hoard prefers the realm figure, the region one fills gaps.
TSM's terms (tradeskillmaster.com/public-data, read 22 Nov 2026): free in your own addons and tools; "Download each file at most once per
update". So the realm file is ASKED for every 15 minutes (each auto-update run) with a "only if changed since" question (If-None-Match / If-Modified-Since): an unchanged
file answers "not modified" and nothing is downloaded. If TSM's server gives no such tag, the realm file is asked for every 3 hours instead.
The last good copy of each file is kept in tools\\data\\tsm\\ (git-ignored, the owner's PC only) so either can be rewritten without the other.

Rules (tests: tools/tests/test_crowd_prices.py):
  * region file: at most one download every FETCH_EVERY_SECONDS (3 hours; TSM says region data changes daily), FAIL_RETRY_SECONDS after a failure;
  * realm file: asked every REALM_EVERY_SECONDS (10 minutes, so on every 15-minute auto-update run) only while TSM's server answers "not modified" questions, else every 3 hours;
  * a download that fails, or a file that does not look right (wrong columns, too few rows), changes nothing: the old CrowdPrices.lua stays;
  * the file is rewritten only when TSM's own updatedAt changed (or the file is missing), through a temp name and an atomic replace;
  * only numbers are written (no item names), so whatever TSM's file holds, the Lua loads; only when Hoard is installed (no folder is made);
  * never raises: a crowd-price problem never fails, slows past FETCH_TIMEOUT, or marks as stuck an auto-update run;
  * history (for the backtester, hoard-tools backtest/tsm_rules.py): when the auto-updater passes history_dir (tools\\data\\tsm-history),
    each new TSM file is also kept there once, gzipped, named by TSM's own date (tsm-eu-region-20261121-0056.csv.gz, tsm-eu-normal-horde-..., about 150 KB a day), so a
    later backtest judges each old scan against the TSM price of THAT day, not today's. A history problem never stops the price update.

By hand (for a look, writes nothing):  python tools\\crowd_prices.py --show
"""
import csv
import gzip
import hashlib
import io
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REGION = "eu"
URL = "https://public-data.tradeskillmaster.com/forever/{region}/region/items.csv"
REALM = "normal-horde"                                 # Forever: ruleset-faction auction house (Normal = PvE). the owner: Horde, PvE
REALM_URL = "https://public-data.tradeskillmaster.com/forever/{region}/realm/{realm}/items.csv"
REALM_EVERY_SECONDS = 10 * 60                          # so every 15-minute auto-update run asks (a run can start a few seconds early) (the owner 23 Nov: "keep it up to date"); only with an "only if changed" tag from TSM's server; without one, FETCH_EVERY_SECONDS
CACHE_DIR = Path(__file__).resolve().parent / "data" / "tsm"   # last good copy of each file (git-ignored data folder, the owner's PC only)
GAME_ADDON, GAME_FILE = "Hoard", "CrowdPrices.lua"     # <game>\Interface\AddOns\Hoard\CrowdPrices.lua, read by Hoard at login (Crowd.lua)
FETCH_EVERY_SECONDS = 3 * 60 * 60
FAIL_RETRY_SECONDS = 60 * 60
FETCH_TIMEOUT = 20
MAX_BYTES = 8 * 1024 * 1024                            # the real file is about 55 KB; anything this big is not it
MIN_ROWS = 50                                          # fewer usable rows than this = a broken or empty file (TSM served header-only files for KR/TW once)
MAX_ROWS = 60000
MAX_COPPER = 100000000000                              # 10^11 copper, the same sanity ceiling Hoard's Share.lua and Community.lua use
USER_AGENT = "HoardCrowdPrices/1"
NEEDED = ("itemId", "marketValue", "historical", "avgSalePrice", "saleRate", "soldPerDay", "updatedAt")
REALM_NEEDED = ("itemId", "marketValue", "minBuyout", "recent", "historical", "updatedAt")
RULESETS = {"normal": "Normal", "pvp": "PvP", "rp": "RP", "roleplay": "RP", "hardcore": "Hardcore"}


def parse_time(text):
    """TSM's updatedAt ("2026-11-20T00:22:49Z") -> epoch seconds (int), or 0 when it cannot be read."""
    try:
        t = str(text).strip()
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return 0


def _copper(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f != f or f < 0 or f > MAX_COPPER:
        return 0
    return int(round(f))


def _rate(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f < 0 or f > 1e7:
        return 0.0
    return round(f, 3)


def _parse(text, needed, pick):
    """The shared CSV reader: columns found by NAME; pick(rec, col) -> row tuple or None. Returns (rows, newest updatedAt)."""
    if not isinstance(text, str):
        raise ValueError("not text")
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise ValueError("empty file")
    missing = [c for c in needed if c not in header]
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))
    col = {c: header.index(c) for c in needed}
    width = max(col.values()) + 1
    rows, updated = {}, 0
    for rec in reader:
        if len(rows) >= MAX_ROWS:
            break
        if len(rec) < width:
            continue
        try:
            item_id = int(rec[col["itemId"]])
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or item_id > 10000000:
            continue
        row = pick(rec, col)
        if row is None:
            continue
        rows[item_id] = row
        updated = max(updated, parse_time(rec[col["updatedAt"]]))
    if len(rows) < MIN_ROWS:
        raise ValueError(f"only {len(rows)} usable rows")
    return rows, updated


def parse_csv(text):
    """TSM's region items.csv -> (rows, updated). rows = {itemID: (marketValue, historical, avgSalePrice, saleRate, soldPerDay)} with prices
    in copper; updated = the newest updatedAt (epoch). Columns are found by NAME (a reordered file still reads). Raises ValueError when the file
    is not TSM's price file (missing columns, too few usable rows)."""
    def pick(rec, col):
        mv = _copper(rec[col["marketValue"]])
        if mv <= 0:
            return None
        return (mv, _copper(rec[col["historical"]]), _copper(rec[col["avgSalePrice"]]),
                _rate(rec[col["saleRate"]]), _rate(rec[col["soldPerDay"]]))
    return _parse(text, NEEDED, pick)


def parse_realm_csv(text):
    """TSM's realm items.csv (for Forever: one ruleset-faction auction house) -> (rows, updated).
    rows = {itemID: (marketValue, minBuyout, recent, historical)} in copper (0 = TSM gave none). Raises ValueError like parse_csv."""
    def pick(rec, col):
        mv = _copper(rec[col["marketValue"]])
        if mv <= 0:
            return None
        return (mv, _copper(rec[col["minBuyout"]]), _copper(rec[col["recent"]]), _copper(rec[col["historical"]]))
    return _parse(text, REALM_NEEDED, pick)


def realm_name(region, realm):
    """"eu", "normal-horde" -> "EU Normal Horde" (plain words only, safe to write into Lua)."""
    parts = str(realm).split("-")
    ruleset = RULESETS.get(parts[0], "") if parts else ""
    faction = {"horde": "Horde", "alliance": "Alliance"}.get(parts[-1], "") if len(parts) > 1 else ""
    words = [w for w in (str(region).upper() if region in ("eu", "us", "kr", "tw") else "EU", ruleset, faction) if w]
    return " ".join(words)


def _safe_slug(realm):
    t = str(realm).lower()
    return t if t and len(t) <= 60 and all(ch.isalnum() or ch == "-" for ch in t) else REALM


def _num(f):
    """A rate as short Lua text: 0.371 -> "0.371", 16.0 -> "16"."""
    s = f"{f:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def crowd_lua(rows, updated, fetched, region=REGION, realm=None, extra=""):
    """The text of Hoard\\CrowdPrices.lua. Only numbers and fixed words are written, so it always loads. It sets a FIELD of the Hoard
    table (no new global). items[itemID] = { marketValue, historical, avgSalePrice, saleRate, soldPerDay } (copper; rates as TSM gives them).
    realm (optional, SPEC 226) = {"slug", "rows": {itemID: (marketValue, minBuyout, recent, historical)}, "updated"}: written as
    Hoard.CrowdPrices.realm = { slug, name, updated, count, items = { [itemID] = { marketValue, minBuyout, recent, historical } } }."""
    rows = rows or {}
    reg = region if region in ("eu", "us", "kr", "tw") else "eu"
    out = ["-- Hoard/CrowdPrices.lua -- WRITTEN BY THE AUTO-UPDATER on this PC (tools\\crowd_prices.py). Not edited by hand; the copy in the Hoard repo",
           "-- is an empty placeholder. TradeSkillMaster's free crowd prices for WoW Forever, one figure per item for the whole region (every realm,",
           "-- ruleset and faction together). updated = TSM's own time stamp, fetched = when this PC downloaded it (epoch seconds).",
           "-- items[itemID] = { marketValue, historical, avgSalePrice, saleRate, soldPerDay } (copper). Read by Hoard/Crowd.lua. Personal use only.",
           "-- realm = TSM's figures for YOUR auction house (ruleset + faction), items[itemID] = { marketValue, minBuyout, recent, historical } (SPEC 226).",
           "Hoard = Hoard or {}",
           f'Hoard.CrowdPrices = {{ v = 1, source = "TradeSkillMaster", region = "{reg}", '
           f"updated = {int(updated or 0)}, fetched = {int(fetched)}, count = {len(rows)}, items = {{"]
    for item_id in sorted(rows):
        mv, hist, avg, rate, per_day = rows[item_id]
        out.append(f"[{int(item_id)}]={{{int(mv)},{int(hist)},{int(avg)},{_num(rate)},{_num(per_day)}}},")
    if isinstance(realm, dict) and realm.get("rows"):
        slug = _safe_slug(realm.get("slug", REALM))
        rrows = realm["rows"]
        out.append(f'}}, realm = {{ slug = "{slug}", name = "{realm_name(reg, slug)}", updated = {int(realm.get("updated") or 0)}, '
                   f"count = {len(rrows)}, items = {{")
        for item_id in sorted(rrows):
            mv, mb, rec, hist = rrows[item_id]
            out.append(f"[{int(item_id)}]={{{int(mv)},{int(mb)},{int(rec)},{int(hist)}}},")
        out.append("} }")
    out.append("} }" if out[-1] != "} }" else "}")
    text = "\n".join(out) + "\n"
    if extra:
        # Blizzard's hourly snapshot for your auction house (blizzard_prices.py, Hoard SPEC 230): already plain numbers, set as Hoard.CrowdPrices.blizzard
        text += extra if extra.endswith("\n") else extra + "\n"
    return text


def download(url, timeout=FETCH_TIMEOUT):
    """The file's text. Raises on any failure (the caller turns it into a log note)."""
    text, _tags = download_if_changed(url, None, timeout)
    return text


def download_if_changed(url, tags=None, timeout=FETCH_TIMEOUT):
    """Ask for the file "only if changed" (tags = {"etag", "lastmod"} from the last good download). Returns (text, new tags), or (None, tags)
    when TSM's server answers 304 Not Modified (nothing is downloaded). Raises on any other failure."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv"}
    tags = tags if isinstance(tags, dict) else {}
    if tags.get("etag"):
        headers["If-None-Match"] = str(tags["etag"])[:200]
    if tags.get("lastmod"):
        headers["If-Modified-Since"] = str(tags["lastmod"])[:100]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 - a fixed https URL
            data = resp.read(MAX_BYTES + 1)
            new = {"etag": (resp.headers.get("ETag") or "")[:200], "lastmod": (resp.headers.get("Last-Modified") or "")[:100]}
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, tags
        raise
    if len(data) > MAX_BYTES:
        raise ValueError("file far bigger than expected")
    return data.decode("utf-8", errors="replace"), {k: v for k, v in new.items() if v}


def _whole(v):
    try:
        f = float(v)
        return int(f) if f == f and abs(f) < 1e15 else 0
    except (TypeError, ValueError):
        return 0


def save_history(history_dir, text, updated, region=REGION, kind="region"):
    """Keep one gzipped copy of TSM's raw file per TSM date in history_dir (kind = "region", or the realm slug such as "normal-horde"). Returns the new file's path, or None (no folder given, already kept,
    or any problem). Never raises."""
    try:
        if not history_dir or not updated or not isinstance(text, str):
            return None
        folder = Path(history_dir)
        name = f"tsm-{region}-{kind}-{datetime.fromtimestamp(int(updated), tz=timezone.utc):%Y%m%d-%H%M}.csv.gz"
        path = folder / name
        if path.exists():
            return None
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
                fh.write(text)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return path
    except Exception:  # noqa: BLE001 - history is a courtesy for the backtester
        return None


def _ours(path):
    """True when the game folder's file is one this updater wrote (not missing, not the repo's empty placeholder)."""
    try:
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            return "WRITTEN BY THE AUTO-UPDATER" in fh.readline()
    except OSError:
        return False


def _has_realm(path):
    """True when the game folder's file already carries the realm block (so an updater upgrade rewrites it once)."""
    try:
        return "realm = {" in Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return False


def _get(fetch, url, tags):
    """(text or None when unchanged, tags). fetch = None: the real "only if changed" download. A test's fake fetch(url) may return text,
    or (text_or_None, tags), and may take tags as a second argument."""
    if fetch is None:
        return download_if_changed(url, tags)
    try:
        import inspect
        two = len(inspect.signature(fetch).parameters) >= 2
    except (TypeError, ValueError):
        two = False
    got = fetch(url, tags) if two else fetch(url)
    if isinstance(got, tuple) and len(got) == 2:
        return got[0], (got[1] if isinstance(got[1], dict) else {})
    return got, {}


def _read_cache(path, parser):
    try:
        return parser(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or damaged copy is simply fetched again
        return None


def _write_atomic(path, text, encoding):
    path = Path(path)
    tmp = path.with_name(path.name + ".update-tmp")
    try:
        tmp.write_text(text, encoding=encoding, newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _stamp(t):
    return f"{datetime.fromtimestamp(t, tz=timezone.utc):%d %b %H:%M}"


def _block(blizzard_dir, timing=True):
    """Blizzard's block for the game file (blizzard_prices.py), then the best-times block (blizzard_timing.py, Hoard SPEC 241) when there is
    one, or "" (none yet / not asked for). timing=False: Blizzard's block alone (the safety net below)."""
    if blizzard_dir is None:
        return ""
    try:
        import blizzard_prices
        text = blizzard_prices.block_text(blizzard_dir)
    except Exception:  # noqa: BLE001
        text = ""
    text = text if text.startswith("-- Blizzard") and len(text) < 16 * 1024 * 1024 else ""
    if timing:
        try:
            import blizzard_timing
            more = blizzard_timing.block_text(blizzard_dir)
        except Exception:  # noqa: BLE001
            more = ""
        if more.startswith("-- When things sell") and len(more) < 4 * 1024 * 1024:
            text = (text + ("" if not text or text.endswith("\n") else "\n") + more) if text else more
    return text


def lua_problem(text):
    """Why WoW's Lua 5.1 could not read this text, or None (it reads fine, or lupa is not installed on this PC so it cannot be checked).
    Only compiles it (loadstring): nothing in it runs. 24 Nov 2026: a game file that does not compile loads NOTHING, TSM's prices included."""
    try:
        from lupa import lua51
    except Exception:  # noqa: BLE001 - no lupa: nothing to check with
        return None
    try:
        lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        err = lua.eval("function(s) local f, e = loadstring(s) if f then return nil end return tostring(e) end")(text)
    except Exception:  # noqa: BLE001
        return None
    return " ".join(str(err).split())[-160:] if err else None


def refresh(addons_dir, now, last=None, fetch=None, region=REGION, realm=REALM, cache_dir=None, history_dir=None, blizzard_dir=None):
    """Keep <AddOns>\\Hoard\\CrowdPrices.lua fresh. last = what the previous call returned ({} or None at first); fetch(url) -> text
    (None = download); history_dir = also keep a dated copy of each new TSM file there (save_history; None = keep none). Returns (record to remember, log text: "" when there is nothing worth logging). Never raises.
    The record keeps the region file's tried/ok/updated/count at the top (as before SPEC 226) and the realm file's under "realm".
    realm = None: the region file only."""
    last = dict(last) if isinstance(last, dict) else {}
    try:
        folder = Path(addons_dir) / GAME_ADDON
        if not folder.is_dir():
            return last, ""
        path = folder / GAME_FILE
        cache = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        mine = _ours(path)
        notes = []
        rec = dict(last)
        changed = False

        # --- the region file (every few hours) ---
        reg_cache = cache / f"region-{region}.csv"
        reg = _read_cache(reg_cache, parse_csv)
        tried, ok_before = _whole(last.get("tried")), bool(last.get("ok"))
        wait = FETCH_EVERY_SECONDS if ok_before else FAIL_RETRY_SECONDS
        due = not (tried and 0 <= now - tried < wait and (mine or not ok_before)) or (reg is None and ok_before)
        if due:
            rec["tried"] = int(now)
            try:
                text, tags = _get(fetch, URL.format(region=region), last.get("tags") if reg is not None else None)
                if text is not None:
                    rows, updated = parse_csv(text)
                    save_history(history_dir, text, updated, region)
                    if reg is None or updated != reg[1] or not mine:
                        changed = True
                    reg = (rows, updated)
                    try:
                        cache.mkdir(parents=True, exist_ok=True)
                        _write_atomic(reg_cache, text, "utf-8")
                    except OSError:
                        pass
                    if tags:
                        rec["tags"] = tags
                    else:
                        rec.pop("tags", None)
                rec["ok"] = True
            except Exception as exc:  # noqa: BLE001
                rec["ok"] = False
                why = " ".join(f"{type(exc).__name__}: {exc}".split())[:160]
                notes.append(f"TSM prices: download failed, the old ones stay ({why})")

        # --- the realm file: YOUR auction house (SPEC 226) ---
        rrec = dict(last.get("realm")) if isinstance(last.get("realm"), dict) else {}
        rel = None
        if realm:
            slug = _safe_slug(realm)
            rel_cache = cache / f"realm-{region}-{slug}.csv"
            rel = _read_cache(rel_cache, parse_realm_csv)
            r_tried, r_ok = _whole(rrec.get("tried")), bool(rrec.get("ok"))
            r_wait = (REALM_EVERY_SECONDS if rrec.get("tags") else FETCH_EVERY_SECONDS) if r_ok else FAIL_RETRY_SECONDS
            r_due = (not (r_tried and 0 <= now - r_tried < r_wait)) or (rel is None and r_ok) or (rel is not None and not _has_realm(path))
            if r_due:
                rrec["tried"] = int(now)
                try:
                    text, tags = _get(fetch, REALM_URL.format(region=region, realm=slug), rrec.get("tags") if rel is not None else None)
                    if text is not None:
                        rows, updated = parse_realm_csv(text)
                        save_history(history_dir, text, updated, region, slug)
                        if rel is None or updated != rel[1]:
                            changed = True
                        rel = (rows, updated)
                        try:
                            cache.mkdir(parents=True, exist_ok=True)
                            _write_atomic(rel_cache, text, "utf-8")
                        except OSError:
                            pass
                        rrec.update(updated=int(updated), count=len(rows))
                        if tags:
                            rrec["tags"] = tags
                        else:
                            rrec.pop("tags", None)
                    rrec["ok"] = True
                except Exception as exc:  # noqa: BLE001
                    rrec["ok"] = False
                    why = " ".join(f"{type(exc).__name__}: {exc}".split())[:160]
                    notes.append(f"TSM prices (your auction house): download failed, the old ones stay ({why})")
            if rel is not None and not _has_realm(path):
                changed = True
            rec["realm"] = rrec

        # --- Blizzard's hourly snapshot (blizzard_prices.py, Hoard SPEC 230): rides in the same game file, rewritten when it changes ---
        block = _block(blizzard_dir)
        bzsig = hashlib.sha256(block.encode("ascii", "replace")).hexdigest()[:16] if block else ""
        bz_changed = blizzard_dir is not None and bzsig != (last.get("bzsig") or "")
        if (changed or bz_changed or not mine) and (reg is not None or rel is not None):
            rows, updated = reg if reg is not None else ({}, rel[1])
            realm_doc = {"slug": realm, "rows": rel[0], "updated": rel[1]} if rel is not None else None
            text = crowd_lua(rows, updated, now, region, realm_doc, block)
            if block:
                why = lua_problem(text)
                if why:
                    # the safety net (24 Nov 2026): a Blizzard block the game could not read would stop the whole file loading, so TSM's prices go without it.
                    # 25 Nov 2026 (Hoard SPEC 241): the best-times block goes first; Blizzard's prices stay when they read fine without it.
                    alone = _block(blizzard_dir, timing=False)
                    if alone and alone != block and not lua_problem(crowd_lua(rows, updated, now, region, realm_doc, alone)):
                        text = crowd_lua(rows, updated, now, region, realm_doc, alone)
                        notes.append(f"Best times: left out, the game could not have read them ({why})")
                    else:
                        text = crowd_lua(rows, updated, now, region, realm_doc, "")
                        notes.append(f"Blizzard prices: left out, the game could not have read them ({why})")
            _write_atomic(path, text, "ascii")
            if blizzard_dir is not None:
                rec["bzsig"] = bzsig
            if not (changed or not mine):
                return rec, " | ".join(notes)                     # only Blizzard's part changed: its own step logs it
            if reg is not None:
                rec.update(updated=int(reg[1]), count=len(reg[0]))
            said = []
            if reg is not None:
                said.append(f"{len(reg[0])} items for {region.upper()} (dated {_stamp(reg[1])} UTC)")
            if rel is not None:
                said.append(f"{len(rel[0])} for your auction house {realm_name(region, realm)} (dated {_stamp(rel[1])} UTC)")
            notes.append("TSM prices: " + "; ".join(said))
        return rec, " | ".join(notes)
    except Exception as exc:  # noqa: BLE001 - crowd prices are a courtesy: they never fail or stop a run
        return last, f"TSM prices: skipped ({type(exc).__name__}: {' '.join(str(exc).split())[:120]})"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--show" not in argv:
        print(__doc__)
        return 0
    code = 0
    for what, url, parser in (("region", URL.format(region=REGION), parse_csv),
                              ("your auction house", REALM_URL.format(region=REGION, realm=REALM), parse_realm_csv)):
        try:
            rows, updated = parser(download(url))
            print(f"{what}: {len(rows)} items, dated {datetime.fromtimestamp(updated, tz=timezone.utc):%Y-%m-%d %H:%M} UTC (nothing written)")
        except Exception as exc:  # noqa: BLE001
            print(f"{what}: could not read TSM's file: {exc}")
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())

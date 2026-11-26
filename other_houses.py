#!/usr/bin/env python3
"""
other_houses.py -- every OTHER EU auction house of WoW Forever (Horde and Alliance, every rule type: Normal, PvP, Roleplay, Roleplay
PvP; Neutral too if TSM lists one), collected every hour in the cloud, for comparison only.

In plain words (the owner, 26 Nov 2026: "Do you think having the data from the other auction houses would help give a better reference point
for the items that we're unsure of?" ... "How many auction houses are there in total? I'm just trying to get the most robust data
possible."): the hourly cloud robot (blizzard_cloud.py) now also keeps every other house's hourly snapshot and its sold log, Horde first. NOTHING here reaches the game: not CrowdPrices.lua, not Hoard's figures, not the phone alerts. After about a week the
design thread "Design layered item valuation process" tests whether those houses' prices, scaled, predict what sells on the owner's house.

    1. which house is which (once a week): the same way the owner's house 114 was found. Blizzard's auction lists for every ruleset give the
       candidate houses; TSM's free public file for each ruleset+faction (pvp-horde, rp-alliance, ...) gives that
       house's lowest buyouts; the one Blizzard house that clearly agrees with a TSM file is that ruleset's house
       (blizzard_prices.match_score / pick_house). the owner's own file (normal-horde) is scored too: it must come out as house 114.
    2. every hour: each matched house's snapshot (with its commodities) and sold log, with the SAME blizzard_prices.refresh as the owner's
       house, each house in its own folder.

Kept in the public repo's releases (pre-releases, never git history):
    "blizzard-others"                  survey.json (which house is which, and the scores), state.tar.gz (what each house looked like
                                       last hour), sold-<house>-<day>.json.gz (35 days)
    "blizzard-others-<house>-<month>"  every hourly snapshot of that house (kept OTHER_SNAP_KEEP_DAYS days: ~3 MB an hour per house)

Never raises: a problem goes into the summary (status.json "others") and the owner's own house carries on regardless.
Tests: tools/tests/test_other_houses.py.
"""
import io
import json
import tarfile
import time
from pathlib import Path

OTHERS_TAG = "blizzard-others"
SNAP_TAG = "blizzard-others-{house}-{month}"
SURVEY_ASSET = "survey.json"
STATE_ASSET = "state.tar.gz"
SOLD_ASSET = "sold-{house}-{day}.json.gz"

RULESETS = (1, 3, 15, 2)                               # Forever (community-read, Nov 2026): 1 PvP, 2 Normal, 3 Roleplay, 15 Roleplay PvP
_RULES = {"normal": "Normal", "pvp": "PvP", "rp": "RP", "roleplay": "RP", "rp-pvp": "RP-PvP", "rppvp": "RP-PvP", "roleplay-pvp": "RP-PvP",
          "pvp-rp": "RP-PvP"}                          # several spellings are tried: the ones TSM's server has win
FACTIONS = {"horde": "Horde", "alliance": "Alliance", "neutral": "Neutral"}   # Horde first: the owner plays Horde
TSM_REALMS = {f"{r}-{f}": f"{rl} {fl}" for f, fl in FACTIONS.items() for r, rl in _RULES.items()}   # TSM file name -> "PvP Horde" etc.
MAIN_LABEL = "Normal Horde"                            # the owner's own house (114): collected by blizzard_prices as before, never here
OTHERS_BUDGET = 25 * 60                                # one hourly run spends at most this long on the other houses (Horde ones first)
SURVEY_EVERY = 7 * 86400
SURVEY_RETRY = 6 * 3600                                # nothing matched: look again later
OTHER_SNAP_KEEP_DAYS = 14                              # the comparison needs a week or two of snapshots; the sold logs (small) stay 35 days
SOLD_KEEP_DAYS = 35
MAX_OTHERS = 16                                        # never more houses than this, whatever a survey finds (Forever EU, 26 Nov 2026: 15 listed)
SURVEY_V = 2                                           # 2: houses with listings that match no TSM file are kept too (26 Nov: 116, 117)
HOUSE_FILES = ("prev.json.gz", "sales.json")           # what each house carries from one hour to the next


def _day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _why(exc):
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:160]


def _asset(rel, name):
    for a in (rel or {}).get("assets", []):
        if a.get("name") == name:
            return a
    return None


# ---------------------------------------------------------------------------
# 1. which house is which

def survey(now, B, http, token, tsm_get, work_dir, main_house):
    """-> survey dict: {"at", "houses": {label: {"house", "ruleset", "tsm", "score", "common"}}, "scores": {tsm: {house: [score, common]}},
    "listed": {ruleset: [house ids]}, "notes": [..]}. tsm_get(url) -> text (raises when TSM has no such file)."""
    import crowd_prices
    auth = {"Authorization": "Bearer " + token}
    out = {"v": SURVEY_V, "at": int(now), "houses": {}, "scores": {}, "listed": {}, "notes": []}
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    tsm = {}
    for slug in TSM_REALMS:
        try:
            text = tsm_get(crowd_prices.REALM_URL.format(region=B.REGION, realm=slug))
            if not text:
                continue
            p = work / f"tsm-{slug}.csv"
            p.write_text(text, encoding="utf-8")
            mins = B.tsm_min_buyouts(p)
            if len(mins) >= B.MATCH_MIN_COMMON:
                tsm[slug] = mins
        except Exception:  # noqa: BLE001 - most spellings do not exist on TSM's server
            continue
    if not tsm:
        out["notes"].append("TSM served none of the realm files")
        return out

    where = {}                                          # house id -> the ruleset whose list named it first
    for r in RULESETS:
        try:
            _st, _h, body = http(B.api_url(B.INDEX_PATH.format(ruleset=r)), auth)
            houses = B.list_houses(json.loads(body.decode("utf-8")))             # every side: Horde, Alliance (and Neutral)
            out["listed"][str(r)] = [h["id"] for h in houses]
            for h in houses:
                where.setdefault(h["id"], r)
        except Exception as exc:  # noqa: BLE001
            out["notes"].append(f"ruleset {r}: no auction list ({_why(exc)})")

    lows = {}
    for hid, r in sorted(where.items()):
        try:
            _st, _h, body = http(B.api_url(B.HOUSE_PATH.format(ruleset=r, house=hid)), auth)
            lows[hid] = B.lowest_prices(B.parse_auctions(json.loads(body.decode("utf-8"))))
        except Exception as exc:  # noqa: BLE001
            out["notes"].append(f"house {hid}: not read ({_why(exc)})")

    best = {}                                           # label -> (score, house, slug, common)
    for slug, mins in tsm.items():
        scores = {hid: B.match_score(low, mins) for hid, low in lows.items()}
        out["scores"][slug] = {str(h): [round(s, 3), c] for h, (s, c) in sorted(scores.items())}
        won = B.pick_house(scores)
        if won is None:
            out["notes"].append(f"{slug}: no house clearly matches")
            continue
        label = TSM_REALMS[slug]
        s, c = scores[won]
        if label not in best or s > best[label][0]:
            best[label] = (s, won, slug, c)

    taken = {}
    for label, (s, hid, slug, c) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        if hid in taken:
            out["notes"].append(f"{slug}: house {hid} already matched {taken[hid]}")
            continue
        taken[hid] = label
        out["houses"][label] = {"house": hid, "ruleset": where[hid], "tsm": slug, "score": round(s, 3), "common": c}
    # houses with real listings that no TSM file matches (on 26 Nov 2026 houses 116 and 117: most likely the neutral, goblin ones):
    # kept too, last in line, under their number
    for hid, low in sorted(lows.items()):
        if hid not in taken and len(low) >= B.MATCH_MIN_COMMON:
            out["houses"][f"House {hid} (no TSM file)"] = {"house": hid, "ruleset": where[hid], "tsm": "", "score": 0, "common": len(low)}
    normal = out["houses"].get(MAIN_LABEL)
    if normal and normal["house"] != main_house:
        out["notes"].append(f"check: TSM's normal-horde matched house {normal['house']}, not {main_house}")
    return out


def others_of(sv, main_house):
    """[(label, house, ruleset)] to collect: every matched house except the owner's own, Horde ones first, at most MAX_OTHERS."""
    order = {fl: i for i, fl in enumerate(FACTIONS.values())}
    rows = [(label, int(h["house"]), int(h["ruleset"])) for label, h in (sv or {}).get("houses", {}).items()
            if isinstance(h, dict) and label != MAIN_LABEL and int(h.get("house") or 0) and int(h["house"]) != int(main_house)]
    rows.sort(key=lambda r: (order.get(r[0].rsplit(" ", 1)[-1], 9), r[0]))
    return rows[:MAX_OTHERS]


# ---------------------------------------------------------------------------
# the state each house carries from one hour to the next

def pack(folder, houses):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        names = ["houses.json"] + [f"{h}/{n}" for h in houses for n in HOUSE_FILES]
        for rel in names:
            p = Path(folder) / rel
            if not p.is_file():
                continue
            raw = p.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size, info.mtime, info.mode = len(raw), 0, 0o644
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def unpack(blob, folder, houses):
    allowed = {"houses.json"} | {f"{h}/{n}" for h in houses for n in HOUSE_FILES}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name not in allowed or not m.isfile() or m.size > 256 * 1024 * 1024:
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            p = Path(folder) / m.name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f.read())


# ---------------------------------------------------------------------------
# 2. one hourly run (called by blizzard_cloud.collect after the owner's own house)

def run(now, api, creds, work_dir, main_house, blizzard_http=None, tsm_fetch=None):
    """Returns (summary for status.json, log lines). Never raises."""
    import blizzard_prices as B
    import crowd_prices
    log, summary = [], {"ok": False, "why": "", "houses": {}}
    folder = Path(work_dir) / "others"
    try:
        http = blizzard_http or B.http_request
        rel = api.get_release(OTHERS_TAG) or api.create_release(
            OTHERS_TAG, "Other auction houses (cloud data, not a version)",
            "Every other EU auction house of WoW Forever (Horde and Alliance, every rule type), collected for comparison only by other_houses.py. "
            "Nothing here reaches the game. Not an addon release.")
        sv = {}
        a = _asset(rel, SURVEY_ASSET)
        if a:
            try:
                sv = json.loads(api.download(a).decode("utf-8"))
            except (ValueError, OSError):
                sv = {}
        sv = sv if isinstance(sv, dict) else {}
        age = now - int(sv.get("at") or 0)
        due = not sv or sv.get("v") != SURVEY_V or age >= SURVEY_EVERY or (not sv.get("houses") and age >= SURVEY_RETRY)
        B.save_credentials(creds[0], creds[1], folder)  # this run's temporary folder only; never packed
        token = B.get_token(creds, now, http=http, data_dir=folder)
        if due:
            def tsm_get(url):
                return crowd_prices._get(tsm_fetch, url, None)[0]
            sv = survey(now, B, http, token, tsm_get, folder / "survey", main_house)
            api.upload(rel, SURVEY_ASSET, json.dumps(sv, indent=1, sort_keys=True).encode("utf-8"))
            found = ", ".join(f"{k} = house {v['house']} ({v['score']:.0%} of {v['common']})" for k, v in sorted(sv["houses"].items()))
            log.append("Other houses: " + (found or "none matched yet") + ("; " + "; ".join(sv["notes"]) if sv.get("notes") else ""))
        todo = others_of(sv, main_house)
        if not todo:
            summary["why"] = "no other house matched yet" + (f" ({'; '.join(sv.get('notes', []))[:200]})" if sv.get("notes") else "")
            return summary, log

        ids = [h for _l, h, _r in todo]
        a = _asset(rel, STATE_ASSET)
        if a:
            unpack(api.download(a), folder, ids)
        recs = {}
        try:
            recs = json.loads((folder / "houses.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recs = {}
        recs = recs if isinstance(recs, dict) else {}

        keep_ruleset = B.RULESET
        started = time.time()
        for label, hid, ruleset in todo:
            hdir = folder / str(hid)
            if time.time() - started > OTHERS_BUDGET:
                summary["houses"][label] = {"house": hid, "ok": True, "skipped": "out of time this hour"}
                log.append(f"{label} house {hid}: skipped this hour (the run's time for other houses is used up)")
                continue
            try:
                hdir.mkdir(parents=True, exist_ok=True)
                B.save_credentials(creds[0], creds[1], hdir)
                for day in (_day(now), _day(now - 86400)):
                    sa = _asset(rel, SOLD_ASSET.format(house=hid, day=day))
                    if sa:
                        p = hdir / B.SOLD_DIR / f"{day}.json.gz"
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(api.download(sa))
                last = dict(recs.get(str(hid)) or {})
                last.update(house=hid, matched=int(now))   # the survey above decides which house is which, not refresh's own check
                B.RULESET = ruleset
                try:
                    brec, btext = B.refresh(now, last, http=http, data_dir=hdir)
                finally:
                    B.RULESET = keep_ruleset
                recs[str(hid)] = {k: v for k, v in brec.items() if k in ("snap", "lastmod", "tried", "ok", "count", "house", "matched")}
                for p in sorted((hdir / B.SNAP_DIR).glob("*/*.json.gz")):
                    day = p.parent.name
                    tag = SNAP_TAG.format(house=hid, month=day[:7])
                    srel = api.get_release(tag) or api.create_release(
                        tag, f"{label} auction house {hid}, hourly snapshots {day[:7]} (cloud data, not a version)",
                        "Every hourly Blizzard auction snapshot of this house, as Blizzard sent it (gzip), for comparison only. "
                        "Written by other_houses.py. Not an addon release.")
                    api.upload(srel, f"{day}T{p.name}", p.read_bytes())
                    p.unlink()
                new_snap = int(brec.get("snap") or 0)
                if new_snap and new_snap != int(last.get("snap") or 0):
                    for p in sorted((hdir / B.SOLD_DIR).glob("*.json.gz")):
                        if p.name[:10] >= _day(new_snap - 86400):
                            api.upload(rel, SOLD_ASSET.format(house=hid, day=p.name[:10]), p.read_bytes())
                summary["houses"][label] = {"house": hid, "snap": new_snap or int(last.get("snap") or 0),
                                            "count": int(brec.get("count") or 0), "ok": bool(brec.get("ok"))}
                if btext:
                    log.append(f"{label} house {hid}: {btext}")
            except Exception as exc:  # noqa: BLE001 - one house failing never stops the others
                summary["houses"][label] = {"house": hid, "ok": False, "why": _why(exc)}
                log.append(f"{label} house {hid}: failed ({_why(exc)})")
        (folder / "houses.json").write_text(json.dumps(recs, sort_keys=True), encoding="utf-8")
        api.upload(rel, STATE_ASSET, pack(folder, ids))

        # tidy: old sold days and snapshot months
        for sa in list(rel.get("assets", [])):
            n = sa["name"]
            if n.startswith("sold-") and n.endswith(".json.gz") and n[-18:-8] < _day(now - SOLD_KEEP_DAYS * 86400):
                api.delete_asset(sa)
                rel["assets"].remove(sa)
        oldest = _day(now - OTHER_SNAP_KEEP_DAYS * 86400)
        for r in api.list_releases():
            t = r["tag_name"]
            if t.startswith("blizzard-others-") and t != OTHERS_TAG:
                month = t[-7:]
                if month < oldest[:7]:
                    api.delete_release(r)
                    continue
                if month == oldest[:7]:
                    full = api.get_release(t) or {}
                    for sa in list(full.get("assets", [])):
                        if sa["name"][:10] < oldest:
                            api.delete_asset(sa)
        summary["ok"] = all(h.get("ok") for h in summary["houses"].values())
        if not summary["ok"]:
            summary["why"] = "; ".join(f"{k}: {v.get('why') or 'not read'}" for k, v in summary["houses"].items() if not v.get("ok"))[:300]
    except Exception as exc:  # noqa: BLE001
        summary["ok"] = False
        summary["why"] = f"other houses failed: {_why(exc)}"
        log.append(summary["why"])
    return summary, log


def fetch(api, now, out_dir, days=2):
    """For the comparison study: survey.json, each other house's sold days and its newest snapshot into out_dir/others/<house>/.
    Returns the paths written."""
    out, wrote = Path(out_dir) / "others", []
    rel = api.get_release(OTHERS_TAG)
    if not rel:
        return wrote
    a = _asset(rel, SURVEY_ASSET)
    if not a:
        return wrote
    raw = api.download(a)
    (out).mkdir(parents=True, exist_ok=True)
    (out / SURVEY_ASSET).write_bytes(raw)
    wrote.append(out / SURVEY_ASSET)
    sv = json.loads(raw.decode("utf-8"))
    for _label, hid, _r in others_of(sv, 0):
        hdir = out / str(hid)
        for d in range(days):
            day = _day(now - d * 86400)
            sa = _asset(rel, SOLD_ASSET.format(house=hid, day=day))
            if sa:
                p = hdir / "sold" / f"{day}.json.gz"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(api.download(sa))
                wrote.append(p)
        for m in sorted({_day(now)[:7], _day(now - 86400)[:7]}, reverse=True):
            names = sorted((x for x in (api.get_release(SNAP_TAG.format(house=hid, month=m)) or {}).get("assets", [])
                            if x["name"].endswith("Z.json.gz")), key=lambda x: x["name"])
            if names:
                p = hdir / "snapshots" / names[-1]["name"][:10] / names[-1]["name"][11:]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(api.download(names[-1]))
                wrote.append(p)
                break
    return wrote

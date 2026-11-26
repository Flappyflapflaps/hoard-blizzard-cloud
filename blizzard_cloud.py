#!/usr/bin/env python3
"""
blizzard_cloud.py -- Blizzard's hourly auction snapshot collected in the cloud (GitHub), so it keeps coming while the owner's PC is off.

In plain words (the owner, 24 Nov 2026: "And there's no way to do this without the PC being on?" ... "Yeah, let's make that happen."):
Until now the owner's PC read Blizzard's hourly snapshot of his auction house itself (blizzard_prices.py, every auto-update run). With the PC
off, nothing was collected: no price history, no sale counts, no phone alerts. Now a free GitHub robot runs the SAME code once an hour on
GitHub's computers. It runs in its own small PUBLIC repo, Flappyflapflaps/hoard-blizzard-cloud (CLOUD_REPO), built from these files by
cloud/make_public_repo.py: public repos get GitHub Actions minutes free and unlimited, and the private repos' free minutes ran out in
Nov 2026 (the owner, 25 Nov: "we used up all our free minutes"). No addon code goes there; only this collector, the price, alert and TSM
readers it needs, the public item table and four small Hoard tables:

    CLOUD (collect, every hour)                               PC (sync, every auto-update run while it is on)
    1. gets back what it knew last hour                       1. reads the cloud's status (one small file)
    2. reads Blizzard's newest snapshot (blizzard_prices)     2. downloads every hourly snapshot it does not have yet (14 days back)
    3. keeps the snapshot whole, and the sold log             3. takes the cloud's sold log days
    4. sends the phone alerts (deal_alerts, same rules)       4. once the cloud has run a whole day: takes the cloud's figures for Hoard
    5. puts it all back for the next hour and for the PC         instead of downloading the snapshot itself
                                                              5. stays quiet on the phone while the cloud sends the alerts

Where it is kept: GitHub "releases" of that public repo, never in git history (a snapshot is ~3 MB with commodities, ~75 MB a day; Blizzard's
auction data is public anyway):
    release "blizzard-cloud"            status.json, state.tar.gz (what the robot remembers), alerts-state.json, sold-<day>.json.gz
    release "blizzard-snapshots-<month>" one <day>T<HHMM>Z.json.gz per hourly snapshot (as Blizzard sent it, gzip); kept 62 days in the cloud
Both are marked "pre-release", so nothing that looks for the latest release ever sees them. The PC keeps its own copy for good
(blizzard_prices.keep_snapshot's 30 GB rule).

The two Blizzard codes and the phone's ntfy topic live ONLY in that repo's GitHub Actions secrets BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET and
HOARD_NTFY_TOPIC (the owner pastes them once on GitHub's own page; secrets stay locked in a public repo too). They are never written to a
release, a log or the repo. Without the codes the robot only says "waiting for the Blizzard codes"; without the topic it sends no alerts.

The PC stays the backup: when the cloud has nothing newer than 2.5 hours, the PC reads Blizzard itself exactly as before (and alerts as
before), so a GitHub outage loses nothing while the PC is on. The PC reads the public releases with the GitHub sign-in it already uses for
"git fetch" (Git Credential Manager); no new password. No sign-in = the PC simply carries on alone, as before.

Cost: nothing. Standard GitHub runners are free and unlimited for public repos (in a private repo the hourly run would use about 720 of the
~2,000 free minutes a month). Release files do not count against the Actions storage.

Rules (tests: tools/tests/test_blizzard_cloud.py):
  * the cloud run never fails loudly: every problem becomes "ok": false and a reason in status.json (a red run every hour would email the owner);
  * the PC side never raises and never fails or marks as stuck an auto-update run; no sign-in or no cloud = nothing changes on the PC;
  * nothing secret is ever uploaded: credentials.json, token.json and alerts/settings.json (the topic) are never put in state.tar.gz;
  * the PC only takes the cloud's figures for the same Blizzard house it uses, and only from a cloud that has run for a whole day;
  * one alert per deal: while the cloud's alerts are live the PC sends none, and the PC learns which listings the cloud already sent.

By hand:  python tools/blizzard_cloud.py --collect   (what the GitHub workflow runs; needs GITHUB_TOKEN and the two Blizzard secrets)
          python tools/blizzard_cloud.py --status    (on the PC: what the cloud says, downloads nothing else)
          python tools/blizzard_cloud.py --fetch <folder> [--days 2]   (status.json, the sold log of the last days and the newest
                                                     snapshot, e.g. for the daily strategy run in a Claude cloud session)
"""
import gzip
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

REPO = "Flappyflapflaps/hoard-tools"                    # this repo: where the PC's git sign-in comes from
CLOUD_REPO = "Flappyflapflaps/hoard-blizzard-cloud"    # the small PUBLIC repo the hourly robot runs in (public = free Actions minutes; 25 Nov 2026)
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
USER_AGENT = "HoardBlizzardCloud/1"
TIMEOUT = 60

STATE_TAG = "blizzard-cloud"
SNAP_TAG = "blizzard-snapshots-{month}"                # one release a month: at most 31 x 24 = 744 files (GitHub allows 1,000)
STATUS_ASSET = "status.json"
STATE_ASSET = "state.tar.gz"
ALERTS_ASSET = "alerts-state.json"
SOLD_ASSET = "sold-{day}.json.gz"

HOUSE = 114                                            # EU Normal Horde: the house the owner's PC matched with TSM on 24 Nov 2026 (re-checked weekly)
# the phone's ntfy topic comes ONLY from the robot's secret HOARD_NTFY_TOPIC (never in code or a release: anyone with it could read the pushes)
CLOUD_SNAP_KEEP_DAYS = 62                              # the PC keeps its own copy for good
SOLD_KEEP_DAYS = 35                                    # = blizzard_prices.SOLD_KEEP_DAYS

LIVE_FOR = int(2.5 * 3600)                             # the cloud counts as live while its newest snapshot is younger than this
PROVEN_AFTER = 24 * 3600                               # the PC takes the cloud's figures only after the cloud has run this long
SYNC_EVERY = 10 * 60                                   # the PC looks at most this often (every auto-update run is 15 minutes)
SYNC_FAIL_RETRY = 30 * 60
SYNC_DAYS = 14                                         # snapshots the PC fetches back (older ones stay in the cloud for 62 days)
SYNC_MAX_FILES = 40                                    # snapshot files per PC run (~70 MB), newest first; the rest next run
STATE_FILES = ("blizzard/prev.json.gz", "blizzard/sales.json", "blizzard/block.lua.txt", "blizzard/index.json",
               "tsm/region-eu.csv", "tsm/realm-eu-normal-horde.csv", "alerts/state.json", "cloud.json")   # never alerts/settings.json (the topic)
NEVER_UPLOAD = ("credentials.json", "token.json")
MAX_ASSET = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# small helpers

def _why(exc):
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:160]


def _read_json(path, default=None):
    try:
        p = Path(path)
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return default


def _write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path, doc):
    _write_bytes(path, json.dumps(doc, indent=1, sort_keys=True).encode("utf-8"))


def _day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _stamp(t):
    return time.strftime("%d %b %H:%M", time.gmtime(t)) if t else "?"


def snapshot_asset_name(day, file_name):
    """data\\blizzard\\snapshots\\2026-11-24\\1823Z.json.gz -> "2026-11-24T1823Z.json.gz" (release blizzard-snapshots-2026-11)."""
    return f"{day}T{file_name}"


def snapshot_local_path(snap_dir, asset_name):
    """The reverse: "2026-11-24T1823Z.json.gz" -> <snap_dir>/2026-11-24/1823Z.json.gz, or None for any other name."""
    name = str(asset_name)
    if len(name) != len("2026-11-24T1823Z.json.gz") or name[10] != "T" or not name.endswith("Z.json.gz"):
        return None
    day, hhmm = name[:10], name[11:15]
    if not (day[:4].isdigit() and day[4] == "-" and day[5:7].isdigit() and day[7] == "-" and day[8:].isdigit() and hhmm.isdigit()):
        return None
    return Path(snap_dir) / day / f"{hhmm}Z.json.gz"


# ---------------------------------------------------------------------------
# GitHub releases (the only place the cloud keeps anything)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None                                       # a release file answers with a redirect to storage: followed by hand, WITHOUT the token


class GitHubReleases:
    """Just the release calls the cloud and the PC need. token = a GitHub token (the workflow's GITHUB_TOKEN, or the PC's git sign-in).
    http(method, url, headers, data) -> (status, headers, body) can be swapped in by a test."""

    def __init__(self, token, repo=REPO, http=None):
        self.repo = repo
        self.token = token
        self.http = http or self._http

    @staticmethod
    def _http(method, url, headers=None, data=None):
        req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(MAX_ASSET + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308, 404, 422):
                return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() if exc.code != 302 else b""
            raise

    def _auth(self, extra=None):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        h.update(extra or {})
        return h

    def _json(self, method, path, doc=None):
        data = json.dumps(doc).encode("utf-8") if doc is not None else None
        hdrs = self._auth({"Content-Type": "application/json"} if data is not None else None)
        status, _h, body = self.http(method, API + path, hdrs, data)
        if status == 404:
            return None
        if status >= 300:
            raise ValueError(f"GitHub answered {status} to {method} {path.split('?')[0]}")
        return json.loads(body.decode("utf-8")) if body else {}

    def get_release(self, tag):
        """The release with this tag ({id, tag_name, assets: [{id, name, size, updated_at}]}) or None. Every asset, not just the first page."""
        rel = self._json("GET", f"/repos/{self.repo}/releases/tags/{urllib.parse.quote(tag)}")
        if not rel:
            return None
        assets, page = [], 1
        while True:
            got = self._json("GET", f"/repos/{self.repo}/releases/{rel['id']}/assets?per_page=100&page={page}") or []
            assets.extend({"id": a["id"], "name": a["name"], "size": a.get("size", 0), "updated_at": a.get("updated_at", "")} for a in got)
            if len(got) < 100:
                break
            page += 1
        rel["assets"] = assets
        return rel

    def list_releases(self):
        out, page = [], 1
        while page < 20:
            got = self._json("GET", f"/repos/{self.repo}/releases?per_page=100&page={page}") or []
            out.extend({"id": r["id"], "tag_name": r["tag_name"]} for r in got)
            if len(got) < 100:
                break
            page += 1
        return out

    def create_release(self, tag, name, body):
        rel = self._json("POST", f"/repos/{self.repo}/releases",
                         {"tag_name": tag, "name": name, "body": body, "prerelease": True, "make_latest": "false"})
        rel["assets"] = []
        return rel

    def delete_release(self, rel):
        self._json("DELETE", f"/repos/{self.repo}/releases/{rel['id']}")
        self._json("DELETE", f"/repos/{self.repo}/git/refs/tags/{urllib.parse.quote(rel['tag_name'])}")

    def delete_asset(self, asset):
        self._json("DELETE", f"/repos/{self.repo}/releases/assets/{asset['id']}")

    def upload(self, rel, name, data):
        """Puts a file in a release, replacing one of the same name. Updates rel["assets"]."""
        for a in [a for a in rel.get("assets", []) if a["name"] == name]:
            self.delete_asset(a)
            rel["assets"].remove(a)
        url = f"{UPLOADS}/repos/{self.repo}/releases/{rel['id']}/assets?name={urllib.parse.quote(name)}"
        status, _h, body = self.http("POST", url, self._auth({"Content-Type": "application/octet-stream"}), bytes(data))
        if status >= 300:
            raise ValueError(f"GitHub answered {status} to the upload of {name}")
        a = json.loads(body.decode("utf-8"))
        rel.setdefault("assets", []).append({"id": a["id"], "name": a["name"], "size": a.get("size", len(data)),
                                             "updated_at": a.get("updated_at", "")})
        return a

    def download(self, asset):
        """The file's bytes. GitHub answers with a redirect to its file storage, which is followed without the token."""
        url = f"{API}/repos/{self.repo}/releases/assets/{asset['id']}"
        status, hdrs, body = self.http("GET", url, self._auth({"Accept": "application/octet-stream"}), None)
        for _hop in range(3):
            if status in (301, 302, 303, 307, 308) and hdrs.get("location"):
                status, hdrs, body = self.http("GET", hdrs["location"], {"Accept": "application/octet-stream"}, None)
                continue
            break
        if status != 200:
            raise ValueError(f"GitHub answered {status} to the download of {asset.get('name')}")
        if len(body) > MAX_ASSET:
            raise ValueError("file far bigger than expected")
        return body


def asset(rel, name):
    for a in (rel or {}).get("assets", []):
        if a["name"] == name:
            return a
    return None


# ---------------------------------------------------------------------------
# the state the cloud carries from one hour to the next

def pack_state(data_dir):
    """state.tar.gz bytes of STATE_FILES under data_dir (missing ones left out; credentials and the sign-in token never)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for rel in STATE_FILES:
            p = Path(data_dir) / rel
            if p.name in NEVER_UPLOAD or not p.is_file():
                continue
            raw = p.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size, info.mtime, info.mode = len(raw), 0, 0o644
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def unpack_state(blob, data_dir, only=STATE_FILES):
    """Writes the named files of a state.tar.gz into data_dir. Anything else in the archive (other names, folders, links) is ignored.
    Returns the names written."""
    wrote = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name not in only or not m.isfile() or Path(m.name).name in NEVER_UPLOAD or m.size > MAX_ASSET:
                continue
            f = tar.extractfile(m)
            if f is None:
                continue
            _write_bytes(Path(data_dir) / m.name, f.read())
            wrote.append(m.name)
    return wrote


# ---------------------------------------------------------------------------
# CLOUD: one hourly run (the GitHub workflow)

def _hello(ntfy_topic, http=None):
    import deal_alerts
    deal_alerts.send_ntfy(ntfy_topic, "Hoard deal alerts now come from the cloud",
                          "Blizzard's hourly auction data is now read on GitHub, so deal alerts keep coming while your PC is off "
                          "(a few a day at most, never 23:00-07:00). Nothing is ever bought for you.", http=http)


def collect(now, api, env, work_dir, repo_dir=THIS_DIR, blizzard_http=None, tsm_fetch=None, ntfy_http=None):
    """One hourly cloud run. Returns (status dict, log lines). Never raises: a problem is written into status.json as ok False + why."""
    import blizzard_prices as B
    import crowd_prices
    import deal_alerts
    log = []
    work, data = Path(work_dir), Path(work_dir) / "data"
    bz = data / "blizzard"
    status = {"v": 1, "updated": int(now), "ok": False, "why": ""}
    rel = None
    try:
        rel = api.get_release(STATE_TAG) or api.create_release(
            STATE_TAG, "Blizzard prices (cloud data, not a version)",
            "Written every hour by .github/workflows/blizzard-cloud.yml (tools/blizzard_cloud.py). Not an addon release: nothing installs it.")
        old = {}
        a = asset(rel, STATUS_ASSET)
        if a:
            try:
                old = json.loads(api.download(a).decode("utf-8"))
            except (ValueError, OSError):
                old = {}
        old = old if isinstance(old, dict) else {}
        for k in ("since", "snap", "house", "snaps", "hello", "alerts"):
            if k in old:
                status[k] = old[k]
        cid, sec = str(env.get("BLIZZARD_CLIENT_ID") or "").strip(), str(env.get("BLIZZARD_CLIENT_SECRET") or "").strip()
        if not cid or not sec:
            status["why"] = "waiting for the Blizzard codes (GitHub secrets BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET)"
            log.append(status["why"])
            api.upload(rel, STATUS_ASSET, json.dumps(status, indent=1, sort_keys=True).encode("utf-8"))
            return status, log

        # 1. what the robot knew last hour
        a = asset(rel, STATE_ASSET)
        if a:
            unpack_state(api.download(a), data)
        for day in (_day(now), _day(now - 86400)):
            a = asset(rel, SOLD_ASSET.format(day=day))
            if a:
                _write_bytes(bz / B.SOLD_DIR / f"{day}.json.gz", api.download(a))
        cloud = _read_json(data / "cloud.json", {})
        cloud = cloud if isinstance(cloud, dict) else {}
        B.save_credentials(cid, sec, bz)                     # this run's temporary folder only; never packed (NEVER_UPLOAD)
        last = cloud.get("blizzard") if isinstance(cloud.get("blizzard"), dict) else {}
        if not last.get("house"):
            last = {"house": int(env.get("HOARD_HOUSE") or HOUSE), "matched": int(now)}
        topic = str(env.get("HOARD_NTFY_TOPIC") or "").strip()
        topic = topic if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", topic) else ""
        settings = {"topic": topic or "hoard-no-topic-set-0000", "on": bool(topic)}   # rebuilt every run from the secret; never packed
        _write_json(data / "alerts" / "settings.json", settings)
        if not (data / "alerts" / "state.json").is_file():
            _write_json(data / "alerts" / "state.json", {"welcomed": int(now)})     # the cloud says its own hello (below), not the PC's

        # 2. TSM's files (the house check and the alerts use them), only-if-changed
        addons = work / "addons"
        (addons / "Hoard").mkdir(parents=True, exist_ok=True)
        crec, ctext = crowd_prices.refresh(addons, now, cloud.get("crowd"), fetch=tsm_fetch, cache_dir=data / "tsm")
        cloud["crowd"] = crec
        if ctext:
            log.append(ctext)

        # 3. Blizzard's newest snapshot, with the same code as the PC
        B.SNAP_MIN_FREE = 0                                  # GitHub's machine: the snapshot is uploaded and removed straight away
        B.SUFFIX_BONUS_FILE = Path(repo_dir) / "cloud" / "hoard" / "SuffixBonus_Data.lua"
        brec, btext = B.refresh(now, last, http=blizzard_http, data_dir=bz, tsm_csv=data / "tsm" / "realm-eu-normal-horde.csv")
        cloud["blizzard"] = brec
        if btext:
            log.append(btext)
        new_snap = int(brec.get("snap") or 0)
        got_new = bool(new_snap) and new_snap != int(last.get("snap") or 0)

        # 4. keep the snapshot and the sold log where the PC can fetch them
        for p in sorted((bz / B.SNAP_DIR).glob("*/*.json.gz")):
            day = p.parent.name
            srel = api.get_release(SNAP_TAG.format(month=day[:7])) or api.create_release(
                SNAP_TAG.format(month=day[:7]), f"Blizzard hourly snapshots {day[:7]} (cloud data, not a version)",
                "Every hourly Blizzard auction snapshot of the owner's auction house this month, as Blizzard sent it (gzip). Written by "
                "tools/blizzard_cloud.py. Not an addon release.")
            api.upload(srel, snapshot_asset_name(day, p.name), p.read_bytes())
            p.unlink()
        if got_new:
            for p in sorted((bz / B.SOLD_DIR).glob("*.json.gz")):
                if p.name[:10] >= _day(new_snap - 86400):
                    api.upload(rel, SOLD_ASSET.format(day=p.name[:10]), p.read_bytes())

        # 5. the phone alerts
        alerts = dict(status.get("alerts") or {})
        if got_new:
            text = deal_alerts.run(now, data_dir=data, hoard_dir=Path(repo_dir) / "cloud" / "hoard", http=ntfy_http)
            alerts.update(ok=bool(topic) and not text.startswith("Deal alerts: skipped"), checked=int(now),
                          why="" if topic else "no phone topic (GitHub secret HOARD_NTFY_TOPIC)")
            if text.startswith("Deal alerts: sent"):
                alerts["last_sent"] = int(now)
            if text:
                log.append(text)
            if topic and not status.get("hello") and alerts["ok"] and not deal_alerts.quiet_now(now, settings):
                try:
                    _hello(topic, http=ntfy_http)
                    status["hello"] = int(now)
                    log.append("Deal alerts: hello push sent (alerts now come from the cloud)")
                except Exception as exc:  # noqa: BLE001
                    log.append(f"Deal alerts: hello push failed ({_why(exc)})")
        status["alerts"] = alerts

        # 6. put it all back: the state first, then the status that points at it
        cloud["v"] = 1
        _write_json(data / "cloud.json", cloud)
        api.upload(rel, STATE_ASSET, pack_state(data))
        st_alerts = _read_json(data / "alerts" / "state.json", {})
        if got_new and isinstance(st_alerts, dict):
            api.upload(rel, ALERTS_ASSET, json.dumps({k: st_alerts.get(k) for k in ("sent", "keys", "day", "today")},
                                                     sort_keys=True).encode("utf-8"))
        snaps = [int(t) for t in (status.get("snaps") or []) if isinstance(t, (int, float)) and now - t < 2 * 86400]
        if got_new:
            snaps.append(new_snap)
            if not status.get("since"):
                status["since"] = new_snap
        status.update(snaps=sorted(set(snaps)), snap=new_snap or status.get("snap") or 0, house=brec.get("house"),
                      matched=brec.get("matched"), lastmod=brec.get("lastmod") or "", count=brec.get("count") or 0)
        status["ok"] = bool(brec.get("ok")) and bool(status["snap"])
        status["why"] = "" if status["ok"] else (btext or "no snapshot yet")[:300]

        # 7. tidy: sold days and snapshot months the cloud no longer keeps (the PC has its own copies)
        for a in list(rel.get("assets", [])):
            if a["name"].startswith("sold-") and a["name"][5:15] < _day(now - SOLD_KEEP_DAYS * 86400):
                api.delete_asset(a)
                rel["assets"].remove(a)
        oldest = _day(now - CLOUD_SNAP_KEEP_DAYS * 86400)[:7]
        for r in api.list_releases():
            t = r["tag_name"]
            if t.startswith("blizzard-snapshots-") and len(t) == len("blizzard-snapshots-2026-11") and t[-7:] < oldest:
                api.delete_release(r)
                log.append(f"removed the cloud copy of {t[-7:]} (older than {CLOUD_SNAP_KEEP_DAYS} days)")
    except Exception as exc:  # noqa: BLE001 - the robot never goes red: it says why in status.json
        status["ok"] = False
        status["why"] = f"cloud run failed: {_why(exc)}"
        log.append(status["why"])
    if rel is not None:
        try:
            api.upload(rel, STATUS_ASSET, json.dumps(status, indent=1, sort_keys=True).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.append(f"status not saved: {_why(exc)}")
    return status, log


# ---------------------------------------------------------------------------
# PC: fetch what the cloud collected (called by auto_update.py every run)

def git_token(tools_dir=THIS_DIR, runner=None):
    """The GitHub token Git already uses on this PC for "git fetch" (Git Credential Manager), or None. Never prompts, never shows a window.
    It is only ever sent to api.github.com."""
    inp = f"protocol=https\nhost=github.com\npath={REPO}.git\n\n"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="", SSH_ASKPASS="")
    cmd = ["git", "-c", "credential.interactive=false", "credential", "fill"]
    try:
        if runner:
            out = runner(cmd, inp)
        else:
            flags = 0x08000000 if os.name == "nt" else 0
            out = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=30, cwd=str(tools_dir), env=env,
                                 creationflags=flags).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in str(out or "").splitlines():
        if line.startswith("password="):
            tok = line[len("password="):].strip()
            return tok or None
    return None


def _missing_snapshots(api, now, snap_dir):
    """[(release, asset, local path)] for cloud snapshots of the last SYNC_DAYS days this PC does not have, newest first."""
    months = sorted({_day(now - d * 86400)[:7] for d in range(SYNC_DAYS + 1)})
    since = _day(now - SYNC_DAYS * 86400)
    out = []
    for m in months:
        rel = api.get_release(SNAP_TAG.format(month=m))
        for a in (rel or {}).get("assets", []):
            p = snapshot_local_path(snap_dir, a["name"])
            if p is None or a["name"][:10] < since or p.is_file():
                continue
            out.append((rel, a, p))
    out.sort(key=lambda x: x[1]["name"], reverse=True)
    return out


def _merge_alerts(path, cloud_doc, now):
    """The PC learns which listings and items the cloud already pushed, so a PC that takes over never sends them again."""
    mine = _read_json(path, {})
    mine = mine if isinstance(mine, dict) else {}
    changed = False
    for k in ("sent", "keys"):
        theirs = cloud_doc.get(k) if isinstance(cloud_doc.get(k), dict) else {}
        have = mine.get(k) if isinstance(mine.get(k), dict) else {}
        for key, t in theirs.items():
            if isinstance(t, (int, float)) and now - t < 3 * 86400 and float(have.get(key, 0)) < t:
                have[key] = int(t)
                changed = True
        mine[k] = have
    if cloud_doc.get("day") == _day(now) and int(cloud_doc.get("today") or 0) > (int(mine.get("today") or 0) if mine.get("day") == _day(now) else 0):
        mine.update(day=cloud_doc["day"], today=int(cloud_doc["today"]))
        changed = True
    if changed:
        _write_json(path, mine)
    return changed


def sync(now, last, bz_last, data_dir, api=None, token_fn=None):
    """The PC's part. last = what the previous call returned; bz_last = the PC's own blizzard_prices record.
    Returns (blizzard record to use instead of bz_last, or None; log text; record to remember). Never raises."""
    import blizzard_prices as B
    last = dict(last) if isinstance(last, dict) else {}
    rec = dict(last)
    notes = []
    try:
        data = Path(data_dir)
        bz = data / "blizzard"
        if not (bz / B.CREDS_FILE).is_file():
            return None, "", last                            # no Blizzard on this PC at all
        tried = int(last.get("tried") or 0)
        if tried and 0 <= now - tried < (SYNC_EVERY if last.get("ok", True) else SYNC_FAIL_RETRY):
            return None, "", last
        rec["tried"] = int(now)
        if api is None:
            token = (token_fn or git_token)()
            if not token:
                rec["ok"] = False
                said = "" if last.get("said_no_token") and now - last["said_no_token"] < 86400 else \
                    "Cloud prices: this PC has no GitHub sign-in for downloads, so it carries on alone"
                if said:
                    rec["said_no_token"] = int(now)
                return None, said, rec
            api = GitHubReleases(token, repo=CLOUD_REPO)
        rel = api.get_release(STATE_TAG)
        a = asset(rel, STATUS_ASSET)
        if not a:
            rec["ok"] = True
            return None, "", rec                             # the cloud has not run yet
        status = json.loads(api.download(a).decode("utf-8"))
        cloud_snap = int(status.get("snap") or 0)
        pc_house = (bz_last or {}).get("house") if isinstance(bz_last, dict) else None
        same_house = not pc_house or not status.get("house") or int(status["house"]) == int(pc_house)
        live = bool(status.get("ok")) and cloud_snap and 0 <= now - cloud_snap <= LIVE_FOR and same_house
        if not same_house:
            notes.append(f"Cloud prices: ignored (the cloud reads house {status.get('house')}, this PC house {pc_house})")

        # phone alerts: quiet on the PC while the cloud sends them
        alerts = status.get("alerts") if isinstance(status.get("alerts"), dict) else {}
        until = cloud_snap + LIVE_FOR if live and alerts.get("ok") else 0
        marker = data / "alerts" / "cloud.json"
        if (_read_json(marker, {}) or {}).get("until") != until:
            _write_json(marker, {"until": until, "checked": int(now)})
        aa = asset(rel, ALERTS_ASSET)
        if aa and aa.get("updated_at") != last.get("alerts_at"):
            doc = json.loads(api.download(aa).decode("utf-8"))
            if isinstance(doc, dict):
                _merge_alerts(data / "alerts" / "state.json", doc, now)
            rec["alerts_at"] = aa.get("updated_at")

        if not same_house:
            rec["ok"] = True
            return None, " | ".join(notes), rec

        # every hourly snapshot the PC missed (the history), within the drive's room
        got = 0
        free = B._free_bytes(bz)
        if free is None or free >= B.SNAP_MIN_FREE:
            for srel, sa, p in _missing_snapshots(api, now, bz / B.SNAP_DIR)[:SYNC_MAX_FILES]:
                _write_bytes(p, api.download(sa))
                got += 1
        if got:
            notes.append(f"Cloud prices: {got} hourly snapshot{'s' if got != 1 else ''} fetched from the cloud")

        # the sold log days (for the tip grader): the cloud's day replaces this PC's when it watched at least as many hours
        seen = dict(last.get("sold") or {})
        for sa in (rel or {}).get("assets", []):
            name = sa["name"]
            if not (name.startswith("sold-") and name.endswith(".json.gz")) or name[5:15] < _day(now - B.SOLD_KEEP_DAYS * 86400):
                continue
            if seen.get(name) == sa.get("updated_at"):
                continue
            blob = api.download(sa)
            theirs = json.loads(gzip.decompress(blob).decode("utf-8"))
            p = bz / B.SOLD_DIR / f"{name[5:15]}.json.gz"
            mine = _read_json(p, {})
            if len((theirs or {}).get("w") or []) >= len((mine or {}).get("w") or []):
                _write_bytes(p, blob)
            seen[name] = sa.get("updated_at")
        rec["sold"] = {k: v for k, v in seen.items() if k[5:15] >= _day(now - B.SOLD_KEEP_DAYS * 86400)}

        # Hoard's figures: from a cloud that has run a whole day, instead of the PC asking Blizzard itself
        adopted = None
        since = int(status.get("since") or 0)
        pc_snap = int((bz_last or {}).get("snap") or 0) if isinstance(bz_last, dict) else 0
        if live and since and now - since >= PROVEN_AFTER and cloud_snap > pc_snap:
            sa = asset(rel, STATE_ASSET)
            if sa:
                wrote = unpack_state(api.download(sa), data,
                                     only=("blizzard/prev.json.gz", "blizzard/sales.json", "blizzard/block.lua.txt"))
                if "blizzard/prev.json.gz" in wrote and "blizzard/block.lua.txt" in wrote:
                    adopted = dict(bz_last) if isinstance(bz_last, dict) else {}
                    adopted.update(snap=cloud_snap, lastmod=status.get("lastmod") or "", house=int(status.get("house") or pc_house or HOUSE),
                                   ok=True, tried=int(now), count=int(status.get("count") or 0))
                    if status.get("matched"):
                        adopted["matched"] = int(status["matched"])
                    adopted.pop("match", None)
                    notes.append(f"Cloud prices: Hoard's Blizzard figures taken from the cloud (snapshot {_stamp(cloud_snap)} UTC)")
        elif not live and status.get("since"):
            if not last.get("said_stale") or now - last["said_stale"] >= 6 * 3600:
                notes.append(f"Cloud prices: the cloud's newest snapshot is from {_stamp(cloud_snap)} UTC"
                             + (f" ({status.get('why')})" if status.get("why") else "") + "; this PC reads Blizzard itself")
                rec["said_stale"] = int(now)
        rec.update(ok=True, live=bool(live), snap=cloud_snap)
        return adopted, " | ".join(notes), rec
    except Exception as exc:  # noqa: BLE001 - a courtesy: never fails a run
        rec["ok"] = False
        notes.append(f"Cloud prices: skipped ({_why(exc)})")
        return None, " | ".join(notes), rec


# ---------------------------------------------------------------------------
# anyone else (the daily strategy run in a Claude cloud session): the sold log days and the newest snapshot, never credentials

def fetch(api, now, out_dir, days=2):
    """Downloads into out_dir: status.json, sold/<day>.json.gz for the last `days` days (today included) and the newest hourly snapshot as
    snapshots/<day>/<HHMM>Z.json.gz. Returns the paths written."""
    out, wrote = Path(out_dir), []
    rel = api.get_release(STATE_TAG)
    a = asset(rel, STATUS_ASSET)
    if a:
        _write_bytes(out / STATUS_ASSET, api.download(a))
        wrote.append(out / STATUS_ASSET)
    for d in range(days):
        day = _day(now - d * 86400)
        a = asset(rel, SOLD_ASSET.format(day=day))
        if a:
            _write_bytes(out / "sold" / f"{day}.json.gz", api.download(a))
            wrote.append(out / "sold" / f"{day}.json.gz")
    for m in sorted({_day(now)[:7], _day(now - 86400)[:7]}, reverse=True):
        names = sorted((a for a in (api.get_release(SNAP_TAG.format(month=m)) or {}).get("assets", [])
                        if snapshot_local_path(out, a["name"])), key=lambda a: a["name"])
        if names:
            p = snapshot_local_path(out / "snapshots", names[-1]["name"])
            _write_bytes(p, api.download(names[-1]))
            wrote.append(p)
            break
    return wrote


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--collect" in argv:
        import tempfile
        api = GitHubReleases(os.environ.get("GITHUB_TOKEN", ""), repo=os.environ.get("GITHUB_REPOSITORY") or CLOUD_REPO)
        work = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "hoard-cloud"
        status, log = collect(time.time(), api, os.environ, work)
        for line in log:
            print(line)
        print(("OK" if status.get("ok") else "NOT OK") + f": snapshot {_stamp(status.get('snap'))} UTC, house {status.get('house')}"
              + (f" ({status['why']})" if status.get("why") else ""))
        return 0                                             # never red: status.json says what went wrong
    if "--status" in argv:
        token = git_token()
        if not token:
            print("No GitHub sign-in found on this PC.")
            return 1
        api = GitHubReleases(token, repo=CLOUD_REPO)
        a = asset(api.get_release(STATE_TAG), STATUS_ASSET)
        print(api.download(a).decode("utf-8") if a else "The cloud has not run yet.")
        return 0
    if "--fetch" in argv:
        i = argv.index("--fetch")
        out = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("--") else "blizzard-cloud-data"
        days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 2
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or git_token() or ""   # "" = a session whose proxy signs in
        for p in fetch(GitHubReleases(token, repo=CLOUD_REPO), time.time(), out, days):
            print(p)
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# hoard-blizzard-cloud

An hourly GitHub robot that reads Blizzard's public auction-house snapshot of one WoW Forever auction house (EU Normal Horde) through
Blizzard's Game Data API, keeps each snapshot and a log of the listings that sold, and pushes a short note to the owner's phone when a
listing is clearly cheap. It never buys anything. It feeds the owner's private Hoard addons, which download what it collected.

* Runs: `.github/workflows/collect.yml`, every hour at :53 (and "Run workflow" by hand).
* Code: `blizzard_cloud.py` (the robot), `blizzard_prices.py` (reads the snapshot), `deal_alerts.py` (the phone note rules),
  `crowd_prices.py` (TradeSkillMaster's free public price files), `other_houses.py` (the other Horde houses: PvP, RP, RP-PvP,
  kept for comparison only). Python standard library only.
* Data: the `blizzard-cloud` and `blizzard-snapshots-<month>` pre-releases of this repo (snapshots kept 62 days); the other houses in
  `blizzard-others` and `blizzard-others-<house>-<month>` (snapshots kept 14 days, sold logs 35).
* Secrets: `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`, `HOARD_NTFY_TOPIC`. They are never printed, logged or uploaded.

This repo is built from the private hoard-tools repo by `cloud/make_public_repo.py`; change the code there, not here.

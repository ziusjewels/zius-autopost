# Zius Jewels autopost

Posts one WHOOP-case carousel a day to the **Zius Jewels Facebook Page** and **@ziusjewels on Instagram** at 21:00 Gulf time (22:30 India), from 8 Oct to 22 Nov 2026. Pinterest picks up each Instagram post automatically through the claimed Instagram account.

| File | What it is |
|---|---|
| `schedule.json` | The 46-day plan: date, SKU, photos, captions |
| `posts/` | The carousel photos (1080×1350) and captions for each day |
| `posted.json` | What has already gone out (updated by the robot) |
| `autopost.py` | The posting script |
| `.github/workflows/autopost.yml` | Runs the script every evening |

**Needs:** a repository secret `META_TOKEN` (Settings → Secrets and variables → Actions).

**Test:** Actions → "Daily Instagram + Facebook post" → Run workflow (Test only ticked).
**Post a specific day now:** Run workflow, untick Test only, enter the day number.
**Pause:** Actions → the workflow → ⋯ → Disable workflow.
**Change a caption or date:** edit `schedule.json`.

The Meta token lasts 60 days, which covers this plan. Make a new one before adding the next batch.

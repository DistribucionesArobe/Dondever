# Daily Instagram Reel

Render existing Dondever cron: `python post_reel.py`, daily at 18:00 UTC (12:00 Monterrey). Uses existing INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN and INTERNAL_API_KEY. No new paid service.

20s vertical 1080x1920 H.264, AAC 48kHz/128kbps. Verified upcoming same-day ESPN game, minimum 30 minutes before kickoff. Missing-country channels remain explicitly unconfirmed; no fallback guesses. NFL uses approved stadium design, other sports neutral studio. No eligible game: skip.

Music: All This, Kevin MacLeod, CC BY 4.0; attribution included in every caption. Font license files shipped.

Videos and publication checkpoints live on existing /data/reels disk. Videos expire after 14 days; checkpoints remain. Only MP4 routes are public. Private API uses constant-time header authentication. Duplicate reservations are rejected. Publish intent is saved before Meta publish; an uncertain failure will stop rather than post twice. Inspect checkpoints and Meta container manually before resetting a failed day.

Validation: `python test_reels.py`; `python post_reel.py --dry-run` checks private storage when configured and selects games without uploading/publishing. Official flow: https://www.postman.com/meta/instagram/folder/y6xustx/reels-publishing

Facebook Reel publication is not enabled by this Instagram cron.

## Memory fix and independent carousel
720x1280 output; only the current overlay is retained. x264 uses one thread, ultrafast, zerolatency and no lookahead. Audio is resampled to 48 kHz before and after normalization.
Render test: `python post_reel.py --render-test` generates a synthetic preview locally and never publishes.
Existing cron: `python post_daily_instagram.py`, schedule `0 13,18 * * *`: carousel at 13:00 UTC and Reel at 18:00 UTC (07:00 and 12:00 Monterrey). No second paid service.

## Approved Canva direction

The opener uses the exact approved Canva PNG (design DAHXo0VXJ2s). Live data scenes use navy, off-white, lime angular accents and matching condensed type. Canva is the editable visual master; cron renders automatically from the exported local asset and does not need a live Canva login. To change the opener, export the approved Canva page as 1080x1920 PNG and replace static/reels/canva-approved-cover.png.

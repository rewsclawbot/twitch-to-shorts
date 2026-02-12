# Instagram Reels Setup — Paused

## Status: Blocked on Meta Developer portal SMS verification

## Completed
- [x] Code implementation (src/instagram_uploader.py, pipeline integration, CI, tests)
- [x] Instagram Business/Creator account (Step 1)
- [x] Facebook Page linked to IG account (Step 2)

## Blocked
- [ ] Meta Developer App creation (Step 3) — SMS verification won't send code
  - Using existing FB account with verified number
  - Portal requires separate developer SMS verification
  - Tried: waiting, retrying — SMS not arriving
  - Try next: different browser, Google Voice number, business.facebook.com flow, Meta support

## Remaining After Unblock
- [ ] Add Instagram Graph API product to app (Step 3 cont.)
- [ ] Confirm permissions: instagram_basic, instagram_content_publish, pages_show_list (Step 4)
- [ ] Generate short-lived token via Graph API Explorer (Step 5)
- [ ] Get ig_user_id via /me/accounts query (Step 6)
- [ ] Exchange for long-lived token (Step 7)
  ```bash
  curl "https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=APP_SECRET&access_token=SHORT_LIVED_TOKEN"
  ```
- [ ] Create credentials/theburntpeanut_instagram.json:
  ```json
  {
    "access_token": "LONG_LIVED_TOKEN",
    "ig_user_id": "IG_USER_ID",
    "token_expiry": "YYYY-MM-DDTHH:MM:SSZ"
  }
  ```
- [ ] Set GitHub secret:
  ```bash
  base64 -w0 credentials/theburntpeanut_instagram.json | gh secret set INSTAGRAM_TOKEN_THEBURNTPEANUT
  ```
- [ ] Enable in config.yaml:
  - Set `instagram_enabled: true` in pipeline section
  - Uncomment `instagram_credentials` for TheBurntPeanut streamer
- [ ] Test with a single clip run
- [ ] Verify Reel appears on Instagram profile
- [ ] Verify temp GitHub release cleaned up
- [ ] Verify instagram_id populated in DB

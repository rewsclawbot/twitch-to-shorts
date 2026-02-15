#!/usr/bin/env python3
"""Re-authenticate with full YouTube scopes and rebrand the channel.

Run this interactively (it opens a browser for OAuth):
    cd ~/Projects/twitch-to-shorts-claw
    .venv/bin/python scripts/rebrand_channel.py

This will:
1. Delete existing credentials (to force re-auth with new scopes)
2. Open browser for OAuth with full youtube + force-ssl scopes
3. Update channel name, description, and keywords
"""
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.youtube_uploader import get_authenticated_service, SCOPES

CLIENT_SECRETS = "credentials/client_secret.json"
CREDENTIALS = "credentials/theburntpeanut_youtube.json"

NEW_CHANNEL_NAME = "ClipFrenzy"
NEW_DESCRIPTION = (
    "Daily gaming highlights from the best Twitch streamers. "
    "Clutch plays, funny moments, and viral clips — all in 60 seconds or less.\n\n"
    "New shorts every day! Subscribe for your daily dose of gaming highlights.\n\n"
    "#gaming #twitchclips #shorts #gamingclips #twitchhighlights"
)
NEW_KEYWORDS = "gaming twitch clips highlights shorts viral funny clutch moments streamers esports"


def main():
    os.chdir(Path(__file__).parent.parent)
    
    print(f"Required scopes: {SCOPES}")
    
    # Check if current credentials have all scopes
    creds_path = Path(CREDENTIALS)
    if creds_path.exists():
        with open(creds_path) as f:
            data = json.load(f)
        current_scopes = set(data.get("scopes", []))
        needed = set(SCOPES)
        missing = needed - current_scopes
        if missing:
            print(f"\nMissing scopes: {missing}")
            print("Deleting old credentials to force re-auth...")
            backup = str(creds_path) + ".bak"
            creds_path.rename(backup)
            print(f"Backup saved to {backup}")
        else:
            print("All scopes present in current credentials.")
    
    print("\n--- Authenticating (this may open a browser) ---")
    service = get_authenticated_service(CLIENT_SECRETS, CREDENTIALS)
    print("✅ Authentication successful!")
    
    # Get current channel info
    resp = service.channels().list(part="snippet,brandingSettings", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        print("❌ No channel found!")
        sys.exit(1)
    
    channel = items[0]
    channel_id = channel["id"]
    current_title = channel["snippet"]["title"]
    print(f"\nCurrent channel: {current_title} (ID: {channel_id})")
    
    # Update branding
    print(f"\nRebranding to: {NEW_CHANNEL_NAME}")
    body = {
        "id": channel_id,
        "brandingSettings": {
            "channel": {
                "title": NEW_CHANNEL_NAME,
                "description": NEW_DESCRIPTION,
                "keywords": NEW_KEYWORDS,
            }
        }
    }
    
    resp = service.channels().update(part="brandingSettings", body=body).execute()
    new_title = resp["brandingSettings"]["channel"]["title"]
    print(f"✅ Channel rebranded to: {new_title}")
    print(f"✅ Description updated")
    print(f"✅ Keywords set")
    print("\nDone! The channel is now '{}'.".format(new_title))
    print("Note: The @handle cannot be changed via API — do that manually in YouTube Studio if needed.")


if __name__ == "__main__":
    main()

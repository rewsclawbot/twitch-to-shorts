"""YouTube engagement features: first comment, pinned comment, etc.

Posts engagement-boosting first comments on uploaded shorts to
encourage interaction and signal activity to the algorithm.
"""
import logging
import os
import random

from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

# Comment templates designed to boost engagement
# Using {game}, {streamer}, {title} placeholders
_FIRST_COMMENT_TEMPLATES = [
    "What would you do in this situation? 🎮 Drop your answer below! 👇",
    "This {game} moment was wild 🔥 Who else plays {game}?",
    "Rate this play 1-10 ⬇️",
    "Full credit to {streamer} on Twitch for this insane clip 🙌",
    "Would you have survived this? 😂 Let me know below!",
    "If you enjoyed this, smash that subscribe button! More {game} clips coming 🚀",
    "Comment your favorite {game} moment! 🎯",
    "This is why {streamer} is built different 💪 Agree or disagree?",
    "POV: You're trying to do this in {game} 😅 How'd it go?",
    "Who's your favorite {game} streamer? Drop their name! 👇",
]

_PINNED_COMMENT_TEMPLATES = [
    "📺 Full credit: {streamer} on Twitch\n🎮 Game: {game}\n💬 Subscribe for daily gaming clips!",
    "🔥 Clip from {streamer}'s stream\n🎮 Playing: {game}\n👇 Drop a comment if you want more!",
]


def _render_comment(template: str, game: str, streamer: str, title: str) -> str:
    """Render a comment template with clip metadata."""
    return template.format(
        game=game or "this game",
        streamer=streamer or "the streamer",
        title=title or "this clip",
    )


def post_first_comment(
    service,
    video_id: str,
    game_name: str = "",
    streamer_name: str = "",
    clip_title: str = "",
    pin: bool = False,
) -> str | None:
    """Post an engagement-boosting first comment on a video.
    
    Args:
        service: Authenticated YouTube API service
        video_id: YouTube video ID
        game_name: Game name for template rendering
        streamer_name: Streamer name for template rendering
        clip_title: Clip title for template rendering
        pin: Whether to pin the comment (requires additional API call)
        
    Returns:
        Comment ID if successful, None otherwise
    """
    # Pick a random template — mix of engagement and credit comments
    templates = _FIRST_COMMENT_TEMPLATES
    if pin:
        # If we're pinning, use the more informative pinned templates
        templates = _PINNED_COMMENT_TEMPLATES
    
    template = random.choice(templates)
    comment_text = _render_comment(template, game_name, streamer_name, clip_title)
    
    try:
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {
                        "textOriginal": comment_text,
                    }
                },
            }
        }
        
        response = service.commentThreads().insert(
            part="snippet",
            body=body,
        ).execute()
        
        comment_id = response.get("id")
        log.info("Posted first comment on %s: %s", video_id, comment_text[:50])
        
        return comment_id
        
    except HttpError as e:
        if e.resp.status == 403:
            log.warning("Cannot post comment on %s (403 forbidden — comments may be disabled or quota issue)", video_id)
        elif e.resp.status == 400:
            log.warning("Bad request posting comment on %s: %s", video_id, e)
        else:
            log.warning("Failed to post comment on %s: %s", video_id, e)
        return None
    except Exception:
        log.warning("Failed to post comment on %s", video_id, exc_info=True)
        return None

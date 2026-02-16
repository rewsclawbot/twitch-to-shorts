"""YouTube comment monitoring and auto-engagement.

Checks for new comments on uploaded Shorts and auto-responds to drive engagement.
The algorithm loves active comment sections in the first hour after posting.
"""
import hashlib
import logging
import random
import re
import sqlite3
from datetime import UTC, datetime, timedelta

from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

# Reply templates organized by comment type
# Keep replies short (1-2 sentences), natural, not spammy

_LAUGH_REPLIES = [
    "😂 Right?! That was hilarious",
    "Glad you enjoyed that moment! 🤣",
    "Haha same reaction here! 😄",
    "I know right? Couldn't stop laughing 💀",
    "That was pure comedy gold 😂",
]

_QUESTION_REPLIES = [
    "Great question! Check the description for more info 👆",
    "Thanks for asking! Hit that subscribe button for more content like this 🔔",
    "Good point! More clips coming soon 🎮",
    "Interesting question! What do you think? 💭",
]

_STREAMER_MENTION_REPLIES = [
    "{streamer} is insane at this game! Check out their Twitch 🎮",
    "Yeah, {streamer} always delivers epic moments like this 🔥",
    "{streamer} has been crushing it lately! Full credit to them 🙌",
    "That's {streamer} for you - always entertaining! 💪",
]

_POSITIVE_REPLIES = [
    "Thanks! Subscribe for more epic gaming moments 🚀",
    "Appreciate it! More coming soon 🎮",
    "Glad you liked it! Drop a like if you want more 👍",
    "Thanks for watching! Check out more shorts on the channel 📺",
]

_GENERIC_REPLIES = [
    "Thanks for watching! What do you want to see next? 💬",
    "Appreciate the comment! Subscribe for daily clips 🔔",
    "Glad you're here! More epic moments coming soon 🎮",
    "Thanks for engaging! Hit subscribe if you enjoyed this 🚀",
]


def fetch_comments(youtube_service, video_id: str) -> list[dict]:
    """Fetch comments on a video using YouTube Data API v3.
    
    Args:
        youtube_service: Authenticated YouTube API service
        video_id: YouTube video ID to fetch comments for
    
    Returns:
        List of comment dicts with keys: comment_id, author, text, published_at, like_count
        Returns empty list if comments are disabled or fetch fails.
    """
    try:
        request = youtube_service.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=100,  # Fetch up to 100 most recent comments
            order="time",  # Sort by newest first
        )
        response = request.execute()
        
        comments = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            top_comment = snippet.get("topLevelComment", {})
            comment_snippet = top_comment.get("snippet", {})
            
            comment_id = top_comment.get("id")
            if not comment_id:
                continue
                
            comments.append({
                "comment_id": comment_id,
                "author": comment_snippet.get("authorDisplayName", "Unknown"),
                "text": comment_snippet.get("textOriginal", ""),
                "published_at": comment_snippet.get("publishedAt", ""),
                "like_count": comment_snippet.get("likeCount", 0),
            })
        
        log.info("Fetched %d comments for video %s", len(comments), video_id)
        return comments
        
    except HttpError as e:
        if e.resp.status == 403:
            log.warning("Comments disabled or quota issue for %s (403)", video_id)
        elif e.resp.status == 404:
            log.warning("Video not found: %s", video_id)
        else:
            log.warning("Failed to fetch comments for %s: %s", video_id, e)
        return []
    except Exception:
        log.warning("Failed to fetch comments for %s", video_id, exc_info=True)
        return []


def generate_reply(comment_text: str, video_title: str, streamer_name: str) -> str:
    """Generate a contextual, friendly reply to a comment.
    
    Uses template-based responses to keep replies fast and natural without LLM overhead.
    Rotates through templates based on comment content hash for variety.
    
    Args:
        comment_text: The comment text to respond to
        video_title: The video title for context
        streamer_name: The streamer name for context
    
    Returns:
        A contextual reply string (1-2 sentences)
    """
    comment_lower = comment_text.lower().strip()
    
    # Detect comment type and pick appropriate template pool
    templates = _GENERIC_REPLIES
    
    # Check for laughter/humor
    if any(marker in comment_lower for marker in ["lol", "lmao", "😂", "haha", "💀", "funny", "hilarious"]):
        templates = _LAUGH_REPLIES
    
    # Check for questions (question marks or question words)
    elif "?" in comment_text or any(word in comment_lower for word in ["how", "what", "why", "when", "where", "who"]):
        templates = _QUESTION_REPLIES
    
    # Check for streamer mention
    elif streamer_name.lower() in comment_lower:
        templates = _STREAMER_MENTION_REPLIES
    
    # Check for positive sentiment
    elif any(word in comment_lower for word in ["good", "great", "amazing", "awesome", "epic", "love", "nice", "fire", "🔥", "best", "clutch", "insane"]):
        templates = _POSITIVE_REPLIES
    
    # Hash the comment to deterministically pick a template (same comment always gets same reply)
    comment_hash = hashlib.md5(comment_text.encode('utf-8')).hexdigest()
    template_index = int(comment_hash, 16) % len(templates)
    template = templates[template_index]
    
    # Render template with context
    reply = template.format(streamer=streamer_name)
    
    return reply


def reply_to_comment(youtube_service, comment_id: str, reply_text: str) -> bool:
    """Post a reply to a comment using YouTube Data API v3.
    
    Args:
        youtube_service: Authenticated YouTube API service
        comment_id: Comment ID to reply to
        reply_text: Reply text to post
    
    Returns:
        True if reply posted successfully, False otherwise
    """
    try:
        body = {
            "snippet": {
                "parentId": comment_id,
                "textOriginal": reply_text,
            }
        }
        
        youtube_service.comments().insert(
            part="snippet",
            body=body,
        ).execute()
        
        log.info("Posted reply to comment %s: %s", comment_id, reply_text[:50])
        return True
        
    except HttpError as e:
        if e.resp.status == 403:
            log.warning("Cannot reply to comment %s (403 forbidden — quota or permissions)", comment_id)
        elif e.resp.status == 400:
            log.warning("Bad request replying to comment %s: %s", comment_id, e)
        else:
            log.warning("Failed to reply to comment %s: %s", comment_id, e)
        return False
    except Exception:
        log.warning("Failed to reply to comment %s", comment_id, exc_info=True)
        return False


def monitor_and_engage(
    youtube_service,
    conn: sqlite3.Connection,
    max_videos: int = 5,
    max_replies_per_video: int = 2,
    max_total_replies: int = 10,
    dry_run: bool = False,
) -> dict:
    """Monitor recent uploads and auto-reply to top comments.
    
    Fetches comments on recent uploads (last 48h), replies to top unreplied comments
    by like_count, and tracks replied comments in the database to avoid duplicates.
    
    Args:
        youtube_service: Authenticated YouTube API service
        conn: Database connection
        max_videos: Maximum number of videos to check per run (default: 5)
        max_replies_per_video: Maximum replies per video per run (default: 2)
        max_total_replies: Maximum total replies per run (default: 10)
        dry_run: If True, don't actually post replies (default: False)
    
    Returns:
        Dict with keys: videos_checked, comments_fetched, replies_posted, videos_engaged
    """
    # Get recent uploads (last 48h) that have a youtube_id
    cutoff = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    rows = conn.execute(
        """SELECT youtube_id, clip_id, title, streamer, posted_at
           FROM clips
           WHERE youtube_id IS NOT NULL
             AND posted_at >= ?
           ORDER BY posted_at DESC
           LIMIT ?""",
        (cutoff, max_videos),
    ).fetchall()
    
    if not rows:
        log.info("No recent uploads to monitor (last 48h)")
        return {
            "videos_checked": 0,
            "comments_fetched": 0,
            "replies_posted": 0,
            "videos_engaged": 0,
        }
    
    log.info("Monitoring %d recent uploads for comments", len(rows))
    
    total_comments_fetched = 0
    total_replies_posted = 0
    videos_engaged = 0
    
    for row in rows:
        if total_replies_posted >= max_total_replies:
            log.info("Reached max total replies (%d), stopping", max_total_replies)
            break
            
        youtube_id = row["youtube_id"]
        clip_id = row["clip_id"]
        title = row["title"] or ""
        streamer = row["streamer"] or ""
        
        # Fetch comments for this video
        comments = fetch_comments(youtube_service, youtube_id)
        total_comments_fetched += len(comments)
        
        if not comments:
            continue
        
        # Filter out comments we've already replied to
        already_replied = {
            r[0] for r in conn.execute(
                "SELECT comment_id FROM comment_replies WHERE video_id = ?",
                (youtube_id,),
            ).fetchall()
        }
        
        unreplied = [c for c in comments if c["comment_id"] not in already_replied]
        
        if not unreplied:
            log.info("No new comments on %s (all already replied)", youtube_id)
            continue
        
        # Sort by like_count descending (engage with most popular comments first)
        unreplied.sort(key=lambda c: c["like_count"], reverse=True)
        
        # Reply to top N unreplied comments
        replies_this_video = 0
        for comment in unreplied[:max_replies_per_video]:
            if total_replies_posted >= max_total_replies:
                break
            
            reply_text = generate_reply(comment["text"], title, streamer)
            
            if dry_run:
                log.info(
                    "[DRY RUN] Would reply to comment by %s on %s: %s",
                    comment["author"],
                    youtube_id,
                    reply_text,
                )
                success = True
            else:
                success = reply_to_comment(youtube_service, comment["comment_id"], reply_text)
            
            if success:
                # Track in DB to avoid duplicate replies
                conn.execute(
                    """INSERT INTO comment_replies (comment_id, video_id, reply_text, replied_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(comment_id) DO NOTHING""",
                    (comment["comment_id"], youtube_id, reply_text, datetime.now(UTC).isoformat()),
                )
                conn.commit()
                
                replies_this_video += 1
                total_replies_posted += 1
        
        if replies_this_video > 0:
            videos_engaged += 1
            log.info(
                "Posted %d replies on %s (video: %s)",
                replies_this_video,
                youtube_id,
                title[:50],
            )
    
    result = {
        "videos_checked": len(rows),
        "comments_fetched": total_comments_fetched,
        "replies_posted": total_replies_posted,
        "videos_engaged": videos_engaged,
    }
    
    log.info(
        "Comment monitoring complete: %d videos checked, %d comments fetched, %d replies posted, %d videos engaged",
        result["videos_checked"],
        result["comments_fetched"],
        result["replies_posted"],
        result["videos_engaged"],
    )
    
    return result

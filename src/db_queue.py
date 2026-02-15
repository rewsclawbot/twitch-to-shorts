"""Clip queue management for persistence between pipeline runs.

Stores ranked clips that can't be uploaded immediately (outside posting windows)
for later retrieval when inside a posting window.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from src.models import Clip


def enqueue_clips(conn: sqlite3.Connection, clips_with_scores: list[tuple[Clip, float]]):
    """Insert or update clips in the queue with their scores.
    
    Args:
        conn: Database connection
        clips_with_scores: List of (Clip, score) tuples to enqueue
    """
    now = datetime.now(UTC).isoformat()
    
    for clip, score in clips_with_scores:
        # Serialize clip data as JSON
        clip_data = json.dumps({
            'id': clip.id,
            'url': clip.url,
            'title': clip.title,
            'view_count': clip.view_count,
            'created_at': clip.created_at,
            'duration': clip.duration,
            'game_id': clip.game_id,
            'streamer': clip.streamer,
            'channel_key': clip.channel_key,
            'game_name': clip.game_name,
            'score': clip.score,
            'vod_id': clip.vod_id,
            'vod_offset': clip.vod_offset,
        })
        
        # Insert or replace (update score if clip already queued)
        conn.execute("""
            INSERT OR REPLACE INTO clip_queue 
                (clip_id, streamer, score, queued_at, status, clip_data)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (clip.id, clip.streamer, score, now, clip_data))
    
    conn.commit()


def dequeue_top_clips(conn: sqlite3.Connection, limit: int = 3, streamer: str | None = None) -> list[Clip]:
    """Get top pending clips by score.
    
    Args:
        conn: Database connection
        limit: Maximum number of clips to return
        streamer: Optional streamer filter
        
    Returns:
        List of Clip objects ordered by score (descending)
    """
    query = """
        SELECT clip_id, clip_data, score 
        FROM clip_queue 
        WHERE status = 'pending'
    """
    params: list = []
    
    if streamer:
        query += " AND streamer = ?"
        params.append(streamer)
    
    query += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    
    rows = conn.execute(query, params).fetchall()
    
    clips: list[Clip] = []
    for row in rows:
        clip_data = json.loads(row['clip_data'])
        clip = Clip(
            id=clip_data['id'],
            url=clip_data['url'],
            title=clip_data['title'],
            view_count=clip_data['view_count'],
            created_at=clip_data['created_at'],
            duration=clip_data['duration'],
            game_id=clip_data['game_id'],
            streamer=clip_data['streamer'],
            channel_key=clip_data.get('channel_key', ''),
            game_name=clip_data.get('game_name', ''),
            score=clip_data.get('score', 0.0),
            vod_id=clip_data.get('vod_id'),
            vod_offset=clip_data.get('vod_offset'),
        )
        clips.append(clip)
    
    return clips


def mark_clip_uploaded(conn: sqlite3.Connection, clip_id: str):
    """Mark a queued clip as uploaded.
    
    Args:
        conn: Database connection
        clip_id: ID of the clip that was uploaded
    """
    conn.execute("""
        UPDATE clip_queue 
        SET status = 'uploaded' 
        WHERE clip_id = ?
    """, (clip_id,))
    conn.commit()


def expire_old_queue(conn: sqlite3.Connection, max_age_hours: int = 72):
    """Mark old pending clips as expired.
    
    Args:
        conn: Database connection
        max_age_hours: Age threshold in hours (default 72 = 3 days)
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
    
    result = conn.execute("""
        UPDATE clip_queue 
        SET status = 'expired' 
        WHERE status = 'pending' 
        AND queued_at < ?
    """, (cutoff,))
    
    conn.commit()
    
    if result.rowcount > 0:
        return result.rowcount
    return 0


def get_queue_stats(conn: sqlite3.Connection, streamer: str | None = None) -> dict:
    """Get statistics about the clip queue.
    
    Args:
        conn: Database connection
        streamer: Optional streamer filter
        
    Returns:
        Dictionary with pending/uploaded/expired counts
    """
    query_base = "SELECT status, COUNT(*) as cnt FROM clip_queue"
    params: list = []
    
    if streamer:
        query_base += " WHERE streamer = ?"
        params.append(streamer)
    
    query = query_base + " GROUP BY status"
    
    rows = conn.execute(query, params).fetchall()
    
    stats = {'pending': 0, 'uploaded': 0, 'expired': 0}
    for row in rows:
        stats[row['status']] = row['cnt']
    
    return stats

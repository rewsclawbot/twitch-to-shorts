"""Tests for comment monitoring and auto-engagement."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.comment_monitor import (
    fetch_comments,
    generate_reply,
    monitor_and_engage,
    reply_to_comment,
)


class TestFetchComments:
    def test_fetches_comments_successfully(self):
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "authorDisplayName": "User1",
                                "textOriginal": "Great clip!",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 5,
                            },
                        }
                    }
                },
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment2",
                            "snippet": {
                                "authorDisplayName": "User2",
                                "textOriginal": "How did you do that?",
                                "publishedAt": "2024-01-01T13:00:00Z",
                                "likeCount": 2,
                            },
                        }
                    }
                },
            ]
        }
        
        result = fetch_comments(service, "video123")
        
        assert len(result) == 2
        assert result[0]["comment_id"] == "comment1"
        assert result[0]["author"] == "User1"
        assert result[0]["text"] == "Great clip!"
        assert result[0]["like_count"] == 5
        assert result[1]["comment_id"] == "comment2"
        
    def test_returns_empty_list_on_403(self):
        from googleapiclient.errors import HttpError
        
        service = MagicMock()
        resp = MagicMock()
        resp.status = 403
        error = HttpError(resp, b"forbidden")
        service.commentThreads().list().execute.side_effect = error
        
        result = fetch_comments(service, "video123")
        assert result == []
    
    def test_returns_empty_list_on_404(self):
        from googleapiclient.errors import HttpError
        
        service = MagicMock()
        resp = MagicMock()
        resp.status = 404
        error = HttpError(resp, b"not found")
        service.commentThreads().list().execute.side_effect = error
        
        result = fetch_comments(service, "video123")
        assert result == []
    
    def test_handles_empty_response(self):
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {}
        
        result = fetch_comments(service, "video123")
        assert result == []
    
    def test_handles_malformed_response(self):
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {
            "items": [
                {"snippet": {}},  # Missing topLevelComment
                {"snippet": {"topLevelComment": {}}},  # Missing id
            ]
        }
        
        result = fetch_comments(service, "video123")
        assert result == []


class TestGenerateReply:
    def test_laugh_response(self):
        reply = generate_reply("lol that was hilarious", "Epic Win", "Ninja")
        assert any(marker in reply for marker in ["😂", "🤣", "😄", "💀", "Haha", "Glad"])
    
    def test_laugh_response_emoji(self):
        reply = generate_reply("😂😂😂", "Epic Win", "Ninja")
        assert any(marker in reply for marker in ["😂", "🤣", "😄", "💀"])
    
    def test_question_response(self):
        reply = generate_reply("How did you do that?", "Epic Win", "Ninja")
        assert any(word in reply for word in ["question", "Question", "subscribe", "check", "info"])
    
    def test_question_response_what(self):
        reply = generate_reply("what game is this", "Epic Win", "Ninja")
        assert any(word in reply for word in ["question", "Question", "subscribe", "check", "info"])
    
    def test_streamer_mention_response(self):
        reply = generate_reply("Ninja is the best!", "Epic Win", "Ninja")
        assert "Ninja" in reply
        assert any(word in reply for word in ["insane", "delivers", "crushing", "entertaining"])
    
    def test_positive_response(self):
        reply = generate_reply("This is amazing!", "Epic Win", "Ninja")
        assert any(word in reply for word in ["Thanks", "Subscribe", "Appreciate", "Glad"])
    
    def test_positive_response_fire_emoji(self):
        reply = generate_reply("fire 🔥", "Epic Win", "Ninja")
        assert any(word in reply for word in ["Thanks", "Subscribe", "Appreciate", "Glad"])
    
    def test_generic_response(self):
        reply = generate_reply("first", "Epic Win", "Ninja")
        assert any(word in reply for word in ["Thanks", "watching", "Subscribe", "Appreciate"])
    
    def test_deterministic_reply(self):
        # Same comment should always get same reply
        reply1 = generate_reply("test comment", "Epic Win", "Ninja")
        reply2 = generate_reply("test comment", "Epic Win", "Ninja")
        assert reply1 == reply2
    
    def test_different_comments_different_replies(self):
        # Different comments should get different replies (with high probability)
        replies = set()
        for i in range(10):
            reply = generate_reply(f"comment {i}", "Epic Win", "Ninja")
            replies.add(reply)
        # At least some variety
        assert len(replies) > 1


class TestReplyToComment:
    def test_posts_reply_successfully(self):
        service = MagicMock()
        service.comments().insert().execute.return_value = {"id": "reply123"}
        
        result = reply_to_comment(service, "comment123", "Great point!")
        assert result is True
        # Verify insert was called with correct parameters
        service.comments().insert.assert_called()
        call_args = service.comments().insert.call_args
        assert call_args[1]["part"] == "snippet"
        assert call_args[1]["body"]["snippet"]["parentId"] == "comment123"
        assert call_args[1]["body"]["snippet"]["textOriginal"] == "Great point!"
    
    def test_returns_false_on_403(self):
        from googleapiclient.errors import HttpError
        
        service = MagicMock()
        resp = MagicMock()
        resp.status = 403
        error = HttpError(resp, b"forbidden")
        service.comments().insert().execute.side_effect = error
        
        result = reply_to_comment(service, "comment123", "Great point!")
        assert result is False
    
    def test_returns_false_on_400(self):
        from googleapiclient.errors import HttpError
        
        service = MagicMock()
        resp = MagicMock()
        resp.status = 400
        error = HttpError(resp, b"bad request")
        service.comments().insert().execute.side_effect = error
        
        result = reply_to_comment(service, "comment123", "Great point!")
        assert result is False
    
    def test_returns_false_on_exception(self):
        service = MagicMock()
        service.comments().insert().execute.side_effect = RuntimeError("boom")
        
        result = reply_to_comment(service, "comment123", "Great point!")
        assert result is False


class TestMonitorAndEngage:
    @pytest.fixture
    def db_conn(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create clips table
        conn.execute("""
            CREATE TABLE clips (
                clip_id TEXT PRIMARY KEY,
                youtube_id TEXT,
                title TEXT,
                streamer TEXT,
                posted_at TEXT
            )
        """)
        # Create comment_replies table
        conn.execute("""
            CREATE TABLE comment_replies (
                comment_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                reply_text TEXT,
                replied_at TEXT
            )
        """)
        conn.commit()
        yield conn
        conn.close()
    
    def test_no_recent_uploads(self, db_conn):
        service = MagicMock()
        result = monitor_and_engage(service, db_conn)
        
        assert result["videos_checked"] == 0
        assert result["comments_fetched"] == 0
        assert result["replies_posted"] == 0
        assert result["videos_engaged"] == 0
    
    def test_monitors_recent_uploads(self, db_conn):
        # Insert a recent upload
        now = datetime.now(UTC).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Epic Win", "Ninja", now),
        )
        db_conn.commit()
        
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "authorDisplayName": "User1",
                                "textOriginal": "This is amazing!",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 10,
                            },
                        }
                    }
                }
            ]
        }
        service.comments().insert().execute.return_value = {"id": "reply1"}
        
        result = monitor_and_engage(service, db_conn)
        
        assert result["videos_checked"] == 1
        assert result["comments_fetched"] == 1
        assert result["replies_posted"] == 1
        assert result["videos_engaged"] == 1
    
    def test_dry_run_mode(self, db_conn):
        now = datetime.now(UTC).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Epic Win", "Ninja", now),
        )
        db_conn.commit()
        
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "authorDisplayName": "User1",
                                "textOriginal": "Great clip!",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 5,
                            },
                        }
                    }
                }
            ]
        }
        
        result = monitor_and_engage(service, db_conn, dry_run=True)
        
        assert result["videos_checked"] == 1
        assert result["comments_fetched"] == 1
        assert result["replies_posted"] == 1  # Counts dry run as posted
        # Verify no actual API call was made
        service.comments().insert.assert_not_called()
    
    def test_rate_limiting_per_video(self, db_conn):
        now = datetime.now(UTC).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Epic Win", "Ninja", now),
        )
        db_conn.commit()
        
        service = MagicMock()
        # 5 comments available
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": f"comment{i}",
                            "snippet": {
                                "authorDisplayName": f"User{i}",
                                "textOriginal": f"Comment {i}",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 10 - i,
                            },
                        }
                    }
                }
                for i in range(5)
            ]
        }
        service.comments().insert().execute.return_value = {"id": "reply1"}
        
        # Max 2 replies per video
        result = monitor_and_engage(
            service, db_conn, max_replies_per_video=2, max_total_replies=10
        )
        
        assert result["replies_posted"] == 2  # Limited to 2
    
    def test_rate_limiting_total(self, db_conn):
        now = datetime.now(UTC).isoformat()
        # Insert 3 videos
        for i in range(3):
            db_conn.execute(
                "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
                (f"clip{i}", f"video{i}", f"Epic Win {i}", "Ninja", now),
            )
        db_conn.commit()
        
        service = MagicMock()
        # Each video has 3 comments
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": f"comment{i}",
                            "snippet": {
                                "authorDisplayName": f"User{i}",
                                "textOriginal": f"Comment {i}",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 10 - i,
                            },
                        }
                    }
                }
                for i in range(3)
            ]
        }
        service.comments().insert().execute.return_value = {"id": "reply1"}
        
        # Max 2 replies per video, max 5 total
        result = monitor_and_engage(
            service, db_conn, max_replies_per_video=2, max_total_replies=5
        )
        
        assert result["replies_posted"] == 5  # Limited to 5 total (not 6 = 3 videos * 2)
    
    def test_skips_already_replied_comments(self, db_conn):
        now = datetime.now(UTC).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Epic Win", "Ninja", now),
        )
        # Mark comment1 as already replied
        db_conn.execute(
            "INSERT INTO comment_replies (comment_id, video_id, reply_text, replied_at) VALUES (?, ?, ?, ?)",
            ("comment1", "video1", "Already replied", now),
        )
        db_conn.commit()
        
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "authorDisplayName": "User1",
                                "textOriginal": "Comment 1",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 20,  # High likes, but already replied
                            },
                        }
                    }
                },
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment2",
                            "snippet": {
                                "authorDisplayName": "User2",
                                "textOriginal": "Comment 2",
                                "publishedAt": "2024-01-01T13:00:00Z",
                                "likeCount": 10,  # Lower likes, but unreplied
                            },
                        }
                    }
                },
            ]
        }
        service.comments().insert().execute.return_value = {"id": "reply1"}
        
        result = monitor_and_engage(service, db_conn)
        
        assert result["comments_fetched"] == 2
        assert result["replies_posted"] == 1  # Only replied to comment2
        
        # Verify comment1 was not replied to again
        all_replies = db_conn.execute(
            "SELECT comment_id FROM comment_replies WHERE video_id = ?",
            ("video1",),
        ).fetchall()
        assert len(all_replies) == 2  # Original + new reply
        reply_ids = {r[0] for r in all_replies}
        assert "comment1" in reply_ids
        assert "comment2" in reply_ids
    
    def test_prioritizes_high_like_count(self, db_conn):
        now = datetime.now(UTC).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Epic Win", "Ninja", now),
        )
        db_conn.commit()
        
        service = MagicMock()
        # Comments in reverse like order
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment_low",
                            "snippet": {
                                "authorDisplayName": "User1",
                                "textOriginal": "Low likes",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 1,
                            },
                        }
                    }
                },
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment_high",
                            "snippet": {
                                "authorDisplayName": "User2",
                                "textOriginal": "High likes",
                                "publishedAt": "2024-01-01T13:00:00Z",
                                "likeCount": 100,
                            },
                        }
                    }
                },
            ]
        }
        service.comments().insert().execute.return_value = {"id": "reply1"}
        
        result = monitor_and_engage(service, db_conn, max_replies_per_video=1)
        
        assert result["replies_posted"] == 1
        
        # Verify we replied to the high-like comment
        reply = db_conn.execute(
            "SELECT comment_id FROM comment_replies WHERE video_id = ?",
            ("video1",),
        ).fetchone()
        assert reply[0] == "comment_high"
    
    def test_ignores_old_uploads(self, db_conn):
        # Insert an upload from 50 hours ago (outside 48h window)
        old_time = (datetime.now(UTC) - timedelta(hours=50)).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Old Upload", "Ninja", old_time),
        )
        db_conn.commit()
        
        service = MagicMock()
        result = monitor_and_engage(service, db_conn)
        
        assert result["videos_checked"] == 0
        assert result["comments_fetched"] == 0
    
    def test_tracks_replied_comments_in_db(self, db_conn):
        now = datetime.now(UTC).isoformat()
        db_conn.execute(
            "INSERT INTO clips (clip_id, youtube_id, title, streamer, posted_at) VALUES (?, ?, ?, ?, ?)",
            ("clip1", "video1", "Epic Win", "Ninja", now),
        )
        db_conn.commit()
        
        service = MagicMock()
        service.commentThreads().list().execute.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "authorDisplayName": "User1",
                                "textOriginal": "Great!",
                                "publishedAt": "2024-01-01T12:00:00Z",
                                "likeCount": 5,
                            },
                        }
                    }
                }
            ]
        }
        service.comments().insert().execute.return_value = {"id": "reply1"}
        
        monitor_and_engage(service, db_conn)
        
        # Check DB
        reply = db_conn.execute(
            "SELECT comment_id, video_id, reply_text, replied_at FROM comment_replies"
        ).fetchone()
        
        assert reply["comment_id"] == "comment1"
        assert reply["video_id"] == "video1"
        assert reply["reply_text"] is not None
        assert reply["replied_at"] is not None

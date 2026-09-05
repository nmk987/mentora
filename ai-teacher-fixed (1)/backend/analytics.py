"""
Learning analytics - deterministic, cross-session, never LLM-computed.

Everything here reads from `student_memory` and `analytics_events` (see
db.py) for one student_id and produces plain numbers/lists a dashboard can
render directly. No model call happens in this file.
"""
from typing import Dict, Any, List
from datetime import datetime, timezone

import progress


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def compute_streak(session_completed_events: List[Dict[str, Any]]) -> int:
    """Consecutive-day streak of completed sessions, ending today (UTC)."""
    if not session_completed_events:
        return 0
    days = sorted({_day_key(e["created_at"]) for e in session_completed_events}, reverse=True)
    today = _day_key(__import__("time").time())
    if days[0] != today:
        # Allow the streak to still count if the most recent session was
        # yesterday (don't zero it out the instant the clock rolls over).
        from datetime import timedelta
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        if days[0] != yesterday:
            return 0
    streak = 1
    from datetime import timedelta
    cursor = datetime.strptime(days[0], "%Y-%m-%d")
    for d in days[1:]:
        cursor -= timedelta(days=1)
        if d == cursor.strftime("%Y-%m-%d"):
            streak += 1
        else:
            break
    return streak


def build_dashboard(student_id: str, memory_rows: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """memory_rows: db.get_student_memory(student_id) - one row per (topic, concept) ever studied.
    events: db.get_analytics_events(student_id) - full event log.
    """
    topics = sorted({m["topic"] for m in memory_rows})
    concept_statuses = [progress.concept_status(m["score"], m["attempts"], m["correct"]) for m in memory_rows]

    mastered = sum(1 for s in concept_statuses if s == progress.MASTERED)
    weak = sum(1 for s in concept_statuses if s == progress.WEAK)
    learning = sum(1 for s in concept_statuses if s == progress.LEARNING)
    total_concepts = len(memory_rows)
    overall_mastery_percent = round((mastered / total_concepts) * 100) if total_concepts else 0

    weak_areas = [f"{m['topic']} — {m['concept']}" for m, s in zip(memory_rows, concept_statuses) if s == progress.WEAK]
    strong_areas = [f"{m['topic']} — {m['concept']}" for m, s in zip(memory_rows, concept_statuses) if s == progress.MASTERED]

    # Repeated misconceptions: same misconception text seen 2+ times across any concept/topic.
    from collections import Counter
    all_misconceptions = [mc for m in memory_rows for mc in m["misconceptions"]]
    repeated = [text for text, count in Counter(all_misconceptions).items() if count >= 2]

    quiz_events = [e for e in events if e["event_type"] == "quiz_scored"]
    quiz_scores_over_time = [{"date": _day_key(e["created_at"]), "score": e["payload"].get("score_percent", 0)} for e in quiz_events]

    session_events = [e for e in events if e["event_type"] == "session_completed"]
    total_time_seconds = sum(e["payload"].get("duration_seconds", 0) for e in session_events)
    streak = compute_streak(session_events)

    improvement = None
    if len(quiz_scores_over_time) >= 2:
        improvement = quiz_scores_over_time[-1]["score"] - quiz_scores_over_time[0]["score"]

    return {
        "student_id": student_id,
        "topics_studied": topics,
        "topics_completed": len(session_events),
        "overall_mastery_percent": overall_mastery_percent,
        "concepts_mastered": mastered,
        "concepts_learning": learning,
        "concepts_weak": weak,
        "weak_areas": weak_areas,
        "strong_areas": strong_areas,
        "repeated_misconceptions": repeated,
        "quiz_scores_over_time": quiz_scores_over_time,
        "total_time_minutes": round(total_time_seconds / 60, 1),
        "learning_streak_days": streak,
        "improvement_percent_points": improvement,
    }

"""
Deterministic session-level progress logic.

`adaptive_engine.py` decides what happens on the NEXT turn (reteach / harder /
next concept / finish). This module is the complement: it looks at the WHOLE
session so far and answers questions like "what's this student's knowledge
map right now", "what score did they get", and "should we gate progression
because a prerequisite is weak". Kept out of the LLM on purpose — mastery
status, scores and progression rules should be reproducible and explainable,
not subject to phrasing changes in a model's output.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

MASTERED = "mastered"
LEARNING = "learning"
WEAK = "weak"
NOT_STARTED = "not_started"

STATUS_EMOJI = {MASTERED: "🟢", LEARNING: "🟡", WEAK: "🔴", NOT_STARTED: "⚪"}

# Thresholds on the running mastery score (see db.upsert_mastery: each answer
# contributes roughly -0.5 (wrong) .. +0.5 (fully correct)).
MASTERED_SCORE = 1.0
WEAK_SCORE = -0.5
MASTERED_ACCURACY = 0.6


def concept_status(score: float, attempts: int, correct: int) -> str:
    if attempts == 0:
        return NOT_STARTED
    accuracy = correct / attempts
    if score >= MASTERED_SCORE and accuracy >= MASTERED_ACCURACY:
        return MASTERED
    if score <= WEAK_SCORE:
        return WEAK
    return LEARNING


def build_knowledge_map(plan_concepts: List[Dict[str, Any]], mastery_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every concept in the plan gets an entry, even ones not yet attempted."""
    by_concept = {m["concept"]: m for m in mastery_rows}
    out = []
    for c in plan_concepts:
        name = c["name"]
        m = by_concept.get(name)
        if m is None:
            out.append({
                "concept": name, "status": NOT_STARTED, "emoji": STATUS_EMOJI[NOT_STARTED],
                "score": 0.0, "attempts": 0, "correct": 0, "misconceptions": [],
            })
            continue
        status = concept_status(m["score"], m["attempts"], m["correct"])
        out.append({
            "concept": name, "status": status, "emoji": STATUS_EMOJI[status],
            "score": round(m["score"], 2), "attempts": m["attempts"], "correct": m["correct"],
            "misconceptions": m["misconceptions"],
        })
    return out


@dataclass
class SessionStats:
    score_percent: int
    questions_attempted: int
    questions_correct: int
    mastered_concepts: List[str]
    learning_concepts: List[str]
    weak_concepts: List[str]
    suggested_next_difficulty: int


def compute_session_stats(knowledge_map: List[Dict[str, Any]], turns: List[Dict[str, Any]],
                           final_difficulty: int, quiz_answers: Optional[List[Dict[str, Any]]] = None) -> SessionStats:
    """All numbers here are computed from stored data, never asked of the LLM,
    so the score a student sees can't drift between runs of the same history.
    """
    quiz_answers = quiz_answers or []
    eval_turns = [t for t in turns if t["kind"] == "answer_eval"]
    attempted = len(eval_turns) + len(quiz_answers)
    correct = sum(1 for t in eval_turns if t["content"].get("correct")) + \
        sum(1 for q in quiz_answers if q.get("correct"))
    partial_sum = sum(float(t["content"].get("partial_credit", 1.0 if t["content"].get("correct") else 0.0))
                       for t in eval_turns)
    partial_sum += sum(float(q.get("partial_credit", 1.0 if q.get("correct") else 0.0)) for q in quiz_answers)

    score_percent = round((partial_sum / attempted) * 100) if attempted else 0

    mastered = [c["concept"] for c in knowledge_map if c["status"] == MASTERED]
    learning = [c["concept"] for c in knowledge_map if c["status"] == LEARNING]
    weak = [c["concept"] for c in knowledge_map if c["status"] == WEAK]

    # Suggested starting difficulty next time: nudge down if there's any weak
    # concept (don't want to open the next session too hard), otherwise track
    # the difficulty the student ended this session at.
    suggested = final_difficulty
    if weak:
        suggested = max(1, final_difficulty - 1)
    elif mastered and not learning:
        suggested = min(5, final_difficulty + 1)
    suggested = max(1, min(5, suggested))

    return SessionStats(
        score_percent=score_percent,
        questions_attempted=attempted,
        questions_correct=correct,
        mastered_concepts=mastered,
        learning_concepts=learning,
        weak_concepts=weak,
        suggested_next_difficulty=suggested,
    )


def gate_next_topic(knowledge_map: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Deterministic prerequisite gate: if anything in THIS session is weak,
    recommend revising it before moving to a brand-new topic. Returns None if
    there's nothing blocking progression.
    """
    weak = [c["concept"] for c in knowledge_map if c["status"] == WEAK]
    if not weak:
        return None
    return {
        "gated": True,
        "reason": f"{', '.join(weak)} still {'needs' if len(weak) == 1 else 'need'} work before moving on.",
        "revise_first": weak,
    }


def intelligent_recommendation(
    knowledge_map: List[Dict[str, Any]],
    cross_session_weak: List[Dict[str, Any]],
    quiz_score_percent: Optional[int],
    learning_goal: Optional[str],
) -> Dict[str, Any]:
    """Combines everything progress.py knows deterministically into one
    recommendation-with-reasons object, per Priority 9: prerequisites, this
    session's weak concepts, mastered concepts, quiz score, and the stated
    learning goal. The actual next-TOPIC name (when not gated) still comes
    from the LLM (see prompts.learning_path_prompt) — this function only
    decides WHETHER to gate and WHY, which needs to be reproducible.
    """
    reasons: List[str] = []
    this_session_weak = [c["concept"] for c in knowledge_map if c["status"] == WEAK]
    if this_session_weak:
        reasons.append(f"{', '.join(this_session_weak)} {'was' if len(this_session_weak) == 1 else 'were'} weak in this session.")

    # cross_session_weak: rows from db.get_student_memory(student_id) with status == 'weak',
    # from OTHER topics too — long-term memory, not just this session.
    other_weak = [f"{m['topic']} — {m['concept']}" for m in cross_session_weak if m["status"] == WEAK]
    if other_weak:
        reasons.append(f"Past sessions show unresolved difficulty with: {', '.join(other_weak[:3])}.")

    if quiz_score_percent is not None and quiz_score_percent < 60:
        reasons.append(f"Quiz score this session was {quiz_score_percent}%, below the 60% mastery bar.")

    if learning_goal:
        reasons.append(f"Learning goal on file: \"{learning_goal}\".")

    gated = bool(this_session_weak) or (quiz_score_percent is not None and quiz_score_percent < 40)
    revise_first = this_session_weak or ([w.split(" — ")[-1] for w in other_weak[:1]] if gated else [])

    return {
        "gated": gated,
        "revise_first": revise_first,
        "reasons": reasons or ["No blockers found — mastery and quiz score both support moving forward."],
    }


# ---------------------------------------------------------------------------
# v4 — Priority 3: deterministic confidence/struggle signal.
#
# Explicitly NOT emotion detection — this only looks at observable
# interaction data already in the DB (wrong-answer streaks, retry counts,
# answer length, repeated misconceptions). It labels a concept HIGH / MEDIUM
# / LOW confidence so the teach prompt can be told to slow down or speed up,
# without ever claiming to know how the student *feels*.
# ---------------------------------------------------------------------------
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
SHORT_ANSWER_WORD_COUNT = 3


def compute_confidence(recent_evals: List[Dict[str, Any]], wrong_streak: int,
                        consecutive_correct: int, current_difficulty: int,
                        baseline_difficulty: int) -> str:
    """recent_evals: most-recent-last list of {"correct": bool, "answer": str,
    "misconception": str|None} for the CURRENT concept only.
    """
    if not recent_evals:
        return MEDIUM

    last = recent_evals[-1]
    answer_word_count = len((last.get("answer") or "").split())
    very_short_answer = 0 < answer_word_count < SHORT_ANSWER_WORD_COUNT
    empty_answer = answer_word_count == 0

    misconceptions = [e["misconception"] for e in recent_evals if e.get("misconception")]
    repeated_misconception = len(misconceptions) != len(set(misconceptions)) if len(misconceptions) >= 2 else False

    if wrong_streak >= 2 or repeated_misconception or empty_answer or (very_short_answer and not last.get("correct")):
        return LOW
    if consecutive_correct >= 2 and current_difficulty >= baseline_difficulty + 1:
        return HIGH
    return MEDIUM


CONFIDENCE_ADJUSTMENT_NOTE = {
    LOW: "Teacher adjusted the lesson because this concept needs more practice — "
         "using a simpler explanation, a fresh analogy, and an easier question.",
    MEDIUM: None,
    HIGH: "Teacher raised the bar because this concept is clicking — moving to a "
          "harder, more applied question.",
}


# ---------------------------------------------------------------------------
# v4 — Priority 12: prerequisite-aware knowledge graph.
# ---------------------------------------------------------------------------
def build_knowledge_graph(plan_concepts: List[Dict[str, Any]], knowledge_map: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turns the plan's prerequisite lists (from prompts.plan_prompt) plus the
    live knowledge map into a node/edge graph the frontend can render with
    the same node/edge visual renderer already used for diagrams.
    """
    status_by_concept = {c["concept"]: c["status"] for c in knowledge_map}
    nodes = [
        {"id": c["name"], "label": c["name"], "status": status_by_concept.get(c["name"], NOT_STARTED)}
        for c in plan_concepts
    ]
    edges = []
    for c in plan_concepts:
        for prereq in c.get("prerequisites") or []:
            edges.append({"from": prereq, "to": c["name"]})
    return {"nodes": nodes, "edges": edges}


def check_prerequisite_gate(concept: Dict[str, Any], knowledge_map: List[Dict[str, Any]]) -> Optional[str]:
    """Before teaching `concept`, check whether any of ITS prerequisites are
    currently weak. Returns a human-readable advisory string, or None if
    there's nothing to flag. This never blocks the lesson outright (the
    concept was already scheduled by the planner) — it just surfaces an
    honest note, per the brief's example: "You are ready for Circuits, but
    Resistance needs a quick revision first."
    """
    status_by_concept = {c["concept"]: c["status"] for c in knowledge_map}
    weak_prereqs = [p for p in (concept.get("prerequisites") or []) if status_by_concept.get(p) == WEAK]
    if not weak_prereqs:
        return None
    return (f"You're ready for {concept['name']}, but {', '.join(weak_prereqs)} could use a quick "
            f"revision first — keep that in mind as we go.")


# ---------------------------------------------------------------------------
# v4 — Priority 8: Exam Mode 2.0 — deterministic exam readiness score.
# ---------------------------------------------------------------------------
def compute_exam_readiness(knowledge_map: List[Dict[str, Any]], quiz_score_percent: Optional[int]) -> Dict[str, Any]:
    mastered = [c["concept"] for c in knowledge_map if c["status"] == MASTERED]
    weak = [c["concept"] for c in knowledge_map if c["status"] == WEAK]
    learning = [c["concept"] for c in knowledge_map if c["status"] == LEARNING]
    total = len(knowledge_map) or 1

    mastery_component = (len(mastered) / total) * 100
    quiz_component = quiz_score_percent if quiz_score_percent is not None else mastery_component
    readiness = round(0.5 * mastery_component + 0.5 * quiz_component)

    if weak:
        pipeline = "Revision → Practice → Mock test"
    elif learning:
        pipeline = "Practice → Mock test"
    else:
        pipeline = "Mock test"

    return {
        "readiness_percent": readiness,
        "strong": mastered,
        "weak": weak,
        "learning": learning,
        "recommended_pipeline": pipeline,
    }


# ---------------------------------------------------------------------------
# v4 — Priority 19: three-way "smart next lesson" recommendation.
# ---------------------------------------------------------------------------
def build_next_lesson_options(knowledge_map: List[Dict[str, Any]], path: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministically assembles the three choices; `path` is the LLM-suggested
    ordered topic list from prompts.learning_path_prompt (content only — the
    choice of WHICH of the three slots gets filled, and why, is decided here).
    """
    weak = [c["concept"] for c in knowledge_map if c["status"] == WEAK]
    options = {}
    if weak:
        options["recommended"] = {"title": f"Revise {weak[0]}", "reason": f"{weak[0]} was weak this session — solidify it before building on it."}
    elif path:
        options["recommended"] = {"title": path[0]["topic"], "reason": path[0].get("why_now", "The natural next step.")}
    else:
        options["recommended"] = {"title": "Revisit today's concepts", "reason": "No new-topic suggestion available yet."}

    continue_idx = 1 if (weak and path) else 0
    if path and continue_idx < len(path):
        options["continue"] = {"title": path[continue_idx]["topic"], "reason": path[continue_idx].get("why_now", "Builds directly on this session.")}
    else:
        options["continue"] = {"title": "Practice more on today's topic", "reason": "Keep reinforcing before moving on."}

    challenge_idx = continue_idx + 1
    if path and challenge_idx < len(path):
        options["challenge"] = {"title": f"Challenge: {path[challenge_idx]['topic']}", "reason": "A stretch topic for once you're confident."}
    else:
        options["challenge"] = {"title": "Challenge: an application problem on today's topic", "reason": "Test the depth of what you just mastered."}

    return options


# ---------------------------------------------------------------------------
# v5 -- Priority 4/5: deterministic spaced-repetition schedule.
#
# The LLM never sees or decides these dates. Python maps a concept's current
# status + how many times it's been reviewed onto a fixed interval table --
# the same rule for every concept, every student, every run, so a judge can
# be told exactly why a given date was chosen.
# ---------------------------------------------------------------------------
import time as _time

REVIEW_INTERVAL_DAYS = {
    WEAK: 1,          # first/weak review -- see it again tomorrow
    LEARNING: 3,       # improved but not yet solid
    MASTERED: 7,       # solid -- standard spaced-repetition follow-up
}
REPEATED_MASTERED_INTERVAL_DAYS = 14  # mastered AND already reviewed at least once before


def schedule_next_review(status: str, review_count: int) -> Dict[str, Any]:
    """Returns {"days": int, "reason": str} -- pure function of status and
    how many times this concept has already been through a review cycle.
    """
    if status == MASTERED and review_count >= 1:
        return {"days": REPEATED_MASTERED_INTERVAL_DAYS, "reason": "repeatedly strong -- long-interval follow-up"}
    days = REVIEW_INTERVAL_DAYS.get(status, REVIEW_INTERVAL_DAYS[LEARNING])
    reason = {
        WEAK: "weak this session -- review again soon",
        LEARNING: "improving but not yet solid",
        MASTERED: "solid -- standard follow-up interval",
    }.get(status, "default interval")
    return {"days": days, "reason": reason}


def describe_due_date(next_review_at: float, now: Optional[float] = None) -> str:
    now = now if now is not None else _time.time()
    days_until = (next_review_at - now) / 86400
    if days_until <= 0:
        return "Due today"
    if days_until < 1.5:
        return "Due tomorrow"
    return f"Due in {round(days_until)} days"


def prioritize_reviews(reviews: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort order per the brief: weakest first, then most overdue, matching
    the dashboard's 🔴/🟡/🟢 ordering. `reviews` rows come from
    db.get_spaced_reviews (mastery, next_review_at already present).
    """
    def sort_key(r):
        return (r["mastery"], r["next_review_at"])  # lower mastery first, then earlier due date
    return sorted(reviews, key=sort_key)


# ---------------------------------------------------------------------------
# v5 -- Priority 3: Time-Budget Honesty.
#
# The LLM estimates how many minutes EACH concept needs (a content judgment
# it's well-placed to make); Python deterministically decides which concepts
# fit the requested time budget, whether the request was realistic, and
# what the recommended time would be -- the feasibility decision itself is
# never left to the model.
# ---------------------------------------------------------------------------
PLANNING_OVERHEAD_MINUTES = 2  # reserved for intro/quiz/wrap-up, not concept teaching time


def build_time_plan(requested_minutes: int, concepts_with_estimates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """concepts_with_estimates: [{"name", "estimated_minutes", "priority"}, ...]
    ordered however the LLM returned them; `priority` (1 = most important) is
    used as the tie-breaker / selection order, computed by the LLM but ACTED
    ON by this deterministic function.
    """
    ordered = sorted(concepts_with_estimates, key=lambda c: c.get("priority", 999))
    usable_minutes = max(requested_minutes - PLANNING_OVERHEAD_MINUTES, 1)

    selected, deferred = [], []
    running_total = 0
    for c in ordered:
        est = max(1, int(c.get("estimated_minutes", 5)))
        if running_total + est <= usable_minutes or not selected:
            # `or not selected`: always teach at least one concept, even if a
            # single concept alone slightly exceeds the budget.
            selected.append(c["name"])
            running_total += est
        else:
            deferred.append(c["name"])

    estimated_required_minutes = sum(max(1, int(c.get("estimated_minutes", 5))) for c in concepts_with_estimates) + PLANNING_OVERHEAD_MINUTES
    is_feasible = estimated_required_minutes <= requested_minutes
    recommended_time_minutes = int(round(estimated_required_minutes / 5.0) * 5) or 5

    if is_feasible:
        honesty_message = None
    else:
        n_total = len(concepts_with_estimates)
        n_selected = len(selected)
        honesty_message = (
            f"{requested_minutes} minutes isn't enough to properly cover all {n_total} concepts. "
            f"I'll cover the {n_selected} most important now"
            + (f" and give you a revision plan for the rest ({', '.join(deferred)})." if deferred else ".")
        )

    return {
        "requested_time_minutes": requested_minutes,
        "estimated_required_minutes": estimated_required_minutes,
        "is_feasible": is_feasible,
        "recommended_time_minutes": recommended_time_minutes,
        "selected_concepts": selected,
        "deferred_concepts": deferred,
        "honesty_message": honesty_message,
    }

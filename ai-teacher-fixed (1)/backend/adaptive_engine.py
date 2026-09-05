"""
Adaptive Teaching Engine.

This is the "novelty" the whole project is judged on, so it lives in its own
module, deliberately separate from prompt text and API plumbing, so it can be
pointed at directly during the demo ("this file decides what happens next").

It is a small, explainable state machine — not a black box. Given the
outcome of a student's answer, it decides ONE of four actions:

  RETEACH        -> same concept, simpler explanation, new analogy, difficulty -1
  ADVANCE_DIFF   -> same concept, harder follow-up question, difficulty +1
  NEXT_CONCEPT   -> move on, difficulty stays or nudges toward session baseline
  FINISH         -> no concepts left -> generate the final report

Rules (kept intentionally simple and legible for judges):
  - Difficulty is an integer 1..5.
  - A correct answer with high confidence raises difficulty and counts toward
    "mastered" (2 correct in a row on a concept, or 1 correct at difficulty
    >= session baseline + 1, moves to the next concept).
  - A wrong / partially-wrong answer lowers difficulty and triggers a
    same-concept reteach with a fresh analogy (never repeats an analogy).
  - A concept is abandoned as "weak" (not mastered) after 3 attempts, to
    prevent the student getting stuck in a loop -> engine advances anyway
    but the concept is flagged weak in the final report.
"""
from dataclasses import dataclass
from typing import Optional, List

MAX_ATTEMPTS_PER_CONCEPT = 3
MIN_DIFFICULTY = 1
MAX_DIFFICULTY = 5

RETEACH = "reteach"
ADVANCE_DIFF = "advance_difficulty"
NEXT_CONCEPT = "next_concept"
FINISH = "finish"


@dataclass
class EngineDecision:
    action: str
    new_difficulty: int
    reason: str


def decide(
    *,
    correct: bool,
    partial_credit: float,
    current_difficulty: int,
    session_baseline_difficulty: int,
    attempts_current: int,
    consecutive_correct: int,
    is_last_concept: bool,
) -> EngineDecision:
    """Pure function: no I/O, no DB, easy to unit-test and easy to demo.

    partial_credit is 0..1 from the evaluator LLM; treated as "correct enough"
    at >= 0.7 even if the boolean `correct` flag is borderline.
    """
    effectively_correct = correct or partial_credit >= 0.7

    if not effectively_correct:
        if attempts_current + 1 >= MAX_ATTEMPTS_PER_CONCEPT:
            # Stop hammering the student on the same concept; move on but
            # the caller is responsible for flagging this concept as weak.
            if is_last_concept:
                return EngineDecision(FINISH, current_difficulty, "max attempts reached, no concepts left")
            return EngineDecision(
                NEXT_CONCEPT,
                max(MIN_DIFFICULTY, session_baseline_difficulty - 1),
                "max attempts reached on this concept — moving on, flagged weak",
            )
        new_diff = max(MIN_DIFFICULTY, current_difficulty - 1)
        return EngineDecision(RETEACH, new_diff, "incorrect/misconception detected — simplifying and re-explaining")

    # effectively correct
    mastered_by_streak = consecutive_correct + 1 >= 2
    mastered_by_stretch = current_difficulty >= session_baseline_difficulty + 1
    if mastered_by_streak or mastered_by_stretch:
        if is_last_concept:
            return EngineDecision(FINISH, current_difficulty, "concept mastered, no concepts left")
        return EngineDecision(
            NEXT_CONCEPT,
            min(MAX_DIFFICULTY, current_difficulty),
            "concept mastered — advancing to next concept",
        )

    new_diff = min(MAX_DIFFICULTY, current_difficulty + 1)
    return EngineDecision(ADVANCE_DIFF, new_diff, "correct — raising difficulty to verify depth of understanding")


def estimate_concept_count(time_minutes: int) -> int:
    return max(2, min(6, round(time_minutes / 6)))

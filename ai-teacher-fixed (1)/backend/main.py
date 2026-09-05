import os
import json
import time
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import db
import rag
import llm
import prompts
import adaptive_engine as engine
import progress
import visual_engine
import scenes as scene_engine
import analytics
from schemas import (
    AnswerRequest, SessionStateResponse, LanguageRequest, QuizSubmitRequest,
    PersonalityRequest, HomeworkSubmitRequest, StrategyRequest, ExplainBackRequest,
)

app = FastAPI(title="AI Teacher API")

origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()

# In-memory retriever cache: session_id -> rag.Retriever
_retrievers = {}


def _get_retriever(session_id: str) -> Optional[rag.Retriever]:
    if session_id in _retrievers:
        return _retrievers[session_id]
    chunks_meta = db.get_chunks_with_meta(session_id)
    if not chunks_meta:
        return None
    texts = [c["text"] for c in chunks_meta]
    metadata = [{"source": c["source"], "page": c["page"]} for c in chunks_meta]
    r = rag.Retriever(texts, metadata)
    _retrievers[session_id] = r
    return r


def _plan(session_row) -> dict:
    return json.loads(session_row["plan_json"])


def _current_concept(session_row) -> Optional[dict]:
    plan = _plan(session_row)
    idx = session_row["concept_index"]
    concepts = plan["concepts"]
    if idx >= len(concepts):
        return None
    return concepts[idx]


def _retrieve_context(session_id: str, query: str) -> List[str]:
    retriever = _get_retriever(session_id)
    if retriever is None:
        return []
    return retriever.top_k(query, k=4)


def _retrieve_sources(session_id: str, query: str) -> List[Dict[str, Any]]:
    """v5 — RAG citations: same ranking as _retrieve_context, but returns
    source/page metadata so the teach turn can show "Based on your uploaded
    material — Page 37" instead of a bare, unattributed excerpt.
    """
    retriever = _get_retriever(session_id)
    if retriever is None:
        return []
    hits = retriever.top_k_with_meta(query, k=4)
    seen = set()
    sources = []
    for h in hits:
        key = (h.get("source"), h.get("page"))
        if key in seen:
            continue
        seen.add(key)
        sources.append({"source": h.get("source"), "page": h.get("page")})
    return sources


def _last_teach_turn(session_id: str) -> Optional[dict]:
    turns = db.get_turns(session_id)
    for t in reversed(turns):
        if t["kind"] == "teach":
            return t
    return None


def _concept_history(session_id: str, concept: str):
    """Return (prior_misconceptions, prior_analogies, prior_visual_types, prior_explanations,
    recent_evals) for a concept — the "teacher memory" of what's already happened THIS lesson.
    """
    turns = db.get_turns(session_id)
    misconceptions, analogies, visual_types, explanations, evals = [], [], [], [], []
    for t in turns:
        if t["concept"] != concept:
            continue
        if t["kind"] == "teach":
            if t["content"].get("analogy"):
                analogies.append(t["content"]["analogy"])
            if t["content"].get("explanation"):
                explanations.append(t["content"]["explanation"])
            visual = t["content"].get("visual") or {}
            if visual.get("type"):
                visual_types.append(visual["type"])
        if t["kind"] == "answer_eval":
            if t["content"].get("misconception"):
                misconceptions.append(t["content"]["misconception"])
            evals.append({
                "correct": bool(t["content"].get("correct")),
                "answer": t["content"].get("student_answer", ""),
                "misconception": t["content"].get("misconception"),
            })
    return misconceptions, analogies, visual_types, explanations, evals


def _generate_teach_content(session_row, is_reteach: bool = False) -> dict:
    sid = session_row["id"]
    plan = _plan(session_row)
    concept = _current_concept(session_row)
    if concept is None:
        raise HTTPException(400, "No more concepts in this session")

    prior_misconceptions, prior_analogies, prior_visual_types, prior_explanations, recent_evals = \
        _concept_history(sid, concept["name"])
    context = _retrieve_context(sid, f"{concept['name']} {concept['objective']}")
    sources = _retrieve_sources(sid, f"{concept['name']} {concept['objective']}") if session_row["grounded"] else []
    grounded_but_empty = bool(session_row["grounded"]) and not context

    misconception_detail = None
    if is_reteach:
        misconception_detail = [
            {"misconception": m["misconception"], "why_wrong": m["why_wrong"]}
            for m in db.get_misconceptions(sid) if m["concept"] == concept["name"]
        ]

    # The recall-previous-struggle note only opens the very first scene of the
    # very first concept of the session — never repeated on reteach/later concepts.
    is_first_teach_turn = session_row["concept_index"] == 0 and not is_reteach and not db.get_turns(sid)
    recall_note = session_row["recall_note"] if is_first_teach_turn else None

    # v5: deterministic confidence signal (observable interaction data only —
    # see progress.compute_confidence's docstring on why this is not emotion detection).
    confidence = progress.compute_confidence(
        recent_evals=recent_evals, wrong_streak=session_row["attempts_current"],
        consecutive_correct=session_row["consecutive_correct"],
        current_difficulty=session_row["difficulty"],
        baseline_difficulty=plan.get("starting_difficulty", 2),
    )

    knowledge_map = progress.build_knowledge_map(plan["concepts"], db.get_mastery(sid))
    prerequisite_note = progress.check_prerequisite_gate(concept, knowledge_map)

    system, user = prompts.teach_prompt(
        topic=plan["topic"],
        concept=concept["name"],
        objective=concept["objective"],
        level=session_row["level"],
        language=session_row["language"],
        difficulty=session_row["difficulty"],
        context_snippets=context,
        prior_misconceptions=prior_misconceptions,
        is_reteach=is_reteach,
        avoid_analogies=prior_analogies,
        avoid_visual_types=prior_visual_types,
        misconception_detail=misconception_detail,
        personality=session_row["personality"],
        mode=session_row["mode"],
        strategy=session_row["strategy"],
        visual_hint=visual_engine.visual_hint_for_subject(plan["topic"]),
        recall_note=recall_note,
        confidence=confidence,
        prerequisite_note=prerequisite_note,
        grounded_but_empty=grounded_but_empty,
        prior_explanations=prior_explanations,
    )
    content = llm.call_json(system, user, model=llm.TEACH_MODEL)
    content["visual"] = visual_engine.normalize_visual(content.get("visual"))
    content["sources"] = sources
    content["grounded_but_empty"] = grounded_but_empty
    content["confidence"] = confidence
    content["confidence_note"] = progress.CONFIDENCE_ADJUSTMENT_NOTE.get(confidence)
    content["prerequisite_note"] = prerequisite_note
    content["concept"] = concept["name"]
    content["concept_index"] = session_row["concept_index"]
    content["total_concepts"] = len(plan["concepts"])
    content["difficulty"] = session_row["difficulty"]
    content["is_reteach"] = is_reteach
    content["grounded"] = bool(context)

    turns = db.get_turns(sid)
    db.add_turn(sid, len(turns), concept["name"], session_row["difficulty"], "teach", content)

    # Deterministic scene decomposition — no extra LLM call, just structures
    # the content that was already generated into a playable sequence.
    scenes = scene_engine.build_scenes(content, mode=session_row["mode"], recall_note=recall_note)
    db.add_scenes(sid, concept["name"], scenes)
    content["scenes"] = scenes

    return content


@app.post("/api/sessions")
async def create_session(
    topic: str = Form(...),
    level: str = Form("Beginner"),
    language: str = Form("English"),
    time_minutes: int = Form(20),
    learning_goal: Optional[str] = Form(None),
    personality: str = Form("Friendly"),
    strategy: str = Form("Standard"),
    mode: str = Form("Learn"),
    student_id: Optional[str] = Form(None),
    review_concept: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    context_snippets: List[str] = []
    stored_chunks: List[Dict[str, Any]] = []  # [{"text":..., "page":...}, ...]

    if file is not None:
        raw = await file.read()
        pages = rag.extract_pages(file.filename, raw)
        stored_chunks = rag.chunk_pages(pages)
        if stored_chunks:
            # sample a few chunks up front to help the planner ground the plan
            context_snippets = [c["text"] for c in stored_chunks[: min(6, len(stored_chunks))]]

    # --- long-term student memory: deterministic recall + revision-mode gating ---
    forced_concepts = None
    recall_parts: List[str] = []
    memory_rows = db.get_student_memory(student_id, topic=topic) if student_id else []
    weak_or_learning = [m for m in memory_rows if m["status"] in (progress.WEAK, progress.LEARNING)]

    # v5: also fold in anything due for spaced review on this exact topic —
    # a due review and a "you struggled with this" note are the same kind of
    # nudge, so they share one recall mechanism instead of stacking two.
    due_reviews_this_topic = []
    if student_id:
        due_reviews_this_topic = [
            r for r in db.get_spaced_reviews(student_id)
            if r["topic"] == topic and r["next_review_at"] <= time.time()
        ]

    if review_concept:
        # Explicit "start a review" request from the spaced-review dashboard card.
        forced_concepts = [review_concept]
        mode = "Revision"
    elif mode == "Revision":
        forced_concepts = [m["concept"] for m in weak_or_learning] or [r["concept"] for r in due_reviews_this_topic] or None
        if not forced_concepts:
            mode = "Learn"  # nothing to revise yet — fall back rather than send an empty plan request

    if mode != "Revision":
        concern_names = list(dict.fromkeys(
            [m["concept"] for m in weak_or_learning[:2]] + [r["concept"] for r in due_reviews_this_topic[:2]]
        ))[:2]
        if concern_names:
            recall_parts.append(
                f"Welcome back! Before we continue, let's quickly revise {', '.join(concern_names)} "
                f"because you had some difficulty with it previously."
            )

    system, user = prompts.plan_prompt(
        topic=topic, level=level, language=language, time_minutes=time_minutes,
        learning_goal=learning_goal, context_snippets=context_snippets,
        mode=mode, forced_concepts=forced_concepts,
    )
    plan = llm.call_json(system, user, model=llm.TEACH_MODEL)
    plan["starting_difficulty"] = plan.get("starting_difficulty", 2)

    if mode == "Revision" and forced_concepts:
        # Deterministic safety net: don't just trust the prompt instruction --
        # actually filter to the forced set, in case the model still included
        # extra concepts.
        forced_set = set(forced_concepts)
        filtered = [c for c in plan.get("concepts", []) if c["name"] in forced_set]
        plan["concepts"] = filtered or plan.get("concepts", [])[:1]

    # --- v5: Time-Budget Honesty — deterministic selection, never left to the LLM ---
    time_plan = None
    if not (mode == "Revision" and forced_concepts):
        time_plan = progress.build_time_plan(time_minutes, plan.get("concepts", []))
        selected = set(time_plan["selected_concepts"])
        plan["concepts"] = [c for c in plan["concepts"] if c["name"] in selected] or plan["concepts"][:1]
        plan["time_plan"] = time_plan
        if time_plan["honesty_message"]:
            recall_parts.append(time_plan["honesty_message"])

    recall_note = " ".join(recall_parts) or None

    sid = db.create_session(topic, level, language, time_minutes, learning_goal, plan,
                             personality=personality, mode=mode, student_id=student_id, recall_note=recall_note)
    db.update_session(sid, strategy=strategy)

    if stored_chunks:
        db.add_chunks(sid, file.filename, [c["text"] for c in stored_chunks], pages=[c["page"] for c in stored_chunks])
        db.update_session(sid, grounded=1)

    return {
        "session_id": sid, "plan": plan, "grounded": bool(stored_chunks),
        "mode": mode, "personality": personality, "strategy": strategy, "recall_note": recall_note,
        "time_plan": time_plan,
    }


@app.get("/api/sessions/{session_id}/state", response_model=SessionStateResponse)
def get_state(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    concept = _current_concept(row)
    return SessionStateResponse(
        session_id=session_id,
        topic=plan["topic"],
        level=row["level"],
        language=row["language"],
        time_minutes=row["time_minutes"],
        concept_index=row["concept_index"],
        total_concepts=len(plan["concepts"]),
        current_concept=concept["name"] if concept else None,
        current_difficulty=row["difficulty"],
        time_used_minutes=round((time.time() - row["started_at"]) / 60, 1),
        grounded=bool(row["grounded"]),
        finished=bool(row["finished"]),
    )


@app.get("/api/sessions/{session_id}/next")
def next_step(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    if row["finished"]:
        return {"finished": True}

    time_used = (time.time() - row["started_at"]) / 60
    if time_used >= row["time_minutes"] and row["concept_index"] > 0:
        return _all_concepts_done(session_id, reason="time_up")

    if _current_concept(row) is None:
        return _all_concepts_done(session_id, reason="all_concepts_complete")

    content = _generate_teach_content(row, is_reteach=False)
    return {"finished": False, "teach": content}


def _all_concepts_done(session_id: str, reason: str) -> dict:
    """All concepts have been taught (or time ran out). If the final quiz hasn't
    been taken yet, signal the frontend to fetch it; only mark the session
    fully `finished` once the quiz step has happened (or been skipped by the
    caller going straight to /report).
    """
    row = db.get_session(session_id)
    if row["quiz_done"]:
        db.update_session(session_id, finished=1)
        return {"finished": True, "reason": reason}
    return {"finished": False, "quiz_ready": True, "reason": reason}


@app.post("/api/sessions/{session_id}/answer")
def submit_answer(session_id: str, body: AnswerRequest):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    last_teach = _last_teach_turn(session_id)
    if last_teach is None:
        raise HTTPException(400, "no active question — call /next first")

    plan = _plan(row)
    concept_name = last_teach["concept"]
    question = last_teach["content"]["question"]

    system, user = prompts.eval_prompt(
        topic=plan["topic"], concept=concept_name, question=question,
        student_answer=body.answer, language=row["language"], personality=row["personality"],
    )
    evaluation = llm.call_json(system, user, model=llm.EVAL_MODEL)
    evaluation["concept"] = concept_name
    evaluation["student_answer"] = body.answer

    turns_so_far = db.get_turns(session_id)
    db.add_turn(session_id, len(turns_so_far), concept_name, row["difficulty"], "answer_eval", evaluation)

    if evaluation.get("misconception"):
        db.add_misconception(
            session_id, concept_name, evaluation["misconception"],
            evaluation.get("why_wrong", ""), question, body.answer,
        )

    correct = bool(evaluation.get("correct"))
    partial = float(evaluation.get("partial_credit", 1.0 if correct else 0.0))
    is_last_concept = row["concept_index"] >= len(plan["concepts"]) - 1

    decision = engine.decide(
        correct=correct,
        partial_credit=partial,
        current_difficulty=row["difficulty"],
        session_baseline_difficulty=plan.get("starting_difficulty", 2),
        attempts_current=row["attempts_current"],
        consecutive_correct=row["consecutive_correct"],
        is_last_concept=is_last_concept,
    )

    mastery_delta = partial - 0.5  # roughly -0.5 (wrong) .. +0.5 (fully correct)
    db.upsert_mastery(session_id, concept_name, mastery_delta, correct, evaluation.get("misconception"))

    result = {
        "evaluation": evaluation,
        "engine_decision": {"action": decision.action, "reason": decision.reason},
    }

    if decision.action == engine.RETEACH:
        db.update_session(
            session_id, difficulty=decision.new_difficulty,
            attempts_current=row["attempts_current"] + 1, consecutive_correct=0,
        )
        fresh_row = db.get_session(session_id)
        result["teach"] = _generate_teach_content(fresh_row, is_reteach=True)
        result["finished"] = False

    elif decision.action == engine.ADVANCE_DIFF:
        db.update_session(
            session_id, difficulty=decision.new_difficulty,
            attempts_current=0, consecutive_correct=row["consecutive_correct"] + 1,
        )
        fresh_row = db.get_session(session_id)
        result["teach"] = _generate_teach_content(fresh_row, is_reteach=False)
        result["finished"] = False

    elif decision.action == engine.NEXT_CONCEPT:
        db.update_session(
            session_id, concept_index=row["concept_index"] + 1, difficulty=decision.new_difficulty,
            attempts_current=0, consecutive_correct=0,
        )
        fresh_row = db.get_session(session_id)
        if _current_concept(fresh_row) is None:
            result.update(_all_concepts_done(session_id, reason="concept_mastered"))
        else:
            result["teach"] = _generate_teach_content(fresh_row, is_reteach=False)
            result["finished"] = False

    else:  # FINISH — engine ran out of attempts or mastered the last concept
        db.update_session(session_id, concept_index=len(plan["concepts"]))
        result.update(_all_concepts_done(session_id, reason=decision.reason))

    # Attach the live knowledge map so the frontend can update its progress
    # panel after every single answer, not just at the end of the session.
    result["knowledge_map"] = progress.build_knowledge_map(plan["concepts"], db.get_mastery(session_id))
    return result


# ---------------------------------------------------------------------------
# v5 — SIGNATURE FEATURE: Explain-It-Back Verification.
#
# Teach concept -> ask the student to explain it back in their own words ->
# semantic evaluation (never keyword matching) -> if a misconception is
# found, the SAME reteach machinery used elsewhere (_generate_teach_content
# with is_reteach=True) fires, with the misconception logged first so the
# reteach prompt sees it -> student explains again -> deterministic mastery
# update. The LLM only ever produces the question and the evaluation; every
# state change (mastery, attempt numbering, what happens next) is decided
# here in Python, per the architecture rule.
# ---------------------------------------------------------------------------
@app.get("/api/sessions/{session_id}/explain-back")
def get_explain_back_question(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    concept = _current_concept(row)
    if concept is None:
        raise HTTPException(400, "no active concept — call /next first")

    prior_attempts = db.get_explain_back_attempts(session_id, concept["name"])
    is_retry = len(prior_attempts) > 0

    system, user = prompts.explain_back_question_prompt(
        topic=plan["topic"], concept=concept["name"], objective=concept["objective"],
        language=row["language"], is_retry=is_retry,
    )
    generated = llm.call_json(system, user, model=llm.TEACH_MODEL)
    return {
        "concept": concept["name"],
        "question_type": generated.get("question_type", "definition"),
        "prompt": generated.get("prompt", f"Explain {concept['name']} in your own words."),
        "attempt_number": len(prior_attempts) + 1,
    }


@app.post("/api/sessions/{session_id}/explain-back")
def submit_explain_back(session_id: str, body: ExplainBackRequest):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    concept = _current_concept(row)
    if concept is None:
        raise HTTPException(400, "no active concept — call /next first")

    system, user = prompts.explain_back_eval_prompt(
        topic=plan["topic"], concept=concept["name"], objective=concept["objective"],
        question_prompt=body.prompt, student_response=body.response,
        language=row["language"], personality=row["personality"],
    )
    evaluation = llm.call_json(system, user, model=llm.EVAL_MODEL)

    stored = db.add_explain_back_attempt(
        row["student_id"], session_id, concept["name"], body.question_type or "definition",
        body.prompt, body.response, evaluation,
    )

    # --- deterministic conversion of the semantic evaluation into learning state ---
    score = int(evaluation.get("score", 0))
    action = evaluation.get("action", "continue")
    correct_enough = score >= 70
    mastery_delta = (score / 100.0) - 0.5  # same -0.5..+0.5 scale used by db.upsert_mastery elsewhere

    misconceptions = evaluation.get("misconceptions") or []
    first_misconception = misconceptions[0] if misconceptions else None
    misconception_text = (
        first_misconception.get("concept") if isinstance(first_misconception, dict) else first_misconception
    )
    db.upsert_mastery(session_id, concept["name"], mastery_delta, correct_enough, misconception_text)
    if misconception_text:
        why_wrong = first_misconception.get("why_wrong", "") if isinstance(first_misconception, dict) else ""
        db.add_misconception(session_id, concept["name"], misconception_text, why_wrong, body.prompt, body.response)

    result = {
        "attempt_number": stored["attempt_number"],
        "evaluation": evaluation,
        "knowledge_map": progress.build_knowledge_map(plan["concepts"], db.get_mastery(session_id)),
    }

    # This is what makes it "adaptive reteaching based on the result", not just
    # a recorded score: reteach/simplify actually regenerates the teaching
    # content right now, using the exact misconception just found.
    if action in ("reteach", "simplify"):
        fresh_row = db.get_session(session_id)
        result["teach"] = _generate_teach_content(fresh_row, is_reteach=True)
        result["next_action"] = "reteach"
    elif action in ("clarify", "ask_follow_up") and evaluation.get("follow_up_question"):
        result["next_action"] = "follow_up"
        result["follow_up_question"] = evaluation["follow_up_question"]
    else:
        result["next_action"] = "continue"

    return result


@app.get("/api/sessions/{session_id}/explain-back/history")
def get_explain_back_history(session_id: str, concept: Optional[str] = None):
    return {"attempts": db.get_explain_back_attempts(session_id, concept)}


@app.get("/api/sessions/{session_id}/knowledge-map")
def get_knowledge_map(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    return {"knowledge_map": progress.build_knowledge_map(plan["concepts"], db.get_mastery(session_id))}


@app.post("/api/sessions/{session_id}/language")
def switch_language(session_id: str, body: LanguageRequest):
    """Switch teaching language mid-lesson. Concept index, difficulty, mastery,
    and turn history are all untouched — only content generated FROM NOW ON
    is in the new language, so context isn't lost.
    """
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    db.update_session(session_id, language=body.language)
    return {"session_id": session_id, "language": body.language}


@app.post("/api/sessions/{session_id}/personality")
def switch_personality(session_id: str, body: PersonalityRequest):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    db.update_session(session_id, personality=body.personality)
    return {"session_id": session_id, "personality": body.personality}


@app.post("/api/sessions/{session_id}/strategy")
def switch_strategy(session_id: str, body: StrategyRequest):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    db.update_session(session_id, strategy=body.strategy)
    return {"session_id": session_id, "strategy": body.strategy}


@app.get("/api/sessions/{session_id}/quiz")
def get_quiz(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")

    existing = db.get_quiz_questions(session_id)
    if existing:
        return {"questions": [{"id": q["id"], "concept": q["concept"], "question": q["question"]} for q in existing]}

    plan = _plan(row)
    n_questions = min(5, max(3, len(plan["concepts"])))
    system, user = prompts.quiz_prompt(
        topic=plan["topic"], level=row["level"], language=row["language"],
        concepts=plan["concepts"], n_questions=n_questions,
    )
    generated = llm.call_json(system, user, model=llm.TEACH_MODEL)
    ids = db.add_quiz_questions(session_id, generated["questions"])
    questions = [
        {"id": qid, "concept": q["concept"], "question": q["question"]}
        for qid, q in zip(ids, generated["questions"])
    ]
    return {"questions": questions}


@app.post("/api/sessions/{session_id}/quiz/submit")
def submit_quiz(session_id: str, body: QuizSubmitRequest):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    questions = {q["id"]: q for q in db.get_quiz_questions(session_id)}
    if not questions:
        raise HTTPException(400, "no quiz questions — call GET /quiz first")

    results = []
    for qid, answer in body.answers.items():
        q = questions.get(qid)
        if q is None:
            continue
        system, user = prompts.eval_prompt(
            topic=plan["topic"], concept=q["concept"], question=q["question"],
            student_answer=answer, language=row["language"], personality=row["personality"],
        )
        evaluation = llm.call_json(system, user, model=llm.EVAL_MODEL)
        correct = bool(evaluation.get("correct"))
        partial = float(evaluation.get("partial_credit", 1.0 if correct else 0.0))
        db.add_quiz_answer(qid, session_id, answer, correct, partial,
                            evaluation.get("feedback", ""), evaluation.get("misconception"))
        db.upsert_mastery(session_id, q["concept"], partial - 0.5, correct, evaluation.get("misconception"))
        if evaluation.get("misconception"):
            db.add_misconception(session_id, q["concept"], evaluation["misconception"],
                                  evaluation.get("why_wrong", ""), q["question"], answer)
        results.append({"concept": q["concept"], "correct": correct, "feedback": evaluation.get("feedback")})

    db.update_session(session_id, quiz_done=1, finished=1)

    if row["student_id"]:
        quiz_score = round(sum(1 for r in results if r["correct"]) / len(results) * 100) if results else 0
        db.add_analytics_event(row["student_id"], session_id, "quiz_scored", {"score_percent": quiz_score})

    return {"results": results, "finished": True}


@app.get("/api/sessions/{session_id}/notes")
def get_notes(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    turns = db.get_turns(session_id)
    misconceptions = db.get_misconceptions(session_id)
    system, user = prompts.notes_prompt(
        topic=plan["topic"], level=row["level"], language=row["language"],
        turns=turns, misconceptions=misconceptions,
    )
    notes = llm.call_json(system, user, model=llm.TEACH_MODEL)
    return {"notes": notes}


@app.get("/api/sessions/{session_id}/learning-path")
def get_learning_path(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    knowledge_map = progress.build_knowledge_map(plan["concepts"], db.get_mastery(session_id))
    quiz_answers = db.get_quiz_answers(session_id)
    turns = db.get_turns(session_id)
    stats = progress.compute_session_stats(knowledge_map, turns, row["difficulty"], quiz_answers)

    cross_session_weak = (
        [m for m in db.get_student_memory(row["student_id"]) if m["topic"] != plan["topic"]]
        if row["student_id"] else []
    )
    rec = progress.intelligent_recommendation(
        knowledge_map=knowledge_map, cross_session_weak=cross_session_weak,
        quiz_score_percent=stats.score_percent if quiz_answers else None,
        learning_goal=row["learning_goal"],
    )

    if rec["gated"]:
        # Deterministic gate wins outright — no LLM call needed to know this
        # student has unresolved difficulty, in this session or a past one.
        return {"gated": True, "revise_first": rec["revise_first"], "reasons": rec["reasons"], "path": []}

    mastered = [c["concept"] for c in knowledge_map if c["status"] == progress.MASTERED]
    system, user = prompts.learning_path_prompt(
        topic=plan["topic"], level=row["level"], language=row["language"],
        mastered_concepts=mastered, deterministic_reasons=rec["reasons"],
    )
    generated = llm.call_json(system, user, model=llm.TEACH_MODEL)
    return {"gated": False, "reasons": rec["reasons"], "path": generated.get("path", [])}


@app.get("/api/sessions/{session_id}/report")
def get_report(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    turns = db.get_turns(session_id)
    mastery = db.get_mastery(session_id)
    misconceptions = db.get_misconceptions(session_id)
    quiz_answers = db.get_quiz_answers(session_id)

    knowledge_map = progress.build_knowledge_map(plan["concepts"], mastery)
    stats = progress.compute_session_stats(knowledge_map, turns, row["difficulty"], quiz_answers)
    gate = progress.gate_next_topic(knowledge_map)

    system, user = prompts.report_prompt(
        topic=plan["topic"], level=row["level"], language=row["language"],
        knowledge_map=knowledge_map, misconceptions=misconceptions,
        stats=stats.__dict__,
    )
    narrative = llm.call_json(system, user, model=llm.TEACH_MODEL)

    next_topic = narrative.get("next_topic")
    next_topic_note = None
    if gate:
        # Deterministic gate overrides whatever the LLM suggested — don't
        # recommend a brand-new topic while this session's own concepts are weak.
        next_topic = f"Revise first: {', '.join(gate['revise_first'])}"
        next_topic_note = gate["reason"]

    report = {
        "score_percent": stats.score_percent,
        "questions_attempted": stats.questions_attempted,
        "questions_correct": stats.questions_correct,
        "mastered_concepts": stats.mastered_concepts,
        "learning_concepts": stats.learning_concepts,
        "weak_concepts": stats.weak_concepts,
        # kept for backwards compatibility with the original report shape:
        "strong_concepts": stats.mastered_concepts,
        "misconceptions": [m["misconception"] for m in misconceptions],
        "misconceptions_detail": misconceptions,
        "recommended_revision": narrative.get("recommended_revision", []),
        "next_topic": next_topic,
        "next_topic_note": next_topic_note,
        "suggested_next_difficulty": stats.suggested_next_difficulty,
        "summary": narrative.get("summary", ""),
        "knowledge_map": knowledge_map,
    }

    db.update_session(session_id, finished=1)

    review_schedule = []
    if row["student_id"]:
        sid_student = row["student_id"]
        for c in knowledge_map:
            db.upsert_student_memory(
                sid_student, plan["topic"], c["concept"], c["status"], c["score"],
                c["attempts"], c["correct"], c["misconceptions"],
            )
            # v5: deterministic spaced-repetition scheduling — Python decides
            # the date, never the LLM (see progress.schedule_next_review).
            existing_review = db.get_spaced_review(sid_student, plan["topic"], c["concept"])
            review_count = (existing_review["review_count"] if existing_review else 0) + 1
            interval = progress.schedule_next_review(c["status"], existing_review["review_count"] if existing_review else 0)
            next_review_at = time.time() + interval["days"] * 86400
            db.upsert_spaced_review(sid_student, plan["topic"], c["concept"], c["score"],
                                     review_count, next_review_at, interval["reason"])
            review_schedule.append({
                "concept": c["concept"], "mastery_status": c["status"],
                "next_review_in_days": interval["days"], "reason": interval["reason"],
            })

        duration_seconds = time.time() - row["started_at"]
        db.add_analytics_event(sid_student, session_id, "session_completed",
                                {"duration_seconds": duration_seconds, "score_percent": stats.score_percent})
        existing_profile = db.get_student_profile(sid_student)
        prev_time = existing_profile["total_time_seconds"] if existing_profile else 0
        prev_count = existing_profile["sessions_completed"] if existing_profile else 0
        db.upsert_student_profile(
            sid_student, preferred_language=row["language"], preferred_personality=row["personality"],
            recent_topic=plan["topic"], learning_goal=row["learning_goal"],
            total_time_seconds=prev_time + duration_seconds, sessions_completed=prev_count + 1,
        )

    report["review_schedule"] = review_schedule
    report["explain_back_summary"] = _explain_back_summary(session_id)
    return {"report": report, "mastery_raw": mastery}


def _explain_back_summary(session_id: str) -> List[Dict[str, Any]]:
    """Groups explain-back attempts by concept and shows first -> last score,
    for the report's "Understanding improved" section (Priority 6).
    """
    attempts = db.get_explain_back_attempts(session_id)
    by_concept: Dict[str, List[Dict[str, Any]]] = {}
    for a in attempts:
        by_concept.setdefault(a["concept"], []).append(a)
    summary = []
    for concept, items in by_concept.items():
        items.sort(key=lambda x: x["attempt_number"])
        summary.append({
            "concept": concept,
            "first_score": items[0]["score"],
            "final_score": items[-1]["score"],
            "improved": items[-1]["score"] > items[0]["score"],
            "attempts": len(items),
        })
    return summary


@app.get("/api/health")
def health():
    return {"status": "ok"}



# ---------------------------------------------------------------------------
# Priority 7: personalized homework, generated from this session's weak/learning concepts
# ---------------------------------------------------------------------------
def _generate_homework(session_id: str, row) -> List[Dict[str, Any]]:
    plan = _plan(row)
    knowledge_map = progress.build_knowledge_map(plan["concepts"], db.get_mastery(session_id))
    targets = [c for c in knowledge_map if c["status"] in (progress.WEAK, progress.LEARNING)]
    if not targets:
        # Nothing weak — still give practice on whatever was taught, so the
        # button always produces something useful during a demo.
        targets = knowledge_map[: min(2, len(knowledge_map))]

    system, user = prompts.homework_prompt(
        topic=plan["topic"], level=row["level"], language=row["language"],
        weak_concepts=[{"concept": c["concept"], "status": c["status"], "misconceptions": c["misconceptions"]} for c in targets],
    )
    generated = llm.call_json(system, user, model=llm.TEACH_MODEL)
    items = generated.get("homework", [])
    ids = db.add_homework(session_id, items)
    return [{"id": hid, "concept": item["concept"], "tier": item["tier"], "question": item["question"]}
            for hid, item in zip(ids, items)]


@app.get("/api/sessions/{session_id}/homework")
def get_homework(session_id: str):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    existing = db.get_homework(session_id)
    if existing:
        return {"homework": [{"id": h["id"], "concept": h["concept"], "tier": h["tier"], "question": h["question"]} for h in existing]}
    return {"homework": _generate_homework(session_id, row)}


@app.post("/api/sessions/{session_id}/homework/retry")
def retry_homework(session_id: str):
    """Regenerate a fresh homework set — e.g. after the student got several wrong."""
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    db.clear_homework(session_id)
    return {"homework": _generate_homework(session_id, row)}


@app.post("/api/sessions/{session_id}/homework/submit")
def submit_homework(session_id: str, body: HomeworkSubmitRequest):
    row = db.get_session(session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    plan = _plan(row)
    questions = {h["id"]: h for h in db.get_homework(session_id)}
    if not questions:
        raise HTTPException(400, "no homework — call GET /homework first")

    results = []
    for qid, answer in body.answers.items():
        q = questions.get(qid)
        if q is None:
            continue
        system, user = prompts.eval_prompt(
            topic=plan["topic"], concept=q["concept"], question=q["question"],
            student_answer=answer, language=row["language"], personality=row["personality"],
        )
        evaluation = llm.call_json(system, user, model=llm.EVAL_MODEL)
        correct = bool(evaluation.get("correct"))
        partial = float(evaluation.get("partial_credit", 1.0 if correct else 0.0))
        db.add_homework_answer(qid, session_id, answer, correct, partial, evaluation.get("feedback", ""))
        results.append({
            "id": qid, "concept": q["concept"], "tier": q["tier"],
            "correct": correct, "feedback": evaluation.get("feedback"),
        })

    # Deterministic score — never asked of the LLM.
    score_percent = round(sum(1 for r in results if r["correct"]) / len(results) * 100) if results else 0
    if row["student_id"]:
        db.add_analytics_event(row["student_id"], session_id, "homework_scored", {"score_percent": score_percent})

    return {"results": results, "score_percent": score_percent}


# ---------------------------------------------------------------------------
# Priority 8: learning analytics dashboard (fully deterministic — see analytics.py)
# ---------------------------------------------------------------------------
@app.get("/api/students/{student_id}/analytics")
def get_student_analytics(student_id: str):
    memory_rows = db.get_student_memory(student_id)
    events = db.get_analytics_events(student_id)
    return analytics.build_dashboard(student_id, memory_rows, events)


# ---------------------------------------------------------------------------
# v5 — Priority 5: spaced-review dashboard. Dates/ordering are 100%
# deterministic (progress.py); this endpoint just formats what's already
# stored for display, and the /start endpoint kicks off a real Revision-mode
# session scoped to that one concept (reuses the existing session-creation
# pipeline — no separate "review" code path to keep in sync).
# ---------------------------------------------------------------------------
@app.get("/api/students/{student_id}/reviews")
def get_reviews(student_id: str):
    reviews = db.get_spaced_reviews(student_id)
    ordered = progress.prioritize_reviews(reviews)
    due_now = [r for r in ordered if r["next_review_at"] <= time.time()]
    return {
        "reviews": [
            {
                "topic": r["topic"], "concept": r["concept"], "mastery": r["mastery"],
                "due_label": progress.describe_due_date(r["next_review_at"]),
                "next_review_at": r["next_review_at"], "reason": r["reason"],
            }
            for r in ordered
        ],
        "due_count": len(due_now),
    }


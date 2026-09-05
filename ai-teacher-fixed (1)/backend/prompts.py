"""
Prompt architecture.

Every prompt asks Claude to return ONLY a JSON object, no prose, no markdown
fences, so the backend can parse it directly. Each function returns
(system_prompt, user_prompt).
"""
import json
from typing import List, Optional, Dict, Any

LANGUAGE_NOTE = {
    "English": "Write entirely in English.",
    "Hindi": "Write entirely in Hindi, using the Devanagari script.",
    "Hinglish": "Write in Hinglish (Hindi mixed with English, Roman script), "
                "the way Indian students actually talk to each other while studying.",
}

PERSONALITY_NOTE = {
    "Friendly": "Warm, encouraging, casual tone. Use everyday relatable examples. Celebrate small wins.",
    "Strict exam teacher": "Crisp, no-nonsense tone. Focus on precision and exam-relevant detail. Point out "
                            "mistakes directly (but never rudely) and emphasize what loses marks.",
    "Storyteller": "Wrap the explanation in a short narrative or scenario. Use characters, stakes, or a "
                   "mini-story to make the concept memorable, without sacrificing accuracy.",
    "Socratic teacher": "Lead with a guiding question before revealing the answer. Nudge the student to "
                         "reason their way to the concept rather than stating it outright where possible.",
    "Technical mentor": "Precise, industry-practitioner tone, as if mentoring a junior colleague. Use "
                         "correct terminology and real-world engineering/professional framing.",
}

# Teaching STRATEGY is independent of personality (v4 Priority 15): personality is
# tone/voice, strategy is the structural shape of the explanation. The two combine
# freely — e.g. "Friendly" + "Socratic", or "Strict exam teacher" + "Example-first".
STRATEGY_NOTE = {
    "Standard": "Explain the concept directly, then check understanding with a question.",
    "Socratic": "Do NOT give the explanation directly. Instead: (1) ask a short guiding question that "
                "makes the student think about the concept themselves, framed as if you're about to hear "
                "their answer, (2) in the same explanation field, include a brief hint or a related fact "
                "that nudges them toward the right reasoning WITHOUT stating the conclusion outright, "
                "(3) the 'question' field should be that guiding question itself — a question designed "
                "to make them reason their way to the answer, not recall a fact you already gave them.",
    "Exam-focused": "Frame the explanation around what's commonly tested: lead with the highest-yield "
                     "fact, mention a common mistake students make, and phrase the question the way an "
                     "exam would (precise, unambiguous, one correct answer).",
    "Example-first": "Open with a concrete worked example BEFORE the abstract explanation — show the "
                      "instance first, then generalize to the rule.",
}

MODE_NOTE = {
    "Learn": "Normal teaching pace: full explanation, analogy, example, then a check question.",
    "Exam": "Exam-prep focus: be concise, prioritize high-value/commonly-tested points and common student "
            "mistakes, mention time-management where relevant, and phrase the question in an exam style.",
    "Revision": "Revision focus: assume the student has seen this before — skip basic setup, go straight "
                "to the part they previously struggled with, and ask a question that specifically re-tests it.",
    "Practice": "Practice focus: minimal explanation (a single sentence at most), maximum question density — "
                "the student is here to answer questions, not relisten to a lecture.",
}


def _lang(language: str) -> str:
    return LANGUAGE_NOTE.get(language, LANGUAGE_NOTE["English"])


def _personality(personality: str) -> str:
    return PERSONALITY_NOTE.get(personality, PERSONALITY_NOTE["Friendly"])


def _mode(mode: str) -> str:
    return MODE_NOTE.get(mode, MODE_NOTE["Learn"])


def _strategy(strategy: str) -> str:
    return STRATEGY_NOTE.get(strategy, STRATEGY_NOTE["Standard"])


def plan_prompt(topic: str, level: str, language: str, time_minutes: int,
                 learning_goal: Optional[str], context_snippets: List[str],
                 mode: str = "Learn", forced_concepts: Optional[List[str]] = None):
    system = (
        "You are an expert curriculum designer creating a lesson plan for a one-on-one "
        "AI tutoring session. You are careful about prerequisite structure — a concept "
        "should never be scheduled before the concepts it depends on. You always respond "
        "with a single valid JSON object and nothing else — no markdown fences, no commentary."
    )
    context_block = ""
    if context_snippets:
        joined = "\n---\n".join(context_snippets[:8])
        context_block = (
            f"\nThe student uploaded material. Find the concepts that actually matter in "
            f"it (don't just chunk it mechanically), and ground the plan in these excerpts "
            f"(do not invent facts that contradict them):\n{joined}\n"
        )

    mode_block = ""
    if mode == "Revision" and forced_concepts:
        mode_block = (
            f"\nThis is a REVISION session — a deterministic system has already identified "
            f"exactly which concepts this student needs to revisit: {forced_concepts}. Use "
            f"ONLY these concepts, in this exact set (you may still order them sensibly), do "
            f"not introduce new concepts.\n"
        )
    elif mode == "Exam":
        mode_block = (
            "\nThis is an EXAM-PREP session — bias concept selection toward the highest-value, "
            "most commonly tested concepts for this topic rather than full foundational coverage.\n"
        )
    elif mode == "Practice":
        mode_block = (
            "\nThis is a PRACTICE session — the student mostly wants questions, not lectures. "
            "Keep each objective narrow so each concept can be checked with a quick question "
            "rather than a long explanation.\n"
        )

    time_budget_block = "" if (mode == "Revision" and forced_concepts) else (
        "\nIMPORTANT — do NOT pre-filter concepts to fit the time budget yourself. List every "
        "genuinely important concept for this topic at this level (typically 3-10 concepts, "
        "however many the topic actually needs), each with your honest estimate of how many "
        "minutes it would take to teach properly, and a priority rank (1 = most essential to "
        "understand first). A separate deterministic system will decide which of your listed "
        "concepts actually fit in the available time — that is not your job.\n"
    )

    user = f"""
Design a lesson plan.

Topic: {topic}
Student level: {level}
Session length: {time_minutes} minutes
Learning goal: {learning_goal or "general understanding"}
{context_block}{mode_block}{time_budget_block}
{_lang(language)}

Steps:
1. Identify the important concepts (from the material above if provided, otherwise from
   your own knowledge of the topic).
2. Work out which concepts are prerequisites of which others.
3. Order concepts from easy -> difficult, respecting that prerequisite structure — a
   concept never appears before something it depends on.
4. Give each concept its own difficulty baseline (1-5) — later concepts should generally
   start at or above the difficulty of the concepts they depend on.
5. Give each concept one short checkpoint question idea — what you'd ask right after
   teaching it to confirm the student is ready to move on.
6. Give each concept an honest "estimated_minutes" (how long to teach it properly) and a
   "priority" (1 = most essential).

Return JSON with this exact shape:
{{
  "topic": "string",
  "starting_difficulty": 1-5 integer appropriate for the student's level overall,
  "concepts": [
    {{
      "name": "string",
      "objective": "one sentence, what the student should be able to do after this concept",
      "prerequisites": ["names of earlier concepts in THIS plan it depends on, [] if none"],
      "difficulty": 1-5 integer starting difficulty for this specific concept,
      "checkpoint_idea": "one short sentence describing what the checkpoint question should probe",
      "estimated_minutes": integer minutes to teach this concept properly,
      "priority": 1-N integer, 1 = most essential
    }}
  ]
}}
"""
    return system, user


VISUAL_TYPE_GUIDE = """
The "visual" field is one JSON object. Its "type" is one of: equation, graph, diagram,
timeline, code, molecule, comparison, flowchart, process, simulation. Include ONLY the
fields relevant to the chosen type, omit all others, and never include comments — valid
JSON only:
- type "equation" (math/physics formulas): also include "formula" (e.g. "V = I * R") and
  "variables" (list of {"symbol", "meaning"}).
- type "graph" (a relationship between two numeric quantities): also include "x_label",
  "y_label", and "points" (4-8 objects {"x", "y"} describing the curve/line shape).
- type "diagram" (labeled parts of something, e.g. a cell or a circuit, or a force
  diagram) OR "timeline" (a sequence of events) OR "flowchart"/"process" (steps in a
  process or a programming execution flow) — all four use the same generic shape: also
  include "nodes" (list of {"id", "label"}) and "edges" (list of {"from", "to", "label"},
  "label" optional).
- type "code" (programming topics): also include "code" (a short illustrative snippet),
  "language", and optionally "output" (what running it would print — a deterministic
  simulated result, since code is never actually executed).
- type "molecule" (chemistry structures): also include "atoms" (list of {"id", "label"})
  and "bonds" (list of {"from", "to", "order"} where order is 1/2/3 for single/double/triple).
- type "comparison" (contrasting two or more things side by side): also include "columns"
  (list of short column headers) and "rows" (list of {"label", "values"} where values has
  one entry per column).
- type "simulation" (an interactive demonstration is genuinely more useful than a static
  visual — e.g. Ohm's Law, projectile motion, a function graph, a probability experiment):
  also include "simulation" (one of: ohms_law, projectile_motion, function_graph,
  probability_experiment, reaction) and "parameters" (an object of starting numeric
  values appropriate to that simulation).
"""


def teach_prompt(topic: str, concept: str, objective: str, level: str, language: str,
                  difficulty: int, context_snippets: List[str],
                  prior_misconceptions: List[str], is_reteach: bool = False,
                  avoid_analogies: Optional[List[str]] = None,
                  avoid_visual_types: Optional[List[str]] = None,
                  misconception_detail: Optional[List[Dict[str, str]]] = None,
                  personality: str = "Friendly", mode: str = "Learn", strategy: str = "Standard",
                  visual_hint: str = "", recall_note: Optional[str] = None,
                  confidence: Optional[str] = None, prerequisite_note: Optional[str] = None,
                  context_sources: Optional[List[Dict[str, Any]]] = None,
                  grounded_but_empty: bool = False, prior_explanations: Optional[List[str]] = None):
    system = (
        "You are a warm, encouraging, extremely clear human-like AI teacher. You explain "
        "one concept at a time, always with a concrete example or analogy AND a visual "
        "that fits the subject, then check understanding with exactly one question. "
        f"Teaching style for this session: {_personality(personality)} "
        f"Teaching strategy for this session: {_strategy(strategy)} Never let personality "
        "or strategy change any fact, formula, or definition — only tone, structure, and "
        "phrasing. You always respond with a single valid JSON object and nothing else — "
        "no markdown fences, no commentary."
    )
    context_block = ""
    if context_snippets:
        joined = "\n---\n".join(context_snippets[:5])
        context_block = f"\nGround your explanation in these excerpts from the student's own material:\n{joined}\n"
    elif grounded_but_empty:
        context_block = (
            "\nThe student uploaded material, but nothing in it is relevant to this specific "
            "concept. Explicitly say in your explanation that this part isn't covered in "
            "their uploaded material and you're using general knowledge instead — do not "
            "silently pass off general knowledge as being from their notes.\n"
        )

    recall_block = ""
    if recall_note:
        recall_block = f"\nBefore teaching, this exact sentence should open the lesson (use it verbatim as the start of your explanation): \"{recall_note}\"\n"

    prereq_block = f"\n{prerequisite_note}\n" if prerequisite_note else ""

    confidence_block = ""
    if confidence == "LOW":
        confidence_block = ("\nThe student is showing signs of struggle on this concept (wrong-answer streak, "
                             "short answers, or a repeated misconception). Slow down: simpler words, a fresh "
                             "analogy, an easier question than the target difficulty suggests.\n")
    elif confidence == "HIGH":
        confidence_block = ("\nThe student is showing strong signs of understanding (correct streak at a "
                             "raised difficulty). Move faster: keep the explanation brief and make the "
                             "question a harder, applied problem rather than a recall check.\n")

    memory_block = ""
    if prior_explanations:
        memory_block = (f"\nYou already explained this concept earlier in THIS lesson using this wording — "
                         f"do not repeat it near-verbatim, say it differently this time:\n"
                         f"{prior_explanations[-1][:400]}\n")

    reteach_block = ""
    if is_reteach:
        detail_lines = ""
        if misconception_detail:
            detail_lines = "\n".join(
                f"- misconception: {d.get('misconception')} | why it's wrong: {d.get('why_wrong')}"
                for d in misconception_detail if d.get("misconception")
            )
        reteach_block = f"""
The student just got this wrong. Do NOT simply mark it wrong again — actively re-teach:
1. Name the likely misconception in plain terms (don't be clinical about it).
2. Briefly explain WHY that line of thinking leads to the wrong answer.
3. Re-explain the concept in SIMPLER words than before.
4. Use a DIFFERENT analogy than any of these already used: {avoid_analogies or []}.
5. Use a DIFFERENT visual type than any of these already used, if it still fits the
   subject: {avoid_visual_types or []}.
6. Give a simpler concrete example than before.
7. Ask a new follow-up question that specifically checks whether the misconception is gone.

Known misconceptions on this concept so far:
{detail_lines or prior_misconceptions}
"""

    user = f"""
Topic: {topic}
Concept to teach right now: {concept}
Objective: {objective}
Student level: {level}
Target difficulty (1=very easy, 5=expert): {difficulty}
Session mode: {mode} — {_mode(mode)}
{context_block}{recall_block}{prereq_block}{confidence_block}{memory_block}{reteach_block}
{_lang(language)}

Decide what kind of visual best supports THIS explanation given the subject (e.g. math ->
equation or graph; physics -> diagram, equation, or a "simulation" if genuinely interactive
would help; biology -> a labeled diagram; history -> a timeline; chemistry -> molecule or
equation; programming -> code or a flowchart).
Subject hint: {visual_hint or "pick whatever type genuinely fits"}. Don't force a visual
that doesn't fit — pick the type genuinely most useful here.
{VISUAL_TYPE_GUIDE}
Return JSON with this exact shape:
{{
  "explanation": "sentences teaching the concept clearly, length appropriate to the session mode above",
  "analogy": "one short relatable analogy (skip/keep minimal if mode is Practice)",
  "example": "one short concrete worked example",
  "slide": {{
    "title": "short slide title",
    "bullets": ["3-5 short bullet points summarizing the explanation for a slide"]
  }},
  "visual": {{"type": "...", "title": "...", "...type-specific fields as described above": "..."}},
  "question": "one question that checks whether the student understood THIS concept, styled per the session mode and strategy",
  "question_type": "conceptual | numerical | recall"
}}
"""
    return system, user


def eval_prompt(topic: str, concept: str, question: str, student_answer: str, language: str,
                 personality: str = "Friendly"):
    system = (
        "You are grading a student's spoken/typed answer during a live tutoring session. "
        "Be generous with partial credit reasoning but strict about factual correctness. "
        "When the answer is wrong, your job is diagnostic, not just judgmental: figure out "
        "the SPECIFIC misconception behind the wrong answer, not just that it's wrong. "
        f"Feedback tone for this session: {_personality(personality)} The correctness "
        "judgment itself must never change with tone — only how the feedback sentence is worded. "
        "You always respond with a single valid JSON object and nothing else."
    )
    user = f"""
Topic: {topic}
Concept: {concept}
Question asked: {question}
Student's answer: {student_answer}

{_lang(language)}

If the answer is wrong or partially wrong, think about what mental model would produce
that exact answer — that's the misconception to name. Never just say "incorrect".

Return JSON with this exact shape:
{{
  "correct": true/false,
  "partial_credit": 0.0-1.0,
  "misconception": "short phrase naming the specific misunderstanding, or null if fully correct",
  "why_wrong": "one sentence explaining WHY that reasoning leads to a wrong answer, or null if fully correct",
  "feedback": "1-2 encouraging sentences of feedback to show the student immediately"
}}
"""
    return system, user


EXPLAIN_BACK_QUESTION_TYPES = ["definition", "causal", "relationship", "application", "teach_a_friend", "compare"]


def explain_back_question_prompt(topic: str, concept: str, objective: str, language: str,
                                  is_retry: bool = False):
    """Generates the "explain it back to me" prompt itself -- the LLM picks
    whichever question TYPE (definition/causal/relationship/application/
    teach-a-friend/compare) genuinely fits this concept, rather than always
    asking the same style of question.
    """
    system = (
        "You are an AI teacher designing an 'explain it back to me' checkpoint. Instead of "
        "testing recall with a multiple-choice question, you want the student to articulate "
        "their own understanding in their own words, so you can see what they actually grasped. "
        "You always respond with a single valid JSON object and nothing else."
    )
    retry_note = (
        "\nThis is a RETRY after a misconception was corrected -- phrase the prompt so it "
        "specifically re-checks the part that was previously misunderstood, without simply "
        "repeating the exact same wording as before.\n" if is_retry else ""
    )
    user = f"""
Topic: {topic}
Concept: {concept}
Objective: {objective}
{retry_note}
{_lang(language)}

Choose the question type that best fits explaining THIS concept back, from:
{EXPLAIN_BACK_QUESTION_TYPES}
- definition: "Explain X in your own words."
- causal: "Why does X happen?"
- relationship: "How are X and Y related?"
- application: "Explain how you would use X in this situation."
- teach_a_friend: "Imagine you're teaching this to a friend. Explain it simply."
- compare: "What is the difference between X and Y?"

Return JSON with this exact shape:
{{
  "question_type": "one of the types above",
  "prompt": "the actual question to show the student, in the student's language"
}}
"""
    return system, user


def explain_back_eval_prompt(topic: str, concept: str, objective: str, question_prompt: str,
                              student_response: str, language: str, personality: str = "Friendly"):
    """The semantic evaluator -- deliberately NOT keyword matching. Asked to
    reason about what the student's own words demonstrate they understand,
    what's missing, and what specific misconception (if any) is present.
    """
    system = (
        "You are semantically evaluating a student's own-words explanation of a concept they "
        "were just taught. Do NOT grade based on whether specific keywords appear -- read the "
        "explanation for whether the underlying reasoning, relationships, and cause/effect are "
        "correct, even if the student uses completely different words or an analogy of their own. "
        f"Feedback tone: {_personality(personality)} The score and understanding judgment must "
        "never change with tone -- only how the feedback sentence is worded. "
        "You always respond with a single valid JSON object and nothing else."
    )
    user = f"""
Topic: {topic}
Concept: {concept}
Objective (what a correct explanation should demonstrate): {objective}
Question asked: {question_prompt}
Student's own-words explanation: {student_response}

{_lang(language)}

Analyze:
- What did the student correctly understand (concepts AND relationships/cause-effect)?
- What important idea is missing?
- Is there a specific misconception (an incorrect idea, not just an incomplete one)?
- Overall completeness: complete | mostly_correct | partially_correct | incorrect
- Confidence in your read of their understanding: high | medium | low
- What should happen next: continue | clarify | reteach | simplify | ask_follow_up

Return JSON with this exact shape:
{{
  "understanding_level": "complete | mostly_correct | partially_correct | incorrect",
  "score": 0-100 integer,
  "understood": ["short phrase per correctly-understood idea, [] if none"],
  "missing": ["short phrase per important missing idea, [] if none"],
  "misconceptions": [{{"concept": "short label", "why_wrong": "one sentence"}}],
  "feedback": "1-2 sentences of feedback to show the student immediately",
  "action": "continue | clarify | reteach | simplify | ask_follow_up",
  "follow_up_question": "a specific follow-up question if action is clarify/ask_follow_up/reteach, else null"
}}
"""
    return system, user


def quiz_prompt(topic: str, level: str, language: str, concepts: List[Dict[str, Any]], n_questions: int):
    system = (
        "You are writing a short final quiz for a completed tutoring session, mixing "
        "questions across the concepts that were taught. You always respond with a single "
        "valid JSON object and nothing else."
    )
    concept_list = "\n".join(f"- {c['name']}: {c['objective']}" for c in concepts)
    user = f"""
Topic: {topic}
Student level: {level}
{_lang(language)}

Concepts taught this session:
{concept_list}

Write exactly {n_questions} quiz questions covering a mix of these concepts (favor concepts
that are more central to the topic, but touch as many different concepts as you reasonably
can). Vary question type (conceptual / numerical / recall) across the set.

Return JSON with this exact shape:
{{
  "questions": [
    {{"concept": "must exactly match one of the concept names above", "question": "string", "difficulty": 1-5}}
  ]
}}
"""
    return system, user


def report_prompt(topic: str, level: str, language: str, knowledge_map: List[Dict[str, Any]],
                   misconceptions: List[Dict[str, Any]], stats: Dict[str, Any]):
    """Note: score, mastered/learning/weak lists, and questions-attempted are all
    computed deterministically in progress.py and passed in as `stats` — the LLM is
    only asked for the parts that genuinely need judgment/language: revision phrasing,
    a next-topic suggestion, and an encouraging summary. This keeps the numbers a
    student sees reproducible instead of re-derived by the model each time.
    """
    system = (
        "You are summarizing a completed tutoring session into a student-facing report. "
        "The scoring has already been computed deterministically — do not invent different "
        "numbers, just write the qualitative parts. You always respond with a single valid "
        "JSON object and nothing else."
    )
    user = f"""
Topic: {topic}
Student level: {level}
{_lang(language)}

Computed session stats (already final, just for your context):
{json.dumps(stats, indent=2)}

Knowledge map (per concept status):
{json.dumps(knowledge_map, indent=2)}

Misconceptions caught during the session, with the reasoning behind each:
{json.dumps(misconceptions, indent=2)[:4000]}

Return JSON with this exact shape:
{{
  "recommended_revision": ["short actionable revision tip, grounded in the specific weak/learning concepts and misconceptions above", "..."],
  "next_topic": "one specific next topic to study, building on this session — assume prerequisites here were fully mastered",
  "summary": "2-3 sentence encouraging summary of how the session went, mentioning at least one specific concept by name"
}}
"""
    return system, user


def notes_prompt(topic: str, level: str, language: str, turns: List[Dict[str, Any]],
                  misconceptions: List[Dict[str, Any]]):
    system = (
        "You are turning a completed tutoring session into study notes the student can "
        "revise from later. You always respond with a single valid JSON object and nothing else."
    )
    teach_turns = [t for t in turns if t["kind"] == "teach"]
    condensed = [
        {
            "concept": t["concept"],
            "explanation": t["content"].get("explanation"),
            "analogy": t["content"].get("analogy"),
            "example": t["content"].get("example"),
            "visual": t["content"].get("visual"),
        }
        for t in teach_turns
    ]
    user = f"""
Topic: {topic}
Student level: {level}
{_lang(language)}

Everything taught this session, concept by concept:
{json.dumps(condensed, indent=2)[:6000]}

Mistakes made this session:
{json.dumps(misconceptions, indent=2)[:3000]}

Return JSON with this exact shape:
{{
  "key_concepts": ["short concept name", ...],
  "definitions": [{{"term": "string", "definition": "one clear sentence"}}],
  "formulas": ["string, empty list if the topic has none"],
  "examples": ["short worked example, one per concept where useful"],
  "mistakes_to_avoid": ["one sentence per misconception caught, phrased as a reminder"],
  "revise_next": ["short list of what to review before the next session"]
}}
"""
    return system, user


def homework_prompt(topic: str, level: str, language: str, weak_concepts: List[Dict[str, Any]]):
    """weak_concepts: [{"concept": str, "status": "weak"|"learning", "misconceptions": [...]}]"""
    system = (
        "You are creating personalized homework targeting a student's specific weak concepts "
        "from a tutoring session. You always respond with a single valid JSON object and nothing else."
    )
    concept_block = json.dumps(weak_concepts, indent=2)
    user = f"""
Topic: {topic}
Student level: {level}
{_lang(language)}

Concepts to target (with what went wrong, if known):
{concept_block}

For EACH concept above, write exactly 5 homework questions forming a difficulty ladder:
1. easy — a simple recall/application question
2. medium — combines the concept with one other idea
3. application — a real-world or numerical application problem
4. explain_own_words — "explain this concept in your own words" style
5. challenge — a harder problem that stretches slightly past this session's difficulty

Return JSON with this exact shape:
{{
  "homework": [
    {{"concept": "string", "tier": "easy", "question": "string"}},
    {{"concept": "string", "tier": "medium", "question": "string"}},
    {{"concept": "string", "tier": "application", "question": "string"}},
    {{"concept": "string", "tier": "explain_own_words", "question": "string"}},
    {{"concept": "string", "tier": "challenge", "question": "string"}}
  ]
}}
"""
    return system, user


def learning_path_prompt(topic: str, level: str, language: str, mastered_concepts: List[str],
                          deterministic_reasons: Optional[List[str]] = None):
    system = (
        "You are a curriculum advisor suggesting what a student should study next, given a "
        "broader subject area. You always respond with a single valid JSON object and nothing else."
    )
    reasons_block = ""
    if deterministic_reasons:
        reasons_block = (
            "\nA deterministic system already analyzed this student's history and found:\n"
            + "\n".join(f"- {r}" for r in deterministic_reasons)
            + "\nFactor this into your suggestion.\n"
        )
    user = f"""
Topic the student just finished a session on: {topic}
Student level: {level}
Concepts they've now mastered in this subject area: {mastered_concepts}
{reasons_block}
{_lang(language)}

Suggest a short learning path (3-5 topics) for what to study next in this subject area,
each one building on the last, starting from the most natural next step after what they
just mastered.

Return JSON with this exact shape:
{{
  "path": [
    {{"topic": "string", "why_now": "one short sentence on why this comes next"}}
  ]
}}
"""
    return system, user

"""
Teaching Video Engine.

Turns one teach-turn's JSON (explanation / analogy / example / visual /
question, all LLM-generated) into a SEQUENCE OF SCENES the frontend plays one
after another - narration, visual, on-screen text, and an optional question,
each with an estimated duration and a transition. This is deterministic:
no extra LLM call is needed because the content already exists in the teach
turn, Python is just responsible for pacing/structuring it into a short
"video" instead of one big monologue block. That also satisfies the
architecture rule (LLM generates content, Python decides structure/timing).

Reusable scene schema (every scene has exactly these fields):

{
  "id": "scene_0",
  "type": "intro" | "explain" | "visual" | "question",
  "narration": "what the teacher says out loud during this scene",
  "on_screen_text": ["short bullet", ...],
  "visual": {...} | null,           # a visual_engine-normalized spec, or null
  "duration": 12.4,                 # seconds, estimated from narration length
  "transition": "fade" | "slide" | "none",
  "question": {...} | null          # only set on the final scene, if any
}

Provider-swap note: nothing here assumes browser TTS. `narration` is plain
text; a future integration with HeyGen/D-ID/ElevenLabs would consume the same
scene list and just render `narration` through a different pipeline instead
of `SpeechSynthesis` - the scene schema itself doesn't change.
"""
from typing import Dict, Any, List, Optional

WORDS_PER_SECOND = 2.3   # a calm teaching pace, used only to estimate scene duration
MIN_SCENE_SECONDS = 3.0
MAX_SCENE_SECONDS = 45.0


def _estimate_duration(text: str) -> float:
    words = len((text or "").split())
    return round(min(MAX_SCENE_SECONDS, max(MIN_SCENE_SECONDS, words / WORDS_PER_SECOND)), 1)


def build_scenes(teach: Dict[str, Any], mode: str = "Learn", recall_note: Optional[str] = None) -> List[Dict[str, Any]]:
    """Deterministically split one teach turn into scenes.

    `mode` changes STRUCTURE (which scenes exist / how much detail), never
    facts: Practice mode skips the explain/visual scenes almost entirely and
    jumps to the question; Exam mode keeps explanation short and tags the
    question scene as exam-style; Learn/Revision keep the full sequence.
    """
    scenes: List[Dict[str, Any]] = []
    slide = teach.get("slide") or {}
    bullets = slide.get("bullets") or []
    visual = teach.get("visual")

    if recall_note:
        scenes.append({
            "id": f"scene_{len(scenes)}", "type": "intro",
            "narration": recall_note, "on_screen_text": [], "visual": None,
            "duration": _estimate_duration(recall_note), "transition": "fade", "question": None,
        })

    if mode == "Practice":
        # Minimal explanation, maximum questions: a one-line intro then straight to the question.
        intro_text = f"Quick one on {teach.get('concept', 'this')}."
        scenes.append({
            "id": f"scene_{len(scenes)}", "type": "intro",
            "narration": intro_text, "on_screen_text": bullets[:1], "visual": visual,
            "duration": _estimate_duration(intro_text), "transition": "none", "question": None,
        })
    else:
        intro_text = teach.get("explanation", "")
        scenes.append({
            "id": f"scene_{len(scenes)}", "type": "intro",
            "narration": intro_text, "on_screen_text": [slide.get("title", teach.get("concept", ""))],
            "visual": None, "duration": _estimate_duration(intro_text), "transition": "fade", "question": None,
        })

        explain_parts = []
        if teach.get("analogy"):
            explain_parts.append(teach["analogy"])
        if teach.get("example"):
            explain_parts.append(f"For example: {teach['example']}")
        explain_text = " ".join(explain_parts)
        if explain_text and mode != "Exam":
            scenes.append({
                "id": f"scene_{len(scenes)}", "type": "explain",
                "narration": explain_text, "on_screen_text": bullets, "visual": visual,
                "duration": _estimate_duration(explain_text), "transition": "slide", "question": None,
            })
        elif visual:
            # Exam mode: skip the analogy/example narration but still show the visual.
            scenes.append({
                "id": f"scene_{len(scenes)}", "type": "visual",
                "narration": "", "on_screen_text": bullets, "visual": visual,
                "duration": MIN_SCENE_SECONDS, "transition": "slide", "question": None,
            })

    question_text = teach.get("question", "")
    if mode == "Exam":
        question_text = f"Exam-style question: {question_text}"
    scenes.append({
        "id": f"scene_{len(scenes)}", "type": "question",
        "narration": question_text, "on_screen_text": [], "visual": None,
        "duration": _estimate_duration(question_text), "transition": "fade",
        "question": {"text": teach.get("question", ""), "question_type": teach.get("question_type", "conceptual")},
    })

    return scenes


def total_duration(scenes: List[Dict[str, Any]]) -> float:
    return round(sum(s["duration"] for s in scenes), 1)

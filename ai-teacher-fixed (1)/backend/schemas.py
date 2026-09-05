from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class CreateSessionRequest(BaseModel):
    topic: Optional[str] = None
    level: str = "Beginner"            # Beginner | Intermediate | Advanced
    language: str = "English"          # English | Hindi | Hinglish
    time_minutes: int = 20             # 5 | 20 | 60
    learning_goal: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    plan: Dict[str, Any]


class AnswerRequest(BaseModel):
    answer: str


class LanguageRequest(BaseModel):
    language: str  # English | Hindi | Hinglish


class PersonalityRequest(BaseModel):
    personality: str  # Friendly | Strict exam teacher | Storyteller | Socratic teacher | Technical mentor


class StrategyRequest(BaseModel):
    strategy: str  # Standard | Socratic | Exam-focused | Example-first


class ExplainBackRequest(BaseModel):
    prompt: str                        # the question that was shown to the student
    response: str                      # the student's own-words explanation
    question_type: Optional[str] = None


class QuizSubmitRequest(BaseModel):
    answers: Dict[str, str]  # question_id -> student answer


class HomeworkSubmitRequest(BaseModel):
    answers: Dict[str, str]  # homework question_id -> student answer


class SessionStateResponse(BaseModel):
    session_id: str
    topic: str
    level: str
    language: str
    time_minutes: int
    concept_index: int
    total_concepts: int
    current_concept: Optional[str]
    current_difficulty: int
    time_used_minutes: float
    grounded: bool
    finished: bool

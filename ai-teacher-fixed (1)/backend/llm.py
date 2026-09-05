"""
LLM provider wrapper.

v5: switched the primary provider from Anthropic/Claude to Groq. The rest of
the application (main.py, prompts.py) only ever calls `llm.call_json(...)`
and reads `llm.TEACH_MODEL` / `llm.EVAL_MODEL` -- nothing outside this file
knows which provider is behind those names, so swapping providers again
later (or adding a fallback) means editing only this module.

    main.py -> llm.py -> Groq (this file's only external dependency)

Env vars (see backend/.env.example):
    GROQ_API_KEY     required
    GROQ_MODEL       optional, defaults to a current Groq-hosted model
    GROQ_EVAL_MODEL  optional, defaults to GROQ_MODEL if unset -- lets you
                     point grading at a cheaper/faster model than teaching
                     without needing a second env var most of the time.
"""
import os
import json
import re
from groq import Groq

_client = None

# console.groq.com/docs/models lists current model IDs -- this default is a
# large general-purpose Groq-hosted Llama model at the time this was
# written; override via GROQ_MODEL if it's been retired or you want a
# different one (Groq deprecates/rotates hosted models over time).
TEACH_MODEL = os.environ["GROQ_MODEL"]
EVAL_MODEL = os.environ.get("GROQ_EVAL_MODEL", TEACH_MODEL)


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy backend/.env.example to backend/.env "
                "and add your key from https://console.groq.com/keys"
            )
        _client = Groq(api_key=api_key)
    return _client


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def call_json(system: str, user: str, model: str = None, max_tokens: int = 1200) -> dict:
    """Call the LLM and parse a JSON object from the response.

    Tries Groq's native JSON mode first (response_format={"type":"json_object"}),
    which most current Groq-hosted models support and which avoids the
    stray-prose-before-JSON problem entirely. Falls back to a plain call plus
    manual fence-stripping + brace-extraction + one retry if JSON mode isn't
    supported by the chosen model or still doesn't parse -- so this keeps
    working even if GROQ_MODEL is pointed at a model without JSON mode.
    """
    client = _get_client()
    model = model or TEACH_MODEL

    for attempt in range(2):
        prompt = user if attempt == 0 else user + "\n\nReturn ONLY the JSON object. No other text."
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        raw = None
        if attempt == 0:
            try:
                resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                raw = resp.choices[0].message.content
            except Exception:
                raw = None  # model/account doesn't support JSON mode -- fall through to plain call
        if raw is None:
            resp = client.chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content

        cleaned = _strip_json_fences(raw or "")
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        candidate = match.group(0) if match else cleaned
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            if attempt == 1:
                raise RuntimeError(f"Model did not return valid JSON:\n{(raw or '')[:800]}")
    raise RuntimeError("Unreachable")

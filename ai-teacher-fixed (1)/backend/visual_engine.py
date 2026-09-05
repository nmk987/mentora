"""
Visual Engine.

Architecture rule this file exists to enforce: "the LLM decides WHAT visual is
needed, deterministic code decides HOW to render it." The teach prompt asks
Claude for a `visual` object with a `type` and type-specific fields. This
module never calls the LLM — it just validates and normalizes whatever came
back into a shape the frontend renderer can always trust, filling in sane
defaults or falling back to a simpler type if the model's output is
malformed or incomplete. No image API, no network call: SVG/HTML/Canvas
rendering happens entirely in the browser from this normalized spec.

Supported subject-agnostic visual types (kept deliberately small and
composable rather than one bespoke type per subject):

  equation   - math/physics formulas                (formula, variables)
  graph      - numeric x/y relationships             (points, x_label, y_label)
  diagram    - labeled parts / force diagrams /
               process diagrams / architecture       (nodes, edges)
  timeline   - sequences of events (history)          (nodes, edges - same shape as diagram)
  code       - programming                            (code, language, output)
  molecule   - chemistry structures                   (atoms, bonds)
  bullets    - safe fallback when nothing else fits    (points as text bullets)

A physics "force diagram", a biology "process diagram", a history "event
relationship" map, and a programming "execution flow" are all just
different labels for the same node/edge shape - one renderer covers all
four, which is why "diagram" and "timeline" are structurally identical here.
"""
from typing import Dict, Any, List

VALID_TYPES = {"equation", "graph", "diagram", "timeline", "code", "molecule", "bullets",
               "comparison", "flowchart", "process", "simulation"}


def _as_list(x) -> List[Any]:
    return x if isinstance(x, list) else []


def _normalize_equation(v: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "equation",
        "title": v.get("title") or "Formula",
        "formula": v.get("formula") or "",
        "variables": [
            {"symbol": str(x.get("symbol", "?")), "meaning": str(x.get("meaning", ""))}
            for x in _as_list(v.get("variables"))
        ][:6],
    }


def _normalize_graph(v: Dict[str, Any]) -> Dict[str, Any]:
    points = []
    for p in _as_list(v.get("points")):
        try:
            points.append({"x": float(p["x"]), "y": float(p["y"])})
        except (KeyError, TypeError, ValueError):
            continue
    if len(points) < 2:
        return _normalize_bullets({"title": v.get("title"), "points": [v.get("formula", "relationship unclear")]})
    return {
        "type": "graph",
        "title": v.get("title") or "Graph",
        "x_label": v.get("x_label", ""),
        "y_label": v.get("y_label", ""),
        "points": points[:12],
    }


def _normalize_node_edge(v: Dict[str, Any], type_name: str) -> Dict[str, Any]:
    nodes = [{"id": str(n.get("id", i)), "label": str(n.get("label", "?"))}
             for i, n in enumerate(_as_list(v.get("nodes")))][:8]
    if not nodes:
        return _normalize_bullets(v)
    valid_ids = {n["id"] for n in nodes}
    edges = [
        {"from": str(e.get("from")), "to": str(e.get("to")), "label": str(e.get("label", ""))}
        for e in _as_list(v.get("edges"))
        if str(e.get("from")) in valid_ids and str(e.get("to")) in valid_ids
    ][:10]
    return {"type": type_name, "title": v.get("title") or ("Timeline" if type_name == "timeline" else "Diagram"),
            "nodes": nodes, "edges": edges}


def _normalize_code(v: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "code",
        "title": v.get("title") or "Code",
        "code": str(v.get("code", ""))[:2000],
        "language": v.get("language", "python"),
        "output": v.get("output"),
    }


def _normalize_molecule(v: Dict[str, Any]) -> Dict[str, Any]:
    atoms = [
        {"id": str(a.get("id", i)), "label": str(a.get("label", "C"))}
        for i, a in enumerate(_as_list(v.get("atoms")))
    ][:12]
    if not atoms:
        return _normalize_bullets(v)
    valid_ids = {a["id"] for a in atoms}
    bonds = []
    for b in _as_list(v.get("bonds")):
        if str(b.get("from")) in valid_ids and str(b.get("to")) in valid_ids:
            order_raw = b.get("order", 1)
            order = int(order_raw) if str(order_raw).isdigit() else 1
            bonds.append({"from": str(b.get("from")), "to": str(b.get("to")), "order": order})
    return {"type": "molecule", "title": v.get("title") or "Molecule", "atoms": atoms, "bonds": bonds[:14]}


def _normalize_bullets(v: Dict[str, Any]) -> Dict[str, Any]:
    points = [str(p) for p in _as_list(v.get("points"))][:6]
    if not points and v.get("formula"):
        points = [str(v["formula"])]
    return {"type": "bullets", "title": v.get("title") or "Key point", "points": points or ["See explanation above"]}


def _normalize_comparison(v: Dict[str, Any]) -> Dict[str, Any]:
    columns = [str(c) for c in _as_list(v.get("columns"))][:5]
    rows = []
    for r in _as_list(v.get("rows")):
        label = str(r.get("label", "?"))
        values = [str(x) for x in _as_list(r.get("values"))][: len(columns) or 5]
        rows.append({"label": label, "values": values})
    if not columns or not rows:
        return _normalize_bullets(v)
    return {"type": "comparison", "title": v.get("title") or "Comparison", "columns": columns, "rows": rows[:8]}


# "flowchart" and "process" are structurally identical to diagram/timeline
# (a sequence of labeled steps) — same node/edge shape, just a different
# semantic label the LLM might use for programming/biology processes.
def _normalize_flow(v: Dict[str, Any], type_name: str) -> Dict[str, Any]:
    return {**_normalize_node_edge(v, "diagram"), "type": type_name}


SIMULATION_DEFAULTS = {
    "ohms_law": {"voltage": 10, "resistance": 5},
    "projectile_motion": {"velocity": 20, "angle": 45},
    "function_graph": {"a": 1, "b": 0, "c": 0, "expression": "a*x^2 + b*x + c"},
    "probability_experiment": {"trials": 20, "sides": 6},
    "reaction": {"reactants": ["A", "B"], "products": ["AB"]},
}


def _normalize_simulation(v: Dict[str, Any]) -> Dict[str, Any]:
    sim_name = v.get("simulation")
    if sim_name not in SIMULATION_DEFAULTS:
        # Unknown/unsupported simulation name — degrade gracefully rather
        # than show a broken widget.
        return _normalize_bullets({"title": v.get("title"), "points": [f"(Simulation '{sim_name}' not available in this MVP)"]})
    params = dict(SIMULATION_DEFAULTS[sim_name])
    raw_params = v.get("parameters") if isinstance(v.get("parameters"), dict) else {}
    for k, val in raw_params.items():
        if k in params:
            params[k] = val
    return {"type": "simulation", "title": v.get("title") or sim_name.replace("_", " ").title(),
            "simulation": sim_name, "parameters": params}


def normalize_visual(raw: Any) -> Dict[str, Any]:
    """Always returns a well-formed visual spec, never raises, never passes
    through unvalidated model output to the frontend.
    """
    if not isinstance(raw, dict):
        return _normalize_bullets({})
    vtype = raw.get("type")
    if vtype == "equation":
        return _normalize_equation(raw)
    if vtype == "graph":
        return _normalize_graph(raw)
    if vtype in ("diagram", "timeline"):
        return _normalize_node_edge(raw, vtype)
    if vtype in ("flowchart", "process"):
        return _normalize_flow(raw, vtype)
    if vtype == "code":
        return _normalize_code(raw)
    if vtype == "molecule":
        return _normalize_molecule(raw)
    if vtype == "comparison":
        return _normalize_comparison(raw)
    if vtype == "simulation":
        return _normalize_simulation(raw)
    return _normalize_bullets(raw)


def visual_hint_for_subject(topic: str) -> str:
    """A tiny deterministic heuristic used only to nudge the prompt with a
    subject hint - the LLM still makes the final call on visual type. Kept
    intentionally crude (keyword match); it's a hint, not a classifier.
    """
    t = topic.lower()
    if any(k in t for k in ("react", "molecule", "compound", "bond", "acid", "chemical")):
        return "chemistry (consider 'molecule' or 'equation')"
    if any(k in t for k in ("code", "program", "algorithm", "function", "python", "javascript", "loop")):
        return "programming (consider 'code' or 'diagram' for execution flow)"
    if any(k in t for k in ("history", "war", "empire", "revolution", "century", "treaty")):
        return "history (consider 'timeline')"
    if any(k in t for k in ("cell", "organ", "photosynthesis", "biology", "anatomy", "gene")):
        return "biology (consider 'diagram' for labeled structures)"
    if any(k in t for k in ("force", "circuit", "velocity", "energy", "physics", "motion", "voltage")):
        return "physics (consider 'diagram' or 'equation')"
    if any(k in t for k in ("equation", "algebra", "calculus", "geometry", "math")):
        return "math (consider 'equation' or 'graph')"
    return "general (pick whatever type genuinely fits)"

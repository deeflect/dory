from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from dory_core.llm.openrouter import OpenRouterClient

JudgeOutcome = Literal["passed", "partial", "failed"]


@dataclass(frozen=True, slots=True)
class EvalJudgeRequest:
    question: str
    question_type: str
    notes: str
    retrieved_paths: tuple[str, ...]
    retrieved_snippets: tuple[str, ...]
    wake_block: str | None = None


@dataclass(frozen=True, slots=True)
class EvalJudgeDecision:
    outcome: JudgeOutcome
    rationale: str


class EvalJudge(Protocol):
    def judge(self, request: EvalJudgeRequest) -> EvalJudgeDecision: ...


_EVAL_JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["passed", "partial", "failed"],
        },
        "rationale": {"type": "string"},
    },
    "required": ["outcome", "rationale"],
}


@dataclass(frozen=True, slots=True)
class OpenRouterEvalJudge:
    client: OpenRouterClient

    def judge(self, request: EvalJudgeRequest) -> EvalJudgeDecision:
        retrieved_paths = "\n- ".join(request.retrieved_paths or ("<none>",))
        rendered_snippets = _render_snippets(request.retrieved_snippets)
        payload = self.client.generate_json(
            system_prompt=(
                "You are judging a retrieval harness, not generating a final answer. "
                "Use only the provided evidence. For negation or temporal-abstention questions, "
                "mark passed only when the retrieved evidence appropriately supports abstaining or a cautious answer. "
                "Mark partial when evidence is mixed or insufficient. Mark failed when retrieval is misleading or clearly wrong."
            ),
            user_prompt=(
                f"Question type: {request.question_type}\n"
                f"Question: {request.question}\n"
                f"Notes: {request.notes}\n"
                f"Retrieved paths:\n- {retrieved_paths}\n\n"
                f"Retrieved snippets:\n{rendered_snippets}\n\n"
                f"Wake block:\n{request.wake_block or '<none>'}\n"
            ),
            schema_name="eval_judgement",
            schema=_EVAL_JUDGE_SCHEMA,
        )
        outcome = payload.get("outcome")
        rationale = payload.get("rationale")
        if outcome not in {"passed", "partial", "failed"}:
            return EvalJudgeDecision(outcome="partial", rationale="Judge returned an invalid outcome.")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = "Judge returned no rationale."
        return EvalJudgeDecision(outcome=outcome, rationale=rationale.strip())


def _render_snippets(snippets: tuple[str, ...]) -> str:
    if not snippets:
        return "<none>"
    rendered: list[str] = []
    for index, snippet in enumerate(snippets, start=1):
        rendered.append(f"[{index}] {snippet}")
    return "\n\n".join(rendered)

"""Model configuration and decision adapters for the Week 4 baseline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ActionName = Literal[
    "list_files",
    "read_file",
    "replace_text",
    "apply_patch",
    "run_tests",
    "finish",
]


class ModelConfigError(ValueError):
    """Raised when a real OpenAI-compatible model is not configured."""


class LLMSettings(BaseSettings):
    """Settings shared by cloud OpenAI-compatible APIs and future vLLM."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = Field(default=512, ge=64, le=2048)
    llm_replace_max_tokens: int = Field(default=384, ge=64, le=1024)

    @property
    def configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


class AgentDecision(BaseModel):
    """One bounded action returned by the single Agent model."""

    action: ActionName
    reason: str = ""
    path: str | None = None
    patch: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    command: list[str] = Field(default_factory=lambda: ["pytest", "-q"])
    summary: str = ""

    @field_validator("command", mode="before")
    @classmethod
    def normalize_empty_test_command(cls, value: object) -> object:
        """Keep an omitted or empty model command within the fixed test profile."""

        if value is None or value == []:
            return ["pytest", "-q"]
        return value


class DecisionModel(Protocol):
    mode: str

    def decide(self, state: Mapping[str, Any]) -> AgentDecision:
        """Choose exactly one next action."""
        ...


class ReplaceTextDecision(BaseModel):
    """A single exact source replacement generated in the edit phase."""

    path: str
    old_text: str = Field(min_length=1, max_length=600)
    new_text: str = Field(max_length=600)
    reason: str = ""


def _shorten_context_text(value: object, limit: int) -> object:
    """Bound text fields before they are serialized into the model context."""

    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit] + "\n... <truncated for model context> ..."


def build_prompt_context(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep durable task evidence while dropping duplicated LangGraph chatter."""

    context: dict[str, Any] = {}
    for key in (
        "task_id",
        "request",
        "goal",
        "acceptance_criteria",
        "repository_files",
        "changed_files",
        "tool_call_count",
        "status",
    ):
        if key in state:
            context[key] = state[key]

    observed = state.get("observed_files")
    if isinstance(observed, Mapping):
        observed_context: dict[str, object] = {}
        remaining = 6_000
        for path, content in observed.items():
            if not isinstance(path, str) or remaining <= 0:
                continue
            bounded = _shorten_context_text(content, min(3_000, remaining))
            observed_context[path] = bounded
            if isinstance(bounded, str):
                remaining -= len(bounded)
        context["observed_files"] = observed_context

    history = state.get("tool_history")
    if isinstance(history, list):
        context["tool_history"] = history[-8:]

    for key in ("last_tool_result", "test_results"):
        result = state.get(key)
        if not isinstance(result, Mapping):
            continue
        compact = {name: value for name, value in result.items() if name != "content"}
        for text_key in ("stdout", "stderr"):
            if text_key in compact:
                compact[text_key] = _shorten_context_text(compact[text_key], 2_000)
        context[key] = compact
    return context


def create_llm(settings: LLMSettings) -> Any:
    """Create the guide-compatible ChatOpenAI adapter when configured."""

    if not settings.configured:
        raise ModelConfigError("LLM_NOT_CONFIGURED: set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL")
    base_url = settings.llm_base_url
    api_key = settings.llm_api_key
    model = settings.llm_model
    assert base_url is not None and api_key is not None and model is not None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
        temperature=settings.llm_temperature,
        max_completion_tokens=settings.llm_max_tokens,
        timeout=60,
        max_retries=1,
    )


class OpenAICompatibleDecisionModel:
    """Structured-output model adapter for cloud APIs or later vLLM."""

    mode = "openai_compatible"

    def __init__(self, settings: LLMSettings | None = None, llm: Any | None = None) -> None:
        self.settings = settings or LLMSettings()
        self.llm = llm or create_llm(self.settings)
        self.structured_llm = self.llm.with_structured_output(AgentDecision)

    @staticmethod
    def _observed_source_files(state: Mapping[str, Any]) -> dict[str, object]:
        observed = state.get("observed_files")
        if not isinstance(observed, Mapping):
            return {}
        result: dict[str, object] = {}
        for path, content in observed.items():
            if not isinstance(path, str):
                continue
            filename = path.rsplit("/", maxsplit=1)[-1]
            if filename.startswith("test_") or "/tests/" in f"/{path}":
                continue
            result[path] = content
        return result

    def _should_replace_text(self, state: Mapping[str, Any]) -> bool:
        history = state.get("tool_history")
        last_action = (
            history[-1].get("action")
            if isinstance(history, list) and history and isinstance(history[-1], Mapping)
            else None
        )
        return last_action == "read_file" and bool(self._observed_source_files(state))

    @staticmethod
    def _verification_action(state: Mapping[str, Any]) -> AgentDecision | None:
        """Deterministically close the edit-test loop once source changed."""

        history = state.get("tool_history")
        last = (
            history[-1]
            if isinstance(history, list) and history and isinstance(history[-1], Mapping)
            else None
        )
        if not isinstance(last, Mapping):
            return None
        if last.get("action") in {"replace_text", "apply_patch"} and last.get("ok") is True:
            return AgentDecision(
                action="run_tests",
                reason="A source edit succeeded; run the fixed public test profile now.",
            )
        if last.get("action") != "run_tests":
            return None
        test_results = state.get("test_results")
        if isinstance(test_results, Mapping) and test_results.get("ok") is True:
            return AgentDecision(
                action="finish",
                reason="Public tests passed after the source edit.",
                summary="Public tests passed after the source edit.",
            )
        changed_files = state.get("changed_files")
        if isinstance(changed_files, list) and len(changed_files) == 1:
            return AgentDecision(
                action="read_file",
                path=str(changed_files[0]),
                reason="Tests failed; refresh the edited source before another exact replacement.",
            )
        return None

    def _decide_replacement(self, state: Mapping[str, Any]) -> AgentDecision:
        context = build_prompt_context(state)
        context["observed_files"] = self._observed_source_files(state)
        prompt = json.dumps(context, ensure_ascii=False, default=str)
        replace_tokens = min(
            self.settings.llm_replace_max_tokens,
            self.settings.llm_max_tokens,
        )
        response = (
            self.llm.bind(max_completion_tokens=replace_tokens)
            .with_structured_output(ReplaceTextDecision)
            .invoke(
                [
                    (
                        "system",
                        """You are in the exact-edit phase of a cautious software maintenance Agent.
Return a JSON object matching the schema. Choose one non-test path from observed_files.
old_text must be a nonempty exact substring copied from that file and must identify one
localized change. new_text must be the smallest correct replacement. Keep both strings
short and do not include surrounding functions. Do not use a diff, Markdown,
explanations, or test-file edits.""",
                    ),
                    ("human", prompt),
                ]
            )
        )
        replacement = (
            response
            if isinstance(response, ReplaceTextDecision)
            else ReplaceTextDecision.model_validate(response)
        )
        return AgentDecision(
            action="replace_text",
            reason=replacement.reason or "Apply an exact localized source replacement.",
            path=replacement.path,
            old_text=replacement.old_text,
            new_text=replacement.new_text,
        )

    def decide(self, state: Mapping[str, Any]) -> AgentDecision:
        verification = self._verification_action(state)
        if verification is not None:
            return verification
        if self._should_replace_text(state):
            return self._decide_replacement(state)
        prompt = json.dumps(build_prompt_context(state), ensure_ascii=False, default=str)
        response = self.structured_llm.invoke(
            [
                (
                    "system",
                    """You are a cautious software maintenance Agent. Follow the user request and acceptance criteria.

Rules:
1. If no repository file list is available in the current state, choose list_files first.
2. Read relevant source or tests before editing.
3. Choose exactly one action per turn.
4. Apply the smallest patch; never edit tests to hide a failure.
5. Run the public tests after editing.
6. Finish only when evidence supports the acceptance criteria.
7. For run_tests, use command ["pytest", "-q"]; never send an empty command.
8. repository_files and observed_files are durable state. Do not repeat a successful
   list_files or read_file solely to recover information already present there.
9. Prefer replace_text for one localized source edit: provide path, exact old_text from
   observed_files, and its smallest new_text replacement. The replacement is applied only
   if old_text occurs exactly once.
10. For apply_patch, patch must contain only one complete unified diff. It must start
   with "diff --git a/<path> b/<path>", use --- a/<path> and +++ b/<path>, and
   copy the exact pre-change lines from observed_files. Do not use Markdown fences,
   explanations, placeholder hashes, or invented context. A minimal valid shape is:
   diff --git a/app.py b/app.py
   --- a/app.py
   +++ b/app.py
   @@ -1 +1 @@
   -old
   +new
11. If apply_patch fails, use its error and observed_files to produce a corrected
    diff before running tests.
Return one compact JSON action matching the response schema; do not add prose outside it.
Allowed actions: list_files, read_file, replace_text, apply_patch, run_tests, finish.""",
                ),
                ("human", prompt),
            ]
        )
        if isinstance(response, AgentDecision):
            return response
        return AgentDecision.model_validate(response)


class OfflineRepairModel:
    """Deterministic harness policy used when no real model key is configured.

    It consumes the task's reference patch only to validate the Agent graph,
    sandbox, trajectory schema, and evaluator. Its success rate must not be
    reported as an LLM capability score.
    """

    mode = "offline_oracle_harness"

    def __init__(self, reference_patch: str) -> None:
        self.reference_patch = reference_patch
        self.read_path = self._patch_path(reference_patch)

    @staticmethod
    def _patch_path(patch: str) -> str:
        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                return line[6:].strip()
        raise ModelConfigError("REFERENCE_PATCH_PATH_MISSING")

    def decide(self, state: Mapping[str, Any]) -> AgentDecision:
        history = state.get("tool_history", [])
        successful_actions = {
            item.get("action")
            for item in history
            if isinstance(item, Mapping) and item.get("ok") is True
        }
        tests = state.get("test_results", {})
        if "read_file" not in successful_actions:
            return AgentDecision(
                action="read_file",
                path=self.read_path,
                reason="Inspect the target source before editing.",
            )
        if "apply_patch" not in successful_actions:
            return AgentDecision(
                action="apply_patch",
                patch=self.reference_patch,
                reason="Apply the standard repair after reading the target.",
            )
        if not isinstance(tests, Mapping) or not tests.get("ok"):
            return AgentDecision(
                action="run_tests",
                command=["pytest", "-q"],
                reason="Collect test evidence after the patch.",
            )
        return AgentDecision(
            action="finish",
            reason="The public test evidence is successful.",
            summary="Offline harness completed the reference repair workflow.",
        )

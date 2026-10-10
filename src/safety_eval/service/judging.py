"""Judging for the service: one safe wrapper used by both the request path and the worker."""

import logging
import re

import yaml

from safety_eval.clinic import Clinic
from safety_eval.judge import PROMPTS, PromptVersion, judge
from safety_eval.schemas import BaseConversation, JudgeResult

log = logging.getLogger("safety_eval.service")


class PromptError(ValueError):
    """A prompt version the service will not use."""


class NullCache:
    """A cache that never stores. The research cache/ directory is committed to the repo, so a
    service writing there would put model output (derived from client conversations) into git and
    onto disk beside the code. Each service call goes to the model; prompt caching at the API
    (docs/service.md) is the planned way to save cost instead."""

    def get(self, key):
        return None

    def put(self, key, entry):
        pass

    def __contains__(self, key):
        return False


_prompts = {}


def load_prompt(version):
    """The prompt for a version letter. Version A is the rules baseline, not a model prompt, so it
    is refused: the service must never answer with it by mistake."""
    if not re.fullmatch(r"[A-Za-z0-9]{1,8}", version or ""):
        raise PromptError("prompt_version must be a short letter or number")
    if version not in _prompts:
        path = PROMPTS / f"{version}.md"
        if not path.exists():
            raise PromptError(f"unknown prompt_version {version!r}")
        if yaml.safe_load(path.read_text().split("---")[1]).get("mode") == "rules":
            raise PromptError(f"prompt_version {version!r} is the rules baseline, not a model prompt")
        # The leak check guards the committed prompt files against dev/test conversations. It is
        # run by the research harness and needs the research data folders, which a deployment lacks.
        _prompts[version] = PromptVersion.load(version, check_leaks=False)
    return _prompts[version]


def judge_safely(stored, clinic_id, prompt_version, backend):
    """Judge a stored (de-identified) conversation. Anything unexpected becomes needs_review with
    the reason "internal error": never an exception, never pass. Only the exception's type is
    logged, because its message could quote the conversation."""
    try:
        conv = BaseConversation.model_validate(stored)
        return judge(conv, load_prompt(prompt_version), backend, cache=NullCache(),
                     clinic=Clinic.load(clinic_id))
    except Exception as exc:
        log.error("judging failed (%s)", type(exc).__name__)
        return JudgeResult(conversation_id=str(stored.get("conversation_id", "unknown")),
                           prompt_version=prompt_version, model=getattr(backend, "model", "unknown"),
                           outcome="needs_review", reasons=["internal error"], attempts=0, latency_s=0.0)

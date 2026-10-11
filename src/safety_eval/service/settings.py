"""Service settings, read from the environment once at startup. Bad values fail loudly here."""

import os
from dataclasses import dataclass


class StartupError(RuntimeError):
    """The service cannot start safely (missing key, missing de-identifier, bad setting)."""


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./service.db"
    prompt_version: str = "C"
    model: str = "claude-sonnet-5-5"
    evaluate_per_minute: int = 60          # per API key
    batches_per_minute: int = 6            # per API key
    max_evaluate_bytes: int = 2 * 1024 * 1024
    max_batch_bytes: int = 20 * 1024 * 1024
    max_other_bytes: int = 1024 * 1024
    max_batch_items: int = 1000
    worker_batch_size: int = 10
    worker_poll_seconds: float = 5.0
    session_secret: str = ""               # signs reviewer-UI sessions; empty = the UI is not served
    cookie_secure: bool = True             # set UI_COOKIE_SECURE=0 only for local http

    @classmethod
    def from_env(cls, env=os.environ):
        d = cls()
        try:
            return cls(
                database_url=env.get("DATABASE_URL", d.database_url),
                prompt_version=env.get("PROMPT_VERSION", d.prompt_version),
                model=env.get("MODEL", d.model),
                evaluate_per_minute=int(env.get("RATE_LIMIT_EVALUATE_PER_MIN", d.evaluate_per_minute)),
                batches_per_minute=int(env.get("RATE_LIMIT_BATCHES_PER_MIN", d.batches_per_minute)),
                worker_poll_seconds=float(env.get("WORKER_POLL_SECONDS", d.worker_poll_seconds)),
                session_secret=env.get("SESSION_SECRET", d.session_secret),
                cookie_secure=env.get("UI_COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no"),
            )
        except ValueError as exc:
            raise StartupError(f"bad numeric setting in the environment: {exc}") from exc


MIN_SESSION_SECRET = 32


def require_session_secret(settings):
    """The reviewer UI signs its sessions with this; production must not start without a real one."""
    if len(settings.session_secret) < MIN_SESSION_SECRET:
        raise StartupError(f"cannot start: SESSION_SECRET must be set to at least {MIN_SESSION_SECRET} "
                           "random characters (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)")


def production_deidentifier():
    """The real de-identifier (Phase 2B). The service must never run without it, so a failed
    import stops startup rather than falling back to storing raw text."""
    try:
        from safety_eval.deid import deidentify_conversation
    except ImportError as exc:
        raise StartupError("cannot start: safety_eval.deid.deidentify_conversation is not "
                           "available, and the service must never run without de-identification") from exc
    return deidentify_conversation


def production_backend(settings, env=os.environ):
    if not env.get("ANTHROPIC_API_KEY"):
        raise StartupError("cannot start: ANTHROPIC_API_KEY is not set")
    from safety_eval.llm import ApiBackend
    return ApiBackend(model=settings.model)

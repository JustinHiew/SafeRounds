"""Service settings, read from the environment once at startup. Bad values fail loudly here."""

import os
from dataclasses import dataclass


class StartupError(RuntimeError):
    """The service cannot start safely (missing key, missing de-identifier, bad setting)."""


def _flag(value, default):
    if value is None or value == "":
        return default
    if value.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if value.strip().lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"expected true or false, got {value!r}")


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
    effort: str | None = None              # output_config.effort, sent only when set
    fallbacks: bool = True                 # server-side refusal fallback on the synchronous path
    use_batch_api: bool = False            # worker submits service batches to the Message Batches API
    session_secret: str = ""               # signs reviewer-UI sessions; empty = the UI is not served
    cookie_secure: bool = True             # set UI_COOKIE_SECURE=0 only for local http
    spend_limit_usd: float | None = None   # Claude API spend limit (budget.py); required in production

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
                effort=env.get("EFFORT") or None,
                fallbacks=_flag(env.get("FALLBACKS"), d.fallbacks),
                use_batch_api=_flag(env.get("USE_BATCH_API"), d.use_batch_api),
                session_secret=env.get("SESSION_SECRET", d.session_secret),
                cookie_secure=env.get("UI_COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no"),
                spend_limit_usd=float(env["SPEND_LIMIT_USD"]) if env.get("SPEND_LIMIT_USD") else None,
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


def require_spend_limit(settings):
    """Production never calls the paid API without a limit, and never with a model it cannot price."""
    from .budget import check_model
    if settings.spend_limit_usd is None or settings.spend_limit_usd <= 0:
        raise StartupError("cannot start: set SPEND_LIMIT_USD (US dollars) so API spend is capped")
    try:
        check_model(settings.model)
    except ValueError as exc:
        raise StartupError(f"cannot start: {exc}") from exc


# JUDGE_ANTHROPIC_API_KEY is checked first: in a Claude Code cloud session, ANTHROPIC_API_KEY is
# reserved for Claude Code's own sign-in and is not passed to commands, so the service's key
# needs a name of its own there. Anywhere else, ANTHROPIC_API_KEY works as usual.
API_KEY_VARS = ("JUDGE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def api_key(env=os.environ):
    return next((env[v] for v in API_KEY_VARS if env.get(v)), None)


def production_backend(settings, env=os.environ):
    key = api_key(env)
    if not key:
        raise StartupError(f"cannot start: set {' or '.join(API_KEY_VARS)}")
    require_spend_limit(settings)
    import anthropic
    from safety_eval.llm import ApiBackend
    return ApiBackend(model=settings.model, effort=settings.effort, fallbacks=settings.fallbacks,
                      client=anthropic.Anthropic(api_key=key))

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://dev_ifx@localhost:5432/galaxy_2"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── FreeSWITCH ESL ────────────────────────────────────────────────────────
    freeswitch_esl_host: str = "127.0.0.1"
    freeswitch_esl_port: int = 8021
    freeswitch_esl_password: str = "ClueCon"

    # ── Internal service auth ────────────────────────────────────────────────
    # In production: injected from AWS Secrets Manager at container start
    internal_jwt_secret: str = "changeme-in-production"
    internal_jwt_ttl_seconds: int = 900  # 15 min

    # ── App ───────────────────────────────────────────────────────────────────
    app_version: str = "1.0.0"
    debug: bool = False

    # ── Billing constants (match Sentinel Configs/config.php) ────────────────
    # call_bench_mark_time: minimum available seconds to allow a call
    credit_min_seconds: int = 30
    # credit_block_time: maximum pre-deduction block size in seconds
    credit_block_max_seconds: int = 300
    # creditslow threshold: announce "low credit" below this many seconds
    credit_warn_threshold_seconds: int = 320
    # call_minimum_bench_mark_time: below this → SHORT CALL → full refund
    short_call_threshold_seconds: int = 30

    class Config:
        env_file = ".env"  # local dev only; production uses env vars from ECS task def


@lru_cache
def get_settings() -> Settings:
    return Settings()

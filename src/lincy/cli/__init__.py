def main(user: str, resume: str | None = None) -> None:
    """Lazy proxy to avoid circular imports (session -> cli -> session)."""
    from .app import main as _main
    _main(user, resume)


__all__ = ["main"]

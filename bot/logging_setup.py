import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    # Third-party libs are chatty at DEBUG; keep them at WARNING regardless.
    for name in ("urllib3", "httpx", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

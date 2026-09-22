"""Ask OptiBot a question via the Responses API + file_search (spec 07).

Backup for the Playground assistant: same verbatim prompt, same vector store.
Run as a module so `bot` is importable: `python -m scripts.ask "How do I add a YouTube video?"`.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from bot.config import ConfigError, load_config
from bot.vector_store import build_client, resolve_vector_store

# Verbatim from the brief; do not edit.
SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""

DEFAULT_QUESTION = "How do I add a YouTube video?"
DEFAULT_MODEL = "gpt-5-mini"


def ask(client: Any, vector_store_id: str, question: str, model: str) -> Any:
    return client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
        input=question,
    )


def cited_filenames(response: Any) -> list[str]:
    """Unique `file_citation` filenames in the order they are first cited."""
    names: list[str] = []
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for part in item.content:
            for ann in getattr(part, "annotations", None) or []:
                if getattr(ann, "type", None) == "file_citation" and ann.filename not in names:
                    names.append(ann.filename)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask OptiBot a question against the synced vector store.")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL)
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    client = build_client(cfg.openai_api_key)
    store_id = resolve_vector_store(client, cfg.vector_store_id, cfg.vector_store_name)
    response = ask(client, store_id, args.question, args.model)
    print(response.output_text)
    names = cited_filenames(response)
    if names:
        print("\nCited files:")
        for name in names:
            print(f"- {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

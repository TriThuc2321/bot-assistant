"""scripts/ask.py against a fake client — no network."""

from __future__ import annotations

from types import SimpleNamespace

from scripts import ask as ask_mod

BRIEF_PROMPT = (
    "You are OptiBot, the customer-support bot for OptiSigns.com.\n"
    "• Tone: helpful, factual, concise.\n"
    "• Only answer using the uploaded docs.\n"
    "• Max 5 bullet points; else link to the doc.\n"
    '• Cite up to 3 "Article URL:" lines per reply.'
)


def citation(filename):
    return SimpleNamespace(type="file_citation", filename=filename, file_id="f", index=0)


def fake_response():
    return SimpleNamespace(
        output_text="- Step one\nArticle URL: https://support.test/a",
        output=[
            SimpleNamespace(type="file_search_call"),
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        annotations=[citation("1-a.md"), citation("2-b.md"), citation("1-a.md")],
                    ),
                    SimpleNamespace(type="output_text", annotations=[SimpleNamespace(type="url_citation")]),
                ],
            ),
        ],
    )


class FakeClient:
    def __init__(self):
        self.calls = []
        self.responses = SimpleNamespace(create=self._create)
        self.vector_stores = SimpleNamespace(
            retrieve=lambda store_id: SimpleNamespace(id=store_id, name="kb")
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return fake_response()


def test_prompt_is_byte_identical_to_brief():
    assert ask_mod.SYSTEM_PROMPT.encode() == BRIEF_PROMPT.encode()


def test_ask_uses_file_search_on_the_store():
    client = FakeClient()
    ask_mod.ask(client, "vs_1", "How do I add a YouTube video?", "some-model")
    assert client.calls == [{
        "model": "some-model",
        "instructions": BRIEF_PROMPT,
        "tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}],
        "input": "How do I add a YouTube video?",
    }]


def test_cited_filenames_are_unique_and_ordered():
    assert ask_mod.cited_filenames(fake_response()) == ["1-a.md", "2-b.md"]


def test_main_prints_answer_and_citations(monkeypatch, capsys):
    client = FakeClient()
    monkeypatch.setenv("API_KEY", "sk-test")
    monkeypatch.setenv("VECTOR_STORE_ID", "vs_9")
    monkeypatch.setattr(ask_mod, "build_client", lambda key: client)
    assert ask_mod.main(["--model", "m", "q?"]) == 0
    out = capsys.readouterr().out
    assert "Article URL: https://support.test/a" in out
    assert "- 1-a.md\n- 2-b.md" in out
    assert client.calls[0]["tools"][0]["vector_store_ids"] == ["vs_9"]


def test_main_without_key_exits_1(monkeypatch):
    for name in ("OPENAI_API_KEY", "API_KEY"):
        monkeypatch.setenv(name, "")
    assert ask_mod.main(["q?"]) == 1

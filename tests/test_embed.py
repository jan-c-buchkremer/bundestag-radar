import numpy as np

from landscape import corpus, embed


def fake_loader(_model):
    def encode(texts):
        return np.array([[len(t), 1.0] for t in texts], dtype=np.float32)

    return encode, lambda text: len(text.split())


def test_chunk_text_respects_paragraphs():
    _, count = fake_loader(None)
    assert embed.chunk_text("a b c\n\nd e\n\nf g h i", count, max_tokens=5) == ["a b c\n\nd e", "f g h i"]
    assert embed.chunk_text("x " * 20, count, max_tokens=5) == ["x " * 20]  # oversized paragraph stays whole


def test_embed_caches_by_text_hash(conn, tmp_path):
    speeches = corpus.load_week(conn, "2026-W28")
    store = embed.open_store(tmp_path / "e.sqlite")
    calls = []

    def loader(m):
        calls.append(m)
        return fake_loader(m)

    v1 = embed.embed_speeches(speeches, store, model="fake", loader=loader)
    assert v1.shape == (4, 2) and np.allclose(np.linalg.norm(v1, axis=1), 1)
    v2 = embed.embed_speeches(speeches, store, model="fake", loader=loader)
    assert len(calls) == 1 and np.allclose(v1, v2)  # second run served from cache
    speeches[0].text += " geändert"
    embed.embed_speeches(speeches, store, model="fake", loader=loader)
    assert len(calls) == 2
    # ID0 and ID2 share a text, so one row, plus ID1 and the Zwischenfrage ID1-2; the old and the changed ID0 text
    # both stay valid
    assert store.execute("SELECT COUNT(*) FROM embedding").fetchone()[0] == 4

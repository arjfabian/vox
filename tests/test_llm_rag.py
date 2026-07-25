import uuid

import aiosqlite
import pytest

from pathlib import Path

from vox.capabilities.ai.llm.rag import RAGRetriever


@pytest.fixture
def db_path():
    path = Path(f"/tmp/test_rag_{uuid.uuid4().hex[:8]}.db")
    if path.exists():
        path.unlink()
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture
async def populated_db(db_path):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id TEXT PRIMARY KEY,
                details TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS asset_index (
                name TEXT,
                tags TEXT
            )
        """)
        await conn.execute(
            'INSERT INTO activity_log (id, details) VALUES (?, ?)',
            ('1', '{"msg": "hello world"}'),
        )
        await conn.execute(
            "INSERT INTO asset_index (name, tags) VALUES ('report.pdf', '[\"important\"]')",
        )
        await conn.commit()
    return db_path


@pytest.fixture
def retriever():
    return RAGRetriever(max_snippets=5)


@pytest.mark.asyncio
async def test_retrieve_from_empty_db():
    empty = Path(f"/tmp/empty_rag_{uuid.uuid4().hex[:8]}.db")
    try:
        r = RAGRetriever(max_snippets=5)
        result = await r.retrieve(empty, "test")
        assert result == []
    finally:
        if empty.exists():
            empty.unlink()


@pytest.mark.asyncio
async def test_retrieve_with_query(populated_db, retriever):
    result = await retriever.retrieve(populated_db, "hello")
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_retrieve_no_match_returns_empty_list(populated_db, retriever):
    result = await retriever.retrieve(populated_db, "zzzzzzz")
    assert result == []


@pytest.mark.asyncio
async def test_close_is_noop(retriever):
    await retriever.close()


def test_tokenize_short_words_excluded():
    r = RAGRetriever(max_snippets=5)
    tokens = r._tokenize("a be cat dog")
    assert "a" not in tokens
    assert "be" not in tokens
    assert "cat" in tokens
    assert "dog" in tokens


def test_tokenize_punctuation_stripped():
    r = RAGRetriever(max_snippets=5)
    tokens = r._tokenize("hello, world!")
    assert "hello" in tokens
    assert "world" in tokens

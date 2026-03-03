"""Tests for filesystem tools."""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.mark.asyncio
async def test_read_file(tmp_dir):
    from tools.filesystem import _read
    f = tmp_dir / "hello.txt"
    f.write_text("line1\nline2\nline3")
    result = await _read(str(f))
    assert "line1" in result
    assert "1|line1" in result


@pytest.mark.asyncio
async def test_read_with_offset(tmp_dir):
    from tools.filesystem import _read
    f = tmp_dir / "test.txt"
    f.write_text("a\nb\nc\nd")
    result = await _read(str(f), offset=2, limit=2)
    assert "b" in result
    assert "c" in result
    assert "a" not in result


@pytest.mark.asyncio
async def test_write_creates_file(tmp_dir):
    from tools.filesystem import _write
    path = str(tmp_dir / "new.txt")
    result = await _write(path, "hello world")
    assert "Written" in result
    assert Path(path).read_text() == "hello world"


@pytest.mark.asyncio
async def test_write_creates_parent_dirs(tmp_dir):
    from tools.filesystem import _write
    path = str(tmp_dir / "deep" / "nested" / "file.txt")
    result = await _write(path, "content")
    assert Path(path).exists()
    assert "Written" in result


@pytest.mark.asyncio
async def test_edit_success(tmp_dir):
    from tools.filesystem import _edit
    f = tmp_dir / "edit_me.txt"
    f.write_text("Hello World")
    result = await _edit(str(f), "World", "Python")
    assert "success" in result.lower()
    assert f.read_text() == "Hello Python"


@pytest.mark.asyncio
async def test_edit_not_found(tmp_dir):
    from tools.filesystem import _edit
    f = tmp_dir / "edit.txt"
    f.write_text("Hello World")
    result = await _edit(str(f), "Nonexistent", "X")
    assert "Error" in result


@pytest.mark.asyncio
async def test_edit_multiple_occurrences(tmp_dir):
    from tools.filesystem import _edit
    f = tmp_dir / "multi.txt"
    f.write_text("x x x")
    result = await _edit(str(f), "x", "y")
    assert "Error" in result
    assert "3 times" in result


@pytest.mark.asyncio
async def test_list_dir(tmp_dir):
    from tools.filesystem import _list_dir
    (tmp_dir / "file1.txt").write_text("a")
    (tmp_dir / "file2.txt").write_text("b")
    (tmp_dir / "subdir").mkdir()
    result = await _list_dir(str(tmp_dir))
    assert "file1.txt" in result
    assert "file2.txt" in result
    assert "[d] subdir" in result


@pytest.mark.asyncio
async def test_list_dir_not_exist(tmp_dir):
    from tools.filesystem import _list_dir
    result = await _list_dir(str(tmp_dir / "nonexistent"))
    assert "Error" in result


@pytest.mark.asyncio
async def test_glob(tmp_dir):
    from tools.filesystem import _glob
    (tmp_dir / "a.py").write_text("")
    (tmp_dir / "b.py").write_text("")
    (tmp_dir / "c.txt").write_text("")
    result = await _glob("*.py", cwd=str(tmp_dir))
    assert "a.py" in result
    assert "b.py" in result
    assert "c.txt" not in result


@pytest.mark.asyncio
async def test_delete_file(tmp_dir):
    from tools.filesystem import _delete
    f = tmp_dir / "del.txt"
    f.write_text("x")
    result = await _delete(str(f))
    assert "Deleted file" in result
    assert not f.exists()


@pytest.mark.asyncio
async def test_delete_nonexistent(tmp_dir):
    from tools.filesystem import _delete
    result = await _delete(str(tmp_dir / "ghost.txt"))
    assert "Error" in result


@pytest.mark.asyncio
async def test_move_file(tmp_dir):
    from tools.filesystem import _move
    src = tmp_dir / "src.txt"
    src.write_text("content")
    dst = tmp_dir / "dst.txt"
    result = await _move(str(src), str(dst))
    assert "→" in result
    assert dst.read_text() == "content"
    assert not src.exists()

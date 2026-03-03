"""Tests for the CronManager."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_manager(db_path: str):
    env = {
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_OWNER_ID": "1",
        "ANTHROPIC_API_KEY": "sk",
        "CRON_ENABLED": "true",
        "CRON_DB_PATH": db_path,
    }
    with patch.dict(os.environ, env, clear=True):
        import config as cfg_mod
        cfg_mod._config = None
        from scheduler.manager import CronManager
        mgr = CronManager()
        return mgr


@pytest.mark.asyncio
async def test_add_and_list_job(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    result = await mgr.add_job(schedule="0 9 * * *", message="Good morning", description="Morning greeting")
    assert "Job scheduled" in result
    assert "0 9 * * *" in result

    listing = mgr.list_jobs()
    assert "Morning greeting" in listing
    assert "Good morning" in listing


@pytest.mark.asyncio
async def test_add_invalid_schedule(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    result = await mgr.add_job(schedule="not a schedule", message="Test")
    assert "Invalid" in result or "Error" in result


@pytest.mark.asyncio
async def test_remove_job(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    result = await mgr.add_job(schedule="0 9 * * *", message="Test")
    # Extract job id
    job_id = [line.split("`")[1] for line in result.split("\n") if "ID:" in line][0]

    remove_result = mgr.remove_job(job_id)
    assert "removed" in remove_result

    listing = mgr.list_jobs()
    assert job_id not in listing


@pytest.mark.asyncio
async def test_update_job(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    add_result = await mgr.add_job(schedule="0 9 * * *", message="Original message")
    job_id = [line.split("`")[1] for line in add_result.split("\n") if "ID:" in line][0]

    update_result = await mgr.update_job(job_id, {"message": "Updated message"})
    assert "updated" in update_result.lower()

    listing = mgr.list_jobs()
    assert "Updated message" in listing


@pytest.mark.asyncio
async def test_enable_disable_job(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    add_result = await mgr.add_job(schedule="0 9 * * *", message="Toggle test")
    job_id = [line.split("`")[1] for line in add_result.split("\n") if "ID:" in line][0]

    disable_result = await mgr.set_job_enabled(job_id, enabled=False)
    assert "disabled" in disable_result.lower()

    enable_result = await mgr.set_job_enabled(job_id, enabled=True)
    assert "enabled" in enable_result.lower()


@pytest.mark.asyncio
async def test_interval_shorthand(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    result = await mgr.add_job(schedule="30m", message="Every 30 minutes")
    assert "Job scheduled" in result


@pytest.mark.asyncio
async def test_status(tmp_path):
    mgr = _make_manager(str(tmp_path / "cron.db"))
    status = mgr.status()
    assert "Scheduler" in status

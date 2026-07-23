from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import py7zr

from vlog_journal.pipeline.registry import PipelineContext
from vlog_journal.vault.backup import (
    create_encrypted_archive,
    determine_prune_candidates,
    upload_and_prune_remote,
)


def test_determine_prune_candidates():
    remote_files = [
        {"Path": "vlog_backup_2026-07-22_daily.7z"},
        {"Path": "vlog_backup_2026-07-21_daily.7z"},
        {"Path": "vlog_backup_2026-07-20_daily.7z"},
        {"Path": "vlog_backup_2026-07-19_weekly.7z"},
        {"Path": "vlog_backup_2026-07-12_weekly.7z"},
        {"Path": "unrelated_file.txt"},
    ]

    # Retain 2 daily, 1 weekly
    prune = determine_prune_candidates(remote_files, retention_daily_days=2, retention_weekly_weeks=1)

    assert "vlog_backup_2026-07-20_daily.7z" in prune
    assert "vlog_backup_2026-07-12_weekly.7z" in prune
    assert "vlog_backup_2026-07-22_daily.7z" not in prune
    assert "vlog_backup_2026-07-21_daily.7z" not in prune
    assert "vlog_backup_2026-07-19_weekly.7z" not in prune


@pytest.mark.asyncio
async def test_create_encrypted_archive(tmp_path: Path):
    vault_dir = tmp_path / "PersonalVault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("Hello vault secret content", encoding="utf-8")

    mock_config = MagicMock()
    mock_config.app.vault_path = str(vault_dir)
    mock_config.backup_encryption_passphrase.get_secret_value.return_value = "SecretPass123"

    ctx = PipelineContext(chat_id=123, config=mock_config)

    res_ctx = await create_encrypted_archive(ctx)
    archive_path = Path(res_ctx.payload["archive_path"])

    assert archive_path.exists()
    assert archive_path.name.startswith("vlog_backup_")

    # Verify py7zr password protection
    assert py7zr.is_7zfile(archive_path)

    # Wrong password raises PasswordRequired or Bad7zFile upon extraction / testzip
    with pytest.raises(Exception):
        with py7zr.SevenZipFile(archive_path, "r", password="WrongPassword") as sz_wrong:
            sz_wrong.extractall(tmp_path / "wrong_extract")

    # Correct password succeeds
    with py7zr.SevenZipFile(archive_path, "r", password="SecretPass123") as sz:
        extracted_names = sz.getnames()
        assert any("note.md" in name for name in extracted_names)

    # Clean up temp archive
    archive_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_and_prune_remote(tmp_path: Path):
    archive_file = tmp_path / "vlog_backup_2026-07-22_daily.7z"
    archive_file.write_bytes(b"dummy archive data")

    mock_config = MagicMock()
    mock_config.backup.remote_name = "gdrive"
    mock_config.backup.remote_folder = "vlog-backups"
    mock_config.backup.retention_daily_days = 2
    mock_config.backup.retention_weekly_weeks = 1

    ctx = PipelineContext(chat_id=123, config=mock_config)
    ctx.payload = {"archive_path": str(archive_file)}

    mock_proc_copy = AsyncMock()
    mock_proc_copy.communicate.return_value = (b"", b"")
    mock_proc_copy.returncode = 0

    mock_proc_ls = AsyncMock()
    ls_json = [
        {"Path": "vlog_backup_2026-07-22_daily.7z"},
        {"Path": "vlog_backup_2026-07-21_daily.7z"},
        {"Path": "vlog_backup_2026-07-20_daily.7z"},
    ]
    import json
    mock_proc_ls.communicate.return_value = (json.dumps(ls_json).encode("utf-8"), b"")
    mock_proc_ls.returncode = 0

    mock_proc_del = AsyncMock()
    mock_proc_del.wait.return_value = 0

    def mock_subprocess(*args, **kwargs):
        cmd = args
        if "copy" in cmd:
            return mock_proc_copy
        elif "lsjson" in cmd:
            return mock_proc_ls
        elif "deletefile" in cmd:
            return mock_proc_del
        return mock_proc_copy

    with patch("shutil.which", return_value="/usr/bin/rclone"), \
         patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess):
        res_ctx = await upload_and_prune_remote(ctx)

    assert res_ctx.payload.get("backup_complete") is True
    # Local archive file deleted
    assert not archive_file.exists()

import asyncio
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import py7zr
import structlog

from vlog_journal.pipeline.registry import PipelineContext, register_step

logger = structlog.get_logger(__name__)

BACKUP_FILENAME_PATTERN = r"^vlog_backup_(\d{4}-\d{2}-\d{2})_(daily|weekly)\.7z$"

def determine_prune_candidates(
    remote_files: list[dict[str, Any]],
    retention_daily_days: int = 2,
    retention_weekly_weeks: int = 1,
) -> list[str]:
    """Parse remote backup file list and return filenames that exceed retention policy limits.
    
    Files are grouped into 'daily' and 'weekly'. Files matching retention limits are kept,
    older ones are returned for deletion.
    """
    daily_files: list[tuple[datetime, str]] = []
    weekly_files: list[tuple[datetime, str]] = []

    for f in remote_files:
        name = f.get("Path") or f.get("Name") or ""
        match = re.match(BACKUP_FILENAME_PATTERN, name)
        if match:
            date_str, tag = match.group(1), match.group(2)
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                if tag == "daily":
                    daily_files.append((dt, name))
                elif tag == "weekly":
                    weekly_files.append((dt, name))
            except ValueError:
                pass

    # Sort descending by date (newest first)
    daily_files.sort(key=lambda x: x[0], reverse=True)
    weekly_files.sort(key=lambda x: x[0], reverse=True)

    # Retain newest N daily and M weekly
    prune_targets: list[str] = []

    if len(daily_files) > retention_daily_days:
        for _, name in daily_files[retention_daily_days:]:
            prune_targets.append(name)

    if len(weekly_files) > retention_weekly_weeks:
        for _, name in weekly_files[retention_weekly_weeks:]:
            prune_targets.append(name)

    return prune_targets

@register_step("vault.create_encrypted_archive")
async def create_encrypted_archive(ctx: PipelineContext) -> PipelineContext:
    """Compress and AES-256 encrypt vault_path into a .7z archive using py7zr."""
    config = ctx.config
    if not config:
        raise ValueError("PipelineContext config missing for backup execution")

    vault_path = Path(config.app.vault_path)
    if not vault_path.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault_path}")

    # Passphrase from SecretStr or env var
    passphrase = config.backup_encryption_passphrase.get_secret_value() if config.backup_encryption_passphrase else os.getenv("BACKUP_ENCRYPTION_PASSPHRASE", "default_secret")

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    tag = "weekly" if now.weekday() == 6 else "daily"
    archive_name = f"vlog_backup_{date_str}_{tag}.7z"

    temp_backup_dir = Path("data/temp/backups")
    temp_backup_dir.mkdir(parents=True, exist_ok=True)
    archive_path = temp_backup_dir / archive_name

    logger.info("Creating encrypted 7z archive", vault_path=str(vault_path), archive_path=str(archive_path), tag=tag)

    def _compress_in_thread() -> None:
        with py7zr.SevenZipFile(archive_path, "w", password=passphrase) as archive:
            archive.writeall(vault_path, arcname=vault_path.name)

    # Run py7zr in executor to avoid blocking async event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _compress_in_thread)

    file_size_mb = round(os.path.getsize(archive_path) / (1024 * 1024), 2)
    logger.info("Encrypted archive created successfully", archive_name=archive_name, size_mb=file_size_mb)

    ctx.payload["archive_path"] = str(archive_path)
    ctx.payload["archive_name"] = archive_name
    ctx.payload["backup_tag"] = tag
    return ctx

@register_step("vault.upload_and_prune_remote")
async def upload_and_prune_remote(ctx: PipelineContext) -> PipelineContext:
    """Upload encrypted backup to remote cloud storage via rclone and enforce retention pruning."""
    config = ctx.config
    archive_path = ctx.payload.get("archive_path")

    if not archive_path or not Path(archive_path).exists():
        raise FileNotFoundError(f"Backup archive file not found: {archive_path}")

    rclone_bin = shutil.which("rclone")
    if not rclone_bin:
        raise RuntimeError("rclone executable not found on PATH")

    b_cfg = getattr(config, "backup", None)
    remote_name = getattr(b_cfg, "remote_name", "gdrive") if b_cfg else "gdrive"
    remote_folder = getattr(b_cfg, "remote_folder", "vlog-journal-backups") if b_cfg else "vlog-journal-backups"
    ret_daily = getattr(b_cfg, "retention_daily_days", 2) if b_cfg else 2
    ret_weekly = getattr(b_cfg, "retention_weekly_weeks", 1) if b_cfg else 1

    remote_dest = f"{remote_name}:{remote_folder}"

    # 1. Upload file via rclone copy
    logger.info("Uploading archive to remote via rclone", archive=archive_path, destination=remote_dest)
    copy_cmd = [rclone_bin, "copy", archive_path, remote_dest]
    proc = await asyncio.create_subprocess_exec(
        *copy_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace")
        logger.error("rclone copy failed", error=err_msg)
        raise RuntimeError(f"rclone copy failed with code {proc.returncode}: {err_msg}")

    logger.info("rclone copy completed successfully")

    # 2. List remote files to enforce retention policy
    ls_cmd = [rclone_bin, "lsjson", remote_dest]
    proc_ls = await asyncio.create_subprocess_exec(
        *ls_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout_ls, stderr_ls = await proc_ls.communicate()

    if proc_ls.returncode == 0:
        try:
            remote_files = json.loads(stdout_ls.decode("utf-8", errors="replace"))
            prune_targets = determine_prune_candidates(
                remote_files,
                retention_daily_days=ret_daily,
                retention_weekly_weeks=ret_weekly,
            )
            logger.info("Retention analysis complete", total_remote_files=len(remote_files), prune_count=len(prune_targets))

            for target in prune_targets:
                target_dest = f"{remote_dest}/{target}"
                logger.info("Pruning expired remote backup", target=target_dest)
                del_cmd = [rclone_bin, "deletefile", target_dest]
                proc_del = await asyncio.create_subprocess_exec(
                    *del_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc_del.wait()
        except Exception as e:
            logger.warning("Failed to parse remote files for retention pruning", error=str(e))
    else:
        logger.warning("rclone lsjson failed, skipping retention pruning", error=stderr_ls.decode("utf-8", errors="replace"))

    # 3. Cleanup local temp archive file
    try:
        Path(archive_path).unlink(missing_ok=True)
        logger.info("Deleted local temporary backup archive", archive_path=archive_path)
    except Exception as e:
        logger.warning("Failed to delete local backup archive", archive_path=archive_path, error=str(e))

    ctx.payload["backup_complete"] = True
    return ctx

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import duckdb
import httpx

from app.core.config import settings
from app.models.schemas import DownloadFormat, DownloadStatus, DownloadTask
from app.services.socrata import SocrataClient, parse_socrata_reference, sanitize_filename


def resolve_output_dir(output_dir: str | None, default_dir: Path) -> Path:
    """Validate and ensure the target download directory exists."""
    if not output_dir or not output_dir.strip():
        default_dir.mkdir(parents=True, exist_ok=True)
        return default_dir

    target = Path(output_dir.strip()).expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise ValueError(f"La ruta '{target}' es un archivo, no una carpeta.")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"No se pudo crear o acceder a la carpeta '{target}': {exc}") from exc
    return target


def convert_csv_to_parquet(csv_path: Path, parquet_path: Path) -> int:
    """Convert a CSV file to ZSTD-compressed Parquet using DuckDB and return row count."""
    tmp_parquet = parquet_path.with_suffix(".parquet.part")
    csv_str = str(csv_path)
    tmp_str = str(tmp_parquet)

    conn = duckdb.connect(":memory:")
    try:
        try:
            conn.execute(
                "COPY (SELECT * FROM read_csv(?, auto_detect=true)) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [csv_str, tmp_str],
            )
        except duckdb.Error:
            conn.execute(
                "COPY (SELECT * FROM read_csv(?, auto_detect=true, all_varchar=true)) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [csv_str, tmp_str],
            )

        row_count = conn.execute("SELECT COUNT(*) FROM read_parquet(?)", [tmp_str]).fetchone()[0]
        tmp_parquet.replace(parquet_path)
        return int(row_count)
    except Exception:
        tmp_parquet.unlink(missing_ok=True)
        raise
    finally:
        conn.close()


class DownloadManager:
    """Manages concurrent streaming downloads with live SSE event broadcasting."""

    def __init__(self, downloads_dir: Path | None = None, max_concurrent: int | None = None) -> None:
        self._downloads_dir = downloads_dir or settings.downloads_dir
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(max_concurrent or settings.max_concurrent_downloads)
        self._tasks: dict[str, DownloadTask] = {}
        self._bg_jobs: dict[str, asyncio.Task] = {}
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._socrata = SocrataClient()

    def list_tasks(self) -> list[DownloadTask]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def _notify(self, task: DownloadTask) -> None:
        payload = json.dumps(task.model_dump(mode="json"))
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

    async def subscribe(self) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            yield f"data: {json.dumps([t.model_dump(mode='json') for t in self.list_tasks()])}\n\n"
            while True:
                data = await queue.get()
                yield f"event: task_update\ndata: {data}\n\n"
        finally:
            self._subscribers.discard(queue)

    def enqueue_batch(
        self,
        items: list[str],
        fmt: DownloadFormat,
        default_domain: str | None = None,
        auto_parquet_mb: int | None = None,
        output_dir: str | None = None,
    ) -> list[DownloadTask]:
        target_dir = resolve_output_dir(output_dir, self._downloads_dir)
        created: list[DownloadTask] = []
        now = datetime.now(timezone.utc)

        for raw_item in items:
            raw_clean = raw_item.strip()
            if not raw_clean:
                continue

            task_id = uuid.uuid4().hex[:12]
            try:
                domain, dataset_id = parse_socrata_reference(raw_clean, default_domain)
                task = DownloadTask(
                    task_id=task_id,
                    dataset_id=dataset_id,
                    domain=domain,
                    dataset_name=dataset_id,
                    format=fmt,
                    status=DownloadStatus.QUEUED,
                    created_at=now,
                    updated_at=now,
                )
                self._tasks[task_id] = task
                self._notify(task)
                job = asyncio.create_task(self._run_download(task_id, auto_parquet_mb, target_dir))
                self._bg_jobs[task_id] = job
            except ValueError as exc:
                task = DownloadTask(
                    task_id=task_id,
                    dataset_id=raw_clean[:32],
                    domain=default_domain or settings.default_domain,
                    dataset_name=raw_clean[:64],
                    format=fmt,
                    status=DownloadStatus.FAILED,
                    error=str(exc),
                    created_at=now,
                    updated_at=now,
                )
                self._tasks[task_id] = task
                self._notify(task)

            created.append(task)

        return created

    async def cancel_task(self, task_id: str) -> DownloadTask | None:
        task = self._tasks.get(task_id)
        if not task:
            return None

        job = self._bg_jobs.get(task_id)
        if job and not job.done():
            job.cancel()
        if task.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING):
            task.status = DownloadStatus.CANCELLED
            task.updated_at = datetime.now(timezone.utc)
            self._notify(task)
        return task

    def remove_task(self, task_id: str) -> bool:
        job = self._bg_jobs.pop(task_id, None)
        if job and not job.done():
            job.cancel()
        return self._tasks.pop(task_id, None) is not None

    async def _run_download(
        self,
        task_id: str,
        auto_parquet_mb: int | None = None,
        target_dir: Path | None = None,
    ) -> None:
        task = self._tasks[task_id]
        dest_dir = target_dir or self._downloads_dir
        part_path: Path | None = None

        try:
            async with self._semaphore:
                if task.status == DownloadStatus.CANCELLED:
                    return

                task.status = DownloadStatus.DOWNLOADING
                task.updated_at = datetime.now(timezone.utc)
                self._notify(task)

                try:
                    meta = await self._socrata.get_metadata(
                        task.domain, task.dataset_id, include_preview=False
                    )
                    task.dataset_name = meta.name
                except httpx.HTTPError:
                    task.dataset_name = task.dataset_id

                download_fmt = (
                    DownloadFormat.CSV if task.format == DownloadFormat.PARQUET else task.format
                )
                csv_filename = sanitize_filename(task.dataset_name, task.dataset_id, download_fmt)
                final_path = dest_dir / csv_filename
                part_path = dest_dir / f"{csv_filename}.part"
                urls = [
                    self._socrata.build_download_url(task.domain, task.dataset_id, task.format),
                    self._socrata.build_legacy_download_url(task.domain, task.dataset_id, task.format),
                ]

                async with httpx.AsyncClient(
                    timeout=settings.request_timeout_seconds, follow_redirects=True
                ) as client:
                    downloaded = await self._stream_direct_urls(client, urls, part_path, task)
                    if not downloaded:
                        await self._download_paginated_resource(client, part_path, task)

                should_convert_parquet = task.format == DownloadFormat.PARQUET or (
                    task.format == DownloadFormat.CSV
                    and auto_parquet_mb is not None
                    and task.bytes_downloaded >= auto_parquet_mb * 1024 * 1024
                )

                if should_convert_parquet:
                    parquet_filename = sanitize_filename(
                        task.dataset_name, task.dataset_id, DownloadFormat.PARQUET
                    )
                    parquet_path = dest_dir / parquet_filename
                    row_count = await asyncio.to_thread(
                        convert_csv_to_parquet, part_path, parquet_path
                    )
                    part_path.unlink(missing_ok=True)
                    task.format = DownloadFormat.PARQUET
                    task.row_count = row_count
                    task.file_path = str(parquet_path)
                else:
                    part_path.replace(final_path)
                    task.file_path = str(final_path)

                task.status = DownloadStatus.COMPLETED
                task.updated_at = datetime.now(timezone.utc)
                self._notify(task)

        except asyncio.CancelledError:
            if part_path and part_path.exists():
                part_path.unlink(missing_ok=True)
            task.status = DownloadStatus.CANCELLED
            task.updated_at = datetime.now(timezone.utc)
            self._notify(task)
        except Exception as exc:
            if part_path and part_path.exists():
                part_path.unlink(missing_ok=True)
            task.status = DownloadStatus.FAILED
            task.error = str(exc)
            task.updated_at = datetime.now(timezone.utc)
            self._notify(task)
        finally:
            self._bg_jobs.pop(task_id, None)

    async def _stream_direct_urls(
        self,
        client: httpx.AsyncClient,
        urls: list[str],
        part_path: Path,
        task: DownloadTask,
    ) -> bool:
        """Try direct streaming export URLs with short retries for transient Socrata errors."""
        retryable = {400, 404, 405, 410, 429, 500, 502, 503, 504}
        for url in urls:
            for attempt in range(2):
                task.bytes_downloaded = 0
                async with client.stream("GET", url) as resp:
                    if resp.status_code == 200:
                        content_length = resp.headers.get("Content-Length")
                        if content_length and content_length.isdigit():
                            task.total_bytes = int(content_length)

                        last_notify_bytes = 0
                        with part_path.open("wb") as fh:
                            async for chunk in resp.aiter_bytes(settings.chunk_size_bytes):
                                fh.write(chunk)
                                task.bytes_downloaded += len(chunk)
                                if task.bytes_downloaded - last_notify_bytes >= 262144:
                                    task.updated_at = datetime.now(timezone.utc)
                                    self._notify(task)
                                    last_notify_bytes = task.bytes_downloaded
                        return True

                    if resp.status_code in (404, 405, 410):
                        break
                    if resp.status_code in retryable and attempt == 0:
                        await asyncio.sleep(1.5)
                        continue
                    break
        return False

    async def _download_paginated_resource(
        self,
        client: httpx.AsyncClient,
        part_path: Path,
        task: DownloadTask,
    ) -> None:
        """Fallback to SODA2 /resource/{id}.csv paginated download when export endpoints fail."""
        resource_url = self._socrata.build_resource_url(task.domain, task.dataset_id, task.format)
        page_size = 50000
        offset = 0
        task.bytes_downloaded = 0
        task.total_bytes = None

        with part_path.open("wb") as fh:
            while True:
                params = {"$limit": page_size, "$offset": offset, "$order": ":id"}
                resp = await client.get(resource_url, params=params)
                resp.raise_for_status()
                content = resp.content
                if not content.strip():
                    break

                lines = content.splitlines(keepends=True)
                data_lines = lines if offset == 0 else lines[1:]
                if not data_lines:
                    break

                for line in data_lines:
                    fh.write(line)
                task.bytes_downloaded += sum(len(l) for l in data_lines)
                task.updated_at = datetime.now(timezone.utc)
                self._notify(task)

                if len(lines) - 1 < page_size:
                    break
                offset += page_size


download_manager = DownloadManager()

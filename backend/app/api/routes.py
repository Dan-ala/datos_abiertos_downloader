import asyncio
import sys
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
import httpx

from app.core.config import settings
from app.models.schemas import (
    BatchDownloadRequest,
    DatasetMetadata,
    DownloadTask,
    SearchResultItem,
)
from app.services.downloader import download_manager
from app.services.socrata import SocrataClient, parse_socrata_reference

router = APIRouter(prefix="/api", tags=["datos-abiertos"])
socrata_client = SocrataClient()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "downloads_dir": str(settings.downloads_dir)}


@router.get("/search", response_model=list[SearchResultItem])
async def search_datasets(
    q: str = Query(..., min_length=1, description="Texto de búsqueda"),
    limit: int = Query(20, ge=1, le=50),
    domain: str | None = Query(None),
) -> list[SearchResultItem]:
    try:
        return await socrata_client.search_catalog(query=q, domain=domain, limit=limit)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Error consultando catálogo Socrata: {exc}") from exc


@router.get("/datasets/inspect", response_model=DatasetMetadata)
async def inspect_dataset(
    ref: str = Query(..., min_length=4, description="URL de datos.gov.co o ID 4x4 (ej. hwrp-3su3)"),
    domain: str | None = Query(None),
) -> DatasetMetadata:
    try:
        resolved_domain, dataset_id = parse_socrata_reference(ref, domain)
        return await socrata_client.get_metadata(resolved_domain, dataset_id, include_preview=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"No se pudo obtener el dataset '{ref}': {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Error de red con Socrata: {exc}") from exc


@router.post("/system/pick-folder")
async def pick_folder(initial_dir: str | None = Query(None)) -> dict[str, str | None]:
    start_dir = initial_dir or str(settings.downloads_dir)
    script = (
        "import sys, tkinter as tk; from tkinter import filedialog; "
        "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
        "path = filedialog.askdirectory(initialdir=sys.argv[1], title='Seleccionar carpeta de descargas'); "
        "print(path or '', end='')"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        start_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    selected = stdout.decode("utf-8", errors="ignore").strip()
    return {"path": selected or None}


@router.post("/downloads/batch", response_model=list[DownloadTask])
async def create_batch_download(payload: BatchDownloadRequest) -> list[DownloadTask]:
    try:
        tasks = download_manager.enqueue_batch(
            items=payload.items,
            fmt=payload.format,
            default_domain=payload.domain,
            auto_parquet_mb=payload.auto_parquet_mb,
            output_dir=payload.output_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not tasks:
        raise HTTPException(status_code=422, detail="No se recibieron referencias válidas.")
    return tasks


@router.get("/downloads", response_model=list[DownloadTask])
async def list_downloads() -> list[DownloadTask]:
    return download_manager.list_tasks()


@router.get("/downloads/events")
async def stream_download_events() -> StreamingResponse:
    return StreamingResponse(download_manager.subscribe(), media_type="text/event-stream")


@router.post("/downloads/{task_id}/cancel", response_model=DownloadTask)
async def cancel_download(task_id: str) -> DownloadTask:
    task = await download_manager.cancel_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return task


@router.delete("/downloads/{task_id}")
async def delete_download(task_id: str) -> dict[str, bool]:
    removed = download_manager.remove_task(task_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return {"removed": True}


@router.get("/downloads/{task_id}/file")
async def get_downloaded_file(task_id: str) -> FileResponse:
    task = download_manager.get_task(task_id)
    if not task or not task.file_path:
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    return FileResponse(path=task.file_path, filename=task.file_path.split("\\")[-1].split("/")[-1])

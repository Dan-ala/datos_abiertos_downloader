from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class DownloadFormat(str, Enum):
    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DatasetColumn(BaseModel):
    name: str
    field_name: str
    data_type: str


class DatasetMetadata(BaseModel):
    dataset_id: str
    domain: str
    name: str
    description: str = ""
    category: str = ""
    rows_updated_at: datetime | None = None
    columns: list[DatasetColumn] = Field(default_factory=list)
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)


class SearchResultItem(BaseModel):
    dataset_id: str
    domain: str
    name: str
    description: str = ""
    category: str = ""
    updated_at: datetime | None = None
    download_count: int = 0


class BatchDownloadRequest(BaseModel):
    items: list[str] = Field(..., min_length=1, description="List of Socrata URLs or 4x4 dataset IDs")
    format: DownloadFormat = DownloadFormat.CSV
    domain: str | None = None
    output_dir: str | None = Field(
        default=None,
        description="Ruta local donde guardar los archivos descargados.",
    )
    auto_parquet_mb: int | None = Field(
        default=None,
        ge=1,
        description="Si el formato es CSV y el archivo supera este tamaño en MB, convertir automáticamente a Parquet.",
    )


class DownloadTask(BaseModel):
    task_id: str
    dataset_id: str
    domain: str
    dataset_name: str = ""
    format: DownloadFormat
    status: DownloadStatus = DownloadStatus.QUEUED
    bytes_downloaded: int = 0
    total_bytes: int | None = None
    row_count: int | None = None
    file_path: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

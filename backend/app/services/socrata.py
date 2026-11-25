import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
import httpx

from app.core.config import settings
from app.models.schemas import (
    DatasetColumn,
    DatasetMetadata,
    DownloadFormat,
    SearchResultItem,
)

SOCRATA_ID_RE = re.compile(r"\b([a-z0-9]{4}-[a-z0-9]{4})\b", re.IGNORECASE)
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def parse_socrata_reference(raw: str, default_domain: str | None = None) -> tuple[str, str]:
    """Extract (domain, dataset_id) from a Socrata URL or bare 4x4 dataset ID."""
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("La referencia del dataset está vacía.")

    domain = default_domain or settings.default_domain
    if cleaned.startswith(("http://", "https://")):
        parsed = urlparse(cleaned)
        if not parsed.netloc:
            raise ValueError(f"URL inválida: {raw}")
        domain = parsed.netloc
        search_target = parsed.path
    else:
        search_target = cleaned

    match = SOCRATA_ID_RE.search(search_target)
    if not match:
        raise ValueError(f"No se encontró un ID Socrata válido (formato xxxx-xxxx) en: {raw}")

    return domain.lower(), match.group(1).lower()


def sanitize_filename(name: str, dataset_id: str, fmt: DownloadFormat) -> str:
    """Build a safe, deterministic filename for a downloaded dataset."""
    base = INVALID_FILENAME_CHARS_RE.sub("_", name).strip(" ._")
    base = re.sub(r"\s+", "_", base)[:80] or "dataset"
    return f"{base}_{dataset_id}.{fmt.value}"


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class SocrataClient:
    """Async client for Socrata Open Data portals (e.g. datos.gov.co)."""

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout or settings.request_timeout_seconds

    @staticmethod
    def build_download_url(domain: str, dataset_id: str, fmt: DownloadFormat) -> str:
        source_ext = "csv" if fmt == DownloadFormat.PARQUET else fmt.value
        return f"https://{domain}/api/v3/views/{dataset_id}/export.{source_ext}"

    @staticmethod
    def build_legacy_download_url(domain: str, dataset_id: str, fmt: DownloadFormat) -> str:
        source_ext = "csv" if fmt == DownloadFormat.PARQUET else fmt.value
        return f"https://{domain}/api/views/{dataset_id}/rows.{source_ext}?accessType=DOWNLOAD"

    @staticmethod
    def build_resource_url(domain: str, dataset_id: str, fmt: DownloadFormat) -> str:
        source_ext = "csv" if fmt == DownloadFormat.PARQUET else fmt.value
        return f"https://{domain}/resource/{dataset_id}.{source_ext}"

    async def search_catalog(
        self, query: str, domain: str | None = None, limit: int = 20
    ) -> list[SearchResultItem]:
        target_domain = domain or settings.default_domain
        params = {"domains": target_domain, "q": query, "limit": limit, "only": "datasets"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get("https://api.us.socrata.com/api/catalog/v1", params=params)
            resp.raise_for_status()
            payload = resp.json()

        results: list[SearchResultItem] = []
        for entry in payload.get("results", []):
            resource = entry.get("resource", {})
            dataset_id = resource.get("id")
            if not dataset_id:
                continue
            results.append(
                SearchResultItem(
                    dataset_id=dataset_id,
                    domain=entry.get("metadata", {}).get("domain", target_domain),
                    name=resource.get("name", dataset_id),
                    description=resource.get("description", "") or "",
                    category=entry.get("classification", {}).get("domain_category", "") or "",
                    updated_at=_parse_timestamp(resource.get("updatedAt")),
                    download_count=int(resource.get("download_count") or 0),
                )
            )
        return results

    async def get_metadata(
        self, domain: str, dataset_id: str, include_preview: bool = True
    ) -> DatasetMetadata:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            view_resp = await client.get(f"https://{domain}/api/views/{dataset_id}.json")
            view_resp.raise_for_status()
            view_data = view_resp.json()

            preview_rows: list[dict] = []
            if include_preview:
                preview_resp = await client.get(
                    f"https://{domain}/resource/{dataset_id}.json",
                    params={"$limit": 10},
                )
                if preview_resp.status_code == 200:
                    data = preview_resp.json()
                    if isinstance(data, list):
                        preview_rows = data

        columns = [
            DatasetColumn(
                name=col.get("name", ""),
                field_name=col.get("fieldName", ""),
                data_type=col.get("dataTypeName", "text"),
            )
            for col in view_data.get("columns", [])
            if not col.get("fieldName", "").startswith(":@")
        ]

        return DatasetMetadata(
            dataset_id=dataset_id,
            domain=domain,
            name=view_data.get("name", dataset_id),
            description=view_data.get("description", "") or "",
            category=view_data.get("category", "") or "",
            rows_updated_at=_parse_timestamp(view_data.get("rowsUpdatedAt")),
            columns=columns,
            preview_rows=preview_rows,
        )

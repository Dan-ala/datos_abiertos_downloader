export type DownloadFormat = "csv" | "json" | "parquet";

export type DownloadStatus =
  | "queued"
  | "downloading"
  | "completed"
  | "failed"
  | "cancelled";

export interface DatasetColumn {
  name: string;
  field_name: string;
  data_type: string;
}

export interface DatasetMetadata {
  dataset_id: string;
  domain: string;
  name: string;
  description: string;
  category: string;
  rows_updated_at: string | null;
  columns: DatasetColumn[];
  preview_rows: Record<string, unknown>[];
}

export interface SearchResultItem {
  dataset_id: string;
  domain: string;
  name: string;
  description: string;
  category: string;
  updated_at: string | null;
  download_count: number;
}

export interface DownloadTask {
  task_id: string;
  dataset_id: string;
  domain: string;
  dataset_name: string;
  format: DownloadFormat;
  status: DownloadStatus;
  bytes_downloaded: number;
  total_bytes: number | null;
  row_count: number | null;
  file_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Error HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () =>
    requestJson<{ status: string; downloads_dir: string }>("/api/health"),

  pickFolder: (initialDir?: string) =>
    requestJson<{ path: string | null }>(
      `/api/system/pick-folder${
        initialDir ? `?initial_dir=${encodeURIComponent(initialDir)}` : ""
      }`,
      { method: "POST" }
    ),

  search: (q: string, limit = 15) =>
    requestJson<SearchResultItem[]>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`
    ),

  inspect: (ref: string) =>
    requestJson<DatasetMetadata>(
      `/api/datasets/inspect?ref=${encodeURIComponent(ref)}`
    ),

  enqueueBatch: (
    items: string[],
    format: DownloadFormat,
    autoParquetMb?: number | null,
    outputDir?: string | null
  ) =>
    requestJson<DownloadTask[]>("/api/downloads/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items,
        format,
        auto_parquet_mb: autoParquetMb ?? null,
        output_dir: outputDir?.trim() || null,
      }),
    }),

  listDownloads: () => requestJson<DownloadTask[]>("/api/downloads"),

  cancelDownload: (taskId: string) =>
    requestJson<DownloadTask>(`/api/downloads/${taskId}/cancel`, {
      method: "POST",
    }),

  deleteDownload: (taskId: string) =>
    requestJson<{ removed: boolean }>(`/api/downloads/${taskId}`, {
      method: "DELETE",
    }),
};

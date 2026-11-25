import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Database,
  Download,
  Eye,
  FileDown,
  FolderOpen,
  Loader2,
  Plus,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  api,
  DatasetMetadata,
  DownloadFormat,
  DownloadTask,
  SearchResultItem,
} from "./api/client";

const SAMPLE_URL =
  "https://www.datos.gov.co/Ciencia-Tecnolog-a-e-Innovaci-n/Accesos-Fijos-a-Internet-por-Proveedor-Departament/hwrp-3su3/about_data";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function statusBadge(status: DownloadTask["status"]) {
  switch (status) {
    case "completed":
      return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
    case "downloading":
      return "bg-sky-500/15 text-sky-300 border-sky-500/30";
    case "queued":
      return "bg-amber-500/15 text-amber-300 border-amber-500/30";
    case "failed":
      return "bg-rose-500/15 text-rose-300 border-rose-500/30";
    case "cancelled":
      return "bg-slate-500/15 text-slate-300 border-slate-500/30";
  }
}

export default function App() {
  const [batchText, setBatchText] = useState(SAMPLE_URL);
  const [format, setFormat] = useState<DownloadFormat>("csv");
  const [autoParquet, setAutoParquet] = useState(true);
  const [outputDir, setOutputDir] = useState(
    () => localStorage.getItem("dad_output_dir") || ""
  );
  const [pickingFolder, setPickingFolder] = useState(false);
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([]);
  const [preview, setPreview] = useState<DatasetMetadata | null>(null);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [loadingInspect, setLoadingInspect] = useState(false);
  const [submittingBatch, setSubmittingBatch] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const parsedItems = useMemo(
    () =>
      batchText
        .split(/[\n,;]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    [batchText]
  );

  useEffect(() => {
    if (!outputDir) {
      api
        .health()
        .then((h) => {
          setOutputDir(h.downloads_dir);
        })
        .catch(() => {});
    }
    api.listDownloads().then(setTasks).catch(() => {});

    const es = new EventSource("/api/downloads/events");
    es.onmessage = (ev) => {
      try {
        setTasks(JSON.parse(ev.data) as DownloadTask[]);
      } catch {
        // ignore malformed initial event
      }
    };
    es.addEventListener("task_update", (ev) => {
      try {
        const updated = JSON.parse((ev as MessageEvent).data) as DownloadTask;
        setTasks((prev) => {
          const exists = prev.some((t) => t.task_id === updated.task_id);
          if (!exists) return [updated, ...prev];
          return prev.map((t) => (t.task_id === updated.task_id ? updated : t));
        });
      } catch {
        // ignore malformed update
      }
    });

    return () => es.close();
  }, []);

  async function handlePickFolder() {
    setErrorMsg(null);
    setPickingFolder(true);
    try {
      const res = await api.pickFolder(outputDir || undefined);
      if (res.path) {
        setOutputDir(res.path);
        localStorage.setItem("dad_output_dir", res.path);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "No se pudo abrir el selector");
    } finally {
      setPickingFolder(false);
    }
  }

  async function handleBatchDownload() {
    if (parsedItems.length === 0) return;
    setErrorMsg(null);
    setSubmittingBatch(true);
    if (outputDir.trim()) {
      localStorage.setItem("dad_output_dir", outputDir.trim());
    }
    try {
      await api.enqueueBatch(
        parsedItems,
        format,
        format === "csv" && autoParquet ? 50 : null,
        outputDir
      );
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Error al encolar descargas");
    } finally {
      setSubmittingBatch(false);
    }
  }

  async function handleInspect(ref: string) {
    if (!ref.trim()) return;
    setErrorMsg(null);
    setLoadingInspect(true);
    try {
      const meta = await api.inspect(ref.trim());
      setPreview(meta);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Error al inspeccionar dataset");
    } finally {
      setLoadingInspect(false);
    }
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    setErrorMsg(null);
    setLoadingSearch(true);
    try {
      const results = await api.search(searchQuery.trim());
      setSearchResults(results);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Error en búsqueda");
    } finally {
      setLoadingSearch(false);
    }
  }

  function appendToBatch(datasetId: string) {
    setBatchText((prev) => {
      const lines = prev
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      if (lines.includes(datasetId)) return prev;
      return [...lines, datasetId].join("\n");
    });
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/70 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-sky-500/15 text-sky-400">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">
                Datos Abiertos Downloader
              </h1>
              <p className="text-xs text-slate-400">
                Automatización de descargas por lotes para Socrata / datos.gov.co
              </p>
            </div>
          </div>
          <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1 text-xs text-slate-300">
            {tasks.filter((t) => t.status === "completed").length} completadas
          </span>
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-6 px-6 py-6 lg:grid-cols-12">
        {errorMsg && (
          <div className="lg:col-span-12 flex items-center justify-between rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            <span>{errorMsg}</span>
            <button
              onClick={() => setErrorMsg(null)}
              className="text-xs underline hover:text-white"
            >
              Cerrar
            </button>
          </div>
        )}

        {/* Left column: Batch downloader & Search */}
        <section className="space-y-6 lg:col-span-5">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
            <h2 className="mb-1 text-sm font-semibold text-slate-200">
              Descarga en lote (URLs o IDs 4x4)
            </h2>
            <p className="mb-3 text-xs text-slate-400">
              Pega uno o varios links de datos.gov.co o códigos como{" "}
              <code className="rounded bg-slate-800 px-1.5 py-0.5 text-sky-300">
                hwrp-3su3
              </code>{" "}
              (uno por línea).
            </p>
            <textarea
              rows={5}
              value={batchText}
              onChange={(e) => setBatchText(e.target.value)}
              placeholder="https://www.datos.gov.co/.../hwrp-3su3/about_data&#10;abcd-1234"
              className="w-full rounded-xl border border-slate-700 bg-slate-950 p-3 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
            />
            <div className="mt-3">
              <label className="mb-1 block text-xs text-slate-400">
                Carpeta de destino:
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={outputDir}
                  onChange={(e) => {
                    setOutputDir(e.target.value);
                    localStorage.setItem("dad_output_dir", e.target.value);
                  }}
                  placeholder="C:\ruta\de\descargas"
                  className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
                />
                <button
                  type="button"
                  disabled={pickingFolder}
                  onClick={handlePickFolder}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                >
                  {pickingFolder ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <FolderOpen className="h-3.5 w-3.5" />
                  )}
                  Examinar
                </button>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2">
                  <label className="text-xs text-slate-400">Formato:</label>
                  <select
                    value={format}
                    onChange={(e) => setFormat(e.target.value as DownloadFormat)}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100"
                  >
                    <option value="csv">CSV</option>
                    <option value="parquet">Parquet (ZSTD)</option>
                    <option value="json">JSON</option>
                  </select>
                </div>
                {format === "csv" && (
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={autoParquet}
                      onChange={(e) => setAutoParquet(e.target.checked)}
                      className="rounded border-slate-600 bg-slate-950 text-sky-500"
                    />
                    Si supera 50 MB → .parquet
                  </label>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={parsedItems.length === 0 || loadingInspect}
                  onClick={() => handleInspect(parsedItems[0])}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                >
                  {loadingInspect ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Eye className="h-3.5 w-3.5" />
                  )}
                  Inspeccionar
                </button>
                <button
                  type="button"
                  disabled={parsedItems.length === 0 || submittingBatch}
                  onClick={handleBatchDownload}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-sky-500 px-3.5 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-50"
                >
                  {submittingBatch ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5" />
                  )}
                  Descargar ({parsedItems.length})
                </button>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">
              Buscar en catálogo de datos.gov.co
            </h2>
            <form onSubmit={handleSearch} className="flex gap-2">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Ej. internet fijo, contratación, salud..."
                className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none"
              />
              <button
                type="submit"
                disabled={loadingSearch}
                className="inline-flex items-center gap-1.5 rounded-xl bg-slate-800 px-3.5 py-2 text-xs font-medium text-slate-100 hover:bg-slate-700 disabled:opacity-50"
              >
                {loadingSearch ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Search className="h-3.5 w-3.5" />
                )}
                Buscar
              </button>
            </form>

            {searchResults.length > 0 && (
              <ul className="mt-4 max-h-80 space-y-2 overflow-y-auto pr-1">
                {searchResults.map((item) => (
                  <li
                    key={item.dataset_id}
                    className="rounded-xl border border-slate-800 bg-slate-950/70 p-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-xs font-medium text-slate-100">
                          {item.name}
                        </p>
                        <p className="mt-0.5 font-mono text-[11px] text-sky-400">
                          {item.dataset_id}
                          {item.category ? ` · ${item.category}` : ""}
                        </p>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <button
                          type="button"
                          onClick={() => handleInspect(item.dataset_id)}
                          title="Inspeccionar"
                          className="rounded-lg border border-slate-700 p-1.5 text-slate-300 hover:bg-slate-800"
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => appendToBatch(item.dataset_id)}
                          title="Agregar al lote"
                          className="rounded-lg border border-sky-500/40 bg-sky-500/10 p-1.5 text-sky-300 hover:bg-sky-500/20"
                        >
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        {/* Right column: Queue & Preview */}
        <section className="space-y-6 lg:col-span-7">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">
              Cola de descargas
            </h2>
            {tasks.length === 0 ? (
              <p className="rounded-xl border border-dashed border-slate-800 py-8 text-center text-xs text-slate-500">
                Aún no hay descargas en curso. Agrega URLs o IDs y presiona Descargar.
              </p>
            ) : (
              <div className="space-y-2.5">
                {tasks.map((task) => {
                  const pct =
                    task.total_bytes && task.total_bytes > 0
                      ? Math.min(100, Math.round((task.bytes_downloaded / task.total_bytes) * 100))
                      : null;

                  return (
                    <div
                      key={task.task_id}
                      className="rounded-xl border border-slate-800 bg-slate-950/80 p-3.5"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-xs font-semibold text-slate-100">
                              {task.dataset_name || task.dataset_id}
                            </span>
                            <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-300">
                              {task.format}
                            </span>
                            <span
                              className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusBadge(
                                task.status
                              )}`}
                            >
                              {task.status}
                            </span>
                          </div>
                          <p className="mt-1 font-mono text-[11px] text-slate-400">
                            {task.dataset_id} · {formatBytes(task.bytes_downloaded)}
                            {task.total_bytes ? ` / ${formatBytes(task.total_bytes)}` : ""}
                            {task.row_count !== null && task.row_count !== undefined
                              ? ` · ${task.row_count.toLocaleString()} filas`
                              : ""}
                          </p>
                          {task.error && (
                            <p className="mt-1 text-xs text-rose-400">{task.error}</p>
                          )}
                        </div>

                        <div className="flex shrink-0 items-center gap-1.5">
                          {task.status === "completed" && (
                            <a
                              href={`/api/downloads/${task.task_id}/file`}
                              className="inline-flex items-center gap-1 rounded-lg bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-300 hover:bg-emerald-500/25"
                            >
                              <FileDown className="h-3.5 w-3.5" />
                              Guardar
                            </a>
                          )}
                          {(task.status === "queued" || task.status === "downloading") && (
                            <button
                              type="button"
                              onClick={() => api.cancelDownload(task.task_id)}
                              title="Cancelar"
                              className="rounded-lg border border-slate-700 p-1.5 text-slate-300 hover:bg-slate-800"
                            >
                              <XCircle className="h-3.5 w-3.5" />
                            </button>
                          )}
                          {task.status !== "downloading" && (
                            <button
                              type="button"
                              onClick={() => api.deleteDownload(task.task_id)}
                              title="Eliminar de la lista"
                              className="rounded-lg border border-slate-800 p-1.5 text-slate-400 hover:bg-slate-800 hover:text-rose-300"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </div>
                      </div>

                      {task.status === "downloading" && (
                        <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                          <div
                            className={`h-full bg-sky-400 transition-all ${
                              pct === null ? "w-1/2 animate-pulse" : ""
                            }`}
                            style={pct !== null ? { width: `${pct}%` } : undefined}
                          />
                        </div>
                      )}
                      {task.status === "completed" && task.file_path && (
                        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-emerald-400/90">
                          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                          <span className="truncate font-mono">{task.file_path}</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {preview && (
            <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-100">
                    {preview.name}
                  </h2>
                  <p className="mt-0.5 font-mono text-xs text-sky-400">
                    {preview.dataset_id} · {preview.columns.length} columnas
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => appendToBatch(preview.dataset_id)}
                  className="inline-flex items-center gap-1 rounded-lg border border-sky-500/40 bg-sky-500/10 px-2.5 py-1 text-xs text-sky-300 hover:bg-sky-500/20"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Al lote
                </button>
              </div>
              {preview.description && (
                <p className="mb-3 line-clamp-2 text-xs text-slate-400">
                  {preview.description}
                </p>
              )}
              {preview.preview_rows.length > 0 && (
                <div className="overflow-x-auto rounded-xl border border-slate-800">
                  <table className="w-full text-left text-[11px]">
                    <thead className="bg-slate-950 text-slate-400">
                      <tr>
                        {preview.columns.slice(0, 6).map((col) => (
                          <th key={col.field_name} className="px-3 py-2 font-medium">
                            {col.name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/80 bg-slate-950/40">
                      {preview.preview_rows.slice(0, 5).map((row, idx) => (
                        <tr key={idx}>
                          {preview.columns.slice(0, 6).map((col) => (
                            <td
                              key={col.field_name}
                              className="max-w-[160px] truncate px-3 py-1.5 text-slate-300"
                            >
                              {String(row[col.field_name] ?? "")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

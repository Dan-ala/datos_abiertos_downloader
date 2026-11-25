import asyncio
import json
import os
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app.core.config import settings
from app.models.schemas import DownloadFormat, DownloadStatus, DownloadTask
from app.services.downloader import DownloadManager
from app.services.socrata import SocrataClient, parse_socrata_reference

SAMPLE_URL = (
    "https://www.datos.gov.co/Ciencia-Tecnolog-a-e-Innovaci-n/"
    "Accesos-Fijos-a-Internet-por-Proveedor-Departament/hwrp-3su3/about_data"
)
PREFS_FILE = Path.home() / ".datos_abiertos_downloader.json"

FORMAT_LABELS = {
    "CSV (compatible con Excel)": DownloadFormat.CSV,
    "Parquet ZSTD (ideal >1M filas)": DownloadFormat.PARQUET,
}

STATUS_STYLE = {
    DownloadStatus.QUEUED: ("En cola", "#f59e0b"),
    DownloadStatus.DOWNLOADING: ("Descargando...", "#38bdf8"),
    DownloadStatus.COMPLETED: ("Completado", "#10b981"),
    DownloadStatus.FAILED: ("Error", "#f43f5e"),
    DownloadStatus.CANCELLED: ("Cancelado", "#94a3b8"),
}


def _format_bytes(num: int) -> str:
    if num <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB")
    idx = 0
    val = float(num)
    while val >= 1024 and idx < len(units) - 1:
        val /= 1024
        idx += 1
    return f"{val:.0f} {units[idx]}" if idx == 0 else f"{val:.1f} {units[idx]}"


class DatosAbiertosApp(ctk.CTk):
    """Friendly native desktop UI backed by DownloadManager & SocrataClient."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Datos Abiertos Downloader")
        self.geometry("1180x760")
        self.minsize(960, 640)

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._loop_thread.start()

        self._manager = self._run_coro_sync(self._create_manager())
        self._socrata = SocrataClient()
        self._task_widgets: dict[str, dict] = {}
        self._empty_label: ctk.CTkLabel | None = None

        self._build_ui()
        self._load_prefs()
        self._update_batch_count()
        self.after(400, self._poll_tasks)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _create_manager(self) -> DownloadManager:
        return DownloadManager()

    def _run_coro_sync(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _load_prefs(self) -> None:
        default_dir = str(settings.downloads_dir)
        if PREFS_FILE.exists():
            try:
                data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
                default_dir = data.get("output_dir") or default_dir
            except OSError:
                pass
        self.dir_var.set(default_dir)

    def _save_prefs(self) -> None:
        try:
            PREFS_FILE.write_text(
                json.dumps({"output_dir": self.dir_var.get().strip()}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=7)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="x", padx=20, pady=12)

        ctk.CTkLabel(
            header_inner,
            text="Datos Abiertos Downloader",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header_inner,
            text="Descarga masiva desde datos.gov.co (CSV o Parquet)",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=14)

        self.summary_badge = ctk.CTkLabel(
            header_inner,
            text="0 completadas",
            fg_color="#1e293b",
            corner_radius=12,
            padx=12,
            pady=4,
            font=ctk.CTkFont(size=12),
        )
        self.summary_badge.pack(side="right")

        # Left Panel: Tabs (Batch & Search)
        left_tabs = ctk.CTkTabview(self)
        left_tabs.grid(row=1, column=0, sticky="nsew", padx=(18, 9), pady=(12, 8))
        tab_batch = left_tabs.add("Descarga en lote")
        tab_search = left_tabs.add("Buscar en catálogo")

        self._build_batch_tab(tab_batch)
        self._build_search_tab(tab_search)

        # Right Panel: Download Queue
        right = ctk.CTkFrame(self)
        right.grid(row=1, column=1, sticky="nsew", padx=(9, 18), pady=(12, 8))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        q_header = ctk.CTkFrame(right, fg_color="transparent")
        q_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            q_header,
            text="Cola de descargas",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            q_header,
            text="Abrir carpeta",
            width=110,
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._open_dest_folder,
        ).pack(side="right")
        ctk.CTkButton(
            q_header,
            text="Limpiar listas",
            width=105,
            fg_color="transparent",
            border_width=1,
            border_color="#334155",
            hover_color="#1e293b",
            command=self._clear_finished,
        ).pack(side="right", padx=(0, 8))

        self.queue_frame = ctk.CTkScrollableFrame(right)
        self.queue_frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.queue_frame.grid_columnconfigure(0, weight=1)

        # Footer status bar
        self.status_var = ctk.StringVar(value="Listo para descargar.")
        status_bar = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            anchor="w",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=11),
            padx=20,
        )
        status_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    def _build_batch_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            parent,
            text="1. Pega las URLs o códigos 4x4 (uno por línea):",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

        tools_row = ctk.CTkFrame(parent, fg_color="transparent")
        tools_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.batch_count_lbl = ctk.CTkLabel(
            tools_row,
            text="",
            text_color="#38bdf8",
            font=ctk.CTkFont(size=11),
        )
        self.batch_count_lbl.pack(side="left")

        ctk.CTkButton(
            tools_row,
            text="Limpiar",
            width=64,
            height=24,
            fg_color="transparent",
            border_width=1,
            border_color="#334155",
            hover_color="#1e293b",
            font=ctk.CTkFont(size=11),
            command=self._clear_batch_box,
        ).pack(side="right")
        ctk.CTkButton(
            tools_row,
            text="Cargar .txt",
            width=84,
            height=24,
            fg_color="#1e293b",
            hover_color="#334155",
            font=ctk.CTkFont(size=11),
            command=self._load_txt_file,
        ).pack(side="right", padx=(0, 6))

        self.batch_box = ctk.CTkTextbox(parent, font=("Consolas", 11))
        self.batch_box.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.batch_box.insert("1.0", SAMPLE_URL)
        self.batch_box.bind("<KeyRelease>", lambda _e: self._update_batch_count())

        # Destination folder
        ctk.CTkLabel(
            parent,
            text="2. Carpeta donde se guardarán los archivos:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))

        dir_row = ctk.CTkFrame(parent, fg_color="transparent")
        dir_row.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 12))
        dir_row.grid_columnconfigure(0, weight=1)

        self.dir_var = ctk.StringVar()
        ctk.CTkEntry(dir_row, textvariable=self.dir_var, font=("Consolas", 11)).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ctk.CTkButton(dir_row, text="Examinar...", width=96, command=self._pick_folder).grid(
            row=0, column=1
        )

        # Format + auto-parquet
        ctk.CTkLabel(
            parent,
            text="3. Formato de salida:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 4))

        fmt_row = ctk.CTkFrame(parent, fg_color="transparent")
        fmt_row.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 12))

        self.fmt_label_var = ctk.StringVar(value="CSV (compatible con Excel)")
        ctk.CTkOptionMenu(
            fmt_row,
            values=list(FORMAT_LABELS.keys()),
            variable=self.fmt_label_var,
            width=220,
            command=self._on_format_change,
        ).pack(side="left")

        self.auto_parquet_var = ctk.BooleanVar(value=True)
        self.auto_parquet_chk = ctk.CTkCheckBox(
            fmt_row,
            text="Si pesa >50 MB → .parquet",
            variable=self.auto_parquet_var,
            font=ctk.CTkFont(size=12),
        )
        self.auto_parquet_chk.pack(side="left", padx=12)

        # Primary CTA
        ctk.CTkButton(
            parent,
            text="Iniciar descarga",
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_batch,
        ).grid(row=7, column=0, sticky="ew", padx=8, pady=(2, 8))

    def _build_search_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            parent,
            text="Busca datasets públicos en datos.gov.co y agrégalos al lote:",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 6))

        search_bar = ctk.CTkFrame(parent, fg_color="transparent")
        search_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        search_bar.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            search_bar,
            textvariable=self.search_var,
            placeholder_text="Ej. internet fijo, educación, salud, secop...",
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        search_entry.bind("<Return>", lambda _e: self._search_catalog())
        self.search_btn = ctk.CTkButton(
            search_bar, text="Buscar", width=86, command=self._search_catalog
        )
        self.search_btn.grid(row=0, column=1)

        self.search_results_frame = ctk.CTkScrollableFrame(parent)
        self.search_results_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

    def _parse_batch_items(self) -> list[str]:
        raw = self.batch_box.get("1.0", "end")
        return [line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()]

    def _update_batch_count(self) -> None:
        items = self._parse_batch_items()
        valid = 0
        for item in items:
            try:
                parse_socrata_reference(item)
                valid += 1
            except ValueError:
                pass
        if not items:
            self.batch_count_lbl.configure(text="Sin enlaces", text_color="#94a3b8")
        elif valid == len(items):
            self.batch_count_lbl.configure(
                text=f"{valid} dataset(s) válido(s)", text_color="#10b981"
            )
        else:
            self.batch_count_lbl.configure(
                text=f"{valid} válido(s) de {len(items)} línea(s)", text_color="#f59e0b"
            )

    def _clear_batch_box(self) -> None:
        self.batch_box.delete("1.0", "end")
        self._update_batch_count()

    def _load_txt_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Cargar lista de URLs o IDs",
            filetypes=[("Archivos de texto", "*.txt *.csv"), ("Todos", "*.*")],
        )
        if not path:
            return
        content = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
        if content:
            existing = self.batch_box.get("1.0", "end").strip()
            self.batch_box.insert("end", ("\n" if existing else "") + content)
            self._update_batch_count()
            self._set_status(f"Lista cargada desde {Path(path).name}")

    def _on_format_change(self, label: str) -> None:
        fmt = FORMAT_LABELS[label]
        self.auto_parquet_chk.configure(
            state="normal" if fmt == DownloadFormat.CSV else "disabled"
        )

    def _pick_folder(self) -> None:
        selected = filedialog.askdirectory(
            initialdir=self.dir_var.get() or str(settings.downloads_dir),
            title="Seleccionar carpeta de descargas",
        )
        if selected:
            self.dir_var.set(selected)
            self._save_prefs()
            self._set_status(f"Carpeta destino: {selected}")

    def _open_dest_folder(self) -> None:
        target = Path(self.dir_var.get().strip() or str(settings.downloads_dir))
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def _start_batch(self) -> None:
        items = self._parse_batch_items()
        if not items:
            messagebox.showwarning("Sin elementos", "Pega al menos una URL o ID 4x4.")
            return

        self._save_prefs()
        fmt = FORMAT_LABELS[self.fmt_label_var.get()]
        auto_mb = 50 if (fmt == DownloadFormat.CSV and self.auto_parquet_var.get()) else None
        out_dir = self.dir_var.get().strip() or None

        async def _enqueue():
            return self._manager.enqueue_batch(
                items=items, fmt=fmt, auto_parquet_mb=auto_mb, output_dir=out_dir
            )

        try:
            tasks = self._run_coro_sync(_enqueue())
            self._set_status(f"Se encolaron {len(tasks)} descarga(s).")
            self._poll_tasks()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def _search_catalog(self) -> None:
        query = self.search_var.get().strip()
        if not query:
            return
        self.search_btn.configure(state="disabled", text="...")
        self._set_status(f"Buscando '{query}' en datos.gov.co...")

        def _worker() -> None:
            try:
                results = self._run_coro_sync(self._socrata.search_catalog(query, limit=15))
                self.after(0, lambda: self._render_search_results(results))
                self.after(0, lambda: self._set_status(f"Se encontraron {len(results)} resultados."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Error de búsqueda", str(exc)))
                self.after(0, lambda: self._set_status("Error al buscar en el catálogo."))
            finally:
                self.after(0, lambda: self.search_btn.configure(state="normal", text="Buscar"))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_search_results(self, results) -> None:
        for child in self.search_results_frame.winfo_children():
            child.destroy()

        if not results:
            ctk.CTkLabel(
                self.search_results_frame, text="No se encontraron datasets.", text_color="#94a3b8"
            ).pack(pady=16)
            return

        for item in results:
            row = ctk.CTkFrame(self.search_results_frame)
            row.pack(fill="x", pady=4)
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=10, pady=8)

            ctk.CTkLabel(
                info,
                text=item.name,
                anchor="w",
                font=ctk.CTkFont(size=12, weight="bold"),
                wraplength=290,
                justify="left",
            ).pack(anchor="w")
            ctk.CTkLabel(
                info,
                text=f"{item.dataset_id}" + (f" · {item.category}" if item.category else ""),
                anchor="w",
                text_color="#38bdf8",
                font=("Consolas", 10),
            ).pack(anchor="w")

            btn = ctk.CTkButton(
                row,
                text="+ Agregar",
                width=82,
            )
            btn.configure(command=lambda did=item.dataset_id, b=btn: self._append_to_batch(did, b))
            btn.pack(side="right", padx=10)

    def _append_to_batch(self, dataset_id: str, button: ctk.CTkButton | None = None) -> None:
        current = self.batch_box.get("1.0", "end").strip()
        lines = [l.strip() for l in current.splitlines() if l.strip()]
        if dataset_id not in lines:
            self.batch_box.insert("end", ("\n" if current else "") + dataset_id)
            self._update_batch_count()
            self._set_status(f"Agregado {dataset_id} al lote.")
        if button:
            button.configure(text="✓ Agregado", fg_color="#059669", hover_color="#059669")

    def _clear_finished(self) -> None:
        for task in self._manager.list_tasks():
            if task.status in (
                DownloadStatus.COMPLETED,
                DownloadStatus.FAILED,
                DownloadStatus.CANCELLED,
            ):
                self._manager.remove_task(task.task_id)
                w = self._task_widgets.pop(task.task_id, None)
                if w:
                    w["card"].destroy()
        self._render_tasks(self._manager.list_tasks())

    def _poll_tasks(self) -> None:
        self._render_tasks(self._manager.list_tasks())
        self.after(400, self._poll_tasks)

    def _render_tasks(self, tasks: list[DownloadTask]) -> None:
        completed = sum(1 for t in tasks if t.status == DownloadStatus.COMPLETED)
        self.summary_badge.configure(text=f"{completed} completadas")

        if not tasks:
            if not self._empty_label:
                self._empty_label = ctk.CTkLabel(
                    self.queue_frame,
                    text="Aún no hay descargas.\n\nPega un enlace o ID en la izquierda y presiona 'Iniciar descarga'.",
                    text_color="#64748b",
                    justify="center",
                    font=ctk.CTkFont(size=12),
                )
                self._empty_label.grid(row=0, column=0, pady=48)
            return

        if self._empty_label:
            self._empty_label.destroy()
            self._empty_label = None

        for idx, task in enumerate(tasks):
            if task.task_id not in self._task_widgets:
                card = ctk.CTkFrame(self.queue_frame)
                card.grid(row=idx, column=0, sticky="ew", pady=5)
                card.grid_columnconfigure(0, weight=1)

                top = ctk.CTkFrame(card, fg_color="transparent")
                top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
                top.grid_columnconfigure(0, weight=1)

                title_lbl = ctk.CTkLabel(
                    top, text="", anchor="w", font=ctk.CTkFont(size=13, weight="bold")
                )
                title_lbl.grid(row=0, column=0, sticky="ew")

                badge_lbl = ctk.CTkLabel(
                    top, text="", corner_radius=8, padx=8, pady=2, font=ctk.CTkFont(size=11, weight="bold")
                )
                badge_lbl.grid(row=0, column=1, padx=(8, 0))

                meta_lbl = ctk.CTkLabel(
                    card,
                    text="",
                    anchor="w",
                    justify="left",
                    font=("Consolas", 11),
                    text_color="#94a3b8",
                )
                meta_lbl.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 6))

                bar = ctk.CTkProgressBar(card, mode="indeterminate")
                bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

                action_btn = ctk.CTkButton(
                    card,
                    text="Cancelar",
                    width=84,
                    fg_color="#334155",
                    hover_color="#dc2626",
                    command=lambda tid=task.task_id: self._cancel_task(tid),
                )
                action_btn.grid(row=0, column=1, rowspan=3, padx=(0, 12), pady=10)

                self._task_widgets[task.task_id] = {
                    "card": card,
                    "title": title_lbl,
                    "badge": badge_lbl,
                    "meta": meta_lbl,
                    "bar": bar,
                    "action": action_btn,
                    "animating": False,
                }

            w = self._task_widgets[task.task_id]
            w["card"].grid(row=idx, column=0, sticky="ew", pady=5)

            status_text, status_color = STATUS_STYLE[task.status]
            w["title"].configure(text=task.dataset_name or task.dataset_id)
            w["badge"].configure(
                text=f"{task.format.value.upper()} · {status_text}",
                fg_color="#1e293b",
                text_color=status_color,
            )

            rows_txt = f" · {task.row_count:,} filas" if task.row_count is not None else ""
            err_txt = f"\nError: {task.error}" if task.error else ""
            path_txt = f"\nGuardado: {task.file_path}" if task.file_path else ""
            w["meta"].configure(
                text=f"ID: {task.dataset_id} · {_format_bytes(task.bytes_downloaded)}{rows_txt}{err_txt}{path_txt}"
            )

            if task.status == DownloadStatus.DOWNLOADING:
                if not w["animating"]:
                    w["bar"].configure(mode="indeterminate")
                    w["bar"].start()
                    w["animating"] = True
                w["action"].configure(state="normal", text="Cancelar", command=lambda tid=task.task_id: self._cancel_task(tid))
            else:
                if w["animating"]:
                    w["bar"].stop()
                    w["animating"] = False
                if task.status == DownloadStatus.COMPLETED:
                    w["bar"].configure(mode="determinate", progress_color="#10b981")
                    w["bar"].set(1.0)
                    w["action"].configure(
                        state="normal",
                        text="Carpeta",
                        fg_color="#059669",
                        hover_color="#047857",
                        command=self._open_dest_folder,
                    )
                else:
                    w["bar"].configure(mode="determinate", progress_color="#38bdf8")
                    w["bar"].set(0.0)
                    w["action"].configure(
                        state="normal" if task.status == DownloadStatus.QUEUED else "disabled",
                        text="Cancelar",
                        fg_color="#334155",
                        hover_color="#dc2626",
                        command=lambda tid=task.task_id: self._cancel_task(tid),
                    )

    def _cancel_task(self, task_id: str) -> None:
        asyncio.run_coroutine_threadsafe(self._manager.cancel_task(task_id), self._loop)

    def _on_close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self.destroy()


def main() -> None:
    app = DatosAbiertosApp()
    app.mainloop()


if __name__ == "__main__":
    main()

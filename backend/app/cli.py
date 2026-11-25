import argparse
import asyncio
from pathlib import Path

from app.models.schemas import DownloadFormat, DownloadStatus
from app.services.downloader import DownloadManager


def _collect_items(items: list[str], file_path: str | None) -> list[str]:
    collected = list(items)
    if file_path:
        text = Path(file_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                collected.append(clean)
    return collected


async def run_cli(args: argparse.Namespace) -> int:
    items = _collect_items(args.items, args.file)
    if not items:
        print("Error: proporciona al menos una URL/ID con --items o --file.")
        return 1

    manager = DownloadManager()
    tasks = manager.enqueue_batch(
        items=items,
        fmt=DownloadFormat(args.format),
        auto_parquet_mb=args.auto_parquet_mb,
        output_dir=args.out,
    )

    pending_ids = {t.task_id for t in tasks}
    while pending_ids:
        await asyncio.sleep(0.5)
        for tid in list(pending_ids):
            t = manager.get_task(tid)
            if t and t.status in (
                DownloadStatus.COMPLETED,
                DownloadStatus.FAILED,
                DownloadStatus.CANCELLED,
            ):
                pending_ids.discard(tid)
                rows = f" | {t.row_count:,} filas" if t.row_count is not None else ""
                if t.status == DownloadStatus.COMPLETED:
                    print(f"[OK] {t.dataset_id} -> {t.file_path} ({t.bytes_downloaded:,} bytes{rows})")
                else:
                    print(f"[{t.status.value.upper()}] {t.dataset_id}: {t.error}")

    failed = sum(1 for t in tasks if t.status != DownloadStatus.COMPLETED)
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI para descargar en lote datasets de Datos Abiertos (datos.gov.co)."
    )
    parser.add_argument("--items", nargs="*", default=[], help="URLs o IDs 4x4 (ej. hwrp-3su3)")
    parser.add_argument("--file", help="Archivo .txt con una URL o ID por línea")
    parser.add_argument("--format", choices=["csv", "parquet"], default="csv")
    parser.add_argument(
        "--auto-parquet-mb",
        type=int,
        default=50,
        help="Si --format csv supera este tamaño en MB, convertir a .parquet (default: 50, 0 para desactivar)",
    )
    parser.add_argument("--out", help="Carpeta de destino")
    args = parser.parse_args()
    if args.auto_parquet_mb <= 0:
        args.auto_parquet_mb = None

    raise SystemExit(asyncio.run(run_cli(args)))


if __name__ == "__main__":
    main()

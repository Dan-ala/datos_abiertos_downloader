# Datos Abiertos Downloader

Herramienta local para buscar, inspeccionar y descargar en lote datasets de portales Socrata (como **datos.gov.co**) usando URLs completas o identificadores 4x4 (ej. `hwrp-3su3`), con conversión automática a **`.parquet` (ZSTD)** para datasets grandes.

## Modos de uso

### 1. App de Escritorio Local (Recomendado)
Haz doble clic en `run_app.bat` o ejecuta:
```powershell
cd backend
.\.venv\Scripts\python.exe -m app.desktop
```

### 2. CLI para automatización por lotes
```powershell
cd backend
.\.venv\Scripts\python.exe -m app.cli --items hwrp-3su3 --format csv --auto-parquet-mb 50 --out "D:\Datos"
```
También puedes pasar un archivo con múltiples URLs/IDs:
```powershell
.\.venv\Scripts\python.exe -m app.cli --file urls.txt --format parquet --out "D:\Datos"
```

### 3. Modo Web (opcional)
- Backend: `cd backend && .\.venv\Scripts\uvicorn app.main:app --port 8000`
- Frontend: `cd frontend && npm run dev`

## Pruebas

```powershell
cd backend
.\.venv\Scripts\pytest -v
```

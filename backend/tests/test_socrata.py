import duckdb
import pytest
from app.models.schemas import DownloadFormat
from app.services.downloader import convert_csv_to_parquet
from app.services.socrata import parse_socrata_reference, sanitize_filename


def test_parse_full_datos_gov_url():
    url = "https://www.datos.gov.co/Ciencia-Tecnolog-a-e-Innovaci-n/Accesos-Fijos-a-Internet-por-Proveedor-Departament/hwrp-3su3/about_data"
    domain, dataset_id = parse_socrata_reference(url)
    assert domain == "www.datos.gov.co"
    assert dataset_id == "hwrp-3su3"


def test_parse_bare_id():
    domain, dataset_id = parse_socrata_reference("HWRP-3SU3")
    assert domain == "www.datos.gov.co"
    assert dataset_id == "hwrp-3su3"


def test_parse_invalid_reference_raises():
    with pytest.raises(ValueError):
        parse_socrata_reference("https://www.datos.gov.co/sin-id-valido")


def test_sanitize_filename():
    name = sanitize_filename('Accesos: Fijos / "Internet" * 2026', "hwrp-3su3", DownloadFormat.CSV)
    assert name.endswith("_hwrp-3su3.csv")
    assert "/" not in name
    assert ":" not in name


def test_convert_csv_to_parquet(tmp_path):
    csv_file = tmp_path / "sample.csv"
    parquet_file = tmp_path / "sample.parquet"
    csv_file.write_text("col_a,col_b\n1,uno\n2,dos\n3,tres\n", encoding="utf-8")

    rows = convert_csv_to_parquet(csv_file, parquet_file)
    assert rows == 3
    assert parquet_file.exists()

    conn = duckdb.connect(":memory:")
    result = conn.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(parquet_file)]).fetchone()[0]
    conn.close()
    assert result == 3

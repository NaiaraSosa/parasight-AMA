from __future__ import annotations
import csv
import os
import shutil
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np
import tifffile
from scipy.ndimage import binary_erosion

from app.core.config import settings
from app.pipeline.cellpose import segment_cells
from app.pipeline.histograms import save_histogram
from app.pipeline.io import IMAGE_EXTS as IO_IMAGE_EXTS
from app.pipeline.io import load_image_2d
from app.pipeline.metrics import compute_metrics, summarize_job
from app.pipeline.previews import build_input_preview, build_instance_preview, save_preview
from app.pipeline.postprocess import (
    compute_instance_areas,
    filter_cells_by_area,
    filter_parasites_by_area,
    merge_parasites,
)
from app.pipeline.stardist import segment_parasites

IMAGE_EXTS = set(IO_IMAGE_EXTS) | {".zip"}
CELL_MIN_AREA = int(os.getenv("CELL_MIN_AREA", "1500"))
CELL_MAX_ELONGATION = float(os.getenv("CELL_MAX_ELONGATION", "4"))
#CELL_MIN_AREA_PERCENTILE = float(os.getenv("CELL_MIN_AREA_PERCENTILE", "10"))
PARASITE_MAX_AREA = int(os.getenv("PARASITE_MAX_AREA", "500"))
#PARASITE_MAX_AREA_PERCENTILE = float(os.getenv("PARASITE_MAX_AREA_PERCENTILE", "90"))
PARASITE_ASSIGN_SIGMA = float(os.getenv("PARASITE_ASSIGN_SIGMA", "100"))
PARASITE_ASSIGN_THRESHOLD = float(os.getenv("PARASITE_ASSIGN_THRESHOLD", "0.4"))
PARASITE_CLUSTER_REASSIGNMENT = os.getenv("PARASITE_CLUSTER_REASSIGNMENT", "1")
PARASITE_CLUSTER_RADIUS = int(os.getenv("PARASITE_CLUSTER_RADIUS", "30"))
PARASITE_CLUSTER_MIN_SIZE = int(os.getenv("PARASITE_CLUSTER_MIN_SIZE", "2"))
PARASITE_CLUSTER_MARGIN = float(os.getenv("PARASITE_CLUSTER_MARGIN", "1.5"))

def _collect_images_from_dir(input_dir: Path) -> list[Path]:
    images = [p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in IO_IMAGE_EXTS]
    images.sort(key=lambda p: str(p).lower())
    if not images:
        raise ValueError("El directorio no contiene imagenes soportadas (tif/tiff/czi).")
    return images


def _extract_zip_safely(zip_path: Path, unzip_dir: Path) -> None:
    root = unzip_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target = (unzip_dir / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError("El ZIP contiene rutas inseguras.")
        zf.extractall(unzip_dir)


def _resolve_input_images(input_path: Path, job_temp_dir: Path) -> list[Path]:
    """
    Resuelve las imágenes de entrada desde un archivo, ZIP o directorio.

    Si el archivo es una imagen individual (TIFF/CZI), la valida y devuelve en una lista.
    Si es un ZIP, lo descomprime en un directorio temporal, extrae todas las imágenes
    soportadas, las ordena alfabéticamente y las devuelve. Si es un directorio, busca
    imágenes soportadas recursivamente.

    Returns:
        Lista de rutas a las imágenes procesables.

    Raises:
        ValueError: Si la entrada no es soportada o no contiene imágenes válidas.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"No existe la entrada: {input_path}")

    if input_path.is_dir():
        return _collect_images_from_dir(input_path)

    # Caso 1: Archivo individual (no ZIP)
    if input_path.suffix.lower() != ".zip":
        # Verificar que sea una extensión de imagen soportada
        if input_path.suffix.lower() not in IO_IMAGE_EXTS:
            raise ValueError("Archivo no soportado. Usa una imagen TIFF/CZI, un ZIP o un directorio con imagenes.")
        # Devolver la imagen como lista de un elemento
        return [input_path]

    # Caso 2: Archivo ZIP - descomprimir y extraer imágenes
    unzip_dir = job_temp_dir / "unzipped"
    # Limpiar directorio si existe
    if unzip_dir.exists():
        shutil.rmtree(unzip_dir)
    unzip_dir.mkdir(parents=True, exist_ok=True)

    # Extraer todo el contenido del ZIP
    _extract_zip_safely(input_path, unzip_dir)

    # Buscar recursivamente todas las imágenes con extensiones soportadas
    images = [p for p in unzip_dir.rglob("*") if p.is_file() and p.suffix.lower() in IO_IMAGE_EXTS]
    # Ordenar alfabéticamente por ruta completa (case-insensitive)
    images.sort(key=lambda p: str(p).lower())
    # Verificar que se encontraron imágenes
    if not images:
        raise ValueError("El ZIP no contiene imagenes soportadas (tif/tiff/czi).")

    return images

def _convert_to_tiff(img: np.ndarray) -> np.ndarray:
    """
    Convierte una imagen NumPy a formato uint16 para guardar como TIFF.

    Esta función normaliza todo a uint16 (0-65535) que es estándar para TIFF,
    permitiendo rango dinámico completo de 16 bits.

    Args:
        img: Array 2D NumPy, puede ser float o integer.

    Returns:
        Array 2D uint16 normalizado.
    """
    if np.issubdtype(img.dtype, np.floating):
        # Para floats: normalizar al rango completo de uint16
        img = np.nan_to_num(img, nan=0.0, posinf=0.0, neginf=0.0)
        vmin, vmax = float(img.min()), float(img.max())
        if vmax > vmin:
            img = ((img - vmin) / (vmax - vmin) * 65535.0).astype(np.uint16)
        else:
            img = np.zeros_like(img, dtype=np.uint16)
    elif img.dtype != np.uint16:
        # Para otros tipos: convertir directamente
        img = img.astype(np.uint16, copy=False)
    return img


def _counts_to_csv_cell(values: object) -> str:
    if isinstance(values, (list, tuple)):
        return "|".join(str(x) for x in values)
    return str(values or "")


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _write_metrics_csvs(export_root: Path, summary: dict[str, object], image_metrics: list[dict[str, object]]) -> None:
    general_fields = [
        "job_id",
        "imagenes_procesadas",
        "total_celulas",
        "total_parasitos",
        "total_parasitos_asignados",
        "total_parasitos_no_asignados",
        "total_celulas_infectadas",
        "promedio_parasitos_por_celula",
    ]

    general_row = {
        "job_id": summary.get("job_id", ""),
        "imagenes_procesadas": int(summary.get("imagenes_procesadas", 0)),
        "total_celulas": int(summary.get("total_celulas", 0)),
        "total_parasitos": int(summary.get("total_parasitos", 0)),
        "total_parasitos_asignados": int(summary.get("total_parasitos_asignados", 0)),
        "total_parasitos_no_asignados": int(summary.get("total_parasitos_no_asignados", 0)),
        "total_celulas_infectadas": int(summary.get("total_celulas_infectadas", 0)),
        "promedio_parasitos_por_celula": float(summary.get("promedio_parasitos_por_celula", 0.0)),
    }
    _write_csv(export_root / "metricas_generales.csv", general_fields, [general_row])

    image_fields = [
        "job_id",
        "image_id",
        "source_filename",
        "total_celulas",
        "total_parasitos",
        "parasitos_asignados",
        "parasitos_no_asignados",
        "celulas_infectadas",
        "promedio_parasitos_por_celula",
        "parasitos_por_celula",
    ]
    image_rows = [
        {
            "job_id": m.get("job_id", ""),
            "image_id": m.get("image_id", ""),
            "source_filename": m.get("source_filename", ""),
            "total_celulas": int(m.get("total_celulas", 0)),
            "total_parasitos": int(m.get("total_parasitos", 0)),
            "parasitos_asignados": int(m.get("parasitos_asignados", 0)),
            "parasitos_no_asignados": int(m.get("parasitos_no_asignados", 0)),
            "celulas_infectadas": int(m.get("celulas_infectadas", 0)),
            "promedio_parasitos_por_celula": float(m.get("promedio_parasitos_por_celula", 0.0)),
            "parasitos_por_celula": _counts_to_csv_cell(m.get("parasitos_por_celula", [])),
        }
        for m in image_metrics
    ]
    _write_csv(export_root / "metricas_por_imagen.csv", image_fields, image_rows)

    legacy_metrics = export_root / "metrics.csv"
    if legacy_metrics.is_file():
        legacy_metrics.unlink()


def _load_accepted_quality_ids(report_path: Path) -> set[str]:
    if not report_path.exists():
        raise FileNotFoundError("No hay reporte de control de calidad. Ejecuta la revision de calidad primero.")

    accepted: set[str] = set()
    with report_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            status = str(row.get("estado", "")).strip().lower()
            image_id = str(row.get("image_id", "")).strip()
            if status == "usable" and image_id:
                accepted.add(image_id)

    if not accepted:
        raise ValueError("El control de calidad no encontro imagenes usables para procesar.")

    return accepted


def _load_preprocess_cell_mask(folder: Path, image_shape: tuple[int, ...]) -> np.ndarray | None:
    mask_path = folder / "cell_mask_preprocess.tiff"
    if not mask_path.exists():
        return None

    cells_lab = tifffile.imread(str(mask_path))
    if cells_lab.shape != image_shape:
        return None

    return cells_lab.astype(np.int32, copy=False)


def _build_infected_overlay(
    preview: np.ndarray, 
    cells_lab: np.ndarray, 
    parasites_per_cell: list[int] | np.ndarray,
    border_width: int = 1
) -> np.ndarray:
    """
    Crea un overlay RGB marcando en rojo el contorno de células infectadas.

    Superpone marcas rojas sobre la preview de la imagen original para
    resaltar visualmente qué células contienen parásitos. Útil para
    verificación rápida de resultados.

    Args:
        preview_rgb: Preview RGB de la imagen original (Y, X, 3) uint8.
        cells_lab: Máscara de células con IDs únicos (Y, X) int32.
        parasites_per_cell: Lista/array con conteo de parásitos por célula.
            Índice i corresponde a célula con ID (i+1).

    Returns:
        Array RGB (Y, X, 3) uint8 con contornos rojos sobre células infectadas.
        Células no infectadas se ven normales, infectadas tienen borde rojo.
    """
    overlay = preview.copy()
    counts = np.asarray(parasites_per_cell, dtype=int)
    if counts.size == 0:
        return overlay

    infected_ids = np.where(counts > 0)[0] + 1
    if infected_ids.size == 0:
        return overlay

    infected_mask = np.isin(cells_lab, infected_ids)
    if not infected_mask.any():
        return overlay

    size = 2 * border_width + 1
    structure = np.ones((size, size), dtype=bool)
    eroded = binary_erosion(infected_mask, structure=structure, border_value=0)
    border = infected_mask & ~eroded
    overlay[border] = np.array([255, 0, 0], dtype=np.uint8)
    return overlay

def run_pipeline_from_input(
    input_path: Path,
    job_output_dir: Path,
    job_temp_dir: Path,
    job_id: str,
    accepted_image_ids: set[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Path, list[dict[str, object]], dict[str, object]]:
    """progress(done, total) se llama al empezar y después de cada imagen."""
    input_path = Path(input_path)
    job_output_dir = Path(job_output_dir)
    job_temp_dir = Path(job_temp_dir)

    job_output_dir.mkdir(parents=True, exist_ok=True)
    job_temp_dir.mkdir(parents=True, exist_ok=True)

    images = _resolve_input_images(input_path, job_temp_dir)
    image_entries = [(i, img_path) for i, img_path in enumerate(images, start=1)]
    if accepted_image_ids is not None:
        image_entries = [
            (i, img_path)
            for i, img_path in image_entries
            if f"{i:04d}" in accepted_image_ids
        ]
        if not image_entries:
            raise ValueError("Ninguna imagen de entrada coincide con las imagenes usables del control de calidad.")

    export_root = job_output_dir / f"job_{job_id}"
    images_root = export_root / "images"
    images_root.mkdir(parents=True, exist_ok=True)

    all_metrics: list[dict[str, object]] = []
    preview_items: list[dict[str, object]] = []

    if progress:
        progress(0, len(image_entries))

    for i, img_path in image_entries:
        img_id = f"{i:04d}"
        folder = images_root / f"{img_id}__{img_path.stem}"
        folder.mkdir(parents=True, exist_ok=True)

        img2d = load_image_2d(img_path)

        img_out = _convert_to_tiff(img2d)
        tifffile.imwrite(str(folder / "input.tiff"), img_out)

        preview = build_input_preview(img2d, colormap="viridis")
        save_preview(folder / "input_preview.png", preview)

        cells_lab = _load_preprocess_cell_mask(folder, img2d.shape)
        if cells_lab is None:
            cells_lab = segment_cells(img2d)
        #save_preview(folder / "cell_mask_raw_preview.png", build_instance_preview(cells_lab))

        #cell_areas = compute_instance_areas(cells_lab)
        adaptive_cell_min = CELL_MIN_AREA
        #if cell_areas.size > 0:
        #    adaptive_cell_min = max(
        #        CELL_MIN_AREA,
        #        int(np.percentile(cell_areas, CELL_MIN_AREA_PERCENTILE)),
        #    )
        cells_lab = filter_cells_by_area(
            cells_lab,
            min_area=CELL_MIN_AREA,
            max_elongation=CELL_MAX_ELONGATION,
        )

        parasites_lab, _ = segment_parasites(img2d)
        #parasite_areas = compute_instance_areas(parasites_lab)
        adaptive_parasite_max = PARASITE_MAX_AREA
        #if parasite_areas.size > 0:
        #    adaptive_parasite_max = min(
        #        PARASITE_MAX_AREA,
        #        int(np.percentile(parasite_areas, PARASITE_MAX_AREA_PERCENTILE)),
        #    )
        parasites_lab = filter_parasites_by_area(parasites_lab, max_area=adaptive_parasite_max)

        parasites_lab = merge_parasites(parasites_lab, merge_radius=2)

        tifffile.imwrite(str(folder / "cell_mask.tiff"), cells_lab.astype("uint16"))
        tifffile.imwrite(str(folder / "parasite_mask.tiff"), parasites_lab.astype("uint16"))
        save_preview(folder / "cell_mask_preview.png", build_instance_preview(cells_lab))
        save_preview(folder / "parasite_mask_preview.png", build_instance_preview(parasites_lab))

        metrics = {
            "job_id": job_id,
            "image_id": img_id,
            "source_filename": img_path.name,
            **compute_metrics(
                cells_lab,
                parasites_lab,
                assign_sigma=PARASITE_ASSIGN_SIGMA,
                assign_threshold=PARASITE_ASSIGN_THRESHOLD,
                cluster_reassignment=PARASITE_CLUSTER_REASSIGNMENT,
                cluster_radius=PARASITE_CLUSTER_RADIUS,
                cluster_min_size=PARASITE_CLUSTER_MIN_SIZE,
                cluster_margin=PARASITE_CLUSTER_MARGIN,
            ),
        }
        all_metrics.append(metrics)

        save_histogram(
            folder / "histograma_parasitos_por_celula.png",
            metrics.get("parasitos_por_celula", []),
            title=f"Parásitos por celula - {img_path.name}",
        )

        preview_items.append(
            {
                "title": folder.name,
                "folder_name": folder.name,
                "metrics": {
                    "total_celulas": int(metrics.get("total_celulas", 0)),
                    "total_parasitos": int(metrics.get("total_parasitos", 0)),
                    "celulas_infectadas": int(metrics.get("celulas_infectadas", 0)),
                    "parasitos_no_asignados": int(metrics.get("parasitos_no_asignados", 0)),
                    "parasitos_asignados": int(metrics.get("parasitos_asignados", 0)),
                    "promedio_parasitos_por_celula": float(metrics.get("promedio_parasitos_por_celula", 0.0)),
                    "parasitos_por_celula": metrics.get("parasitos_por_celula", []),
                },
            }
        )

        infected_overlay = _build_infected_overlay(
            preview=preview,
            cells_lab=cells_lab,
            parasites_per_cell=metrics.get("parasitos_por_celula", []), border_width=2
        )
        save_preview(folder / "infected_overlay.png", infected_overlay)

        if progress:
            progress(len(preview_items), len(image_entries))

    summary = summarize_job(all_metrics)
    summary_row = {"job_id": job_id, **summary}
    all_parasites_per_cell = [
        int(value)
        for metrics in all_metrics
        for value in metrics.get("parasitos_por_celula", [])
    ]
    save_histogram(
        export_root / "histograma_global_parasitos_por_celula.png",
        all_parasites_per_cell,
        title="Parásitos por celula - Totales",
    )
    _write_metrics_csvs(export_root, summary_row, all_metrics)

    zip_path = job_output_dir / f"results_{job_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in export_root.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(job_output_dir)))

    return zip_path, preview_items, summary_row


def run_pipeline(
    job_id: str,
    accepted_only: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Path, list[dict[str, object]], dict[str, object]]:
    job_upload_dir = settings.uploads_dir / job_id
    job_output_dir = settings.outputs_dir / job_id
    job_temp_dir = settings.processing_dir / job_id

    uploaded_files = [p for p in job_upload_dir.iterdir() if p.is_file()]
    if not uploaded_files:
        raise FileNotFoundError("No hay archivo subido para este job.")

    uploaded = uploaded_files[0]
    accepted_image_ids = None
    if accepted_only:
        report_path = job_output_dir / f"job_{job_id}" / "control_calidad_por_imagen.csv"
        accepted_image_ids = _load_accepted_quality_ids(report_path)

    return run_pipeline_from_input(
        input_path=uploaded,
        job_output_dir=job_output_dir,
        job_temp_dir=job_temp_dir,
        job_id=job_id,
        accepted_image_ids=accepted_image_ids,
        progress=progress,
    )

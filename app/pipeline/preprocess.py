from __future__ import annotations

import csv
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

import numpy as np
import tifffile
from scipy.ndimage import binary_erosion

from app.core.config import settings
from app.pipeline.cellpose import segment_cells
from app.pipeline.io import load_image_2d
from app.pipeline.postprocess import filter_cells_by_area
from app.pipeline.previews import build_input_preview, build_instance_preview, save_preview
from app.pipeline.runner import _convert_to_tiff, _resolve_input_images

logger = logging.getLogger("parasight.preprocess")


QUALITY_MIN_CELL_AREA = int(os.getenv("QUALITY_MIN_CELL_AREA", "700"))
QUALITY_MIN_VALID_CELLS = int(os.getenv("QUALITY_MIN_VALID_CELLS", "3"))
QUALITY_MIN_VALID_CELL_RATIO = float(os.getenv("QUALITY_MIN_VALID_CELL_RATIO", "0.35"))
QUALITY_MIN_SCORE = float(os.getenv("QUALITY_MIN_SCORE", "0.45"))
QUALITY_MIN_BBOX_FILL = float(os.getenv("QUALITY_MIN_BBOX_FILL", "0.25"))
QUALITY_MAX_ASPECT_RATIO = float(os.getenv("QUALITY_MAX_ASPECT_RATIO", "4.0"))
QUALITY_MIN_CIRCULARITY = float(os.getenv("QUALITY_MIN_CIRCULARITY", "0.05"))


def _cell_border(mask: np.ndarray) -> np.ndarray:
    if not mask.any():
        return mask
    eroded = binary_erosion(mask, structure=np.ones((3, 3), dtype=bool), border_value=0)
    return mask & ~eroded


def _cell_features(cells_lab: np.ndarray) -> list[dict[str, object]]:
    total = int(cells_lab.max()) if cells_lab.size else 0
    if total == 0:
        return []

    height, width = cells_lab.shape
    features: list[dict[str, object]] = []

    for cid in range(1, total + 1):
        ys, xs = np.where(cells_lab == cid)
        area = int(ys.size)
        if area == 0:
            continue

        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        bbox_h = max(y1 - y0 + 1, 1)
        bbox_w = max(x1 - x0 + 1, 1)
        bbox_area = bbox_h * bbox_w
        bbox_fill = float(area / bbox_area)
        aspect_ratio = float(max(bbox_h, bbox_w) / max(min(bbox_h, bbox_w), 1))
        touches_border = bool(y0 == 0 or x0 == 0 or y1 == height - 1 or x1 == width - 1)

        local_mask = cells_lab[y0 : y1 + 1, x0 : x1 + 1] == cid
        perimeter = int(_cell_border(local_mask).sum())
        circularity = float((4.0 * np.pi * area) / max(perimeter * perimeter, 1))

        fill_score = min(max(bbox_fill / max(QUALITY_MIN_BBOX_FILL, 1e-6), 0.0), 1.0)
        aspect_score = min(max(QUALITY_MAX_ASPECT_RATIO / max(aspect_ratio, 1e-6), 0.0), 1.0)
        circularity_score = min(max(circularity / max(QUALITY_MIN_CIRCULARITY, 1e-6), 0.0), 1.0)
        shape_score = float((fill_score + aspect_score + circularity_score) / 3.0)

        valid = (
            area >= QUALITY_MIN_CELL_AREA
            and bbox_fill >= QUALITY_MIN_BBOX_FILL
            and aspect_ratio <= QUALITY_MAX_ASPECT_RATIO
            and circularity >= QUALITY_MIN_CIRCULARITY
        )

        features.append(
            {
                "cell_id": cid,
                "area": area,
                "bbox_fill": bbox_fill,
                "aspect_ratio": aspect_ratio,
                "circularity": circularity,
                "touches_border": touches_border,
                "shape_score": shape_score,
                "valid": bool(valid),
            }
        )

    return features


def evaluate_image_quality(img2d: np.ndarray, cells_lab: np.ndarray) -> dict[str, object]:
    features = _cell_features(cells_lab)
    total_cells = len(features)
    valid_features = [f for f in features if bool(f["valid"])]
    valid_cells = len(valid_features)
    valid_ratio = float(valid_cells / total_cells) if total_cells else 0.0
    mean_shape_score = float(np.mean([float(f["shape_score"]) for f in features])) if features else 0.0

    enough_valid_cells = min(valid_cells / max(QUALITY_MIN_VALID_CELLS * 2, 1), 1.0)
    score = float((0.45 * valid_ratio) + (0.35 * enough_valid_cells) + (0.20 * mean_shape_score))

    reasons: list[str] = []
    if total_cells == 0:
        reasons.append("sin celulas detectables")
    if valid_cells < QUALITY_MIN_VALID_CELLS:
        reasons.append("pocas celulas validas")
    if total_cells > 0 and valid_ratio < QUALITY_MIN_VALID_CELL_RATIO:
        reasons.append("morfologia celular mala")
    if score < QUALITY_MIN_SCORE:
        reasons.append("score de calidad bajo")

    usable = not reasons

    return {
        "usable": usable,
        "quality_score": score,
        "total_cells": total_cells,
        "valid_cells": valid_cells,
        "invalid_cells": max(total_cells - valid_cells, 0),
        "valid_cell_ratio": valid_ratio,
        "mean_shape_score": mean_shape_score,
        "rejection_reason": " / ".join(reasons),
        "cell_features": features,
    }


def build_quality_overlay(preview: np.ndarray, cells_lab: np.ndarray, quality: dict[str, object]) -> np.ndarray:
    overlay = preview.copy()
    features = quality.get("cell_features", [])
    if not isinstance(features, list):
        return overlay

    for feature in features:
        cid = int(feature.get("cell_id", 0))
        if cid <= 0:
            continue
        border = _cell_border(cells_lab == cid)
        if not border.any():
            continue
        color = np.array([78, 220, 124], dtype=np.uint8) if feature.get("valid") else np.array([255, 143, 67], dtype=np.uint8)
        overlay[border] = color

    return overlay


def _write_quality_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "job_id",
        "image_id",
        "source_filename",
        "estado",
        "quality_score",
        "mascaras_cellpose",
        "celulas_detectadas",
        "celulas_validas",
        "celulas_invalidas",
        "proporcion_celulas_validas",
        "score_morfologia_promedio",
        "motivo_descarte",
        "error",
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _preprocess_one_image(
    img_path: Path,
    img_id: str,
    folder: Path,
    job_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    img2d = load_image_2d(img_path)
    preview = build_input_preview(img2d, colormap="viridis")
    cells_raw_lab = segment_cells(img2d)
    raw_cell_masks = int(cells_raw_lab.max()) if cells_raw_lab.size else 0
    cells_lab = filter_cells_by_area(cells_raw_lab, min_area=QUALITY_MIN_CELL_AREA)
    quality = evaluate_image_quality(img2d, cells_lab)

    tifffile.imwrite(str(folder / "input.tiff"), _convert_to_tiff(img2d))
    tifffile.imwrite(str(folder / "cell_mask_preprocess.tiff"), cells_lab.astype("uint16"))
    save_preview(folder / "input_preview.png", preview)
    save_preview(folder / "cell_mask_preview.png", build_instance_preview(cells_lab))
    save_preview(folder / "quality_overlay.png", build_quality_overlay(preview, cells_lab, quality))

    status = "usable" if bool(quality["usable"]) else "descartada"
    metrics = {
        "estado": status,
        "quality_score": float(quality["quality_score"]),
        "mascaras_cellpose": raw_cell_masks,
        "celulas_detectadas": int(quality["total_cells"]),
        "celulas_validas": int(quality["valid_cells"]),
        "celulas_invalidas": int(quality["invalid_cells"]),
        "proporcion_celulas_validas": float(quality["valid_cell_ratio"]),
        "score_morfologia_promedio": float(quality["mean_shape_score"]),
        "motivo_descarte": str(quality["rejection_reason"]),
        "error": "",
    }
    csv_row = {
        "job_id": job_id,
        "image_id": img_id,
        "source_filename": img_path.name,
        **metrics,
    }
    preview_item = {
        "title": folder.name,
        "folder_name": folder.name,
        "source_filename": img_path.name,
        "metrics": metrics,
    }
    return csv_row, preview_item


def run_preprocess_from_input(
    input_path: Path,
    job_output_dir: Path,
    job_temp_dir: Path,
    job_id: str,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object], Path]:
    """progress(done, total) se llama al empezar y después de cada imagen."""
    input_path = Path(input_path)
    job_output_dir = Path(job_output_dir)
    job_temp_dir = Path(job_temp_dir)

    job_output_dir.mkdir(parents=True, exist_ok=True)
    job_temp_dir.mkdir(parents=True, exist_ok=True)

    images = _resolve_input_images(input_path, job_temp_dir)
    export_root = job_output_dir / f"job_{job_id}"
    images_root = export_root / "images"
    images_root.mkdir(parents=True, exist_ok=True)

    preview_items: list[dict[str, object]] = []
    csv_rows: list[dict[str, object]] = []

    if progress:
        progress(0, len(images))

    failed_images: list[dict[str, object]] = []

    for i, img_path in enumerate(images, start=1):
        img_id = f"{i:04d}"
        folder = images_root / f"{img_id}__{img_path.stem}"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            csv_row, preview_item = _preprocess_one_image(img_path, img_id, folder, job_id)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            logger.exception("Image %s failed quality control in job %s", img_path, job_id)
            shutil.rmtree(folder, ignore_errors=True)
            failed = {
                "job_id": job_id,
                "image_id": img_id,
                "source_filename": img_path.name,
                "error": error,
            }
            failed_images.append(failed)
            csv_rows.append(
                {
                    **failed,
                    "estado": "error",
                    "quality_score": "",
                    "mascaras_cellpose": "",
                    "celulas_detectadas": "",
                    "celulas_validas": "",
                    "celulas_invalidas": "",
                    "proporcion_celulas_validas": "",
                    "score_morfologia_promedio": "",
                    "motivo_descarte": "",
                }
            )
        else:
            csv_rows.append(csv_row)
            preview_items.append(preview_item)
        finally:
            if progress:
                progress(i, len(images))

    accepted = sum(1 for row in csv_rows if row["estado"] == "usable")
    rejected = sum(1 for row in csv_rows if row["estado"] == "descartada")
    scored_rows = [row for row in csv_rows if row["estado"] != "error"]
    summary = {
        "job_id": job_id,
        "imagenes_revisadas": len(csv_rows),
        "imagenes_usables": accepted,
        "imagenes_descartadas": rejected,
        "imagenes_con_error": len(failed_images),
        "errores_imagenes": failed_images,
        "quality_score_promedio": (
            float(np.mean([float(row["quality_score"]) for row in scored_rows])) if scored_rows else 0.0
        ),
    }

    report_path = export_root / "control_calidad_por_imagen.csv"
    _write_quality_csv(report_path, csv_rows)

    return preview_items, summary, report_path


def run_preprocess(
    job_id: str,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object], Path]:
    job_upload_dir = settings.uploads_dir / job_id
    job_output_dir = settings.outputs_dir / job_id
    job_temp_dir = settings.processing_dir / job_id

    uploaded_files = [p for p in job_upload_dir.iterdir() if p.is_file()]
    if not uploaded_files:
        raise FileNotFoundError("No hay archivo subido para este job.")

    uploaded = uploaded_files[0]
    return run_preprocess_from_input(
        input_path=uploaded,
        job_output_dir=job_output_dir,
        job_temp_dir=job_temp_dir,
        job_id=job_id,
        progress=progress,
    )

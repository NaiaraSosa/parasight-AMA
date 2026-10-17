import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile

from app.pipeline.io import load_image_2d
from app.pipeline.histograms import _split_counts
from app.pipeline import metrics as metrics_module, runner
from app.pipeline.stardist import _validate_stardist_input


class ImageValidationTests(unittest.TestCase):
    def test_stardist_rejects_image_without_percentile_range(self):
        image = np.zeros((32, 32), dtype=np.uint16)

        with self.assertRaisesRegex(ValueError, "rango de intensidad"):
            _validate_stardist_input(image)

    def test_stardist_rejects_sparse_channel_like_crashing_export(self):
        image = np.zeros((100, 100), dtype=np.uint8)
        image.flat[:10] = 255

        with self.assertRaisesRegex(ValueError, "rango de intensidad"):
            _validate_stardist_input(image)

    def test_rgb_tiff_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "export.tif"
            tifffile.imwrite(path, np.zeros((16, 16, 3), dtype=np.uint8), photometric="rgb")

            with self.assertRaisesRegex(ValueError, "TIFF RGB"):
                load_image_2d(path)

    def test_histogram_reports_zeros_separately(self):
        positive_values, zero_count = _split_counts([0, 3, 0, 1, 8])

        self.assertEqual(positive_values, [3, 1, 8])
        self.assertEqual(zero_count, 2)


class BatchResilienceTests(unittest.TestCase):
    def test_bad_image_is_recorded_and_next_image_is_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            (input_dir / "01_bad.tif").touch()
            (input_dir / "02_good.tif").touch()

            progress_updates = []

            def fake_process(img_path, img_id, folder, job_id):
                if img_path.name == "01_bad.tif":
                    raise ValueError("imagen de prueba invalida")
                metrics = {
                    "job_id": job_id,
                    "image_id": img_id,
                    "source_filename": img_path.name,
                    "total_celulas": 1,
                    "total_parasitos": 2,
                    "parasitos_asignados": 2,
                    "parasitos_no_asignados": 0,
                    "celulas_infectadas": 1,
                    "porcentaje_infeccion": 100.0,
                    "promedio_parasitos_por_celula": 2,
                    "parasitos_por_celula": [2],
                }
                return metrics, {"title": folder.name, "folder_name": folder.name, "metrics": {}}

            with (
                patch.object(runner, "_process_one_image", side_effect=fake_process),
                patch.object(runner, "save_histogram"),
                patch.object(runner.time, "perf_counter", side_effect=[100.0, 112.34]),
            ):
                zip_path, previews, summary = runner.run_pipeline_from_input(
                    input_path=input_dir,
                    job_output_dir=root / "output",
                    job_temp_dir=root / "processing",
                    job_id="test-job",
                    progress=lambda done, total: progress_updates.append((done, total)),
                )

            self.assertTrue(zip_path.exists())
            self.assertEqual(len(previews), 1)
            self.assertEqual(summary["imagenes_procesadas"], 1)
            self.assertEqual(summary["imagenes_con_error"], 1)
            self.assertEqual(summary["tiempo_procesamiento_segundos"], 12.34)
            self.assertEqual(summary["errores_imagenes"][0]["source_filename"], "01_bad.tif")
            self.assertEqual(progress_updates[-1], (2, 2))

            csv_path = root / "output" / "job_test-job" / "metricas_por_imagen.csv"
            with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file, delimiter=";"))

            self.assertEqual([row["estado"] for row in rows], ["error", "procesada"])
            self.assertEqual(rows[0]["error"], "imagen de prueba invalida")
            self.assertEqual(rows[1]["porcentaje_infeccion"], "100.0")

            general_csv_path = root / "output" / "job_test-job" / "metricas_generales.csv"
            with general_csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
                general_row = next(csv.DictReader(csv_file, delimiter=";"))

            self.assertEqual(general_row["porcentaje_infeccion"], "100.0")
            self.assertEqual(general_row["tiempo_procesamiento_segundos"], "12.34")


class InfectionPercentageTests(unittest.TestCase):
    def test_percentage_is_calculated_per_image(self):
        assignment = {
            "infected_cells": 1,
            "parasites_per_cell": [2, 0],
            "assigned_parasites": 2,
            "unassigned_parasites": 0,
        }
        with patch.object(metrics_module, "assign_parasites", return_value=assignment):
            result = metrics_module.compute_metrics(
                np.array([[1, 2]], dtype=np.int32),
                np.array([[1, 2]], dtype=np.int32),
            )

        self.assertEqual(result["porcentaje_infeccion"], 50.0)

    def test_global_percentage_uses_total_cells(self):
        summary = metrics_module.summarize_job([
            {"total_celulas": 10, "celulas_infectadas": 5},
            {"total_celulas": 90, "celulas_infectadas": 9},
        ])

        self.assertEqual(summary["porcentaje_infeccion"], 14.0)

    def test_percentage_is_undefined_without_cells(self):
        summary = metrics_module.summarize_job([])

        self.assertIsNone(summary["porcentaje_infeccion"])


if __name__ == "__main__":
    unittest.main()

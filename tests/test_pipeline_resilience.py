import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile

from app.pipeline.io import load_image_2d
from app.pipeline.histograms import _split_counts
from app.pipeline import runner
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
                    "promedio_parasitos_por_celula": 2,
                    "parasitos_por_celula": [2],
                }
                return metrics, {"title": folder.name, "folder_name": folder.name, "metrics": {}}

            with (
                patch.object(runner, "_process_one_image", side_effect=fake_process),
                patch.object(runner, "save_histogram"),
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
            self.assertEqual(summary["errores_imagenes"][0]["source_filename"], "01_bad.tif")
            self.assertEqual(progress_updates[-1], (2, 2))

            csv_path = root / "output" / "job_test-job" / "metricas_por_imagen.csv"
            with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file, delimiter=";"))

            self.assertEqual([row["estado"] for row in rows], ["error", "procesada"])
            self.assertEqual(rows[0]["error"], "imagen de prueba invalida")


if __name__ == "__main__":
    unittest.main()

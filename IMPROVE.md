# Pending improvements

Open issues in the web app, most severe first. Deployment is covered in [DEPLOY.md](DEPLOY.md). Fixed items are removed from this list; see the git history for what was done and how it was tested.

## Current design (context)

- One uvicorn process with **one worker** (the models are loaded once per process).
- Jobs run in a **background queue, one at a time** (`app/services/jobs.py`). Submitting a job redirects to `/jobs/{id}`, which shows queue position, progress and then the results. The state is kept on disk in `outputs/<id>/status.json` and `result.json`.
- Per-job folders: `uploads/<id>/` (original file), `outputs/<id>/` (results), `processing/<id>/` (working files, deleted when the job ends).

---

## 1. An image with no intensity range crashes the whole service (critical)

**Symptom:** the service dies with `code=dumped, status=6/ABRT` in the journal, systemd restarts it, and the job shows as failed with "Interrumpido por un reinicio del servidor". Resubmitting the same job crashes again at the same image. Any other user's running job is lost too.

**Cause (reproduced):** in the ZIP that crashed on 2026-09-25 (194 images, both times after image 18), image 19 was `Markers_Counter Window - Snap-890.czi - C=0.tif`. The pipeline uses channel 0 of that RGB image, which is 99.8% zeros, so its 2nd and 99.8th percentiles are both 0. StarDist normalizes with `PercentileNormalizer` (`pmin=2, pmax=99.8`), effectively dividing by zero (csbdeep adds only a 1e-20 guard). The network then outputs NaN/inf distances, and StarDist's C++ non-maximum suppression (ClipperLib) throws:

```
terminate called after throwing an instance of 'ClipperLib::clipperException'
  what():  Coordinate outside allowed range
Fatal Python error: Aborted
```

The exception escapes the compiled extension, so C++ terminates the process. Python can't catch it. It is not a CUDA problem: Cellpose (GPU) finished the same image normally, and StarDist runs on the CPU.

**Fix:**
- In `segment_parasites` (`app/pipeline/stardist.py`), compute `np.percentile(img, [2, 99.8])` first. If the range is zero (or not finite), skip StarDist and return empty labels (0 parasites).
- Defense in depth: call the model's `predict` and `_instances_from_prediction` separately, and replace non-finite `prob`/`dist` values before the polygon NMS runs, so another degenerate input can't reach ClipperLib either.
- Optional hard isolation: run inference in a child process, so that any native crash fails only that image or job, not the web server. It costs a model load per process, so do this only if other native crashes appear.

## 2. One bad image fails the whole batch (high)

Any exception while processing an image (unreadable file, unexpected shape, and so on) aborts the whole job, and the results already computed for the other images are not delivered.

**Fix:** catch errors per image in `run_pipeline_from_input` and `run_preprocess_from_input`. Record the image as failed, with the error, in `metricas_por_imagen.csv` and on the results page, and continue with the next image. Fail the job only if every image failed.

## 3. RGB exports and non-image files in ZIPs (high; needs a decision)

The crashing ZIP mixed 102 raw `.czi` files, 92 `Markers_Counter Window - … .tif` files (RGB 8-bit exports from ImageJ's Cell Counter, with the markers drawn in), and some `.docx`, `.pdf` and `.xlsx` files. The app:
- processes **every** `.tif`, `.tiff` and `.czi` in the ZIP, including those exports;
- turns a `(Y, X, 3)` RGB image into 2D by taking **channel 0 (red)** (`extract_2d_frame` in `app/pipeline/io.py`), which in these exports is almost empty. Their results are probably meaningless even when they don't crash.

**Options (to decide):**
- skip files that look like annotated exports (e.g. names starting with `Markers_Counter Window`), and list them as skipped;
- convert RGB to grayscale properly, or let the user choose the channel;
- warn on the upload or results page when a batch mixes RGB 8-bit exports with raw microscopy files.

## 4. Crash vs restart: misleading message (medium)

After a crash, systemd restarts the service and the job is marked "Interrumpido por un reinicio del servidor". That wording blames a restart. It should say "Se interrumpió (reinicio o falla del servidor)", and the log should record which job was running when the process died. Optionally, if a job is found `running` at startup a second time, mark it as "falló dos veces" and refuse automatic retries.

## 5. Default upload limit rejects large batches in local use (medium)

`MAX_UPLOAD_MB` defaults to 500 in `app/core/config.py` and is now enforced. A plain `git clone` + `parasight web` (no configuration) rejects a 600 MB ZIP with HTTP 413, which it used to accept. The deploy config raises the limit to 20 GB.

**Fix:** default to `0` (no limit), keeping the explicit value in `deploy/parasight.env.example`.

## 6. No authentication (security)

Anyone who can reach the port can upload images and open or download any job whose ID they know. Job IDs are random UUIDs, so they can't be guessed, but links can be shared. If the app becomes reachable beyond a trusted network, put HTTP basic auth (or institutional login) in front of it at a reverse proxy (DEPLOY.md, section 6).

## 7. Operational limitations

- **The upload limit is checked after spooling.** The whole body is written to `$TMPDIR` before `MAX_UPLOAD_MB` is checked. To reject oversized uploads earlier, cap them at a reverse proxy (`client_max_body_size`).
- **A restart interrupts the running job.** It is marked as failed and must be retried by hand. Queued jobs resume automatically. Resuming a partially processed job isn't supported.
- **Expired job links give a bare 404.** The cleanup job deletes jobs after 30 days, but job links are meant to be bookmarked. `/jobs/{id}` could say "este job expiró" instead of "Job no encontrado".

## 8. Not yet tested

- **Windows** (covered by the README install steps). Nothing Linux-specific was added to the app itself, but no one has run it there.
- **In a real browser:** the "Copiar ID" / "Copiar enlace" buttons (both the `navigator.clipboard` path on HTTPS/localhost and the fallback on plain `http://<ip>`), and drag-and-drop on the upload page.

---

## Design note: why not more workers?

`uvicorn --workers N` (or gunicorn) starts N separate processes. For this app:
- **Each worker loads its own copy of the models**, multiplying GPU and RAM use.
- **Workers share no state**, so the in-process job queue would not work across them without an external store.
- **The GPU is still the bottleneck.** Parallel jobs mostly queue up on it anyway.

Keep `--workers 1`. Only once there are many users, or several GPUs, is it worth moving to a separate worker process per GPU fed by an external queue (e.g. Redis).

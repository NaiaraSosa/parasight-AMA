# Improvements: concurrency and robustness

An analysis of how the web app behaves with several users at once (2–3), with fixes in priority order. It started from reading the code; the results of testing the fixes are in [Verification](#verification). Deployment itself is covered in [DEPLOY.md](DEPLOY.md).

## Status

| Fix | State |
|---|---|
| 1. Non-blocking upload (+ `MAX_UPLOAD_MB` enforced, filename sanitized, format checked) | **Done** |
| 2. One job at a time (`PIPELINE_LOCK`) | **Done** |
| 3. Reject duplicate runs of the same job | **Done** (now: redirect to the job's status page) |
| 4. Thread-safe histograms (no pyplot) and model loading | **Done** (models still load on first use) |
| 5. Background jobs + status page | **Done** |
| 6. Template paths relative to the code | **Done** (authentication: to do) |
| 7. Speed up `compute_metrics` | To do (new, see problem 8) |

Also done: job IDs in URLs must be UUIDs (404 otherwise), so values like `..` can't reach the filesystem.

## Summary

**The stack (FastAPI + a single uvicorn process) has plenty of capacity for 2–3 concurrent users.** The problems at that load come from how the app uses it:

- the upload handler blocks the event loop,
- processing runs synchronously inside the HTTP request,
- the shared models are used from several threads with no coordination.

The fixes are small. More uvicorn workers, gunicorn, or a Celery/Redis queue are **not** needed and would not help at this scale.

---

## How requests are served today

| Route | Kind | Runs on |
|---|---|---|
| `POST /upload` | `async def` | event loop |
| `POST /process/{job_id}`, `/process-accepted/{job_id}`, `/preprocess/{job_id}` | `def` | thread pool (~40 threads) |
| `GET /preview/...`, `/download/...` | `def` | thread pool |

With 3 users processing at once, 3 pipelines run **in parallel threads of the same process**, sharing the Cellpose and StarDist models, which are loaded once and kept in module-level variables (`app/pipeline/cellpose.py`, `app/pipeline/stardist.py`). Each job writes to its own `outputs/<uuid>/` and `processing/<uuid>/` (formerly `temp/`), so **file writes from different jobs don't collide**.

## Problems

### 1. A large upload freezes the app for everyone (high)

`upload_file` (`app/api/routes.py`) is `async def`, but it calls the blocking `shutil.copyfileobj` to copy the uploaded file into `uploads/<uuid>/`. For a multi‑GB ZIP of TIFF/CZI images, this blocks the event loop for the whole copy. Every other user's page loads, preview images and downloads hang until it finishes.

### 2. Refreshing or resubmitting can corrupt a job (high)

Processing runs inside the request and there is no progress page. If a user refreshes (the browser re-POSTs the form) or submits again, **a second pipeline starts on the same `job_id`** while the first is still running. Both write the same folder and the same `results_<uuid>.zip`. Closing the tab doesn't cancel the first run either, because the thread keeps going.

### 3. Several threads use the shared models at once (medium)

- **Loading race:** `_get_cellpose_model()` / `_get_stardist_model()` check `if _MODEL is None` without a lock. Two first requests at the same moment can both load a model, briefly doubling memory/GPU use.
- **Cellpose (PyTorch, GPU):** running the same model from several threads is generally fine. But GPU work from concurrent jobs mostly queues up, so **running 3 jobs together is no faster than running them one after another**. It does use about 3× the working GPU memory, and with large images that can run out.
- **StarDist (TensorFlow/Keras, CPU):** Keras does not guarantee that `predict` is safe to call from several threads. It usually works, which makes it the most likely source of rare, hard-to-reproduce errors. Each call also uses all CPU cores, so concurrent calls compete for the CPU.

### 4. Histograms use pyplot, which isn't thread-safe (medium)

`app/pipeline/histograms.py` uses `plt.subplots()` / `plt.close()`, which go through matplotlib's global figure state. Concurrent jobs can occasionally produce wrong histograms or exceptions.

### 5. The Python-heavy steps don't run in parallel (low)

Post-processing in `app/pipeline/postprocess.py` loops over individual cells, parasites and clusters in Python, which holds Python's global interpreter lock (GIL). Those steps take turns across threads, so each user's job gets slower as more users are active. This is a performance cost, not a correctness bug.

### 6. Long synchronous requests (medium, UX)

Users wait on an open request for the whole job, with no progress indication. A browser or reverse-proxy timeout, or a service restart, loses the result page (the output files stay on disk). There is no way to see or cancel a job in progress.

### 7. Disk use grows with concurrent jobs (operational)

While a job runs it needs about **3× its upload size**: the spooled upload in `$TMPDIR`, the saved copy in `uploads/`, the unzipped copy in `processing/` (formerly `temp/`), then outputs. With 3 users uploading large batches at once, the server needs about 9× the largest batch free. Since the folder rename, the app deletes `processing/<job>` when a job ends; uploads and outputs are removed by the cleanup job in DEPLOY.md.

### 8. `compute_metrics` dominates the time per image (performance)

Measured on the test images (1080×1920, NVIDIA L4), per image:

| Step | Time |
|---|---|
| Cellpose (GPU) | ~3.5 s |
| StarDist (CPU) | ~1 s |
| Filters / merge | ~0.2 s |
| **`compute_metrics`** (parasite→cell assignment, clustering) | **~12–14 s** |

About 70% of the time goes to single-threaded Python loops in `app/pipeline/postprocess.py` / `metrics.py`, not to the models. Vectorizing the assignment (e.g. computing distances/overlaps for all parasites at once with numpy/scipy instead of per parasite) is the biggest available speedup, far more than any server change.

### Other issues (not about concurrency)

- `MAX_UPLOAD_MB` (`app/core/config.py`) is declared but **not enforced**.
- The uploaded filename is used as-is for the saved path (`uploads/<uuid>/<file.filename>`). It should be reduced to its base name.
- **No authentication.** Job UUIDs are the only protection for results.
- `app/main.py` and `app/api/routes.py` load templates/static by **relative path**, so the process must start in the repo root.

---

## Recommended fixes (in priority order)

### Fix 1: Don't block the event loop on upload

The simplest option is to make the handler a plain `def`, so FastAPI runs it in the thread pool:

```python
@router.post("/upload")
def upload_file(request: Request, file: UploadFile = File(...)):
    ...
    file_path = job_upload_dir / Path(file.filename).name   # also strips any directory parts
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer, length=16 * 1024 * 1024)
```

Alternatively, keep `async def` and use `await run_in_threadpool(shutil.copyfileobj, file.file, buffer)`. Enforcing `MAX_UPLOAD_MB` also belongs here: count bytes while copying and return 413 when the limit is exceeded.

### Fix 2: Process one job at a time

Hold a single module-level lock across the whole pipeline, so concurrent jobs wait their turn:

```python
# app/services/jobs.py
import threading
PIPELINE_LOCK = threading.Lock()

# app/pipeline/runner.py (and run_preprocess in preprocess.py)
def run_pipeline(job_id: str, accepted_only: bool = False):
    with PIPELINE_LOCK:
        ...
```

Total throughput stays about the same, because the GPU was already doing the work one piece at a time. In exchange:
- GPU memory use is predictable: one job's worth.
- The model-loading race, the TensorFlow thread-safety risk and the pyplot problem (problems 3 and 4) go away.
- Page loads, previews and downloads stay responsive, since they don't take the lock (together with Fix 1).

Later jobs wait in their own open request. That is acceptable for 2–3 users, and Fix 5 improves it.

### Fix 3: Reject duplicate runs of the same job

```python
_running: set[str] = set()
_running_lock = threading.Lock()

def claim_job(job_id: str) -> bool:
    with _running_lock:
        if job_id in _running:
            return False
        _running.add(job_id)
        return True

def release_job(job_id: str) -> None:
    with _running_lock:
        _running.discard(job_id)
```

In the process routes, return **409 Conflict** ("this job is already being processed") when `claim_job` fails, and call `release_job` in a `finally:` block.

### Fix 4: Make the remaining shared state thread-safe

These are cheap, and they keep the code safe even if the lock in Fix 2 is later relaxed:
- In `histograms.py`, create figures with `matplotlib.figure.Figure()` instead of pyplot:
  ```python
  from matplotlib.figure import Figure
  fig = Figure(figsize=(11, 4), dpi=140)
  axes = fig.subplots(1, 2)
  ...
  fig.savefig(out_path)
  ```
- Put a lock around model loading in `_get_cellpose_model()` / `_get_stardist_model()`, checking `_MODEL is None` both before and after taking the lock.
- Optionally, load both models at startup (`@app.on_event("startup")`) so the first user doesn't wait for the download and load.

### Fix 5: Run jobs in the background, with a status page (done)

Stop running the pipeline inside the request:
1. `POST /process/{job_id}` puts the job in a queue and immediately redirects to `GET /jobs/{job_id}`.
2. A single background worker thread, started on app startup, takes jobs off a `queue.Queue` one at a time (this replaces Fix 2's lock).
3. It writes the status to `outputs/<uuid>/status.json`: queued (with queue position), running (image *n*/*N*), done, or failed with the error.
4. The status page polls it every few seconds and shows the results when done.

This removes the long-open request, makes refresh safe (it just re-reads the status), lets users close the tab and come back, and survives proxy timeouts. At 2–3 users an in-process queue is enough; Celery/Redis would add moving parts without benefit. The one thing it can't do is survive a service restart mid-job. To handle that, mark `running` jobs as `failed` on startup so users can resubmit.

**As implemented** (`app/services/jobs.py`, `app/templates/job_status.html`):
- `POST /process/{id}`, `/process-accepted/{id}` and `/preprocess/{id}` queue the job and answer **303** to `/jobs/{id}` in a few milliseconds. The 303 makes the browser switch to GET, so refreshing never resubmits.
- One daemon worker thread takes jobs from a deque in order; the lock from Fix 2 and the 409 from Fix 3 are gone. Submitting a job that is queued, running, or already done with the same kind just redirects to its page; a failed job can be retried.
- `status.json` (state, kind, image *n*/*N*, timestamps, error) and `result.json` (what the results page needs) are written atomically in `outputs/<id>/`, next to the job folder, so they are not included in the results ZIP.
- `GET /jobs/{id}` shows the queue position ("N job(s) antes que este") or a progress bar, polls `GET /jobs/{id}/status` (JSON) every 3 s, and reloads into the normal results page when done, or an error with a **Reintentar** button when failed. Without JavaScript it refreshes every 5 s.
- On startup, `running` jobs become `failed` ("Interrumpido por un reinicio del servidor") and `queued` jobs are queued again in their original order.
- `run_pipeline*` / `run_preprocess*` take an optional `progress(done, total)` callback; the CLI doesn't pass one and is unchanged.

### Fix 6: Smaller cleanups

- Load templates/static relative to the source file (`Path(__file__).parent / "templates"`), so the working directory no longer matters.
- Consider authentication (at least HTTP basic auth at a reverse proxy) if the app is reachable beyond a trusted network.

---

## Why not more workers?

`uvicorn --workers N` (or gunicorn) starts N separate processes. For this app:
- **Each worker loads its own copy of the models**, multiplying GPU and RAM use.
- **Workers share no state**, so the per-job lock and duplicate-run checks above would not work across them without an external store.
- **The GPU is still the bottleneck.** Parallel jobs mostly queue up on it anyway.

Keep `--workers 1`. Only once there are many users, or several GPUs, is it worth moving to a separate worker process per GPU fed by an external queue.

## Verification

Fixes 1–4 and 6 were tested on a dev instance (single uvicorn worker, NVIDIA L4) with the images in `test-imgs/`.

| Test | Before | After |
|---|---|---|
| `GET /` latency while a 3 GB file is uploaded | median 3 ms, **max 3.4 s** (event loop blocked during the copy) | median 3 ms, **max 18 ms** |
| Two 8-image jobs submitted 2 s apart | ran in parallel threads | second job waits; both return 200 with correct results |
| Same job submitted again while running or queued | second pipeline on the same folder | no second run (409 with Fix 3; since Fix 5, redirect to the status page) |
| Page loads while jobs run | — | 2–13 ms |
| Upload over `MAX_UPLOAD_MB` | accepted | **413**, partial files removed |
| Filename `../../evil.tiff` / `C:\x\win.tiff` | used as given | saved as `evil.tiff` / `win.tiff` inside the job folder |
| Upload of `README.md` | accepted, failed later at processing | **400** unsupported format |
| Job ID `..` / `%2e%2e` / not a UUID | used in paths | **404** |

Histograms from the new Figure-based code were checked visually and render correctly.

Fix 5 was tested the same way:

| Test | Result |
|---|---|
| Submit a job | 303 to `/jobs/{id}` in ~3 ms |
| 3 jobs submitted at once (2 valid, 1 corrupt ZIP) | ran in order; queue position and image *n*/4 reported correctly; the corrupt one failed with "File is not a zip file" and the worker carried on |
| Resubmit a finished job | redirect only, not run again |
| Results page, previews, ZIP download after completion | served from `result.json` / disk |
| Stop the service mid-job (1/4) with another job queued, then start it | running job → failed "Interrumpido…"; queued job resumed and finished; **Reintentar** re-ran the failed one successfully |
| Preprocess → "process only usable images" | both steps queue, report progress and render their pages |
| CLI (`parasight process`) | unchanged |

To repeat under real load:
1. Start the service. In one terminal run `watch -n1 nvidia-smi`; in another, `sudo journalctl -u parasight -f`.
2. From 3 browsers (or scripts using `curl -F file=@batch.zip`), upload the test images and start processing at the same time.
3. While they run, load `/` and a preview URL from a fourth browser. It should respond right away.
4. Record peak GPU memory, time per job and any errors in the log.
5. Refresh a processing page mid-job: it should keep showing progress, never start a second run.

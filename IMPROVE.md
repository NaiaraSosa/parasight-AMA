# Pending improvements

Open issues in the web app, most severe first. Deployment is covered in [DEPLOY.md](DEPLOY.md). Fixed items are removed from this list; see the git history for what was done and how it was tested.

## Current design (context)

- One uvicorn process with **one worker** (the models are loaded once per process).
- Jobs run in a **background queue, one at a time** (`app/services/jobs.py`). Submitting a job redirects to `/jobs/{id}`, which shows queue position, progress and then the results. The state is kept on disk in `outputs/<id>/status.json` and `result.json`.
- Per-job folders: `uploads/<id>/` (original file), `outputs/<id>/` (results), `processing/<id>/` (working files, deleted when the job ends).

---

## 1. Native model crashes are not process-isolated (medium)

The known StarDist crash caused by images with no 2nd-99.8th percentile range is now blocked before inference. RGB TIFFs and ImageJ `Markers_Counter Window` exports are rejected, and ordinary per-image errors are recorded without stopping the batch.

A different fatal error inside a compiled model library could still terminate the web process because inference runs in a thread, not a child process. Add process isolation only if another native crash is observed; loading a model per image would be too expensive.

## 2. Crash vs restart: misleading message (medium)

After a crash, systemd restarts the service and the job is marked "Interrumpido por un reinicio del servidor". That wording blames a restart. It should say "Se interrumpió (reinicio o falla del servidor)", and the log should record which job was running when the process died. Optionally, if a job is found `running` at startup a second time, mark it as "falló dos veces" and refuse automatic retries.

## 3. Default upload limit rejects large batches in local use (medium)

`MAX_UPLOAD_MB` defaults to 500 in `app/core/config.py` and is now enforced. A plain `git clone` + `parasight web` (no configuration) rejects a 600 MB ZIP with HTTP 413, which it used to accept. The deploy config raises the limit to 20 GB.

**Fix:** default to `0` (no limit), keeping the explicit value in `deploy/parasight.env.example`.

## 4. No authentication (security)

Anyone who can reach the port can upload images and open or download any job whose ID they know. Job IDs are random UUIDs, so they can't be guessed, but links can be shared. If the app becomes reachable beyond a trusted network, put HTTP basic auth (or institutional login) in front of it at a reverse proxy (DEPLOY.md, section 6).

## 5. Operational limitations

- **The upload limit is checked after spooling.** The whole body is written to `$TMPDIR` before `MAX_UPLOAD_MB` is checked. To reject oversized uploads earlier, cap them at a reverse proxy (`client_max_body_size`).
- **A restart interrupts the running job.** It is marked as failed and must be retried by hand. Queued jobs resume automatically. Resuming a partially processed job isn't supported.
- **Expired job links give a bare 404.** The cleanup job deletes jobs after 30 days, but job links are meant to be bookmarked. `/jobs/{id}` could say "este job expiró" instead of "Job no encontrado".

## 6. Not yet tested

- **Windows** (covered by the README install steps). Nothing Linux-specific was added to the app itself, but no one has run it there.
- **In a real browser:** the "Copiar ID" / "Copiar enlace" buttons (both the `navigator.clipboard` path on HTTPS/localhost and the fallback on plain `http://<ip>`), and drag-and-drop on the upload page.

---

## Design note: why not more workers?

`uvicorn --workers N` (or gunicorn) starts N separate processes. For this app:
- **Each worker loads its own copy of the models**, multiplying GPU and RAM use.
- **Workers share no state**, so the in-process job queue would not work across them without an external store.
- **The GPU is still the bottleneck.** Parallel jobs mostly queue up on it anyway.

Keep `--workers 1`. Only once there are many users, or several GPUs, is it worth moving to a separate worker process per GPU fed by an external queue (e.g. Redis).

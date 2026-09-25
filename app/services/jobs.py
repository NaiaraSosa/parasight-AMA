# crea job_id, carpetas, estado y la cola de procesamiento en segundo plano

import json
import logging
import os
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("parasight.jobs")

# Tipos de job que acepta la cola.
KINDS = {"process", "process_accepted", "preprocess"}
ACTIVE_STATES = {"queued", "running"}

STATUS_FILE = "status.json"
RESULT_FILE = "result.json"


def create_job() -> str:
    """
    Crea un job_id único y sus carpetas asociadas.
    """
    job_id = str(uuid.uuid4())

    job_upload_dir = settings.uploads_dir / job_id
    job_output_dir = settings.outputs_dir / job_id

    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)

    return job_id


def is_valid_job_id(job_id: str) -> bool:
    """
    Los jobs web son UUID; cualquier otra cosa (p. ej. "..") no debe usarse en rutas.
    """
    try:
        return str(uuid.UUID(job_id)) == job_id
    except ValueError:
        return False


def job_exists(job_id: str) -> bool:
    return (settings.uploads_dir / job_id).is_dir()


# --- Estado persistido en disco (outputs/<job_id>/status.json, result.json) ---

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(value):
    # numpy int/float -> tipos de Python
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"No serializable: {type(value)!r}")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=_json_default), encoding="utf-8")
    os.replace(tmp, path)  # atómico: quien lee nunca ve un archivo a medio escribir


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_status(job_id: str) -> dict | None:
    return _read_json(settings.outputs_dir / job_id / STATUS_FILE)


def _update_status(job_id: str, **fields) -> dict:
    status = read_status(job_id) or {"job_id": job_id}
    status.update(fields)
    _write_json(settings.outputs_dir / job_id / STATUS_FILE, status)
    return status


def read_result(job_id: str) -> dict | None:
    return _read_json(settings.outputs_dir / job_id / RESULT_FILE)


# --- Cola y worker ---
#
# Un solo worker procesa los jobs de a uno: los modelos (Cellpose en GPU,
# StarDist en TF) se comparten en el proceso y no conviene usarlos desde varios
# hilos a la vez. Los requests HTTP solo encolan y responden al instante.

_pending: deque[str] = deque()
_cond = threading.Condition()
_running_job: str | None = None
_worker: threading.Thread | None = None


def submit(job_id: str, kind: str) -> dict:
    """
    Encola el job si hace falta y devuelve su estado.

    No encola de nuevo si el job ya está en cola o procesando, ni si ya terminó
    bien con el mismo tipo (p. ej. al recargar la página): en esos casos solo
    devuelve el estado actual.
    """
    if kind not in KINDS:
        raise ValueError(f"Tipo de job desconocido: {kind}")

    with _cond:
        status = read_status(job_id)
        if status and (
            status.get("state") in ACTIVE_STATES
            or (status.get("state") == "done" and status.get("kind") == kind)
        ):
            return status

        status = _update_status(
            job_id,
            kind=kind,
            state="queued",
            queued_at=_now(),
            started_at=None,
            finished_at=None,
            done=0,
            total=None,
            error=None,
        )
        _pending.append(job_id)
        _cond.notify()
        return status


def queue_position(job_id: str) -> int | None:
    """1 = el próximo en procesarse. None si no está en cola."""
    with _cond:
        try:
            return _pending.index(job_id) + 1
        except ValueError:
            return None


def jobs_ahead(job_id: str) -> int | None:
    """Jobs que se procesan antes que este (incluye el que está corriendo)."""
    position = queue_position(job_id)
    if position is None:
        return None
    with _cond:
        running = 1 if _running_job is not None else 0
    return position - 1 + running


def _run(job_id: str, kind: str) -> dict:
    # Imports acá para no cargar torch/tensorflow al importar este módulo.
    from app.pipeline.preprocess import run_preprocess
    from app.pipeline.runner import run_pipeline

    def progress(done: int, total: int) -> None:
        _update_status(job_id, done=done, total=total)

    if kind == "preprocess":
        preview_items, summary, report_path = run_preprocess(job_id, progress=progress)
        return {
            "kind": kind,
            "preview_items": preview_items,
            "summary": summary,
            "report_name": report_path.name,
        }

    zip_path, preview_items, summary_metrics = run_pipeline(
        job_id,
        accepted_only=(kind == "process_accepted"),
        progress=progress,
    )
    return {
        "kind": kind,
        "preview_items": preview_items,
        "summary_metrics": summary_metrics,
        "zip_name": zip_path.name,
    }


def _worker_loop() -> None:
    global _running_job
    while True:
        with _cond:
            while not _pending:
                _cond.wait()
            job_id = _pending.popleft()
            _running_job = job_id

        status = read_status(job_id) or {}
        kind = status.get("kind", "process")
        _update_status(job_id, state="running", started_at=_now())
        logger.info("Job %s (%s) started", job_id, kind)
        try:
            result = _run(job_id, kind)
            _write_json(settings.outputs_dir / job_id / RESULT_FILE, result)
            _update_status(job_id, state="done", finished_at=_now())
            logger.info("Job %s finished", job_id)
        except Exception as exc:  # el worker no debe morir por un job con error
            logger.exception("Job %s failed", job_id)
            _update_status(job_id, state="failed", finished_at=_now(), error=str(exc) or type(exc).__name__)
        finally:
            with _cond:
                _running_job = None


def start_worker() -> None:
    """
    Arranca el worker (una vez por proceso) y recupera los jobs de un reinicio:
    los que estaban en cola se vuelven a encolar; los que estaban procesando se
    marcan como fallidos (quedaron a medias).
    """
    global _worker
    with _cond:
        if _worker is not None:
            return

        requeue: list[tuple[str, str]] = []
        for status_path in settings.outputs_dir.glob(f"*/{STATUS_FILE}"):
            status = _read_json(status_path)
            if not status:
                continue
            job_id = status_path.parent.name
            if status.get("state") == "running":
                _update_status(
                    job_id,
                    state="failed",
                    finished_at=_now(),
                    error="Interrumpido por un reinicio del servidor. Volvé a enviarlo.",
                )
            elif status.get("state") == "queued":
                requeue.append((status.get("queued_at") or "", job_id))

        for _, job_id in sorted(requeue):
            _pending.append(job_id)

        _worker = threading.Thread(target=_worker_loop, name="parasight-worker", daemon=True)
        _worker.start()

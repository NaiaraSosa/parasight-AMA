# crea job_id, carpetas, estado

import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from app.core.config import settings

# Un solo pipeline a la vez: los modelos (Cellpose en GPU, StarDist en TF) se
# comparten en el proceso y no conviene usarlos desde varios hilos a la vez.
# Los demás jobs esperan su turno.
PIPELINE_LOCK = threading.Lock()

# Jobs en curso o esperando el lock, para rechazar un segundo envío del mismo job
# (p. ej. al recargar la página mientras procesa).
_active_jobs: set[str] = set()
_active_jobs_lock = threading.Lock()


class JobAlreadyRunning(Exception):
    pass


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


@contextmanager
def exclusive_job(job_id: str):
    """
    Marca el job como activo y espera el turno del pipeline.

    Lanza JobAlreadyRunning si el mismo job ya está procesando o en espera.
    """
    with _active_jobs_lock:
        if job_id in _active_jobs:
            raise JobAlreadyRunning(job_id)
        _active_jobs.add(job_id)
    try:
        with PIPELINE_LOCK:
            yield
    finally:
        with _active_jobs_lock:
            _active_jobs.discard(job_id)

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.pipeline.runner import IMAGE_EXTS
from app.services.jobs import (
    create_job,
    is_valid_job_id,
    job_exists,
    jobs_ahead,
    read_result,
    read_status,
    submit,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
PREVIEW_FILES = {
    "input_preview.png",
    "infected_overlay.png",
    "quality_overlay.png",
    "cell_mask_preview.png",
    "parasite_mask_preview.png"
}
_COPY_CHUNK = 16 * 1024 * 1024


def _check_job_id(job_id: str) -> None:
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Job no encontrado.")


# def (no async): la copia del archivo es bloqueante y corre en el threadpool,
# así no congela el event loop para los demás usuarios.
@router.post("/upload")
def upload_file(file: UploadFile = File(...)):
    # Solo el nombre base: descarta directorios o "../" que envíe el cliente.
    filename = Path((file.filename or "").replace("\\", "/")).name
    if not filename or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Archivo inválido")
    if Path(filename).suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail="Formato no soportado. Subí un .tif, .tiff, .czi o .zip.",
        )

    job_id = create_job()
    job_upload_dir = settings.uploads_dir / job_id
    file_path = job_upload_dir / filename

    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    with open(file_path, "wb") as buffer:
        while chunk := file.file.read(_COPY_CHUNK):
            written += len(chunk)
            if max_bytes and written > max_bytes:
                break
            buffer.write(chunk)

    if max_bytes and written > max_bytes:
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        shutil.rmtree(settings.outputs_dir / job_id, ignore_errors=True)
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el máximo permitido ({settings.max_upload_mb} MB).",
        )

    # Redirigir a la página del job: la URL queda como enlace permanente y
    # recargar no vuelve a subir el archivo.
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


def _enqueue(job_id: str, kind: str) -> RedirectResponse:
    _check_job_id(job_id)
    if not job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    submit(job_id, kind)
    # 303: el navegador sigue con GET, así recargar la página no reenvía el POST.
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@router.post("/process/{job_id}")
def process_job(job_id: str):
    return _enqueue(job_id, "process")


@router.post("/process-accepted/{job_id}")
def process_accepted_job(job_id: str):
    return _enqueue(job_id, "process_accepted")


@router.post("/preprocess/{job_id}")
def preprocess_job(job_id: str):
    return _enqueue(job_id, "preprocess")


def _uploaded_filename(job_id: str) -> str | None:
    upload_dir = settings.uploads_dir / job_id
    if not upload_dir.is_dir():
        return None
    files = sorted(p.name for p in upload_dir.iterdir() if p.is_file())
    return files[0] if files else None


def _status_payload(job_id: str) -> dict:
    status = read_status(job_id)
    if status is None:
        # Subido pero todavía no enviado a procesar.
        if _uploaded_filename(job_id) is not None:
            return {"job_id": job_id, "kind": None, "state": "uploaded", "done": 0,
                    "total": None, "jobs_ahead": None, "error": None}
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    return {
        "job_id": job_id,
        "kind": status.get("kind"),
        "state": status.get("state"),
        "done": status.get("done") or 0,
        "total": status.get("total"),
        "jobs_ahead": jobs_ahead(job_id) if status.get("state") == "queued" else None,
        "error": status.get("error"),
    }


@router.get("/jobs/{job_id}/status")
def job_status(job_id: str):
    _check_job_id(job_id)
    return _status_payload(job_id)


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    _check_job_id(job_id)
    status = _status_payload(job_id)
    if status["state"] == "uploaded":
        return templates.TemplateResponse(
            request,
            "upload.html",
            {"job_id": job_id, "filename": _uploaded_filename(job_id)},
        )
    result = read_result(job_id) if status["state"] == "done" else None

    if result is None:
        # En cola, procesando o con error (o terminado sin result.json).
        if status["state"] == "done":
            status["state"] = "failed"
            status["error"] = "No se encontraron los resultados de este job."
        return templates.TemplateResponse(request, "job_status.html", {"status": status})

    preview_items = result.get("preview_items", [])
    if result.get("kind") == "preprocess":
        for item in preview_items:
            folder_name = item.get("folder_name", "")
            item["input_url"] = f"/preview/{job_id}/{folder_name}/input_preview.png"
            item["quality_url"] = f"/preview/{job_id}/{folder_name}/quality_overlay.png"
            item["cell_url"] = f"/preview/{job_id}/{folder_name}/cell_mask_preview.png"

        return templates.TemplateResponse(
            request,
            "preprocessed.html",
            {
                "job_id": job_id,
                "report_name": result.get("report_name", ""),
                "preview_items": preview_items,
                "summary": result.get("summary", {}),
            },
        )

    for item in preview_items:
        folder_name = item.get("folder_name", "")
        item["input_url"] = f"/preview/{job_id}/{folder_name}/input_preview.png"
        item["input_infected_url"] = f"/preview/{job_id}/{folder_name}/infected_overlay.png"
        item["cell_url"] = f"/preview/{job_id}/{folder_name}/cell_mask_preview.png"
        item["parasite_url"] = f"/preview/{job_id}/{folder_name}/parasite_mask_preview.png"

    return templates.TemplateResponse(
        request,
        "processed.html",
        {
            "job_id": job_id,
            "zip_name": result.get("zip_name", ""),
            "preview_items": preview_items,
            "summary_metrics": result.get("summary_metrics", {}),
        },
    )


@router.get("/preview/{job_id}/{image_folder}/{filename}")
def get_preview(job_id: str, image_folder: str, filename: str):
    if filename not in PREVIEW_FILES:
        raise HTTPException(status_code=404, detail="Preview no encontrado.")
    _check_job_id(job_id)

    images_root = settings.outputs_dir / job_id / f"job_{job_id}" / "images"
    root_resolved = images_root.resolve()
    target = (images_root / image_folder / filename).resolve()
    if root_resolved not in target.parents or not target.exists():
        raise HTTPException(status_code=404, detail="Preview no encontrado.")

    return FileResponse(path=str(target), media_type="image/png")


@router.get("/download-quality/{job_id}")
def download_quality_report(job_id: str):
    _check_job_id(job_id)
    report_path = settings.outputs_dir / job_id / f"job_{job_id}" / "control_calidad_por_imagen.csv"

    if not report_path.exists():
        raise HTTPException(status_code=404, detail="No hay reporte de calidad para descargar.")

    return FileResponse(
        path=str(report_path),
        media_type="text/csv",
        filename=f"control_calidad_{job_id}.csv",
    )


@router.get("/download/{job_id}")
def download_results(job_id: str):
    _check_job_id(job_id)
    zip_path = settings.outputs_dir / job_id / f"results_{job_id}.zip"

    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="No hay resultados para descargar. Ya procesaste el job?")

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"results_{job_id}.zip",
    )

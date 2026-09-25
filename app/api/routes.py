import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.pipeline.preprocess import run_preprocess
from app.pipeline.runner import IMAGE_EXTS, run_pipeline
from app.services.jobs import JobAlreadyRunning, create_job, exclusive_job, is_valid_job_id

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


def _run_exclusive(job_id: str, func, *args, **kwargs):
    _check_job_id(job_id)
    try:
        with exclusive_job(job_id):
            return func(job_id, *args, **kwargs)
    except JobAlreadyRunning:
        raise HTTPException(
            status_code=409,
            detail="Este job ya se está procesando. Esperá a que termine.",
        )


# def (no async): la copia del archivo es bloqueante y corre en el threadpool,
# así no congela el event loop para los demás usuarios.
@router.post("/upload")
def upload_file(request: Request, file: UploadFile = File(...)):
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

    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "job_id": job_id,
            "filename": filename,
        },
    )


@router.post("/process/{job_id}")
def process_job(request: Request, job_id: str):
    zip_path, preview_items, summary_metrics = _run_exclusive(job_id, run_pipeline)

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
            "zip_name": zip_path.name,
            "preview_items": preview_items,
            "summary_metrics": summary_metrics,
        },
    )


@router.post("/process-accepted/{job_id}")
def process_accepted_job(request: Request, job_id: str):
    zip_path, preview_items, summary_metrics = _run_exclusive(job_id, run_pipeline, accepted_only=True)

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
            "zip_name": zip_path.name,
            "preview_items": preview_items,
            "summary_metrics": summary_metrics,
        },
    )


@router.post("/preprocess/{job_id}")
def preprocess_job(request: Request, job_id: str):
    preview_items, summary, report_path = _run_exclusive(job_id, run_preprocess)

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
            "report_name": report_path.name,
            "preview_items": preview_items,
            "summary": summary,
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

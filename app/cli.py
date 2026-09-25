from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from app.core.config import ensure_dirs, settings

# Wildcard bind addresses (accept connections on every interface) are valid for
# uvicorn's --host, but they aren't valid URLs to open in a browser. uvicorn logs
# them verbatim ("Uvicorn running on http://0.0.0.0:8000"), which recent Chrome
# versions refuse to navigate to (ERR_ADDRESS_INVALID). Map them to a loopback
# address for display purposes only.
_WILDCARD_HOSTS = {"0.0.0.0", "::"}


def _display_host(host: str) -> str:
    return "127.0.0.1" if host in _WILDCARD_HOSTS else host


app = typer.Typer(
    name="parasight",
    help="Procesa imágenes de microscopía desde consola o levanta la webapp.",
    no_args_is_help=True,
)


def _default_job_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _remove_job_temp(job_temp_dir: Path, default_location: bool) -> None:
    """Borra los temporales del job (p. ej. el ZIP descomprimido) al terminar."""
    shutil.rmtree(job_temp_dir, ignore_errors=True)
    if default_location:
        try:
            job_temp_dir.parent.rmdir()  # <output>/.temp, solo si quedó vacío
        except OSError:
            pass


def _validate_job_id(job_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
        raise typer.BadParameter("Usa solo letras, numeros, guiones, guion bajo o punto.")
    return job_id


@app.command("process")
def process_images(
    input_path: Annotated[
        Path,
        typer.Argument(
            help="Imagen, ZIP o directorio con imagenes .tif, .tiff o .czi.",
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Directorio raiz donde se guarda la carpeta del procesamiento.",
        ),
    ] = settings.outputs_dir,
    temp_dir: Annotated[
        Path | None,
        typer.Option(
            "--temp-dir",
            help="Directorio raiz para temporales. Por defecto se usa <output>/.temp.",
        ),
    ] = None,
    job_id: Annotated[
        str | None,
        typer.Option(
            "--job-id",
            help="Nombre del job. Si no se indica, se usa una marca de tiempo.",
        ),
    ] = None,
    accepted_only: Annotated[
        bool,
        typer.Option(
            "--accepted-only",
            help="Procesar solo imagenes marcadas como usables por control_calidad_por_imagen.csv.",
        ),
    ] = False,
) -> None:
    run_id = _validate_job_id(job_id or _default_job_id())
    input_path = input_path.expanduser().resolve()
    output_root = output_dir.expanduser().resolve()
    job_output_dir = output_root / run_id
    job_temp_dir = (temp_dir.expanduser().resolve() / run_id) if temp_dir else output_root / ".temp" / run_id

    if not input_path.exists():
        raise typer.BadParameter(f"No existe la entrada: {input_path}")

    if output_root.exists() and not output_root.is_dir():
        raise typer.BadParameter(f"La salida debe ser un directorio: {output_root}")

    if not accepted_only and job_output_dir.exists() and any(job_output_dir.iterdir()):
        raise typer.BadParameter(
            f"La carpeta del job ya existe y no esta vacia: {job_output_dir}. "
            "Usa otro --job-id o borra/mueve esos resultados."
        )

    try:
        from app.pipeline.runner import run_pipeline_from_input

        accepted_image_ids = None
        if accepted_only:
            report_path = job_output_dir / f"job_{run_id}" / "control_calidad_por_imagen.csv"
            from app.pipeline.runner import _load_accepted_quality_ids

            accepted_image_ids = _load_accepted_quality_ids(report_path)

        zip_path, preview_items, _summary_metrics = run_pipeline_from_input(
            input_path=input_path,
            job_output_dir=job_output_dir,
            job_temp_dir=job_temp_dir,
            job_id=run_id,
            accepted_image_ids=accepted_image_ids,
        )
    except Exception as exc:
        typer.secho(f"Error procesando imagenes: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        _remove_job_temp(job_temp_dir, default_location=temp_dir is None)

    typer.secho("Procesamiento terminado.", fg=typer.colors.GREEN)
    typer.echo(f"Job: {run_id}")
    typer.echo(f"Imagenes procesadas: {len(preview_items)}")
    typer.echo(f"Salida: {job_output_dir}")
    typer.echo(f"ZIP: {zip_path}")


@app.command("preprocess")
def preprocess_images(
    input_path: Annotated[
        Path,
        typer.Argument(
            help="Imagen, ZIP o directorio con imagenes .tif, .tiff o .czi.",
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Directorio raiz donde se guarda el control de calidad.",
        ),
    ] = settings.outputs_dir,
    temp_dir: Annotated[
        Path | None,
        typer.Option(
            "--temp-dir",
            help="Directorio raiz para temporales. Por defecto se usa <output>/.temp.",
        ),
    ] = None,
    job_id: Annotated[
        str | None,
        typer.Option(
            "--job-id",
            help="Nombre del job. Si no se indica, se usa una marca de tiempo.",
        ),
    ] = None,
) -> None:
    run_id = _validate_job_id(job_id or _default_job_id())
    input_path = input_path.expanduser().resolve()
    output_root = output_dir.expanduser().resolve()
    job_output_dir = output_root / run_id
    job_temp_dir = (temp_dir.expanduser().resolve() / run_id) if temp_dir else output_root / ".temp" / run_id

    if not input_path.exists():
        raise typer.BadParameter(f"No existe la entrada: {input_path}")

    if output_root.exists() and not output_root.is_dir():
        raise typer.BadParameter(f"La salida debe ser un directorio: {output_root}")

    try:
        from app.pipeline.preprocess import run_preprocess_from_input

        preview_items, summary, report_path = run_preprocess_from_input(
            input_path=input_path,
            job_output_dir=job_output_dir,
            job_temp_dir=job_temp_dir,
            job_id=run_id,
        )
    except Exception as exc:
        typer.secho(f"Error revisando calidad: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        _remove_job_temp(job_temp_dir, default_location=temp_dir is None)

    typer.secho("Control de calidad terminado.", fg=typer.colors.GREEN)
    typer.echo(f"Job: {run_id}")
    typer.echo(f"Imagenes revisadas: {len(preview_items)}")
    typer.echo(f"Imagenes usables: {summary.get('imagenes_usables', 0)}")
    typer.echo(f"Imagenes descartadas: {summary.get('imagenes_descartadas', 0)}")
    typer.echo(f"Reporte: {report_path}")


@app.command("web")
def run_webapp(
    host: Annotated[
        str,
        typer.Option("--host", help="Host donde se levanta la webapp."),
    ] = settings.app_host,
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Puerto donde se levanta la webapp."),
    ] = settings.app_port,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Recargar automáticamente durante desarrollo."),
    ] = False,
) -> None:
    ensure_dirs()

    import uvicorn

    config = uvicorn.Config("app.main:app", host=host, port=port, reload=reload)

    if config.should_reload:
        # --reload runs the actual server in a subprocess managed by uvicorn's
        # own supervisor, so our custom Server subclass below can't be threaded
        # across that boundary. Fall back to uvicorn's normal behavior; the
        # early/misleading startup message is a minor cosmetic issue in a
        # dev-only mode.
        uvicorn.run("app.main:app", host=host, port=port, reload=reload)
        return

    class _WebServer(uvicorn.Server):
        """uvicorn.Server that reports the browsable URL only once the app has
        actually finished starting, instead of uvicorn's own message.

        uvicorn's default message is printed *before* startup runs and echoes
        the raw --host value, which can be a non-browsable wildcard address
        like 0.0.0.0 (Chrome refuses to navigate to it, ERR_ADDRESS_INVALID).
        """

        def _log_started_message(self, listeners) -> None:  # noqa: D401
            pass  # replaced by the message below, printed after real startup

        async def startup(self, sockets=None) -> None:
            await super().startup(sockets)
            typer.echo(f"Open in your browser: http://{_display_host(host)}:{port}")

    _WebServer(config=config).run()


if __name__ == "__main__":
    app()

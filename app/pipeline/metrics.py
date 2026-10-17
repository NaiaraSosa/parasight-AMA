from __future__ import annotations

from typing import Dict

import numpy as np

from app.pipeline.postprocess import assign_parasites


def compute_metrics(
    cells_lab: np.ndarray,
    parasites_lab: np.ndarray,
    assign_sigma: float = 40.0,
    assign_threshold: float = 0.5,
    cluster_reassignment: bool = False,
    cluster_radius: int = 25,
    cluster_min_size: int = 3,
    cluster_margin: float = 1.5,
) -> Dict[str, object]:
    """
    Calcula métricas principales por imagen a partir de máscaras de instancias.

    Analiza las máscaras de células y parásitos para generar estadísticas
    cuantitativas del experimento: conteos, asignaciones, promedios, etc.

    Args:
        cells_lab: Array 2D con máscaras de células (IDs únicos).
        parasites_lab: Array 2D con máscaras de parásitos (IDs únicos).
        assign_sigma: Parámetro sigma para decaimiento exponencial en asignación
            (default: 40.0 píxeles).
        assign_threshold: Umbral de confianza para considerar asignación válida
            (default: 0.5).

    Returns:
        Dict con métricas por imagen:
        - total_celulas: Número total de células detectadas
        - total_parasitos: Número total de parásitos detectados
        - parasitos_asignados: Parásitos asignados a células con confianza ≥ threshold
        - parasitos_no_asignados: Parásitos sin asignación confiable
        - celulas_infectadas: Células con al menos un parásito asignado
        - porcentaje_infeccion: Porcentaje de células detectadas que están infectadas
        - promedio_parasitos_por_celula: Promedio de parásitos por célula infectada
        - parasitos_por_celula: Lista con conteo de parásitos por célula

    Notes:
        - Usa assign_parasites_with_confidence para asignación inteligente
        - Asignación considera solapamiento directo (confianza=1.0) y proximidad
        - Threshold filtra asignaciones poco confiables como "no asignadas"
    """
    total_cells = int(cells_lab.max()) if cells_lab.size else 0
    total_parasites = int(parasites_lab.max()) if parasites_lab.size else 0

    assignment = assign_parasites(
        cells_lab,
        parasites_lab,
        sigma=assign_sigma,
        threshold=assign_threshold,
        cluster_reassignment=cluster_reassignment,
        cluster_radius=cluster_radius,
        cluster_min_size=cluster_min_size,
        cluster_margin=cluster_margin,
    )

    infected_cells = int(assignment["infected_cells"])
    parasites_per_cell = np.asarray(assignment["parasites_per_cell"], dtype=int)
    assigned_parasites = int(assignment["assigned_parasites"])
    unassigned_parasites = int(assignment["unassigned_parasites"])

    avg_parasites_per_infected_cell = (
        float(assigned_parasites / infected_cells) if infected_cells > 0 else 0.0
    )
    infection_percentage = (
        round(100.0 * infected_cells / total_cells, 2) if total_cells > 0 else None
    )

    return {
        "total_celulas": total_cells,
        "total_parasitos": total_parasites,
        "parasitos_asignados": assigned_parasites,
        "parasitos_no_asignados": unassigned_parasites,
        "celulas_infectadas": infected_cells,
        "porcentaje_infeccion": infection_percentage,
        "promedio_parasitos_por_celula": round(avg_parasites_per_infected_cell),
        "parasitos_por_celula": parasites_per_cell.tolist(),
    }


def summarize_job(image_metrics: list[Dict[str, object]]) -> Dict[str, object]:
    """
    Agrega métricas a nivel de job completo.

    Suma todas las métricas individuales de cada imagen para obtener
    estadísticas globales del experimento/job.

    Args:
        image_metrics: Lista de dicts con métricas por imagen
            (resultado de compute_metrics para cada imagen).

    Returns:
        Dict con métricas agregadas del job:
        - imagenes_procesadas: Número total de imágenes analizadas
        - total_celulas: Suma de células en todas las imágenes
        - total_parasitos: Suma de parásitos en todas las imágenes
        - total_parasitos_asignados: Suma de parásitos asignados
        - total_parasitos_no_asignados: Suma de parásitos no asignados
        - total_celulas_infectadas: Suma de células infectadas
        - porcentaje_infeccion: Porcentaje global ponderado por cantidad de células
    """
    if not image_metrics:
        return {
            "imagenes_procesadas": 0,
            "total_celulas": 0,
            "total_parasitos": 0,
            "total_parasitos_asignados": 0,
            "total_parasitos_no_asignados": 0,
            "total_celulas_infectadas": 0,
            "porcentaje_infeccion": None,
            "promedio_parasitos_por_celula": 0.0,
        }

    total_cells = int(sum(int(m.get("total_celulas", 0)) for m in image_metrics))
    total_parasites = int(sum(int(m.get("total_parasitos", 0)) for m in image_metrics))
    total_assigned = int(sum(int(m.get("parasitos_asignados", 0)) for m in image_metrics))
    total_unassigned = int(sum(int(m.get("parasitos_no_asignados", 0)) for m in image_metrics))
    total_infected_cells = int(sum(int(m.get("celulas_infectadas", 0)) for m in image_metrics))
    avg_parasites_per_infected_cell = (
        float(total_assigned / total_infected_cells) if total_infected_cells > 0 else 0.0
    )
    infection_percentage = (
        round(100.0 * total_infected_cells / total_cells, 2) if total_cells > 0 else None
    )

    return {
        "imagenes_procesadas": len(image_metrics),
        "total_celulas": total_cells,
        "total_parasitos": total_parasites,
        "total_parasitos_asignados": total_assigned,
        "total_parasitos_no_asignados": total_unassigned,
        "total_celulas_infectadas": total_infected_cells,
        "porcentaje_infeccion": infection_percentage,
        "promedio_parasitos_por_celula": round(avg_parasites_per_infected_cell),
    }

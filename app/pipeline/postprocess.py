from __future__ import annotations

from typing import Dict

import numpy as np
from scipy.ndimage import distance_transform_edt, find_objects, label as ndi_label, maximum_filter1d


def _box_dilation(bw: np.ndarray, radius: int) -> np.ndarray:
    """Dilatación con cuadrado (2r+1)x(2r+1), separable en dos pasadas 1D (equivale a
    binary_dilation con np.ones, pero mucho más rápida para radios grandes)."""
    out = maximum_filter1d(bw.astype(np.uint8), size=2 * radius + 1, axis=0, mode="constant", cval=0)
    out = maximum_filter1d(out, size=2 * radius + 1, axis=1, mode="constant", cval=0)
    return out.astype(bool)


def compute_instance_areas(labels: np.ndarray) -> np.ndarray:
    """
    Calcula las áreas de cada instancia en una máscara etiquetada.

    Args:
        labels: Máscara entera 2D con IDs de instancia (0 = fondo).

    Returns:
        Array 1D con el área de cada instancia, excluyendo el fondo.
    """
    if labels.size == 0 or int(labels.max()) == 0:
        return np.array([], dtype=int)

    areas = np.bincount(labels.ravel())
    return areas[1:]


def _instance_elongation(mask: np.ndarray) -> float:
    ys, xs = np.where(mask)
    if ys.size < 3:
        return float("inf")

    coords = np.column_stack((ys, xs)).astype(float)
    coords -= coords.mean(axis=0, keepdims=True)
    cov = (coords.T @ coords) / max(coords.shape[0], 1)
    eigvals = np.linalg.eigvalsh(cov)
    major = float(np.sqrt(max(eigvals[-1], 0.0)))
    minor = float(np.sqrt(max(eigvals[0], 0.0)))
    if minor <= 1e-6:
        return float("inf")
    return major / minor


def filter_cells_by_area(
    cells_lab: np.ndarray,
    min_area: int,
    max_elongation: float | None = None,
) -> np.ndarray:
    """
    Filtra células por área mínima y re-etiqueta las válidas.

    Elimina detecciones de células que son demasiado pequeñas (probablemente
    ruido o fragmentos) y reasigna IDs consecutivos desde 1 a las células
    que pasan el filtro.

    Args:
        cells_lab: Array 2D con máscaras de células (IDs únicos por célula).
        min_area: Área mínima en píxeles. Células más pequeñas se eliminan.

    Returns:
        Array 2D uint16 con células filtradas. IDs re-etiquetados desde 1..N.
        Células eliminadas se convierten en fondo (0).
    """
    if cells_lab.size == 0 or int(cells_lab.max()) == 0:
        return cells_lab.astype(np.uint16, copy=False)

    shape_filter_enabled = max_elongation is not None and float(max_elongation) > 0
    if min_area <= 0 and not shape_filter_enabled:
        return cells_lab.astype(np.uint16, copy=False)

    areas = np.bincount(cells_lab.ravel())
    candidate_ids = np.arange(1, areas.size)
    if min_area > 0:
        candidate_ids = candidate_ids[areas[candidate_ids] >= int(min_area)]

    if shape_filter_enabled:
        slices = find_objects(cells_lab)
        candidate_ids = np.array(
            [
                old_id
                for old_id in candidate_ids
                if slices[old_id - 1] is not None
                and _instance_elongation(cells_lab[slices[old_id - 1]] == old_id) <= float(max_elongation)
            ],
            dtype=int,
        )

    return _relabel_keep(cells_lab, candidate_ids)


def _relabel_keep(lab: np.ndarray, keep_ids: np.ndarray) -> np.ndarray:
    """Conserva sólo keep_ids (ascendente) y los re-etiqueta 1..N con una tabla de búsqueda."""
    lut = np.zeros(int(lab.max()) + 1, dtype=np.uint16)
    lut[keep_ids] = np.arange(1, len(keep_ids) + 1, dtype=np.uint16)
    return lut[lab]


def filter_parasites_by_area(parasites_lab: np.ndarray, max_area: int) -> np.ndarray:
    """
    Filtra parásitos por área máxima y re-etiqueta los válidos.

    Elimina detecciones de parásitos que son demasiado grandes (probablemente
    ruido, agregados o células mal segmentadas) y reasigna IDs consecutivos
    desde 1 a los parásitos que pasan el filtro.

    Args:
        parasites_lab: Array 2D con máscaras de parásitos (IDs únicos por parásito).
        max_area: Área máxima en píxeles. Parásitos más grandes se eliminan.

    Returns:
        Array 2D uint16 con parásitos filtrados. IDs re-etiquetados desde 1..N.
        Parásitos eliminados se convierten en fondo (0).
    """
    if parasites_lab.size == 0 or int(parasites_lab.max()) == 0:
        return parasites_lab.astype(np.uint16, copy=False)

    if max_area <= 0:
        return parasites_lab.astype(np.uint16, copy=False)

    areas = np.bincount(parasites_lab.ravel())
    keep_ids = np.where(areas <= int(max_area))[0]
    keep_ids = keep_ids[keep_ids != 0]

    return _relabel_keep(parasites_lab, keep_ids)


def merge_parasites(parasites_lab: np.ndarray, merge_radius: int = 2) -> np.ndarray:
    """
    Une parásitos cercanos para reducir doble conteo.

    Cuando StarDist detecta parásitos muy juntos, puede segmentarlos como
    objetos separados aunque sean el mismo parásito. Esta función los
    agrupa usando dilatación morfológica.

    Args:
        parasites_lab: Array 2D con máscaras de parásitos (IDs únicos).
        merge_radius: Radio de dilatación en píxeles (default: 2).
            Parásitos separados por ≤ 2*radius se unen.

    Returns:
        Array 2D uint16 con parásitos fusionados. IDs re-etiquetados desde 1..N.

    Notes:
        - Usa binary_dilation para "inflar" las máscaras
        - Luego ndi_label para identificar componentes conectados
        - Reduce falsos positivos de segmentación por proximidad
        - merge_radius=2 significa que parásitos separados por ≤4px se unen
    """
    if parasites_lab.size == 0 or int(parasites_lab.max()) == 0:
        return parasites_lab.astype(np.uint16, copy=False)

    merged, _ = ndi_label(_box_dilation(parasites_lab > 0, merge_radius))
    return merged.astype(np.uint16, copy=False)


def nearest_cell_distance(cells_lab: np.ndarray, pmask: np.ndarray) -> tuple[int, float]:
    """
    Encuentra la célula más cercana a un parásito y calcula distancia.

    Cuando un parásito no solapa directamente con una célula, esta función
    busca la célula más cercana al parásito y calcula la distancia euclidiana
    desde el centro del parásito hasta el borde de la célula más cercana.

    Args:
        cells_lab: Array 2D con máscaras de células (IDs únicos).
        pmask: Array 2D booleano, máscara del parásito (True = píxeles del parásito).

    Returns:
        Tuple (cell_id, distance):
        - cell_id: ID de la célula más cercana (0 si no hay células)
        - distance: Distancia euclidiana desde centro parásito a borde célula
    """
    cell_bw = cells_lab > 0
    if not cell_bw.any():
        return 0, float("inf")

    ys, xs = np.where(pmask)
    if ys.size == 0:
        return 0, float("inf")

    # Distance Transform: calcula distancia a borde de célula más cercano
    dist_map, (iy, ix) = distance_transform_edt(~cell_bw, return_indices=True)
    # Centro del parásito (promedio de sus píxeles)
    y0 = int(np.round(float(ys.mean())))
    x0 = int(np.round(float(xs.mean())))
    # Encontrar pixel más cercano en la célula
    ny, nx = int(iy[y0, x0]), int(ix[y0, x0])
    cid = int(cells_lab[ny, nx])
    distance = float(dist_map[y0, x0])
    return cid, distance


def _owner_by_contact(cell_ids: np.ndarray, contact: np.ndarray, margin: float) -> int:
    """Célula dueña de un cluster, dados los píxeles de contacto de cada célula tocada
    (cell_ids ascendente, sin fondo). 0 si no hay contacto o el margen no se cumple."""
    if contact.size == 0:
        return 0

    best = int(contact.argmax())  # menor ID en empates
    best_cid = int(cell_ids[best])
    best_contact = int(contact[best])
    if best_contact <= 0:
        return 0

    second_contact = int(np.partition(contact, -2)[-2]) if contact.size >= 2 else 0
    safe_margin = max(float(margin), 1.0)
    if second_contact > 0 and best_contact < second_contact * safe_margin:
        return 0

    return best_cid


def _refine_assignments_by_clusters(
    cells_lab: np.ndarray,
    parasites_lab: np.ndarray,
    cell_ids: np.ndarray,
    confidences: np.ndarray,
    direct_overlaps: np.ndarray,
    threshold: float,
    radius: int,
    min_size: int,
    margin: float,
) -> None:
    if radius <= 0 or min_size <= 1 or int(parasites_lab.max()) == 0:
        return

    cluster_lab, cluster_count = ndi_label(_box_dilation(parasites_lab > 0, int(radius)))
    if cluster_count == 0:
        return

    c_total = int(cells_lab.max())
    stride = c_total + 1

    # parásitos por cluster: pares (cluster, pid) únicos
    pyx = np.nonzero(parasites_lab)
    pair_keys = np.unique(
        cluster_lab[pyx].astype(np.int64) * (int(parasites_lab.max()) + 1)
        + parasites_lab[pyx].astype(np.int64)
    )
    pair_cluster = pair_keys // (int(parasites_lab.max()) + 1)
    pair_pid = pair_keys % (int(parasites_lab.max()) + 1)
    p_bounds = np.searchsorted(pair_cluster, np.arange(1, cluster_count + 2))

    # contacto de cada cluster (región dilatada) con cada célula: pares (cluster, célula)
    cyx = np.nonzero(cluster_lab)
    ccell = cells_lab[cyx].astype(np.int64)
    on_cell = ccell > 0
    contact_keys, contact_counts = np.unique(
        cluster_lab[cyx][on_cell].astype(np.int64) * stride + ccell[on_cell], return_counts=True
    )
    contact_cluster = contact_keys // stride
    contact_cid = contact_keys % stride
    c_bounds = np.searchsorted(contact_cluster, np.arange(1, cluster_count + 2))

    for cluster_id in range(1, cluster_count + 1):
        parasite_ids = pair_pid[p_bounds[cluster_id - 1]:p_bounds[cluster_id]]
        if parasite_ids.size < min_size:
            continue

        overlapped_cells = {
            int(cell_ids[pid])
            for pid in parasite_ids
            if direct_overlaps[pid] and int(cell_ids[pid]) > 0
        }
        if len(overlapped_cells) > 1:
            continue

        current_cells = {
            int(cell_ids[pid])
            for pid in parasite_ids
            if int(cell_ids[pid]) > 0 and float(confidences[pid]) >= threshold
        }
        has_unassigned = any(float(confidences[pid]) < threshold for pid in parasite_ids)
        if len(current_cells) <= 1 and not has_unassigned:
            continue

        lo, hi = c_bounds[cluster_id - 1], c_bounds[cluster_id]
        owner = _owner_by_contact(contact_cid[lo:hi], contact_counts[lo:hi], margin=margin)
        if owner <= 0:
            continue

        for pid in parasite_ids:
            cell_ids[pid] = owner
            confidences[pid] = max(float(confidences[pid]), float(threshold))


def assign_parasites(
    cells_lab: np.ndarray,
    parasites_lab: np.ndarray,
    sigma: float = 40.0,
    threshold: float = 0.3,
    cluster_reassignment: bool = False,
    cluster_radius: int = 25,
    cluster_min_size: int = 3,
    cluster_margin: float = 1.5,
) -> Dict[str, object]:
    """
    Asigna parásitos a células usando lógica de solapamiento y proximidad.

    Usa dos estrategias:
    1. SOLAPAMIENTO DIRECTO: Si un parásito está dentro de una célula → confianza=1.0
    2. PROXIMIDAD: Si no solapa, busca célula más cercana → confianza=exp(-distancia/sigma)

    Args:
        cells_lab: Array 2D con máscaras de células (IDs únicos, 0=fondo).
        parasites_lab: Array 2D con máscaras de parásitos (IDs únicos, 0=fondo).
        sigma: Parámetro de escala para decaimiento exponencial (default: 40.0 píxeles).
            Controla qué tan rápido cae la confianza con la distancia.
            sigma=40: A 40px de distancia, confianza ≈ 0.37
        threshold: Confianza mínima para considerar asignación válida (default: 0.3).
            Asignaciones con confianza < threshold se marcan como "no asignadas".

    Returns:
        Dict con resultados de asignación:
        - infected_cells: Número de células con al menos 1 parásito asignado
        - parasites_per_cell: Array con conteo de parásitos por célula (índice = ID-1)
        - assigned_parasites: Parásitos asignados con confianza ≥ threshold
        - unassigned_parasites: Parásitos con confianza < threshold
    """
    c_total = int(cells_lab.max())
    p_total = int(parasites_lab.max())
    counts = np.zeros(c_total, dtype=int)
    confidences: list[float] = []
    assigned_parasites = 0
    unassigned_parasites = 0

    if c_total == 0 or p_total == 0:
        return {
            "infected_cells": 0,
            "parasites_per_cell": counts,
            "assigned_parasites": assigned_parasites,
            "unassigned_parasites": p_total,
            "mean_assignment_confidence": 0.0,
        }

    safe_sigma = max(float(sigma), 1e-6)
    cell_ids = np.zeros(p_total + 1, dtype=int)
    confidence_by_pid = np.zeros(p_total + 1, dtype=float)
    direct_overlaps = np.zeros(p_total + 1, dtype=bool)

    # Everything below works on all parasites at once (no per-parasite full-image masks).
    pyx = np.nonzero(parasites_lab)
    ppid = parasites_lab[pyx].astype(np.int64)
    pixel_counts = np.bincount(ppid, minlength=p_total + 1)
    present = pixel_counts > 0
    present[0] = False
    present_pids = np.nonzero(present)[0]

    # caso 1: solapamiento directo → célula con más píxeles solapados (menor ID en empates)
    pcell = cells_lab[pyx].astype(np.int64)
    on_cell = pcell > 0
    if on_cell.any():
        keys, key_counts = np.unique(
            ppid[on_cell] * (c_total + 1) + pcell[on_cell], return_counts=True
        )
        key_pid = keys // (c_total + 1)
        key_cid = keys % (c_total + 1)
        order = np.lexsort((key_cid, -key_counts, key_pid))
        key_pid, key_cid = key_pid[order], key_cid[order]
        first = np.ones(key_pid.size, dtype=bool)
        first[1:] = key_pid[1:] != key_pid[:-1]
        cell_ids[key_pid[first]] = key_cid[first]
        confidence_by_pid[key_pid[first]] = 1.0
        direct_overlaps[key_pid[first]] = True

    # caso 2: no solapa → célula más cercana al centro del parásito (un solo EDT para toda la imagen)
    need = present & ~direct_overlaps
    if need.any():
        dist_map, (iy, ix) = distance_transform_edt(cells_lab == 0, return_indices=True)
        safe_counts = np.maximum(pixel_counts, 1)
        y0 = np.round(np.bincount(ppid, weights=pyx[0], minlength=p_total + 1) / safe_counts).astype(int)
        x0 = np.round(np.bincount(ppid, weights=pyx[1], minlength=p_total + 1) / safe_counts).astype(int)
        npids = np.nonzero(need)[0]
        ny, nx = iy[y0[npids], x0[npids]], ix[y0[npids], x0[npids]]
        cell_ids[npids] = cells_lab[ny, nx]
        confidence_by_pid[npids] = np.exp(-dist_map[y0[npids], x0[npids]] / safe_sigma)

    if cluster_reassignment:
        _refine_assignments_by_clusters(
            cells_lab=cells_lab,
            parasites_lab=parasites_lab,
            cell_ids=cell_ids,
            confidences=confidence_by_pid,
            direct_overlaps=direct_overlaps,
            threshold=threshold,
            radius=cluster_radius,
            min_size=cluster_min_size,
            margin=cluster_margin,
        )

    for pid in present_pids:
        cid = int(cell_ids[pid])
        confidence = float(confidence_by_pid[pid])
        confidences.append(confidence)

        if cid > 0 and confidence >= threshold:
            counts[cid - 1] += 1
            assigned_parasites += 1
        else:
            unassigned_parasites += 1

    infected_cells = int((counts > 0).sum())
    mean_assignment_confidence = float(np.mean(confidences)) if confidences else 0.0

    return {
        "infected_cells": infected_cells,
        "parasites_per_cell": counts,
        "assigned_parasites": assigned_parasites,
        "unassigned_parasites": unassigned_parasites,
        "mean_assignment_confidence": mean_assignment_confidence,
    }

from __future__ import annotations

from pathlib import Path
from typing import Iterable
# Figure directamente (sin pyplot): pyplot usa estado global y no es thread-safe.
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
import numpy as np


def _split_counts(parasites_per_cell: Iterable[int]) -> tuple[list[int], int]:
    values = [int(value) for value in parasites_per_cell if int(value) >= 0]
    return [value for value in values if value > 0], sum(value == 0 for value in values)


def save_histogram(
    path: Path,
    parasites_per_cell: Iterable[int],
    title: str = "Distribución de parásitos por célula",
    zoom_max: int = 15,
) -> None:
    positive_values, zero_count = _split_counts(parasites_per_cell)

    fig = Figure(figsize=(11, 4), dpi=140)
    axes = fig.subplots(1, 2)
    fig.patch.set_facecolor("white")

    def style_axis(ax):
        ax.set_facecolor("white")
        ax.tick_params(colors="#333333", labelsize=9)
        ax.grid(axis="y", color="#dddddd", linewidth=0.8)
        ax.set_xlabel("Parásitos por célula", color="#222222")
        ax.set_ylabel("Cantidad de células", color="#222222")
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))

        for spine in ax.spines.values():
            spine.set_color("#777777")
            spine.set_linewidth(1.1)

    if positive_values:
        max_value = max(positive_values)

        # Histograma completo sin ceros, para que el pico de células no
        # infectadas no comprima visualmente el resto de la distribución.
        bins_full = np.arange(0.5, max_value + 1.5, 1)
        axes[0].hist(
            positive_values,
            bins=bins_full,
            color="#b85c55",
            edgecolor="#7f3530",
            linewidth=0.5,
            alpha=0.85,
        )
        axes[0].set_title(f"Completo (1-{max_value})", color="#222222", fontsize=12)
        axes[0].set_xlim(0.5, max_value + 0.5)
        axes[0].xaxis.set_major_locator(MaxNLocator(integer=True, nbins=10))

        # Zoom 1 a 15
        zoom_values = [value for value in positive_values if value <= zoom_max]
        bins_zoom = np.arange(0.5, zoom_max + 1.5, 1)
        if zoom_values:
            axes[1].hist(
                zoom_values,
                bins=bins_zoom,
                color="#b85c55",
                edgecolor="#7f3530",
                linewidth=0.5,
                alpha=0.85,
            )
        else:
            axes[1].text(
                0.5,
                0.5,
                f"Sin valores entre 1 y {zoom_max}",
                color="#333333",
                ha="center",
                va="center",
                transform=axes[1].transAxes,
                fontsize=12,
            )
        axes[1].set_title(f"Zoom 1-{zoom_max}", color="#222222", fontsize=12)
        axes[1].set_xlim(0.5, zoom_max + 0.5)
        axes[1].set_xticks(range(1, zoom_max + 1, 2))

    else:
        empty_message = (
            "Sin células con parásitos asignados"
            if zero_count
            else "Sin células detectadas"
        )
        for ax in axes:
            ax.text(
                0.5,
                0.5,
                empty_message,
                color="#333333",
                ha="center",
                va="center",
                transform=ax.transAxes,
                fontsize=12,
            )
            ax.set_xticks([])
            ax.set_yticks([])

    for ax in axes:
        style_axis(ax)

    fig.suptitle(title, color="#111111", fontsize=15, y=0.98)
    fig.text(
        0.055,
        0.035,
        f"• Cantidad de células con 0 parásitos asignados: {zero_count}",
        color="#222222",
        fontsize=10,
        ha="left",
    )
    fig.tight_layout(rect=(0, 0.12, 1, 0.92))

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", bbox_inches="tight", facecolor="white")

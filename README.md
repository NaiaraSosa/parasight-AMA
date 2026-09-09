# Parasight-AMA

## Automated segmentation and quantification of parasite-infected cell cultures 

In our laboratory work is underway to find drugs capable of curing Chagas disease. Part of the process involves testing experimental drugs on cultures of cells infected with parasites that cause the disease (_Trypanosoma cruzi_). Microscopic images of the cell culture are taken both before and after infection, and during treatment of cultures with drugs. 

Currently, indirect methods are needed to assess levels of infection of cultures and parasite growth [^1]. Alternatively using microscopy, assessment of infected cell cultures requires time consuming manual inspection of images for counting cells, and parasites. Here we present  **an automated tool for  automatic identification of cells, and parasites based on AI segmentation models**, that speeds up this process.

## Objective

The main objective of this project is to **automate microscopy image analysis tasks**, in order to quantify and label cells accurately, quickly, and reproducibly.

## Cell segmentation program

 - FastAPI web application for running a microscopy image segmentation pipeline, allowing the user to visualize results live. 
 - CLI application for processing batches of images non-interactively

## Requirements

- Python 3.10
- Conda/Miniforge (recommended)

## Installation

1. Miniforge:

- Linux

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh 
# install miniforge under $HOME/miniforge (e.g. /home/user/miniforge)
bash Miniforge3-Linux-x86_64.sh 
# alternatively install in another path prefix
bash Miniforge3-Linux-x86_64.sh -p /<path>/miniforge3
```

- Windows:

Go to: https://github.com/conda-forge/miniforge/releases/latest

Download `Miniforge3-Windows-x86_64.exe` and run the `.exe` installer.

2. Create a conda environment with Python 3.10:



```bash
# create in the default location 
conda create -n parasight python=3.10 -y
# and activate 
conda activate parasight

# or specify the path where you want to create the environment
conda create -p /path/to/conda_envs/parasight python=3.10 -y
conda activate /path/to/conda_envs/parasight
```

3. Install the project dependencies from `pyproject.toml`:

```bash
pip install -U pip
pip install -e .
pip uninstall -y torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

> **Note:** the project pins `numpy>=1.26,<2` because TensorFlow 2.15/StarDist/CSBDeep are not compatible with NumPy 2.x. If `pip install -e .` upgrades NumPy to 2.x, run `pip install -e .` again from this folder to downgrade it to a compatible version.
>
> The PyTorch reinstall step is unrelated to NumPy. `cellpose` depends on PyTorch, and this project has been tested with the CUDA 12.8 PyTorch build (`cu128`) so Cellpose can use the GPU when it is available. `torchvision` is installed because it is required by Cellpose. 

## Running the program

The tool can be used in two ways:
- from the console, to process images directly with commands.
- from the webapp, to use a visual interface in the browser.

To see the available commands and their options:

```bash
parasight --help 
```



To see the options for each mode:
```bash
parasight process --help 
parasight web --help 
```

### Console usage

Process a single image, a ZIP file, or a local directory of images. Example usage specifying the output folder and an experiment identifier:

```bash
parasight process ./test-imgs --output ./data/outputs --job-id experimento_01
```

In this case, `--job-id` allows you to assign a name to the processing run.

The input can be:

- a TIFF (`.tif`, `.tiff`), or ZEISS CZI format `.czi` images.
- a `.zip` file containing supported images in supported formats.
- a directory with images in supported formats, searched recursively through subfolders.

**Important note:** the preprocess command cannot be used yet! (under development).

## Using the webapp

Useful for testing images and reviewing results. From the project folder, run:

```bash
# using default host (localhost) and port (8000)
parasight web 
# or specify your own 
parasight web --host 127.0.0.1 --port 8010
```

Then open the local URL in your browser:

```text
http://127.0.0.1:8000
```

## Project structure

```text
app/
  api/         # FastAPI endpoints
  core/        # General configuration
  pipeline/    # Processing logic
  services/    # Job/file utilities
  cli.py       # Typer entry point for the command line
  main.py      # App entry point
data/
  uploads/     # Files uploaded per job
  outputs/     # Results exported per job
  temp/        # Processing temp files
```

The `data/` directory is intentionally not tracked in Git. The web app creates `data/uploads`, `data/outputs`, and `data/temp` automatically when it starts. The CLI also creates the output and temporary folders it needs for each run.

## Current processing workflow

1. Image loading (`.tif/.tiff/.czi`) from a single file, ZIP, or directory.
2. Cell segmentation with Cellpose 4 [^2].
3. Cell filtering by minimum area (`CELL_MIN_AREA`) and maximum elongation (`CELL_MAX_ELONGATION`).
4. Parasite segmentation with StarDist 0.9 [^3].
5. Parasite filtering by maximum area (`PARASITE_MAX_AREA`).
6. Merging of nearby parasites to reduce double counting.
7. Parasite --> cell assignment by overlap and proximity, and clustering in a second pass.
8. Metric calculation and export of results.

## Exported metrics

The ZIP includes two main CSV files:

- `metricas_generales.csv`: summary of the full processing run.
- `metricas_por_imagen.csv`: one row per processed image.

Main columns:

- total_celulas: total number of detected cells.
- total_parasitos: total number of detected parasites.
- celulas_infectadas: cells with at least one assigned parasite.
- parasitos_no_asignados: number of parasites that could not be assigned to any cell.
- parasitos_por_celula = number of parasites assigned per cell.

## Output ZIP structure

For each image:
- input.tiff: original image converted to TIFF.
- cell_mask.tiff: cell instance mask.
- parasite_mask.tiff: parasite instance mask.
- infected_overlay: original image with infected cells marked in red.

For each experiment:
- metricas_generales.csv: general processing metrics.
- metricas_por_imagen.csv: per-image metrics.
- histograma_global_global_parasitos_por_celula: distribution of parasites per cell across all images.

## References

[1] Didier Garnham M, Agüero FA, Ramírez JC, Agüero F, Salas-Sarduy E. Identification of Antifungal Agents AR-12 and Fosmanogepix as Anti-Trypanosoma cruzi Drugs through an Enhanced Fluorogenic β-Galactosidase Phenotypic Screening Assay. ACS Infect Dis. 2026 Feb 13;12(2):724-737. doi: 10.1021/acsinfecdis.5c00900. Epub 2026 Jan 1. PMID: 41479158.

[2] Stringer C, Wang T, Michaelos M, Pachitariu M. Cellpose: a generalist algorithm for cellular segmentation. Nat Methods. 2021 Jan;18(1):100-106. doi: 10.1038/s41592-020-01018-x. Epub 2020 Dec 14. PMID: 33318659.

[3] Weigert M, Schmidt U. Nuclei Instance Segmentation and Classification in Histopathology Images with Stardist. The IEEE International Symposium on Biomedical Imaging Challenges (ISBIC) (2022). doi: 10.1109/ISBIC56247.2022.9854534.

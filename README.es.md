# Parasight-AMA

## Segmentación y cuantificación automatizada de cultivos celulares infectados con parásitos

En nuestro laboratorio trabajmos para descubrir nuevos fármacos capaces de curar la Enfermedad de Chagas. Parte del proceso consiste en ensayar drogas experimentales en cultivos de células infectadas con el parásito causantes de la enfermedad (_Trypanosoma cruzi_). Se toman imágenes microscópicas del cultivo de células antes y después de la infección, y durante el tratamiento de los cultivos con fármacos.

Actualmente, se requieren métodos indirectos para evaluar los niveles de infección de los cultivos y el crecimiento de los parásitos [^1]. Alternativamente, mediante microscopía, evaluar cultivos de células infectadas requiere una inspección manual de imágenes que demanda mucho tiempo, para contar células y parásitos. Presentamos aquí **una herramienta automatizada para la identificación automática de células y parásitos basada en modelos de segmentación con IA**, que acelera este proceso.

## Objetivo

El objetivo principal de este proyecto es **automatizar las tareas de análisis de imágenes de microscopía**, con el fin de cuantificar y etiquetar células de manera precisa, rápida y reproducible.

## Programa de segmentación celular

 - Aplicación web en FastAPI para ejecutar un pipeline de segmentación de imágenes de microscopía, que permite al usuario visualizar los resultados en vivo.
 - Aplicación de línea de comandos (CLI) para procesar lotes de imágenes de forma no interactiva.

## Requisitos

- Python 3.10
- Conda/Miniforge (recomendado)

## Instalación

1. Miniforge:

- Linux

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh 
# instalar miniforge en $HOME/miniforge (por ejemplo /home/usuario/miniforge)
bash Miniforge3-Linux-x86_64.sh 
# alternativamente instalar en otro prefijo de ruta
bash Miniforge3-Linux-x86_64.sh -p /<ruta>/miniforge3
```

- Windows:

Ingresar a: https://github.com/conda-forge/miniforge/releases/latest

Descargar `Miniforge3-Windows-x86_64.exe` y ejecutar el instalador `.exe`.

2. Crear un entorno conda con Python 3.10:

```bash
# crear en la ubicación por defecto 
conda create -n parasight python=3.10 -y
# y activar 
conda activate parasight

# o especificar la ruta donde se desea crear el entorno
conda create -p /ruta/a/conda_envs/parasight python=3.10 -y
conda activate /ruta/a/conda_envs/parasight
```

3. Instalar las dependencias del proyecto desde `pyproject.toml`:

```bash
pip install -U pip
pip install -e .
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

> **Nota:** el proyecto fija `numpy>=1.26,<2` porque TensorFlow 2.15/StarDist/CSBDeep no son compatibles con NumPy 2.x. Si `pip install -e .` actualiza NumPy a 2.x, volver a ejecutar `pip install -e .` desde esta carpeta lo baja a una versión compatible.

## Ejecutar el programa

La herramienta se puede usar de dos formas:
- desde la consola, para procesar imágenes directamente con comandos.
- desde la webapp, para usar una interfaz visual en el navegador.

Para ver los comandos disponibles y sus opciones:

```bash
parasight --help 
```

Para ver las opciones de cada modo:
```bash
parasight process --help 
parasight web --help 
```

### Uso por consola

Procesar una única imagen, un archivo ZIP o un directorio local con imágenes. Ejemplo de uso indicando la carpeta de salida y un identificador de experimento:

```bash
parasight process ./test-imgs --output ./data/outputs --job-id experimento_01
```

En este caso, `--job-id` permite asignarle un nombre al procesamiento.

La entrada puede ser:

- una imagen TIFF (`.tif`, `.tiff`), o en formato ZEISS CZI `.czi`.
- un archivo `.zip` que contenga imágenes en formatos soportados.
- un directorio con imágenes en formatos soportados, buscando recursivamente en subcarpetas.

**Nota importante:** ¡el comando preprocess todavía no puede utilizarse! (en desarrollo).

## Uso de la webapp

Útil para probar imágenes y revisar resultados. Desde la carpeta del proyecto, ejecutar:

```bash
# usando el host (localhost) y puerto (8000) por defecto
parasight web 
# o especificando los propios 
parasight web --host 127.0.0.1 --port 8010
```

Luego abrir la URL local en el navegador:

```text
http://127.0.0.1:8010
```

## Estructura del proyecto

```text
app/
  api/         # Endpoints de FastAPI
  core/        # Configuración general
  pipeline/    # Lógica de procesamiento
  services/    # Utilidades de jobs/archivos
  cli.py       # Punto de entrada Typer para la línea de comandos
  main.py      # Punto de entrada de la app
data/
  uploads/     # Archivos subidos por job
  outputs/     # Resultados exportados por job
  temp/        # Temporales de procesamiento
```

## Flujo actual de procesamiento

1. Carga de imagen (`.tif/.tiff/.czi`) desde un archivo suelto, ZIP o directorio.
2. Segmentación de células con Cellpose 3 [^2].
3. Filtro de células por área mínima (`CELL_MIN_AREA`) y elongación máxima (`CELL_MAX_ELONGATION`).
4. Segmentación de parásitos con StarDist 0.9.1 [^3].
5. Filtro de parásitos por área máxima (`PARASITE_MAX_AREA`).
6. Fusión de parásitos cercanos para reducir el doble conteo.
7. Asignación parásito --> célula por solape y proximidad, y clustering en una segunda pasada.
8. Cálculo de métricas y exportación de resultados.

## Métricas exportadas

El ZIP incluye dos archivos CSV principales:

- `metricas_generales.csv`: resumen del procesamiento completo.
- `metricas_por_imagen.csv`: una fila por imagen procesada.

Columnas principales:

- total_celulas: número total de células detectadas.
- total_parasitos: número total de parásitos detectados.
- celulas_infectadas: células con al menos un parásito asignado.
- parasitos_no_asignados: cantidad de parásitos que no pudieron asignarse a ninguna célula.
- parasitos_por_celula = cantidad de parásitos asignados por célula.

## Estructura del ZIP de salida

Por cada imagen:
- input.tiff: imagen original convertida a TIFF.
- cell_mask.tiff: máscara de instancias de células.
- parasite_mask.tiff: máscara de instancias de parásitos.
- infected_overlay: imagen original con las células infectadas marcadas en rojo.

Por cada experimento:
- metricas_generales.csv: métricas generales del procesamiento.
- metricas_por_imagen.csv: métricas por imagen.
- histograma_global_global_parasitos_por_celula: distribución de parásitos por célula a lo largo de todas las imágenes.

## Referencias

[1] Didier Garnham M, Agüero FA, Ramírez JC, Agüero F, Salas-Sarduy E. Identification of Antifungal Agents AR-12 and Fosmanogepix as Anti-Trypanosoma cruzi Drugs through an Enhanced Fluorogenic β-Galactosidase Phenotypic Screening Assay. ACS Infect Dis. 2026 Feb 13;12(2):724-737. doi: 10.1021/acsinfecdis.5c00900. Epub 2026 Jan 1. PMID: 41479158.

[2] Stringer C, Wang T, Michaelos M, Pachitariu M. Cellpose: a generalist algorithm for cellular segmentation. Nat Methods. 2021 Jan;18(1):100-106. doi: 10.1038/s41592-020-01018-x. Epub 2020 Dec 14. PMID: 33318659.

[3] Weigert M, Schmidt U. Nuclei Instance Segmentation and Classification in Histopathology Images with Stardist. The IEEE International Symposium on Biomedical Imaging Challenges (ISBIC) (2022). doi: 10.1109/ISBIC56247.2022.9854534.

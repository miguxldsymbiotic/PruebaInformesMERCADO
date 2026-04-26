import faicons as fa
from pathlib import Path
import polars as pl
from shiny import App, reactive, render, ui, session
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
# import ReportEngine removed
import datetime
import base64
import io
import numpy as np
from jinja2 import Template
from weasyprint import HTML
from concurrent.futures import ThreadPoolExecutor

# Custom palette global para Plotly (puntos, lineas, barras, etc.)
px.defaults.color_discrete_sequence = [
    "#31497e", "#674f95", "#a14e9a", "#d44c8d", "#f9596f", "#ff7a47", "#ffa600"
]
import plotly.io as pio
pio.templates[pio.templates.default].layout.separators = ',.'

def format_num_es(val, decimals=0):
    """Formatea números al estilo español: mil=. dec=,"""
    if val is None: return "Sin dato"
    try:
        # Paso 1: Formato con comas y puntos estándar (US)
        s = f"{val:,.{decimals}f}"
        # Paso 2: Intercambio
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "Sin dato"

def format_pct_es(val, decimals=1):
    """Formatea proporciones (0-1) como porcentajes estilo español (ej: 0.5423 -> 54,23%)"""
    if val is None: return "Sin dato"
    return format_num_es(val * 100, decimals=decimals) + "%"

COLOR_SEXO = {
    "FEMENINO": "#31497e",
    "MASCULINO": "#674f95",
    "NO BINARIO": "#a14e9a",
    "TRANS": "#d44c8d"
}

# --- PLAYWRIGHT ENGINE (reemplaza Kaleido por completo) ---
# CLAVE: Playwright Sync API no puede correr dentro del asyncio loop de Shiny.
# SOLUCIÓN: ThreadPoolExecutor con 1 worker = thread dedicado sin asyncio loop.
# El browser Chromium se inicia UNA vez en ese thread y se reutiliza para todos.
import json as _json
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

_PW_INSTANCE   = None
_PW_BROWSER    = None
_PW_PAGE       = None
_PW_PAGE_DIM   = None
_PLOTLY_JS_BUNDLE = None
# Executor con 1 worker: todo Playwright corre en ese único thread limpio
_PW_EXECUTOR   = _ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw_renderer")

def _load_plotlyjs():
    """Carga el bundle de Plotly.js del paquete local (sin necesidad de internet)."""
    global _PLOTLY_JS_BUNDLE
    if _PLOTLY_JS_BUNDLE is None:
        try:
            import plotly.offline as _plo
            _PLOTLY_JS_BUNDLE = _plo.get_plotlyjs()
            print("INFO: Plotly.js bundle cargado en memoria.")
        except Exception as e:
            print(f"WARN: No se pudo cargar Plotly.js bundle: {e}")
            _PLOTLY_JS_BUNDLE = ""
    return _PLOTLY_JS_BUNDLE

def _ensure_pw_page(width: int, height: int):
    """Garantiza browser Playwright + página con Plotly.js listos.
    DEBE llamarse solo desde dentro del _PW_EXECUTOR thread."""
    global _PW_INSTANCE, _PW_BROWSER, _PW_PAGE, _PW_PAGE_DIM

    dim = (width, height)

    if _PW_BROWSER is None or not _PW_BROWSER.is_connected():
        from playwright.sync_api import sync_playwright
        if _PW_INSTANCE:
            try: _PW_INSTANCE.stop()
            except: pass
        _PW_INSTANCE = sync_playwright().start()
        _PW_BROWSER  = _PW_INSTANCE.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        _PW_PAGE = None
        print("INFO: Playwright Chromium iniciado correctamente.")

    if _PW_PAGE is None or _PW_PAGE.is_closed() or _PW_PAGE_DIM != dim:
        if _PW_PAGE and not _PW_PAGE.is_closed():
            try: _PW_PAGE.close()
            except: pass

        plotlyjs = _load_plotlyjs()
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>*{{margin:0;padding:0;box-sizing:border-box;}}body{{background:white;}}</style>
<script>{plotlyjs}</script>
</head><body>
<div id="gd" style="width:{width}px;height:{height}px;"></div>
</body></html>"""

        _PW_PAGE = _PW_BROWSER.new_page(viewport={"width": width + 20, "height": height + 20})
        _PW_PAGE.set_content(html, wait_until="domcontentloaded")
        _PW_PAGE_DIM = dim
        print(f"INFO: Playwright page lista ({width}x{height}px).")

    return _PW_PAGE


def _render_in_thread(fig_json_str: str, width: int, height: int) -> str:
    """Worker que corre en el thread dedicado (sin asyncio loop de Shiny).
    Playwright Sync API funciona perfecto aquí."""
    import asyncio, sys
    # CRÍTICO en Windows: los threads usan SelectorEventLoop por defecto,
    # que NO soporta crear subprocesos. Playwright necesita ProactorEventLoop.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    page = _ensure_pw_page(width, height)

    # Pasar JSON como string y parsear en JS evita límites de serialización
    # de Playwright con objetos grandes (figuras Saber PRO, etc.)
    # Retornamos el contenido base64 del SVG
    b64_svg = page.evaluate(f"""
        async (figJsonStr) => {{
            const spec = JSON.parse(figJsonStr);
            const gd  = document.getElementById('gd');
            gd.style.width  = '{width}px';
            gd.style.height = '{height}px';
            if (gd._fullLayout) Plotly.purge(gd);
            
            await Plotly.newPlot(gd, spec.data,
                Object.assign({{}}, spec.layout, {{ width: {width}, height: {height} }}),
                {{displayModeBar: false, staticPlot: true}}
            );
            
            // Exportar a SVG vía Plotly.toImage
            const dataUrl = await Plotly.toImage(gd, {{
                format: 'svg', 
                width: {width}, 
                height: {height}
            }});
            
            // Extraer la parte base64 o codificar si viene como URI component
            const parts = dataUrl.split(',');
            if (parts[0].includes('base64')) {{
                return parts[1];
            }} else {{
                // Si viene como SVG plano (URI encoded), lo pasamos a base64
                return btoa(unescape(encodeURIComponent(decodeURIComponent(parts[1]))));
            }}
        }}
    """, fig_json_str)

    return b64_svg


def fig_to_base64(fig, width=800, height=450):
    """Convierte figura Plotly a SVG base64 para máxima resolución en PDF.
    Usa Playwright en thread dedicado. Fallback a Kaleido si falla.
    """
    try:
        if fig is None: return ""
        fig_json = fig.to_json()
        # Enviar al thread de Playwright
        future = _PW_EXECUTOR.submit(_render_in_thread, fig_json, width, height)
        # Ya retorna el string base64 directamente
        return future.result(timeout=60)

    except Exception as e:
        print(f"WARN Playwright ({type(e).__name__}): {e}. Fallback a Kaleido (SVG)...")
        try:
            # Fallback usando kaleido en formato svg
            img_bytes = fig.to_image(format="svg", width=width, height=height, engine="kaleido")
            return base64.b64encode(img_bytes).decode('utf-8')
        except Exception as e2:
            print(f"ERROR total en fig_to_base64: {e2}")
            return ""


# Define paths
app_dir = Path(__file__).parent
data_dir = app_dir.parent / "data"

# Load data
df_snies = pl.read_parquet(data_dir / "df_SNIES_Programas.parquet")
# Quitar duplicados
df_snies = df_snies.unique()
# Filtrar registros que no tengan valor en 'reconocimiento_del_ministerio'
df_snies = df_snies.filter(
    pl.col("reconocimiento_del_ministerio").is_not_null() & 
    (pl.col("reconocimiento_del_ministerio") != "") &
    (pl.col("reconocimiento_del_ministerio") != "Sin dato")
)

# Crear columnas concatenadas para filtros
df_snies = df_snies.with_columns(
    pl.concat_str([pl.col("codigo_institucion").cast(pl.Int64).cast(pl.Utf8), pl.lit(" - "), pl.col("nombre_institucion")]).alias("institucion_label"),
    pl.concat_str([pl.col("codigo_snies_del_programa").cast(pl.Int64).cast(pl.Utf8), pl.lit(" - "), pl.col("programa_academico")]).alias("snies_label")
)

df_cobertura = pl.read_parquet(data_dir / "df_Cobertura_distinct.parquet")
# Quitar duplicados
df_cobertura = df_cobertura.unique()

df_pcurso = pl.read_parquet(data_dir / "df_PCurso_agg.parquet")
df_matricula = pl.read_parquet(data_dir / "df_Matricula_agg.parquet")
df_graduados = pl.read_parquet(data_dir / "df_Graduados_agg.parquet")
df_ole_m0 = pl.read_parquet(data_dir / "df_OLE_Movilidad_M0.parquet")
df_ole_salario = pl.read_parquet(data_dir / "df_OLE_Salario_M0.parquet")
df_desercion = pl.read_parquet(data_dir / "df_SPADIES_Desercion.parquet").filter(
    (pl.col("codigo_snies_del_programa") < 1_000_000) &
    pl.col("desercion_anual_mean").is_not_null() &
    pl.col("desercion_anual_mean").is_not_nan()
).with_columns([
    pl.col("codigo_snies_del_programa").cast(pl.Int64),
    pl.col("anno").cast(pl.Int32)
])

# --- CÁLCULO DE PROXY DE DESERCIÓN PARA PROGRAMAS FALTANTES ---
_df_m_agg = df_matricula.with_columns([pl.col("codigo_snies_del_programa").cast(pl.Int64), pl.col("anno").cast(pl.Int32)]).group_by(["codigo_snies_del_programa", "anno"]).agg(pl.col("matricula_sum").sum().alias("matriculados"))
_df_g_agg = df_graduados.with_columns([pl.col("codigo_snies_del_programa").cast(pl.Int64), pl.col("anno").cast(pl.Int32)]).group_by(["codigo_snies_del_programa", "anno"]).agg(pl.col("graduados_sum").sum().alias("graduados"))
_df_p_agg = df_pcurso.with_columns([pl.col("codigo_snies_del_programa").cast(pl.Int64), pl.col("anno").cast(pl.Int32)]).group_by(["codigo_snies_del_programa", "anno"]).agg(pl.col("primer_curso_sum").sum().alias("primer_curso"))

_df_universe = pl.concat([
    _df_m_agg.select(["codigo_snies_del_programa", "anno"]),
    _df_g_agg.select(["codigo_snies_del_programa", "anno"]),
    _df_p_agg.select(["codigo_snies_del_programa", "anno"])
]).unique()

_df_proxy = _df_universe.join(_df_m_agg, on=["codigo_snies_del_programa", "anno"], how="left") \
                        .join(_df_p_agg, on=["codigo_snies_del_programa", "anno"], how="left") \
                        .join(_df_g_agg, on=["codigo_snies_del_programa", "anno"], how="left") \
                        .fill_null(0).sort(["codigo_snies_del_programa", "anno"])

_df_proxy = _df_proxy.with_columns(pl.col("matriculados").shift(1).over("codigo_snies_del_programa").alias("matriculados_t_1"))
_df_proxy = _df_proxy.with_columns(poblacion_riesgo=(pl.col("matriculados_t_1") + pl.col("primer_curso"))) \
                     .filter(pl.col("matriculados_t_1").is_not_null() & (pl.col("poblacion_riesgo") > 0)) \
                     .with_columns(desercion_anual_mean=((pl.col("matriculados_t_1") + pl.col("primer_curso") - pl.col("graduados") - pl.col("matriculados")) / pl.col("poblacion_riesgo"))) \
                     .with_columns(pl.col("desercion_anual_mean").clip(lower_bound=0.0, upper_bound=1.0).cast(pl.Float64)) \
                     .select(["codigo_snies_del_programa", "anno", "desercion_anual_mean"])

_max_anno_orig = df_desercion["anno"].max()

_df_existentes = df_desercion.select(["codigo_snies_del_programa", "anno"]).with_columns(pl.lit(True).alias("existe"))
_df_proxy_filtrado = _df_proxy.filter(pl.col("anno") <= _max_anno_orig) \
                              .join(_df_existentes, on=["codigo_snies_del_programa", "anno"], how="left") \
                              .filter(pl.col("existe").is_null()) \
                              .drop("existe")

df_desercion = pl.concat([df_desercion, _df_proxy_filtrado]).sort(["codigo_snies_del_programa", "anno"])
# -------------------------------------------------------------

max_anno_desercion = df_desercion["anno"].max()

df_saber = pl.read_parquet(data_dir / "df_SaberPRO.parquet").with_columns(
    pl.col("codigo_snies_del_programa").cast(pl.Int64)
)
max_anno_saber = df_saber["anno"].max()

import pandas as pd
# Carga de Salario Mínimo Histórico
try:
    df_smmlv = pd.read_excel(data_dir / "SalarioMinimo.xlsx", sheet_name="Series de datos")
    # Limpiar posibles espacios extraños en nombres de columnas (\xa0 de Excel)
    df_smmlv.columns = [c.replace('\xa0', ' ').strip() for c in df_smmlv.columns]
    
    df_smmlv_pl = pl.from_pandas(df_smmlv).select([
        pl.col("Año").cast(pl.Int32).alias("anno_corte"),
        pl.col("Salario mínimo mensual").cast(pl.Float64).alias("smmlv")
    ]).filter(pl.col("anno_corte").is_not_null())
    # Forzar tipo en la base de salarios para evitar fallos de unión
    df_ole_salario = df_ole_salario.with_columns(pl.col("anno_corte").cast(pl.Int32))
except Exception as e:
    print(f"Error cargando SalarioMinimo.xlsx: {e}")
    # Fallback básico 2022
    df_smmlv_pl = pl.DataFrame({"anno_corte": [2022], "smmlv": [1000000.0]})


# Enriquecimiento Origen-Destino con DIVIPOLA
try:
    import pandas as pd
    import shutil
    import tempfile
    import os
    
    # Bypass Windows lock file & missing fastexcel by copying to temp and using pandas
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    shutil.copy2(data_dir / "DIVIPOLA.xlsx", tmp_path)
    df_divi_pd = pd.read_excel(tmp_path)
    df_divi = pl.from_pandas(df_divi_pd)
    try: os.remove(tmp_path)
    except: pass
    
    df_divi = df_divi.select([
        pl.col("DIVIPOLA_MPIO").cast(pl.Int32),
        pl.col("NOMBRE_DEPARTAMENTO").alias("departamento"),
        pl.col("NOMBRE_MPIO").alias("municipio")
    ])
    
    # Origen
    df_ole_m0 = df_ole_m0.join(
        df_divi.rename({"departamento": "departamento_origen", "municipio": "municipio_origen"}), 
        left_on="divipola_mpio_principal", right_on="DIVIPOLA_MPIO", how="left"
    )
    # Destino
    df_ole_m0 = df_ole_m0.join(
        df_divi.rename({"departamento": "departamento_destino", "municipio": "municipio_destino"}), 
        left_on="divipola_mpio_destino", right_on="DIVIPOLA_MPIO", how="left"
    )
    
    # Si algún divipola no cruza, fallback a string genérico
    df_ole_m0 = df_ole_m0.with_columns([
        pl.col("departamento_origen").fill_null(pl.col("divipola_mpio_principal").cast(pl.Utf8)),
        pl.col("departamento_destino").fill_null(pl.col("divipola_mpio_destino").cast(pl.Utf8)),
        pl.col("municipio_origen").fill_null(pl.col("divipola_mpio_principal").cast(pl.Utf8)),
        pl.col("municipio_destino").fill_null(pl.col("divipola_mpio_destino").cast(pl.Utf8))
    ])
except Exception as e:
    print(f"Advertencia: No se pudo cruzar DIVIPOLA.xlsx ({e})")
    df_ole_m0 = df_ole_m0.with_columns([
        pl.col("divipola_mpio_principal").cast(pl.Utf8).alias("departamento_origen"),
        pl.col("divipola_mpio_destino").cast(pl.Utf8).alias("departamento_destino"),
        pl.col("divipola_mpio_principal").cast(pl.Utf8).alias("municipio_origen"),
        pl.col("divipola_mpio_destino").cast(pl.Utf8).alias("municipio_destino")
    ])

max_anno_snies = df_matricula["anno"].max()
max_anno_ole = df_ole_m0["anno_corte"].max()
max_anno_smmlv = df_smmlv_pl["anno_corte"].max()

# Setup UI initial choices (Sin "Todos")
filtros_cols = [
    "institucion_label",
    "snies_label",
    "estado_programa",
    "modalidad",
    "nivel_de_formacion",
    "area_de_conocimiento",
    "nucleo_basico_del_conocimiento",
    "sector"
]

valores_iniciales = {col: sorted(df_snies[col].drop_nulls().unique().to_list()) for col in filtros_cols}
# Para aligerar la carga inicial del DOM, empezamos vacío el filtro de programas (masivo >15k)
valores_iniciales["snies_label"] = []
departamentos_oferta = sorted(df_cobertura["departamento_oferta"].drop_nulls().unique().to_list())

ICONS = {
    "student": fa.icon_svg("user-graduate", "solid"),
}

# UI definition
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.input_selectize("institucion_label", "Institución (Cód - Nombre)", choices=valores_iniciales["institucion_label"], multiple=True),
        ui.input_selectize("snies_label", "Programa Académico (SNIES)", choices=valores_iniciales["snies_label"], multiple=True),
        ui.input_text("keyword_programa", "Búsqueda por Palabra Clave (Programa)", placeholder="ej: ingeniería sistemas"),
        ui.input_selectize("estado_programa", "Estado del Programa", choices=valores_iniciales["estado_programa"], selected=["ACTIVO"], multiple=True),
        ui.input_selectize("modalidad", "Modalidad", choices=valores_iniciales["modalidad"], multiple=True),
        ui.input_selectize("nivel_de_formacion", "Nivel de Formación", choices=valores_iniciales["nivel_de_formacion"], multiple=True),
        ui.input_selectize("area_de_conocimiento", "Área de Conocimiento", choices=valores_iniciales["area_de_conocimiento"], multiple=True),
        ui.input_selectize("nucleo_basico_del_conocimiento", "Núcleo Básico (NBC)", choices=valores_iniciales["nucleo_basico_del_conocimiento"], multiple=True),
        ui.input_selectize("sector", "Sector", choices=valores_iniciales["sector"], multiple=True),
        ui.input_selectize("departamento", "Departamento de Oferta", choices=departamentos_oferta, multiple=True),
        ui.input_selectize("municipio", "Municipio de Oferta", choices=[], multiple=True),
        ui.input_action_button("btn_calcular", "Aplicar Filtros", class_="btn-danger w-100 mt-2 mb-2", style="font-weight: bold; font-size: 1.1em;"),
        ui.input_action_button("btn_preview_report", "Vista Previa Informe", class_="btn-success w-100 mt-2"),
        open="desktop",
    ),
    ui.navset_card_underline(
        ui.nav_panel(
            "Tendencias SNIES",
            ui.layout_columns(
                ui.value_box("Total Instituciones", ui.output_ui("total_instituciones"), showcase=fa.icon_svg("building-columns", "solid")),
                ui.value_box("Programas Académicos", ui.output_ui("total_programas"), showcase=fa.icon_svg("book-open-reader", "solid")),
        ui.value_box(f"Estudiantes Primer Curso ({max_anno_snies:.0f})", ui.output_ui("total_primer_curso"), showcase=ICONS["student"]),
        ui.value_box(f"Total Matriculados ({max_anno_snies:.0f})", ui.output_ui("total_matriculados"), showcase=fa.icon_svg("users", "solid")),
        ui.value_box(f"Total Graduados ({max_anno_snies:.0f})", ui.output_ui("total_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
        fill=False,
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header(ui.HTML("Tendencia de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
            ui.output_ui("plot_primer_curso_total"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
            ui.output_ui("plot_matriculados_total"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia de Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
            ui.output_ui("plot_graduados_total"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
            ui.output_ui("plot_primer_curso"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
            ui.output_ui("plot_matriculados"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
            ui.output_ui("plot_graduados"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header(ui.HTML("Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
            ui.output_data_frame("table_pcurso"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True
        ),
        ui.card(
            ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
            ui.output_data_frame("table_matriculados"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True
        ),
        ui.card(
            ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
            ui.output_data_frame("table_graduados"), 
            ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True
        ),
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(ui.card_header("Información Básica de Programas"), ui.output_data_frame("table"), full_screen=True),
        class_="mb-4"
    ),
    ui.h3("2. Características de los Programas (Costo y Créditos)", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px; margin-top: 30px;"),
    ui.layout_columns(
        ui.value_box("Costo Promedio (Matrícula)", ui.output_ui("kpi_costo_matricula"), showcase=fa.icon_svg("money-check-dollar", "solid")),
        ui.value_box("Mediana Robusta (Matrícula)", ui.output_ui("kpi_mediana_matricula"), showcase=fa.icon_svg("shield-halved", "solid")),
        ui.value_box("Promedio Créditos", ui.output_ui("kpi_promedio_creditos"), showcase=fa.icon_svg("list-check", "solid")),
        fill=False, class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header(ui.HTML("Distribución de <b style='color: #31497e;'>Costo de Matrícula</b> (Solo Privados)")), 
            ui.output_ui("plot_dist_costo_matricula"), 
            ui.card_footer(ui.HTML("Fuente: SNIES.<br>Se calculan promedios para programas del sector privado con valores mayores a cero.<br><i>Nota técnica: El costo de matrícula solo aplica para programas de instituciones privadas regulados por el SNIES.</i>"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 450px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Distribución de <b style='color: #31497e;'>Número de Créditos</b>")), 
            ui.output_ui("plot_dist_creditos"), 
            ui.card_footer(ui.HTML("Fuente: SNIES.<br>Se calculan promedios para todos los programas que reportan créditos académicos mayores a cero."), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 450px;"
        ),
        class_="mb-5"
    )
    ),
        ui.nav_panel(
            "Observatorio Laboral",
            ui.layout_columns(
                ui.value_box(
                    f"Empleabilidad ({max_anno_ole})", 
                    ui.output_ui("kpi_empleabilidad"), 
                    showcase=fa.icon_svg("briefcase", "solid")
                ),
                ui.value_box(
                    f"Dependientes sobre Graduados ({max_anno_ole})", 
                    ui.output_ui("kpi_dependientes_graduados"), 
                    showcase=fa.icon_svg("user-graduate", "solid")
                ),
                ui.value_box(
                    f"Dependientes sobre Cotizantes ({max_anno_ole})", 
                    ui.output_ui("kpi_cotizantes_dependientes"), 
                    showcase=fa.icon_svg("user-tie", "solid")
                ),
                ui.value_box(
                    f"Tasa de Retención Local ({max_anno_ole})", 
                    ui.output_ui("kpi_retencion"), 
                    showcase=fa.icon_svg("thumbtack", "solid")
                ),
                ui.value_box(
                    f"Ratio Salen / Entran ({max_anno_ole})", 
                    ui.output_ui("kpi_ratio"), 
                    showcase=fa.icon_svg("arrow-right-arrow-left", "solid")
                ),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.div(
                    ui.HTML("<b>Nota Metodológica:</b> Los indicadores porcentuales se calculan promediando las tasas de cada programa académico (SNIES). Las métricas de <b>Empleabilidad</b> y <b>Dependientes sobre Graduados</b> toman como base el 100% de los graduados. Las métricas de <b>Movilidad (Retención/Ratio)</b> y <b>Dependientes sobre Cotizantes</b> utilizan exclusivamente la población que registra cotización laboral activa."),
                    style="font-size: 0.85em; color: #555; background-color: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 4px solid #31497e; margin-bottom: 20px;"
                ),
                fill=False
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia de <b style='color: #31497e;'>Empleabilidad</b>")), ui.output_ui("plot_empleabilidad_total"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                ui.card(ui.card_header(ui.HTML("Tendencia de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b>")), ui.output_ui("plot_dependientes_total"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Empleabilidad por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_empleabilidad_sexo"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                ui.card(ui.card_header(ui.HTML("Relación Dependientes sobre Graduados por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_dependientes_sexo"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución de <b style='color: #31497e;'>Empleabilidad</b> ({max_anno_ole})")), ui.output_ui("plot_dist_empleabilidad"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> ({max_anno_ole})")), ui.output_ui("plot_dist_dependientes"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución de <b style='color: #31497e;'>Empleabilidad</b> por Sexo ({max_anno_ole})")), ui.output_ui("plot_dist_empleabilidad_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> por Sexo ({max_anno_ole})")), ui.output_ui("plot_dist_dependientes_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),

            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Matriz de <b style='color: #31497e;'>Graduados que Cotizan</b> (Origen vs Destino) - {max_anno_ole}")), 
                    ui.output_ui("plot_mobility_matrix"), 
                    ui.card_footer(
                        ui.HTML("Sumatoria de Graduados que Cotizan según zona de estudio (Origen) vs zona de cotización laboral (Destino). Fuente: OLE<br>"),
                        ui.HTML("<small><i>Nota técnica: La matriz se construye exclusivamente sobre graduados con registro de cotización laboral. Se aplican filtros de cobertura estrictos del SNIES; registros sin sede operativa validada son excluidos.</i></small>"),
                        style="font-size: 0.85em; color: gray;"
                    ),
                    full_screen=True, style="min-height: 700px;"
                ),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Dependientes sobre Cotizantes</b>")), 
                    ui.output_ui("plot_dependientes_trend"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de la <b style='color: #31497e;'>Tasa de Retención Local</b>")), 
                    ui.output_ui("plot_retencion_trend"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Ratio Salen / Entran</b>")), 
                    ui.output_ui("plot_ratio_trend"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            )
        ),
        ui.nav_panel(
            "Salario de Enganche",
            ui.layout_columns(
                ui.value_box(
                    f"Graduados Cotizantes Dependientes ({max_anno_ole:.0f})", 
                    ui.output_ui("kpi_salario_dependientes_sum"), 
                    showcase=fa.icon_svg("users-viewfinder", "solid")
                ),
                ui.value_box(
                    f"Salario Promedio Estimado ({max_anno_ole:.0f})", 
                    ui.output_ui("kpi_salario_promedio_total"), 
                    showcase=fa.icon_svg("money-bill-trend-up", "solid")
                ),
                ui.value_box(
                    f"Salario Promedio Mujeres ({max_anno_ole:.0f})", 
                    ui.output_ui("kpi_salario_promedio_fem"), 
                    showcase=fa.icon_svg("venus", "solid")
                ),
                ui.value_box(
                    f"Salario Promedio Hombres ({max_anno_ole:.0f})", 
                    ui.output_ui("kpi_salario_promedio_masc"), 
                    showcase=fa.icon_svg("mars", "solid")
                ),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.div(
                    ui.HTML("<b>Nota Metodológica (Salario):</b> El salario promedio se estima ponderando los puntos medios de cada rango salarial del OLE (ej. 1,25 para el rango entre 1 y 1,5 SMMLV). Para '1 SMMLV' se toma 1,0 y para 'Más de 9 SMMLV' se toma 9,0 como base. Estos factores se multiplican por el Salario Mínimo Legal Mensual de cada año (Fuente: Banco de la República)."),
                    style="font-size: 0.85em; color: #555; background-color: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 4px solid #31497e; margin-bottom: 20px;"
                ),
                fill=False
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Rango Salarial</b> ({max_anno_ole:.0f})")), 
                    ui.output_ui("plot_salario_dist_total"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución Salarial por <b style='color: #31497e;'>Sexo</b> ({max_anno_ole:.0f})")), 
                    ui.output_ui("plot_salario_dist_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos corrientes)")), 
                    ui.output_ui("plot_salario_evolucion_total"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos corrientes)")), 
                    ui.output_ui("plot_salario_evolucion_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos constantes - SMMLV {max_anno_ole:.0f})")), 
                    ui.output_ui("plot_salario_evolucion_total_constante"), 
                    ui.card_footer(ui.HTML(f"Fuente: OLE<br>Ajustado a SMMLV de {max_anno_ole:.0f}"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos constantes - SMMLV {max_anno_ole:.0f})")), 
                    ui.output_ui("plot_salario_evolucion_sexo_constante"), 
                    ui.card_footer(ui.HTML(f"Fuente: OLE<br>Ajustado a SMMLV de {max_anno_ole:.0f}"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
        ),
        ui.nav_panel(
            "Deserción",
            ui.layout_columns(
                ui.value_box(
                    "Tasa de Deserción Promedio (%)", 
                    ui.output_ui("kpi_desercion_promedio"), 
                    showcase=fa.icon_svg("user-minus", "solid"),
                ),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Distribución de la <b style='color: #31497e;'>Tasa de Deserción</b> (Último Año)")), 
                    ui.output_ui("plot_dist_desercion"), 
                    ui.card_footer(ui.HTML("Fuente: SPADIES<br>Distribución por programa en pasos de 2%.<br><i>Nota: Para programas de Posgrado sin registro en SPADIES, se estima un proxy de deserción empleando la fórmula: (Matriculados_{t-1} + Primer Curso_{t} - Graduados_{t} - Matriculados_{t}) / (Matriculados_{t-1} + Primer Curso_{t})</i>"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Tendencia Histórica de <b style='color: #31497e;'>Deserción Anual</b>")), 
                    ui.output_ui("plot_trend_desercion"), 
                    ui.card_footer(ui.HTML("Fuente: SPADIES<br>Evolución promedio de los programas seleccionados.<br><i>Nota: Para programas de Posgrado sin registro en SPADIES, se estima un proxy de deserción empleando la fórmula: (Matriculados_{t-1} + Primer Curso_{t} - Graduados_{t} - Matriculados_{t}) / (Matriculados_{t-1} + Primer Curso_{t})</i>"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            )
        ),
        ui.nav_panel(
            "Prueba SABER",
            ui.layout_columns(
                ui.value_box(
                    f"Puntaje Global Promedio ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_saber_global"), 
                    showcase=fa.icon_svg("award", "solid")
                ),
                ui.value_box(
                    f"Razonamiento Cuantitativo ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_saber_razona"), 
                    showcase=fa.icon_svg("calculator", "solid")
                ),
                ui.value_box(
                    f"Lectura Crítica ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_saber_lectura"), 
                    showcase=fa.icon_svg("book-open", "solid")
                ),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.value_box(
                    f"Competencias Ciudadanas ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_saber_ciuda"), 
                    showcase=fa.icon_svg("users-line", "solid")
                ),
                ui.value_box(
                    f"Inglés ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_saber_ingles"), 
                    showcase=fa.icon_svg("language", "solid")
                ),
                ui.value_box(
                    f"Comunicación Escrita ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_saber_escrita"), 
                    showcase=fa.icon_svg("pen-nib", "solid")
                ),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Competencias Genéricas</b> (Promedio por Programa)")), 
                    ui.output_ui("plot_saber_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución del puntaje promedio de los programas capturados por los filtros activos."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución del <b style='color: #31497e;'>Puntaje Global</b> ({max_anno_saber:.0f})")), 
                    ui.output_ui("plot_saber_dist"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Frecuencia relativa de los programas académicos según su puntaje promedio."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Sexo</b>")), 
                    ui.output_ui("plot_saber_count_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución del conteo de estudiantes que presentaron la prueba."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Edad</b>")), 
                    ui.output_ui("plot_saber_count_edad"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución del conteo de estudiantes que presentaron la prueba."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.h3("Evolución Detallada por Perfil Sociodemográfico", class_="mt-5 mb-3", style="color: #31497e; border-bottom: 2px solid #31497e; padding-bottom: 5px;"),
            
            # FILA 1: PUNTAJE GLOBAL
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_saber_trend_global_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("plot_saber_trend_global_edad"), full_screen=True),
            ),
            # FILA 2: RAZONAMIENTO CUANTITATIVO
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_saber_trend_razona_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("plot_saber_trend_razona_edad"), full_screen=True),
            ),
            # FILA 3: LECTURA CRÍTICA
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_saber_trend_lectura_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("plot_saber_trend_lectura_edad"), full_screen=True),
            ),
            # FILA 4: COMPETENCIAS CIUDADANAS
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_saber_trend_ciuda_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("plot_saber_trend_ciuda_edad"), full_screen=True),
            ),
            # FILA 5: INGLÉS
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_saber_trend_ingles_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("plot_saber_trend_ingles_edad"), full_screen=True),
            ),
            # FILA 6: COMUNICACIÓN ESCRITA
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("plot_saber_trend_escrita_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("plot_saber_trend_escrita_edad"), full_screen=True),
                class_="mb-5"
            ),
        ),
        ui.nav_panel(
            "Socio-demografía",
            ui.layout_columns(
                ui.value_box(
                    f"Total de Evaluados ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_demo_evaluados"), 
                    showcase=fa.icon_svg("users", "solid")
                ),
                ui.value_box(
                    f"Programas Académicos ({max_anno_saber:.0f})", 
                    ui.output_ui("kpi_demo_programas"), 
                    showcase=fa.icon_svg("graduation-cap", "solid")
                ),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Sexo</b> ({max_anno_saber:.0f})")), 
                    ui.output_ui("plot_saber_demo_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Distribución porcentual por sexo de los evaluados en el último año."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Grupo de Edad</b> ({max_anno_saber:.0f})")), 
                    ui.output_ui("plot_saber_demo_edad"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Distribución porcentual por rangos de edad en el último año."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Horas de Trabajo</b> ({max_anno_saber:.0f})")), 
                    ui.output_ui("plot_saber_demo_trabajo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Distribución porcentual de la carga laboral reportada por los estudiantes."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Estrato Social</b> ({max_anno_saber:.0f})")), 
                    ui.output_ui("plot_saber_demo_estrato"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Distribución porcentual por estrato socioeconómico de la vivienda."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.h3("Evolución Temporal de la Socio-demografía", class_="mt-5 mb-3", style="color: #31497e; border-bottom: 2px solid #31497e; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Sexo</b>")), 
                    ui.output_ui("plot_saber_demo_sexo_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la composición por género."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Grupo de Edad</b>")), 
                    ui.output_ui("plot_saber_demo_edad_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la composición por rangos de edad."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Horas de Trabajo</b>")), 
                    ui.output_ui("plot_saber_demo_trabajo_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la participación según carga laboral."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Estrato Social</b>")), 
                    ui.output_ui("plot_saber_demo_estrato_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la composición socioeconómica."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
        ),
        ui.nav_panel(
            "Informe de Mercado",
            ui.h2("Previsualización Continua de Informe", class_="mt-4 mb-4", style="color: #1A05A2; font-weight: bold; text-align: center;"),
            
            ui.h3("1. Tendencias SNIES (Oferta y Demanda)", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Total Instituciones", ui.output_ui("prev_kpi_instituciones"), showcase=fa.icon_svg("building-columns", "solid")),
                ui.value_box("Programas Académicos", ui.output_ui("prev_kpi_programas"), showcase=fa.icon_svg("book-open-reader", "solid")),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.value_box("Primer Curso", ui.output_ui("prev_kpi_pcurso"), showcase=fa.icon_svg("user-graduate", "solid")),
                ui.value_box("Matriculados", ui.output_ui("prev_kpi_matriculados"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Graduados", ui.output_ui("prev_kpi_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), ui.output_ui("prev_pcurso_total"), ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), ui.output_ui("prev_matricula_total"), ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia de Estudiantes <b style='color: #31497e;'>Graduados</b>")), ui.output_ui("prev_graduados_total"), ui.card_footer(ui.HTML("Fuente: SNIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), ui.output_ui("prev_pcurso_sexo"), ui.output_ui("prev_caption_pcurso"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), ui.output_ui("prev_matricula_sexo"), ui.output_ui("prev_caption_matricula"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Graduados</b>")), ui.output_ui("prev_graduados_sexo"), ui.output_ui("prev_caption_graduados"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("2. Observatorio Laboral para la Educación (OLE)", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px; mt-5"),
            ui.layout_columns(
                ui.value_box(f"Empleabilidad ({max_anno_ole})", ui.output_ui("prev_kpi_emp"), showcase=fa.icon_svg("briefcase", "solid")),
                ui.value_box(f"Dependientes sobre Graduados ({max_anno_ole})", ui.output_ui("prev_kpi_dep_grad"), showcase=fa.icon_svg("user-graduate", "solid")),
                ui.value_box(f"Dependientes sobre Cotizantes ({max_anno_ole})", ui.output_ui("prev_kpi_dep_cot"), showcase=fa.icon_svg("user-tie", "solid")),
                ui.value_box(f"Tasa de Retención Local ({max_anno_ole})", ui.output_ui("prev_kpi_ret"), showcase=fa.icon_svg("thumbtack", "solid")),
                ui.value_box(f"Ratio Salen / Entran ({max_anno_ole})", ui.output_ui("prev_kpi_ratio"), showcase=fa.icon_svg("arrow-right-arrow-left", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia de <b style='color: #31497e;'>Empleabilidad</b>")), ui.output_ui("prev_ole_emp_total"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b>")), ui.output_ui("prev_ole_dep_total"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Empleabilidad por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_ole_emp_sexo"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Relación Dependientes sobre Graduados por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_ole_dep_sexo"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución de <b style='color: #31497e;'>Empleabilidad</b> ({max_anno_ole})")), ui.output_ui("prev_ole_dist_emp"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> ({max_anno_ole})")), ui.output_ui("prev_ole_dist_dep"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución de <b style='color: #31497e;'>Empleabilidad</b> por Sexo ({max_anno_ole})")), ui.output_ui("prev_ole_dist_emp_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> por Sexo ({max_anno_ole})")), ui.output_ui("prev_ole_dist_dep_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Matriz de <b style='color: #31497e;'>Graduados que Cotizan</b> (Origen vs Destino) - {max_anno_ole}")), ui.output_ui("prev_ole_mobility"), ui.card_footer(ui.HTML("Fuente: OLE<br><small><i>Nota técnica: La matriz se construye exclusivamente sobre graduados con registro de cotización laboral.</i></small>"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Concentración Geográfica: <b style='color: #31497e;'>Origen</b> (Depto/Mpio Formación)")),
                    ui.output_data_frame("prev_ole_top_origen"),
                    ui.card_footer(ui.HTML("Fuente: OLE. Datos completos sin truncar.")),
                    full_screen=True, style="height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Concentración Geográfica: <b style='color: #31497e;'>Destino</b> (Depto/Mpio Vinculación)")),
                    ui.output_data_frame("prev_ole_top_destino"),
                    ui.card_footer(ui.HTML("Fuente: OLE. Datos completos sin truncar.")),
                    full_screen=True, style="height: 450px;"
                ),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Dependientes sobre Cotizantes</b>")), ui.output_ui("prev_ole_trend_dep"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                ui.card(ui.card_header(ui.HTML("Evolución de la <b style='color: #31497e;'>Tasa de Retención Local</b>")), ui.output_ui("prev_ole_trend_ret"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                ui.card(ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Ratio Salen / Entran</b>")), ui.output_ui("prev_ole_trend_ratio"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                class_="mb-5"
            ),

            ui.h3("3. Salarios de Enganche", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Salario Promedio", ui.output_ui("prev_kpi_sal"), showcase=fa.icon_svg("money-bill-trend-up", "solid")),
                ui.value_box("Brecha Género (F)", ui.output_ui("prev_kpi_sal_f"), showcase=fa.icon_svg("venus", "solid")),
                ui.value_box("Brecha Género (M)", ui.output_ui("prev_kpi_sal_m"), showcase=fa.icon_svg("mars", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Rango Salarial</b> ({max_anno_ole:.0f})")), ui.output_ui("prev_sal_dist_total"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución Salarial por <b style='color: #31497e;'>Sexo</b> ({max_anno_ole:.0f})")), ui.output_ui("prev_sal_dist_sexo"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos corrientes)")), ui.output_ui("prev_sal_evol_total"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos corrientes)")), ui.output_ui("prev_sal_evol_sexo"), ui.card_footer(ui.HTML("Fuente: OLE<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos constantes - SMMLV {max_anno_smmlv:.0f})")), ui.output_ui("prev_sal_evol_constante"), ui.card_footer(ui.HTML(f"Fuente: OLE<br>Ajustado a SMMLV de {max_anno_smmlv:.0f}"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos constantes - SMMLV {max_anno_smmlv:.0f})")), ui.output_ui("prev_sal_evol_sexo_constante"), ui.card_footer(ui.HTML(f"Fuente: OLE<br>Ajustado a SMMLV de {max_anno_smmlv:.0f}"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),

            ui.h1("6-Permanencia y Deserción"),
            ui.layout_columns(
                ui.value_box("Tasa Deserción", ui.output_ui("prev_kpi_des"), showcase=fa.icon_svg("user-minus", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Distribución de la <b style='color: #31497e;'>Tasa de Deserción</b> (Último Año)")), ui.output_ui("prev_des_dist"), ui.card_footer(ui.HTML("Fuente: SPADIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia Histórica de <b style='color: #31497e;'>Deserción Anual</b>")), ui.output_ui("prev_des_trend"), ui.card_footer(ui.HTML("Fuente: SPADIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("7. Excelencia Académica", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Puntaje Global Promedio", ui.output_ui("prev_kpi_saber"), showcase=fa.icon_svg("award", "solid")),
                ui.value_box("Razonamiento Cuantitativo", ui.output_ui("prev_kpi_saber_razona"), showcase=fa.icon_svg("calculator", "solid")),
                ui.value_box("Lectura Crítica", ui.output_ui("prev_kpi_saber_lectura"), showcase=fa.icon_svg("book-open", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.value_box("Competencias Ciudadanas", ui.output_ui("prev_kpi_saber_ciuda"), showcase=fa.icon_svg("users-line", "solid")),
                ui.value_box("Inglés", ui.output_ui("prev_kpi_saber_ingles"), showcase=fa.icon_svg("language", "solid")),
                ui.value_box("Comunicación Escrita", ui.output_ui("prev_kpi_saber_escrita"), showcase=fa.icon_svg("pen-nib", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Competencias Genéricas</b> (Promedio por Programa)")), ui.output_ui("prev_saber_trend"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución del <b style='color: #31497e;'>Puntaje Global</b> ({max_anno_saber:.0f})")), ui.output_ui("prev_saber_dist"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_count_sexo"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_count_edad"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),
            ui.h4("8.Evolución prueba saber por perfil sociodemográfico", class_="mt-4 mb-3", style="color: #31497e;"),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_trend_global_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_trend_global_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_trend_razona_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_trend_razona_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_trend_lectura_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_trend_lectura_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_trend_ciuda_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_trend_ciuda_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_trend_ingles_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_trend_ingles_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_saber_trend_escrita_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Edad</b>")), ui.output_ui("prev_saber_trend_escrita_edad"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("9. Perfil Socio-demográfico de los Evaluados", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Total de Evaluados", ui.output_ui("prev_kpi_evaluados"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Programas Académicos", ui.output_ui("prev_kpi_progs_saber"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Sexo</b> ({max_anno_saber:.0f})")), ui.output_ui("prev_demo_sexo"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Caracterización demográfica de los estudiantes evaluados."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Grupo de Edad</b> ({max_anno_saber:.0f})")), ui.output_ui("prev_demo_edad"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Composición por rangos de edad."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Horas de Trabajo</b> ({max_anno_saber:.0f})")), ui.output_ui("prev_demo_trabajo"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Distribución según carga laboral."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Estrato Social</b> ({max_anno_saber:.0f})")), ui.output_ui("prev_demo_estrato"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Composición socioeconómica de los evaluados."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Sexo</b>")), ui.output_ui("prev_demo_sexo_trend"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la composición por sexo."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Grupo de Edad</b>")), ui.output_ui("prev_demo_edad_trend"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la composición por rangos de edad."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Horas de Trabajo</b>")), ui.output_ui("prev_demo_trabajo_trend"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la participación según carga laboral."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Estrato Social</b>")), ui.output_ui("prev_demo_estrato_trend"), ui.card_footer(ui.HTML("Fuente: ICFES<br>Evolución histórica de la composición socioeconómica."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                class_="mb-5"
            ),
            ui.h3("7. Ranking de Competitividad por Ingresos", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Top de Programas por <b style='color: #31497e;'>Volumen de Ingresos</b> (Primer Curso)")),
                    ui.output_data_frame("prev_top_ingresos"),
                    ui.card_footer(ui.HTML("Fuente: SNIES. Tabla ordenada por número de ingresos en el último año reportado (Primer Curso).")),
                    full_screen=True, style="height: 600px;"
                ),
                class_="mb-5"
            )
        ),
        ui.nav_panel(
            "Tendencia Comparada",
            ui.div(
                ui.h2("Análisis Comparativo de Tendencias", class_="m-0", style="color: #31497e; font-weight: bold;"),
                ui.div(
                    ui.download_button(
                        "btn_download_comp_excel", 
                        "DATOS CLÚSTER", 
                        class_="btn-success btn-lg", 
                        icon=fa.icon_svg("file-excel", "solid"), 
                        style="font-weight: 800; padding: 12px 25px;"
                    ),
                    ui.input_action_button("btn_preview_comp", "VISTA PREVIA INFORME COMPARATIVO", class_="btn-primary btn-lg", icon=fa.icon_svg("file-pdf", "solid"), style="background: linear-gradient(135deg, #31497e, #4a69bd); border: none; font-weight: 800; padding: 12px 25px;"),
                    class_="d-flex gap-2"
                ),
                class_="d-flex justify-content-between align-items-center mt-2 mb-3"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("<b style='color: #31497e;'>1. Selección de Programa Base</b>")),
                    ui.input_selectize("comp_snies_base", "Buscar Programa Académico por SNIES o Nombre:", choices=[], multiple=False, width="100%"),
                    ui.output_ui("comp_perfil_snies"),
                    class_="mb-3"
                ),
                ui.card(
                    ui.card_header(ui.HTML("<b style='color: #31497e;'>2. Definir Grupo Comparable</b>")),
                    ui.div(
                        ui.input_switch("switch_modo_manual", ui.HTML("<b>Fijar Lista Manual (Ignorar Atributos)</b>"), value=False),
                        style="padding-bottom: 5px; border-bottom: 1px solid #ddd; margin-bottom: 10px;"
                    ),
                    ui.input_checkbox_group(
                        "comp_criterios",
                        "Seleccione los atributos que deben coincidir para formar el grupo de comparación:",
                        choices={
                            "departamento_oferta": "Mismo Departamento de Oferta",
                            "nivel_de_formacion": "Mismo Nivel de Formación",
                            "modalidad": "Misma Modalidad",
                            "sector": "Mismo Sector (Público/Privado)",
                            "area_de_conocimiento": "Misma Área de Conocimiento",
                            "nucleo_basico_del_conocimiento": "Mismo Núcleo Básico de Conocimiento"
                        },
                        selected=[
                            "departamento_oferta", "nivel_de_formacion", "modalidad", 
                            "sector", "area_de_conocimiento", "nucleo_basico_del_conocimiento"
                        ],
                        inline=True
                    ),
                    ui.div(
                        ui.HTML("<b>Nota Analítica:</b> Al agregar menos atributos de similitud, el grupo de comparación será más masivo (nivel nacional). Al ir activando criterios, la tendencia comparable representará un nicho cada vez más específico."),
                        style="font-size: 0.85em; color: #555; background-color: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 4px solid #31497e; margin-top: 10px;"
                    ),
                    ui.div(
                        ui.HTML("<b>Nota Técnica (Estadística Robusta):</b> Para las <i>Tendencias de Matrícula</i> se utiliza la <b>Mediana</b> y el <b>MAD</b> (Desviación Absoluta de la Mediana) porque presentan una distribución asimétrica tipo Pareto fuertemente sesgada por valores extremos. <br><br><b>Nota:</b> Para los demás indicadores comparativos (Salarios, Deserción, Saber PRO y Empleabilidad) se emplea el <b>Promedio</b> y la <b>Desviación Estándar (SD)</b> tradicional para definir tanto la tendencia central poblacional como la banda sombreada de dispersión."),
                        style="font-size: 0.85em; color: #555; background-color: #fef9e7; padding: 12px; border-radius: 8px; border-left: 4px solid #ffa600; margin-top: 10px;"
                    ),
                    ui.div(
                        ui.input_action_button("btn_abrir_modal", "Personalizar Grupo...", icon=fa.icon_svg("sliders"), class_="btn-secondary w-100"),
                        ui.div(
                            ui.HTML("<b>Nota Informativa:</b> Solo se incluirán programas con registros históricos válidos. La cantidad de programas en el grupo puede diferir si la selección manual incluye códigos sin datos o de programas actualmente inactivos."),
                            style="font-size: 0.8em; color: #666; margin-top: 8px; line-height: 1.2;"
                        ),
                        style="padding-top: 15px;"
                    ),
                    class_="mb-3"
                ),
                class_="mb-4",
                col_widths=(4, 8)
            ),
            ui.h3("1. Grupo de Comparación", class_="mt-4 mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Universo de Comparación (Últ. Año)", ui.output_ui("comp_kpi_universo"), showcase=fa.icon_svg("users-rays", "solid"), class_="card-comparable"),
                ui.value_box("Total Neto Primer Curso", ui.output_ui("comp_kpi_neto_pcurso"), showcase=ICONS["student"], class_="card-comparable"),
                ui.value_box("Total Neto Matriculados", ui.output_ui("comp_kpi_neto_matricula"), showcase=fa.icon_svg("users", "solid"), class_="card-comparable"),
                ui.value_box("Total Neto Graduados", ui.output_ui("comp_kpi_neto_graduados"), showcase=fa.icon_svg("graduation-cap", "solid"), class_="card-comparable"),
                fill=False, class_="mb-5"
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("Tendencias de Matrícula", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Primer Curso)", ui.output_ui("comp_kpi_base_pcurso"), showcase=ICONS["student"]),
                ui.value_box("Programa Seleccionado (Matriculados)", ui.output_ui("comp_kpi_base_matricula"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Programa Seleccionado (Graduados)", ui.output_ui("comp_kpi_base_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.value_box("Mediana Comparable (Primer Curso)", ui.output_ui("comp_kpi_pcurso"), showcase=ICONS["student"], class_="card-comparable"),
                ui.value_box("Mediana Comparable (Matriculados)", ui.output_ui("comp_kpi_matricula"), showcase=fa.icon_svg("users", "solid"), class_="card-comparable"),
                ui.value_box("Mediana Comparable (Graduados)", ui.output_ui("comp_kpi_graduados"), showcase=fa.icon_svg("graduation-cap", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
                    ui.output_ui("plot_comp_pcurso"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br>La línea central representa la mediana y la zona sombreada el intervalo de dispersión basado en la Mediana (±1.48 MAD)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
                    ui.output_ui("plot_comp_matricula"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br>La línea central representa la mediana y la zona sombreada el intervalo de dispersión basado en la Mediana (±1.48 MAD)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
                    ui.output_ui("plot_comp_graduados"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br>La línea central representa la mediana y la zona sombreada el intervalo de dispersión basado en la Mediana (±1.48 MAD)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("2. Distribución de Costos, Créditos y Carga Académica", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Costo de Matrícula)", ui.output_ui("comp_kpi_base_promedio_matricula"), showcase=fa.icon_svg("money-check-dollar", "solid")),
                ui.value_box("Media Comparable (Costo Promedio)", ui.output_ui("comp_kpi_promedio_matricula"), showcase=fa.icon_svg("money-check-dollar", "solid"), class_="card-comparable"),
                ui.value_box("Media Comparable (Mediana Matrícula)", ui.output_ui("comp_kpi_mediana_matricula"), showcase=fa.icon_svg("shield-halved", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4", col_widths=(4, 4, 4)
            ),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Promedio Créditos)", ui.output_ui("comp_kpi_base_promedio_creditos"), showcase=fa.icon_svg("list-check", "solid")),
                ui.value_box("Media Comparable (Promedio Créditos)", ui.output_ui("comp_kpi_promedio_creditos"), showcase=fa.icon_svg("list-check", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Distribución de <b style='color: #31497e;'>Costo de Matrícula</b> (Solo Privados)")), 
                    ui.output_ui("plot_comp_dist_costo_matricula"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br><b>El fondo gris</b> representa a la oferta total de programas privados que reportan costo.<br><b>La distribución púrpura</b> es el Grupo Comparable.<br><b>La línea azul punteada</b> marca el costo del Programa Seleccionado."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Distribución de <b style='color: #31497e;'>Número de Créditos</b>")), 
                    ui.output_ui("plot_comp_dist_creditos"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br><b>El fondo gris</b> representa a la oferta total de programas.<br><b>La distribución púrpura</b> es el Grupo Comparable.<br><b>La línea azul punteada</b> marca los créditos del Programa Seleccionado."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("3. Desempeño Laboral y Empleabilidad", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Empleabilidad)", ui.output_ui("comp_kpi_base_empleabilidad"), showcase=fa.icon_svg("briefcase", "solid")),
                ui.value_box("Media Comparable (Empleabilidad)", ui.output_ui("comp_kpi_empleabilidad"), showcase=fa.icon_svg("briefcase", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Tendencia Total de <b style='color: #31497e;'>Empleabilidad</b>")), 
                    ui.output_ui("plot_comp_ole_empleabilidad"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación.<br>La línea central representa el promedio y la zona sombreada la dispersión muestral (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Dependientes sobre Cotizantes</b>")), 
                    ui.output_ui("plot_comp_ole_dependientes"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación.<br>La línea central representa el promedio y la zona sombreada la dispersión muestral (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.output_ui("comp_dist_empleabilidad_header")), 
                    ui.output_ui("plot_comp_dist_empleabilidad"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación.<br><b>El fondo gris</b> representa a todos los programas del mismo Nivel de Formación.<br><b>La distribución púrpura</b> es el Grupo Comparable.<br><b>La línea azul punteada</b> marca la tasa del Programa Seleccionado."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.div(),
                class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Programa Referencia: <b style='color: #31497e;'>Origen Geográfico</b>")),
                    ui.output_data_frame("comp_ole_top_origen"),
                    ui.card_footer(ui.HTML("Concentración por Depto/Mpio de formación del programa seleccionado.")),
                    full_screen=True, style="height: 400px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Programa Referencia: <b style='color: #31497e;'>Destino Geográfico</b>")),
                    ui.output_data_frame("comp_ole_top_destino"),
                    ui.card_footer(ui.HTML("Concentración por Depto/Mpio de vinculación laboral (cotizantes).")),
                    full_screen=True, style="height: 400px;"
                ),
                class_="mb-5"
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("Distribución Geográfica de la Fuerza Laboral", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.div(
                ui.HTML("<b>Nota sobre Movilidad:</b> Esta tabla detalla el flujo geográfico de los graduados que registran cotización laboral. Se desglosa el <b>Origen</b> (donde cursaron sus estudios) y el <b>Destino</b> (donde se encuentran laborando actualmente)."),
                style="font-size: 0.85em; color: #555; background-color: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 4px solid #31497e; margin-bottom: 20px;"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Graduados por <b>Lugar de Origen</b> (Seleccionado)")), 
                    ui.output_data_frame("comp_table_mobility_origen"), 
                    full_screen=True
                ),
                ui.card(
                    ui.card_header(ui.HTML("Graduados por <b>Lugar de Destino</b> (Seleccionado)")), 
                    ui.output_data_frame("comp_table_mobility_destino"), 
                    full_screen=True
                ),
                class_="mb-5"
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("Salario de Enganche (Estimado)", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Salario Promedio Estimado", ui.output_ui("comp_kpi_base_salario"), showcase=fa.icon_svg("hand-holding-dollar", "solid")),
                ui.value_box("Promedio Estimado (Grupo)", ui.output_ui("comp_kpi_salario"), showcase=fa.icon_svg("money-bill-trend-up", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.output_ui("comp_salario_evolucion_header")), 
                    ui.output_ui("plot_comp_salario_evolucion"), 
                    ui.card_footer(ui.HTML("Fuente: OLE.<br>Salario estimado en pesos constantes. El grupo comparable presenta la media ±1 Desviación Estándar."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Distribución por <b style='color: #31497e;'>Rango Salarial</b> (Últ. Año)")), 
                    ui.output_ui("plot_comp_salario_dist"), 
                    ui.card_footer(ui.HTML("Fuente: OLE.<br>Frecuencia de graduados por rangos salariales comparando el programa con su grupo."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("6. Permanencia y Deserción", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Tasa Deserción Promedio)", ui.output_ui("comp_kpi_base_desercion"), showcase=fa.icon_svg("user-minus", "solid")),
                ui.value_box("Media Comparable (Tasa Deserción Promedio)", ui.output_ui("comp_kpi_desercion"), showcase=fa.icon_svg("user-minus", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Tendencia Histórica de <b style='color: #31497e;'>Deserción Anual</b>")), 
                    ui.output_ui("plot_comp_desercion_trend"), 
                    ui.card_footer(ui.HTML("Fuente: SPADIES.<br>La línea central representa el promedio y la zona sombreada la dispersión muestral (±1 Desviación Estándar).<br><i>Nota: Para programas de Posgrado sin registro en SPADIES, se estima un proxy de deserción empleando la fórmula: (Matriculados_{t-1} + Primer Curso_{t} - Graduados_{t} - Matriculados_{t}) / (Matriculados_{t-1} + Primer Curso_{t})</i>"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.output_ui("comp_dist_desercion_header")), 
                    ui.output_ui("plot_comp_dist_desercion"), 
                    ui.card_footer(ui.HTML("Fuente: SPADIES.<br><b>El fondo gris</b> representa a todos los programas del mismo Nivel de Formación.<br><b>La distribución púrpura</b> es el Grupo Comparable.<br><b>La línea azul punteada</b> marca la tasa del Programa Seleccionado.<br><i>Nota: Para programas de Posgrado sin registro en SPADIES, se estima un proxy de deserción empleando la fórmula: (Matriculados_{t-1} + Primer Curso_{t} - Graduados_{t} - Matriculados_{t}) / (Matriculados_{t-1} + Primer Curso_{t})</i>"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("7. Excelencia Académica", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.div(
                ui.HTML("<b>Nota:</b> Esta sección aplica exclusivamente para programas habilitados en la Prueba SABER PRO. Si no se visualiza ninguna información, el programa seleccionado es de posgrado o carece de registros."),
                style="font-size: 0.85em; color: #555; background-color: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 4px solid #31497e; margin-bottom: 20px;"
            ),
            ui.layout_columns(
                ui.value_box("Programa (Global)", ui.output_ui("comp_kpi_base_saber_global"), showcase=fa.icon_svg("award", "solid")),
                ui.value_box("Comparable (Global)", ui.output_ui("comp_kpi_saber_global"), showcase=fa.icon_svg("award", "solid"), class_="card-comparable"),
                ui.value_box("Programa (Razonamiento)", ui.output_ui("comp_kpi_base_saber_razona"), showcase=fa.icon_svg("calculator", "solid")),
                ui.value_box("Comparable (Razonamiento)", ui.output_ui("comp_kpi_saber_razona"), showcase=fa.icon_svg("calculator", "solid"), class_="card-comparable"),
                fill=False, class_="mb-3", col_widths=(3, 3, 3, 3)
            ),
            ui.layout_columns(
                ui.value_box("Programa (Lectura)", ui.output_ui("comp_kpi_base_saber_lectura"), showcase=fa.icon_svg("book-open", "solid")),
                ui.value_box("Comparable (Lectura)", ui.output_ui("comp_kpi_saber_lectura"), showcase=fa.icon_svg("book-open", "solid"), class_="card-comparable"),
                ui.value_box("Programa (Ciudadanas)", ui.output_ui("comp_kpi_base_saber_ciuda"), showcase=fa.icon_svg("users-line", "solid")),
                ui.value_box("Comparable (Ciudadanas)", ui.output_ui("comp_kpi_saber_ciuda"), showcase=fa.icon_svg("users-line", "solid"), class_="card-comparable"),
                fill=False, class_="mb-3", col_widths=(3, 3, 3, 3)
            ),
            ui.layout_columns(
                ui.value_box("Programa (Inglés)", ui.output_ui("comp_kpi_base_saber_ingles"), showcase=fa.icon_svg("language", "solid")),
                ui.value_box("Comparable (Inglés)", ui.output_ui("comp_kpi_saber_ingles"), showcase=fa.icon_svg("language", "solid"), class_="card-comparable"),
                ui.value_box("Programa (Com. Escrita)", ui.output_ui("comp_kpi_base_saber_escrita"), showcase=fa.icon_svg("pen-nib", "solid")),
                ui.value_box("Comparable (Com. Escrita)", ui.output_ui("comp_kpi_saber_escrita"), showcase=fa.icon_svg("pen-nib", "solid"), class_="card-comparable"),
                fill=False, class_="mb-4", col_widths=(3, 3, 3, 3)
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución - <b style='color: #31497e;'>Puntaje Global</b>")), ui.output_ui("plot_comp_saber_trend_global"), ui.card_footer(ui.HTML("Fuente: ICFES.<br>La línea central representa el promedio y la zona sombreada la dispersión (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML("Evolución - <b style='color: #31497e;'>Razonamiento Cuantitativo</b>")), ui.output_ui("plot_comp_saber_trend_razona"), ui.card_footer(ui.HTML("Fuente: ICFES.<br>La línea central representa el promedio y la zona sombreada la dispersión (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-3", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución - <b style='color: #31497e;'>Lectura Crítica</b>")), ui.output_ui("plot_comp_saber_trend_lectura"), ui.card_footer(ui.HTML("Fuente: ICFES.<br>La línea central representa el promedio y la zona sombreada la dispersión (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML("Evolución - <b style='color: #31497e;'>Competencias Ciudadanas</b>")), ui.output_ui("plot_comp_saber_trend_ciuda"), ui.card_footer(ui.HTML("Fuente: ICFES.<br>La línea central representa el promedio y la zona sombreada la dispersión (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-3", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución - <b style='color: #31497e;'>Inglés</b>")), ui.output_ui("plot_comp_saber_trend_ingles"), ui.card_footer(ui.HTML("Fuente: ICFES.<br>La línea central representa el promedio y la zona sombreada la dispersión (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML("Evolución - <b style='color: #31497e;'>Comunicación Escrita</b>")), ui.output_ui("plot_comp_saber_trend_escrita"), ui.card_footer(ui.HTML("Fuente: ICFES.<br>La línea central representa el promedio y la zona sombreada la dispersión (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("8. Caracterización Sociodemográfica de la Población Estudiantil", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.div(
                ui.HTML("<b>Nota:</b> Distribución sociodemográfica de los estudiantes matriculados en niveles de pregrado en el último año disponible (Saber PRO). Si no se visualiza ninguna información, el programa seleccionado es de posgrado o carece de registros estadísticos."),
                style="font-size: 0.85em; color: #555; background-color: #f8f9fa; padding: 12px; border-radius: 8px; border-left: 4px solid #31497e; margin-bottom: 20px;"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Distribución por <b style='color: #31497e;'>Sexo</b>")), 
                    ui.output_ui("plot_comp_saber_demo_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 400px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Distribución por <b style='color: #31497e;'>Grupo de Edad</b>")), 
                    ui.output_ui("plot_comp_saber_demo_edad"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 400px;"
                ),
                class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Distribución por <b style='color: #31497e;'>Horas de Trabajo</b>")), 
                    ui.output_ui("plot_comp_saber_demo_trabajo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 400px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Distribución por <b style='color: #31497e;'>Estrato Social</b>")), 
                    ui.output_ui("plot_comp_saber_demo_estrato"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 400px;"
                ),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("Ranking Comparativo de Competitividad", class_="mb-3", style="color: #31497e; bold; font-size: 1.5em;"),
            ui.card(
                ui.card_header(ui.HTML("<b>Programas con Mayor Volumen de Ingresos</b> en el Clúster")),
                ui.output_data_frame("prev_top_ingresos_comp"),
                ui.card_footer(ui.HTML("Este ranking muestra los programas del grupo de comparación ordenados por el número de estudiantes de primer curso en el último año reportado.")),
                class_="mb-5"
            )
        )
    ),
    ui.head_content(
        ui.tags.style("""
            shiny-data-grid { text-align: center !important; }
            shiny-data-grid::part(cell) { justify-content: center !important; text-align: center !important; }
            shiny-data-grid::part(headerCell) { justify-content: center !important; text-align: center !important; }
            .shiny-data-grid-table td, .shiny-data-grid-table th { text-align: center !important; }
            .card-header { font-weight: bold; }
            .card-comparable { background-color: #faf7fc !important; border: 1px solid #e2dcf2 !important; box-shadow: 0 4px 6px rgba(103, 79, 149, 0.05) !important; }
        """)
    ),
    ui.include_css(app_dir / "styles.css"),
    title="Dashboard de Mercado de Educación Superior",
    fillable=False,
)

# Constantes de ordenación y colores
RANGO_SALARIO_ORDER = [
    "1 SMMLV",
    "Entre 1 y 1,5 SMMLV",
    "Entre 1,5 y 2,5 SMMLV",
    "Entre 2,5 y 4 SMMLV",
    "Entre 4 y 6 SMMLV",
    "Entre 6 y 9 SMMLV",
    "Más de 9 SMMLV"
]
SALARIO_MIDPOINTS = {
    "1 SMMLV": 1.0,
    "Entre 1 y 1,5 SMMLV": 1.25,
    "Entre 1,5 y 2,5 SMMLV": 2.0,
    "Entre 2,5 y 4 SMMLV": 3.25,
    "Entre 4 y 6 SMMLV": 5.0,
    "Entre 6 y 9 SMMLV": 7.5,
    "Más de 9 SMMLV": 9.0
}
COLOR_SEXO = {
    "FEMENINO": "#f9596f",
    "MASCULINO": "#31497e",
    "NO BINARIO": "#ffa600",
    "TRANS": "#a14e9a"
}

# Server definition
def server(input, output, session):

    # Diccionario local a la sesión para evitar loops infinitos de actualización
    last_choices = {}

    def is_filtered(val):
        # En múltiple selección, un filtro está activo si no es None y tiene al menos un elemento
        return val is not None and len(val) > 0

    @reactive.effect
    def update_filters():
        # Llenar la lista del selector comparativo (solo la primera vez para no agotar la memoria)
        if len(last_choices.get("comp_snies_base", [])) == 0:
            snies_choices = sorted(df_snies["snies_label"].drop_nulls().unique().to_list())
            ui.update_selectize("comp_snies_base", choices=snies_choices, server=True)
            last_choices["comp_snies_base"] = snies_choices

        # Capturamos como tuplas usando `or ()` por si es None
        curr_vals = {
            "institucion_label": input.institucion_label() or (),
            "snies_label": input.snies_label() or (),
            "estado_programa": input.estado_programa() or (),
            "modalidad": input.modalidad() or (),
            "nivel_de_formacion": input.nivel_de_formacion() or (),
            "area_de_conocimiento": input.area_de_conocimiento() or (),
            "nucleo_basico_del_conocimiento": input.nucleo_basico_del_conocimiento() or (),
            "sector": input.sector() or (),
            "departamento": input.departamento() or (),
            "municipio": input.municipio() or ()
        }

        # Sub-función para filtrar todas las columnas usando DF base excepto una elegida
        def filter_except(exclude_col):
            df = df_snies
            for col in filtros_cols:
                if col != exclude_col and is_filtered(curr_vals.get(col)):
                    df = df.filter(pl.col(col).is_in(curr_vals[col]))
            
            # Sub-filtrado de cobertura geográfica (Depto / Mpio)
            df_cob_filt = df_cobertura
            if exclude_col != "departamento" and is_filtered(curr_vals.get("departamento")):
                df_cob_filt = df_cob_filt.filter(pl.col("departamento_oferta").is_in(curr_vals["departamento"]))
                
            if exclude_col != "municipio" and is_filtered(curr_vals.get("municipio")):
                df_cob_filt = df_cob_filt.filter(pl.col("municipio_oferta").is_in(curr_vals["municipio"]))
                
            # Solo restringir SNIES si hubo algún filtro de cobertura
            if exclude_col != "departamento" and is_filtered(curr_vals.get("departamento")) or \
               exclude_col != "municipio" and is_filtered(curr_vals.get("municipio")):
                df = df.filter(pl.col("codigo_snies_del_programa").is_in(df_cob_filt["codigo_snies_del_programa"].unique()))
            
            return df

        # Actualizar opciones filtro de departamento
        df_for_dept = filter_except("departamento")
        valid_snies_dept = df_for_dept["codigo_snies_del_programa"].unique()
        valid_depts = df_cobertura.filter(pl.col("codigo_snies_del_programa").is_in(valid_snies_dept))["departamento_oferta"].drop_nulls().unique().to_list()
        dept_choices = sorted(valid_depts)
        
        new_dept = [v for v in curr_vals["departamento"] if v in dept_choices]
        new_dept = new_dept if new_dept else None
        
        if last_choices.get("departamento") != dept_choices:
            ui.update_selectize("departamento", choices=dept_choices, selected=new_dept)
            last_choices["departamento"] = dept_choices

        # Actualizar opciones filtro de municipio
        df_for_mpio = filter_except("municipio")
        valid_snies_mpio = df_for_mpio["codigo_snies_del_programa"].unique()
        # El municipio depende fuertemente de qué departamentos estén seleccionados
        df_cob_mpio = df_cobertura.filter(pl.col("codigo_snies_del_programa").is_in(valid_snies_mpio))
        if is_filtered(curr_vals["departamento"]):
            df_cob_mpio = df_cob_mpio.filter(pl.col("departamento_oferta").is_in(curr_vals["departamento"]))
        
        valid_mpios = df_cob_mpio["municipio_oferta"].drop_nulls().unique().to_list()
        mpio_choices = sorted(valid_mpios)
        
        new_mpio = [v for v in curr_vals.get("municipio", []) if v in mpio_choices]
        new_mpio = new_mpio if new_mpio else None
        
        if last_choices.get("municipio") != mpio_choices:
            ui.update_selectize("municipio", choices=mpio_choices, selected=new_mpio)
            last_choices["municipio"] = mpio_choices

        # Actualizar opciones de los demás filtros
        for col in filtros_cols:
            df_for_col = filter_except(col)
            choices = sorted(df_for_col[col].drop_nulls().unique().to_list())
            
            raw_curr = getattr(input, col)()
            if not raw_curr: raw_curr = []
            elif isinstance(raw_curr, str): raw_curr = [raw_curr]
            
            new_val = [v for v in raw_curr if v in choices]
            new_val = new_val if new_val else None
            
            if last_choices.get(col) != choices:
                is_heavy = col == "snies_label"
                if is_heavy and raw_curr:
                    # Actualizar un selectize con server=True borra la selección del usuario en memoria.
                    # Si el usuario ya eligió algo, solo actualizamos si la opción se volvió inválida.
                    if set(new_val or []) != set(raw_curr):
                        ui.update_selectize(col, choices=choices, selected=new_val, server=True)
                        last_choices[col] = choices
                else:
                    ui.update_selectize(col, choices=choices, selected=new_val, server=is_heavy)
                    last_choices[col] = choices

    @reactive.calc
    @reactive.event(lambda: input.btn_calcular(), ignore_none=False)
    def isolated_filters():
        return {
            "institucion_label": input.institucion_label(),
            "snies_label": input.snies_label(),
            "keyword_programa": input.keyword_programa(),
            "estado_programa": input.estado_programa(),
            "modalidad": input.modalidad(),
            "nivel_de_formacion": input.nivel_de_formacion(),
            "area_de_conocimiento": input.area_de_conocimiento(),
            "nucleo_basico_del_conocimiento": input.nucleo_basico_del_conocimiento(),
            "sector": input.sector(),
            "departamento": input.departamento(),
            "municipio": input.municipio()
        }

    def _apply_keyword_filter(df, keyword_raw):
        """Filtra df_snies por palabras clave sobre programa_academico (AND lógico, case-insensitive)."""
        if not keyword_raw or not keyword_raw.strip():
            return df
        palabras = [p.strip().lower() for p in keyword_raw.split() if p.strip()]
        if not palabras:
            return df
        mask = pl.lit(True)
        for palabra in palabras:
            mask = mask & pl.col("programa_academico").str.to_lowercase().str.contains(palabra)
        return df.filter(mask)

    @reactive.calc
    def filtered_snies_no_geo():
        df = df_snies
        f_vals = isolated_filters()
        
        for col in filtros_cols:
            val = f_vals[col]
            if is_filtered(val): 
                df = df.filter(pl.col(col).is_in(val))
        
        # Filtro por palabras clave sobre programa_academico (AND)
        df = _apply_keyword_filter(df, f_vals.get("keyword_programa", ""))
        return df

    @reactive.calc
    def filtered_snies():
        df = df_snies
        f_vals = isolated_filters()
        
        for col in filtros_cols:
            val = f_vals[col]
            if is_filtered(val): 
                df = df.filter(pl.col(col).is_in(val))
        
        # Filtro por palabras clave sobre programa_academico (AND)
        df = _apply_keyword_filter(df, f_vals.get("keyword_programa", ""))
        
        # Filtro de Cobertura Geográfica
        dept = f_vals["departamento"]
        mpio = f_vals["municipio"]
        df_cob = df_cobertura
        has_geo = False
        
        if is_filtered(dept):
            df_cob = df_cob.filter(pl.col("departamento_oferta").is_in(dept))
            has_geo = True
        
        if is_filtered(mpio):
            df_cob = df_cob.filter(pl.col("municipio_oferta").is_in(mpio))
            has_geo = True
            
        if has_geo:
            valid_snies_in_geo = df_cob["codigo_snies_del_programa"].unique()
            df = df.filter(pl.col("codigo_snies_del_programa").is_in(valid_snies_in_geo))
            
        return df

    @reactive.calc
    def valid_divipolas():
        snies_filtered = filtered_snies()
        if len(snies_filtered) == 0:
            return pl.Series(name="snies_divipola", dtype=pl.Utf8)
            
        valid_snies_codes = snies_filtered["codigo_snies_del_programa"].unique()
        
        cobertura_filtered = df_cobertura.filter(
            pl.col("codigo_snies_del_programa").is_in(valid_snies_codes)
        )
        
        dept_vals = isolated_filters()["departamento"]
        if is_filtered(dept_vals):
            cobertura_filtered = cobertura_filtered.filter(
                pl.col("departamento_oferta").is_in(dept_vals)
            )
        
        return cobertura_filtered["snies_divipola"].unique()

    @reactive.calc
    def calc_kpi_empleabilidad():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0: return "Sin dato"
        max_anno_corte = df_ole_m0["anno_corte"].max()
        ole_filtered = df_ole_m0.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos) & (pl.col("anno_corte") == max_anno_corte))
        if len(ole_filtered) == 0: return "Sin dato"
        df_snies_agg = ole_filtered.group_by("codigo_snies_del_programa").agg([pl.col("graduados_que_cotizan").sum().alias("cotizan"), pl.col("graduados").sum().alias("total")])
        df_snies_agg = df_snies_agg.filter(pl.col("total") > 0)
        if len(df_snies_agg) == 0: return "0%"
        df_snies_agg = df_snies_agg.with_columns((pl.col("cotizan") / pl.col("total")).alias("tasa_programa"))
        promedio_empleabilidad = df_snies_agg["tasa_programa"].mean()
        if promedio_empleabilidad is None: return "0,0%"
        return format_pct_es(promedio_empleabilidad)

    @render.ui
    def kpi_empleabilidad():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_empleabilidad()}</div>")

    @reactive.calc
    def calc_kpi_cotizantes_dependientes():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0: return "0%"
        max_anno_corte = df_ole_m0["anno_corte"].max()
        ole_filtered = df_ole_m0.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos) & (pl.col("anno_corte") == max_anno_corte))
        if len(ole_filtered) == 0: return "0%"
        df_snies_agg = ole_filtered.group_by("codigo_snies_del_programa").agg([pl.col("graduados_cotizantes_dependientes").sum().alias("dependientes"), pl.col("graduados_que_cotizan").sum().alias("total_cotizan")])
        df_snies_agg = df_snies_agg.filter(pl.col("total_cotizan") > 0)
        if len(df_snies_agg) == 0: return "0%"
        df_snies_agg = df_snies_agg.with_columns((pl.col("dependientes") / pl.col("total_cotizan")).alias("tasa_programa"))
        promedio_dependientes = df_snies_agg["tasa_programa"].mean()
        if promedio_dependientes is None: return "0,0%"
        return format_pct_es(promedio_dependientes)

    @render.ui
    def kpi_cotizantes_dependientes():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_cotizantes_dependientes()}</div>")

    @reactive.calc
    def calc_kpi_dependientes_graduados():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0: return "0%"
        max_anno_corte = df_ole_m0["anno_corte"].max()
        ole_filtered = df_ole_m0.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos) & (pl.col("anno_corte") == max_anno_corte))
        if len(ole_filtered) == 0: return "0%"
        df_snies_agg = ole_filtered.group_by("codigo_snies_del_programa").agg([pl.col("graduados_cotizantes_dependientes").sum().alias("dependientes"), pl.col("graduados").sum().alias("total_graduados")]).filter(pl.col("total_graduados") > 0)
        if len(df_snies_agg) == 0: return "0%"
        df_snies_agg = df_snies_agg.with_columns((pl.col("dependientes") / pl.col("total_graduados")).alias("tasa"))
        promedio = df_snies_agg["tasa"].mean()
        return format_pct_es(promedio) if promedio is not None else "0,0%"

    @render.ui
    def kpi_dependientes_graduados():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_dependientes_graduados()}</div>")

    @reactive.calc
    def calc_total_instituciones():
        snies_filtered = filtered_snies()
        total = snies_filtered["nombre_institucion"].n_unique()
        return format_num_es(total)

    @render.ui
    def total_instituciones():
        return calc_total_instituciones()

    @reactive.calc
    def calc_total_programas():
        snies_filtered = filtered_snies()
        total = snies_filtered["codigo_snies_del_programa"].n_unique()
        return format_num_es(total)

    @render.ui
    def total_programas():
        return calc_total_programas()

    @reactive.calc
    def calc_costo_matricula_data():
        snies_filtered = filtered_snies()
        # Solo sector PRIVADO y costo > 0
        df = snies_filtered.filter((pl.col("sector") == "PRIVADO") & (pl.col("costo_matricula_estud_nuevos") > 0))
        return df["costo_matricula_estud_nuevos"].to_list()

    @reactive.calc
    def calc_promedio_creditos_data():
        snies_filtered = filtered_snies()
        # Todos los programas y creditos > 0
        df = snies_filtered.filter(pl.col("numero_creditos") > 0)
        return df["numero_creditos"].to_list()

    @render.ui
    def kpi_costo_matricula():
        data = calc_costo_matricula_data()
        if not data: return ui.HTML("<span style='color: gray;'>Sin datos</span>")
        import numpy as np
        avg = np.mean(data)
        std = np.std(data)
        return ui.HTML(f"""
            <div style='font-size: 38px; font-weight: bold; color: #31497e; line-height: 1;'>${format_num_es(avg)}</div>
            <div style='font-size: 15px; color: #666; margin-top: 4px;'>± {format_num_es(std)} (SD)</div>
        """)

    @render.ui
    def kpi_mediana_matricula():
        data = calc_costo_matricula_data()
        if not data: return ui.HTML("<span style='color: gray;'>Sin datos</span>")
        import numpy as np
        median = np.median(data)
        # MAD (Median Absolute Deviation) escalado por 1.4826
        mad = np.median([abs(x - median) for x in data]) * 1.4826
        return ui.HTML(f"""
            <div style='font-size: 38px; font-weight: bold; color: #31497e; line-height: 1;'>${format_num_es(median)}</div>
            <div style='font-size: 15px; color: #666; margin-top: 4px;'>± {format_num_es(mad)} (MAD)</div>
        """)

    @render.ui
    def kpi_promedio_creditos():
        data = calc_promedio_creditos_data()
        if not data: return ui.HTML("<span style='color: gray;'>Sin datos</span>")
        import numpy as np
        avg = np.mean(data)
        std = np.std(data)
        return ui.HTML(f"""
            <div style='font-size: 38px; font-weight: bold; color: #31497e; line-height: 1;'>{avg:.1f}</div>
            <div style='font-size: 15px; color: #666; margin-top: 4px;'>± {std:.1f} (SD)</div>
        """)

    @render.ui
    def plot_dist_costo_matricula():
        data = calc_costo_matricula_data()
        if not data: return ui.HTML(pio.to_html(go.Figure(), full_html=False, include_plotlyjs="cdn"))
        import pandas as pd
        df_pd = pd.DataFrame({"costo": data})
        fig = px.histogram(df_pd, x="costo", histnorm='percent')
        fig.update_traces(
            marker=dict(color="#31497e"), 
            marker_line_width=1, 
            marker_line_color="white",
            xbins=dict(size=200000) # Bin de 200 mil
        )
        fig.update_layout(
            showlegend=False, plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="Costo de Matrícula ($)", yaxis_title="Porcentaje (%)",
            xaxis=dict(tickformat="$,.0f")
        )
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def plot_dist_creditos():
        data = calc_promedio_creditos_data()
        if not data: return ui.HTML(pio.to_html(go.Figure(), full_html=False, include_plotlyjs="cdn"))
        import pandas as pd
        df_pd = pd.DataFrame({"creditos": data})
        fig = px.histogram(df_pd, x="creditos", histnorm='percent')
        fig.update_traces(marker=dict(color="#674f95"), marker_line_width=1, marker_line_color="white")
        fig.update_layout(
            showlegend=False, plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="Número de Créditos", yaxis_title="Porcentaje (%)"
        )
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_total_primer_curso():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return "0"
        max_anno = df_pcurso["anno"].max()
        total = df_pcurso.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") == max_anno))["primer_curso_sum"].sum()
        return format_num_es(total)

    @render.ui
    def total_primer_curso():
        return calc_total_primer_curso()

    @reactive.calc
    def calc_total_matriculados():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return "0"
        max_anno = df_matricula["anno"].max()
        total = df_matricula.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") == max_anno))["matricula_sum"].sum()
        return format_num_es(total)

    @render.ui
    def total_matriculados():
        return calc_total_matriculados()

    @reactive.calc
    def calc_total_graduados():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return "0"
        max_anno = df_graduados["anno"].max()
        total = df_graduados.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") == max_anno))["graduados_sum"].sum()
        return format_num_es(total)

    @render.ui
    def total_graduados():
        return calc_total_graduados()

    @render.data_frame
    def table():
        df = filtered_snies().select([
            "nombre_institucion", 
            "programa_academico", 
            "numero_creditos", 
            "costo_matricula_estud_nuevos"
        ])
        
        df = df.rename({
            "nombre_institucion": "Institución",
            "programa_academico": "Programa Académico",
            "numero_creditos": "Créditos",
            "costo_matricula_estud_nuevos": "Valor Matrícula"
        })
        
        def format_currency(x):
            return f"$ {format_num_es(x).rjust(15, ' ')}"

        df = df.with_columns(
            pl.col("Valor Matrícula").map_elements(format_currency, return_dtype=pl.Utf8)
        )
        
        return render.DataGrid(df, filters=True, width="100%")

    def create_gender_table(df_source, divipolas, col_name):
        import pandas as pd
        if len(divipolas) == 0:
            return pd.DataFrame()
        df_sexo = df_source.filter(
            pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)
        ).group_by(["anno", "sexo"]).agg(pl.col(col_name).sum())
        
        if len(df_sexo) == 0:
            return pd.DataFrame()
            
        df_total = df_sexo.group_by("anno").agg(pl.col(col_name).sum().alias("Total"))
        df_pivot = df_sexo.pivot(index="anno", on="sexo", values=col_name, aggregate_function="sum")
        for col in ["FEMENINO", "MASCULINO", "NO BINARIO", "TRANS"]:
            if col not in df_pivot.columns:
                df_pivot = df_pivot.with_columns(pl.lit(0.0).alias(col))
                
        res = df_total.join(df_pivot, on="anno", how="left").select(
            pl.col("anno").cast(pl.Int64).alias("Año"),
            pl.col("FEMENINO").fill_null(0).alias("Femenino"),
            pl.col("MASCULINO").fill_null(0).alias("Masculino"),
            pl.col("NO BINARIO").fill_null(0).alias("No Binario"),
            pl.col("TRANS").fill_null(0).alias("Trans"),
            pl.col("Total").fill_null(0).alias("Total")
        ).sort("Año", descending=False)
        
        df_pd = res.to_pandas().round(0)
        for col in ["Femenino", "Masculino", "No Binario", "Trans", "Total"]:
            if col in df_pd.columns:
                df_pd[col] = df_pd[col].apply(lambda x: f"{x:,.0f}".replace(",", "."))
                
        return df_pd

    @reactive.calc
    def calc_table_pcurso():
        divipolas = valid_divipolas()
        return create_gender_table(df_pcurso, divipolas, "primer_curso_sum")

    @render.data_frame
    def table_pcurso():
        df_pd = calc_table_pcurso()
        return render.DataGrid(df_pd, filters=False, width="100%", selection_mode="none")

    @reactive.calc
    def calc_table_matriculados():
        divipolas = valid_divipolas()
        return create_gender_table(df_matricula, divipolas, "matricula_sum")

    @render.data_frame
    def table_matriculados():
        df_pd = calc_table_matriculados()
        return render.DataGrid(df_pd, filters=False, width="100%", selection_mode="none")

    def create_ole_trend_df(measure_numerator, measure_denominator, group_by_cols):
        import pandas as pd
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        
        if len(snies_codigos) == 0:
            return pd.DataFrame(columns=group_by_cols + ["tasa"])
            
        ole_filtered = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) & 
            (pl.col("anno_corte") >= 2016)
        )
        if len(ole_filtered) == 0:
            return pd.DataFrame(columns=group_by_cols + ["tasa"])
            
        agg_df = ole_filtered.group_by(group_by_cols).agg([
            pl.col(measure_numerator).sum().alias("num"),
            pl.col(measure_denominator).sum().alias("den")
        ])
        
        agg_df = agg_df.filter(pl.col("den") > 0)
        if len(agg_df) == 0:
            return pd.DataFrame(columns=group_by_cols + ["tasa"])
            
        agg_df = agg_df.with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
        
        final_group_cols = [c for c in group_by_cols if c != "codigo_snies_del_programa"]
        
        res_df = agg_df.group_by(final_group_cols).agg(pl.col("tasa").mean()).sort(final_group_cols)
        return res_df.to_pandas()

    @reactive.calc
    def calc_plot_empleabilidad_total():
        df_pd = create_ole_trend_df("graduados_que_cotizan", "graduados", ["anno_corte", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", text="tasa", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año de Corte", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Tasa de Empleabilidad", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=".1%", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_empleabilidad_total():
        return ui.HTML(pio.to_html(calc_plot_empleabilidad_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_dependientes_total():
        df_pd = create_ole_trend_df("graduados_cotizantes_dependientes", "graduados", ["anno_corte", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", text="tasa", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año de Corte", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Relación Dependientes sobre Graduados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=".1%", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_dependientes_total():
        return ui.HTML(pio.to_html(calc_plot_dependientes_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_empleabilidad_sexo():
        df_pd = create_ole_trend_df("graduados_que_cotizan", "graduados", ["anno_corte", "sexo", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", color="sexo", color_discrete_map=COLOR_SEXO, text="tasa", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        for trace in fig.data:
            trace.marker = dict(size=9, color="white", line=dict(width=1.5, color=trace.line.color))
            trace.line.width = 2
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año de Corte", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Tasa de Empleabilidad", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=".1%", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_empleabilidad_sexo():
        return ui.HTML(pio.to_html(calc_plot_empleabilidad_sexo(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_dependientes_sexo():
        df_pd = create_ole_trend_df("graduados_cotizantes_dependientes", "graduados", ["anno_corte", "sexo", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", color="sexo", color_discrete_map=COLOR_SEXO, text="tasa", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        for trace in fig.data:
            trace.marker = dict(size=9, color="white", line=dict(width=1.5, color=trace.line.color))
            trace.line.width = 2
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año de Corte", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Relación Dependientes sobre Graduados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=".1%", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_dependientes_sexo():
        return ui.HTML(pio.to_html(calc_plot_dependientes_sexo(), full_html=False, include_plotlyjs="cdn"))

    def get_ole_distribution_df(measure_numerator, measure_denominator, group_by_cols=["codigo_snies_del_programa"]):
        import pandas as pd
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        
        if len(snies_codigos) == 0:
            return pd.DataFrame()
            
        max_anno_corte = df_ole_m0["anno_corte"].max()
        ole_filtered = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) & 
            (pl.col("anno_corte") == max_anno_corte)
        )
        if len(ole_filtered) == 0:
            return pd.DataFrame()
            
        agg_df = ole_filtered.group_by(group_by_cols).agg([
            pl.col(measure_numerator).sum().alias("num"),
            pl.col(measure_denominator).sum().alias("den")
        ])
        
        agg_df = agg_df.filter(pl.col("den") > 0)
        if len(agg_df) == 0:
            return pd.DataFrame()
            
        agg_df = agg_df.with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
        return agg_df.to_pandas()

    @reactive.calc
    def calc_plot_dist_empleabilidad():
        df_pd = get_ole_distribution_df("graduados_que_cotizan", "graduados")
        if df_pd.empty: return go.Figure()
        # Caso especial: 1 solo programa o todos con el mismo valor
        if len(df_pd) == 1 or df_pd["tasa"].nunique() == 1:
            val = df_pd["tasa"].iloc[0]
            fig = go.Figure()
            fig.add_vline(x=val, line_width=4, line_color="#31497e")
            fig.add_annotation(x=val, y=0.5, yref="paper", text=f"<b>{val:.1%}</b><br>Valor del programa",
                               showarrow=True, arrowhead=2, arrowcolor="#31497e", font=dict(size=16, color="#31497e"),
                               bgcolor="white", bordercolor="#31497e", borderwidth=2)
            fig.update_layout(showlegend=False, plot_bgcolor='white', paper_bgcolor='white',
                              margin=dict(l=20, r=20, t=40, b=20),
                              xaxis=dict(title=dict(text="Tasa de Empleabilidad", font=dict(size=17)), tickformat=".0%",
                                         range=[max(0, val-0.15), min(1, val+0.15)], showgrid=True, gridcolor='#EEEEEE'),
                              yaxis=dict(visible=False),
                              annotations=[dict(x=0.5, y=1.05, xref="paper", yref="paper", showarrow=False,
                                               text="<i>Programa único seleccionado — se muestra valor puntual</i>",
                                               font=dict(size=12, color="#888"))])
            return fig
        fig = px.histogram(df_pd, x="tasa", histnorm='percent', text_auto='.1f')
        fig.update_traces(marker=dict(color="#31497e"), xbins=dict(start=0.0, end=1.0, size=0.05), 
                          marker_line_width=1, marker_line_color="white", textposition='outside', textfont_size=11)
        fig.update_layout(
            showlegend=False,
            plot_bgcolor='white',
            paper_bgcolor='white',
            separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(
                title=dict(text="Tasa de Empleabilidad", font=dict(size=17), standoff=20),
                tickfont=dict(size=15), 
                tickformat=".0%",
                dtick=0.05,
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            ),
            yaxis=dict(
                title=dict(text="Porcentaje de Programas", font=dict(size=17), standoff=20),
                tickfont=dict(size=15),
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            )
        )
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}% de programas<extra></extra>')
        return fig

    @render.ui
    def plot_dist_empleabilidad():
        return ui.HTML(pio.to_html(calc_plot_dist_empleabilidad(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_dist_dependientes():
        df_pd = get_ole_distribution_df("graduados_cotizantes_dependientes", "graduados")
        if df_pd.empty: return go.Figure()
        # Caso especial: 1 solo programa o todos con el mismo valor
        if len(df_pd) == 1 or df_pd["tasa"].nunique() == 1:
            val = df_pd["tasa"].iloc[0]
            fig = go.Figure()
            fig.add_vline(x=val, line_width=4, line_color="#31497e")
            fig.add_annotation(x=val, y=0.5, yref="paper", text=f"<b>{val:.1%}</b><br>Valor del programa",
                               showarrow=True, arrowhead=2, arrowcolor="#31497e", font=dict(size=16, color="#31497e"),
                               bgcolor="white", bordercolor="#31497e", borderwidth=2)
            fig.update_layout(showlegend=False, plot_bgcolor='white', paper_bgcolor='white',
                              margin=dict(l=20, r=20, t=40, b=20),
                              xaxis=dict(title=dict(text="Dependientes sobre Graduados", font=dict(size=17)), tickformat=".0%",
                                         range=[max(0, val-0.15), min(1, val+0.15)], showgrid=True, gridcolor='#EEEEEE'),
                              yaxis=dict(visible=False),
                              annotations=[dict(x=0.5, y=1.05, xref="paper", yref="paper", showarrow=False,
                                               text="<i>Programa único seleccionado — se muestra valor puntual</i>",
                                               font=dict(size=12, color="#888"))])
            return fig
        fig = px.histogram(df_pd, x="tasa", histnorm='percent', text_auto='.1f')
        fig.update_traces(marker=dict(color="#31497e"), xbins=dict(start=0.0, end=1.0, size=0.05), 
                          marker_line_width=1, marker_line_color="white", textposition='outside', textfont_size=11)
        fig.update_layout(
            showlegend=False,
            plot_bgcolor='white',
            paper_bgcolor='white',
            separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(
                title=dict(text="Dependientes sobre Graduados", font=dict(size=17), standoff=20),
                tickfont=dict(size=15), 
                tickformat=".0%",
                dtick=0.05,
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            ),
            yaxis=dict(
                title=dict(text="Porcentaje de Programas", font=dict(size=17), standoff=20),
                tickfont=dict(size=15),
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            )
        )
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}% de programas<extra></extra>')
        return fig

    @render.ui
    def plot_dist_dependientes():
        return ui.HTML(pio.to_html(calc_plot_dist_dependientes(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_dist_empleabilidad_sexo():
        df_pd = get_ole_distribution_df("graduados_que_cotizan", "graduados", ["codigo_snies_del_programa", "sexo"])
        if df_pd.empty: return go.Figure()
        # Caso especial: 1 solo programa (pocos puntos o sin variabilidad)
        if df_pd["codigo_snies_del_programa"].nunique() == 1 or df_pd["tasa"].nunique() <= len(df_pd["sexo"].unique()):
            fig = go.Figure()
            for sexo_val, color in COLOR_SEXO.items():
                sub = df_pd[df_pd["sexo"] == sexo_val]
                if sub.empty: continue
                val = sub["tasa"].iloc[0]
                fig.add_vline(x=val, line_width=3, line_dash="dash", line_color=color,
                              annotation_text=f"<b>{sexo_val}: {val:.1%}</b>",
                              annotation_position="top", annotation_font=dict(color=color, size=13))
            all_vals = df_pd["tasa"].tolist()
            center = sum(all_vals) / len(all_vals) if all_vals else 0.5
            fig.update_layout(showlegend=False, plot_bgcolor='white', paper_bgcolor='white',
                              margin=dict(l=20, r=20, t=60, b=20),
                              xaxis=dict(title=dict(text="Tasa de Empleabilidad", font=dict(size=17)), tickformat=".0%",
                                         range=[max(0, center-0.2), min(1, center+0.2)], showgrid=True, gridcolor='#EEEEEE'),
                              yaxis=dict(visible=False),
                              annotations=[dict(x=0.5, y=1.08, xref="paper", yref="paper", showarrow=False,
                                               text="<i>Programa único — se muestran valores puntuales por sexo</i>",
                                               font=dict(size=12, color="#888"))])
            return fig
        fig = px.histogram(df_pd, x="tasa", color="sexo", color_discrete_map=COLOR_SEXO, histnorm='percent', barmode='group')
        fig.update_traces(xbins=dict(start=0.0, end=1.0, size=0.05), marker_line_width=1, marker_line_color="white")
        fig.update_layout(
            legend_title_text="Sexo",
            plot_bgcolor='white',
            paper_bgcolor='white',
            separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(
                title=dict(text="Tasa de Empleabilidad", font=dict(size=17), standoff=20),
                tickfont=dict(size=15), 
                tickformat=".0%",
                dtick=0.05,
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            ),
            yaxis=dict(
                title=dict(text="Porcentaje de Sub-grupos", font=dict(size=17), standoff=20),
                tickfont=dict(size=15),
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            )
        )
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}% de sub-grupos<extra></extra>')
        return fig

    @render.ui
    def plot_dist_empleabilidad_sexo():
        return ui.HTML(pio.to_html(calc_plot_dist_empleabilidad_sexo(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_dist_dependientes_sexo():
        df_pd = get_ole_distribution_df("graduados_cotizantes_dependientes", "graduados", ["codigo_snies_del_programa", "sexo"])
        if df_pd.empty: return go.Figure()
        # Caso especial: 1 solo programa
        if df_pd["codigo_snies_del_programa"].nunique() == 1 or df_pd["tasa"].nunique() <= len(df_pd["sexo"].unique()):
            fig = go.Figure()
            for sexo_val, color in COLOR_SEXO.items():
                sub = df_pd[df_pd["sexo"] == sexo_val]
                if sub.empty: continue
                val = sub["tasa"].iloc[0]
                fig.add_vline(x=val, line_width=3, line_dash="dash", line_color=color,
                              annotation_text=f"<b>{sexo_val}: {val:.1%}</b>",
                              annotation_position="top", annotation_font=dict(color=color, size=13))
            all_vals = df_pd["tasa"].tolist()
            center = sum(all_vals) / len(all_vals) if all_vals else 0.5
            fig.update_layout(showlegend=False, plot_bgcolor='white', paper_bgcolor='white',
                              margin=dict(l=20, r=20, t=60, b=20),
                              xaxis=dict(title=dict(text="Dependientes sobre Graduados", font=dict(size=17)), tickformat=".0%",
                                         range=[max(0, center-0.2), min(1, center+0.2)], showgrid=True, gridcolor='#EEEEEE'),
                              yaxis=dict(visible=False),
                              annotations=[dict(x=0.5, y=1.08, xref="paper", yref="paper", showarrow=False,
                                               text="<i>Programa único — se muestran valores puntuales por sexo</i>",
                                               font=dict(size=12, color="#888"))])
            return fig
        fig = px.histogram(df_pd, x="tasa", color="sexo", color_discrete_map=COLOR_SEXO, histnorm='percent', barmode='group')
        fig.update_traces(xbins=dict(start=0.0, end=1.0, size=0.05), marker_line_width=1, marker_line_color="white")
        fig.update_layout(
            legend_title_text="Sexo",
            plot_bgcolor='white',
            paper_bgcolor='white',
            separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(
                title=dict(text="Dependientes sobre Graduados", font=dict(size=17), standoff=20),
                tickfont=dict(size=15), 
                tickformat=".0%",
                dtick=0.05,
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            ),
            yaxis=dict(
                title=dict(text="Porcentaje de Sub-grupos", font=dict(size=17), standoff=20),
                tickfont=dict(size=15),
                automargin=True,
                showgrid=True, gridcolor='#EEEEEE'
            )
        )
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}% de sub-grupos<extra></extra>')
        return fig

    @render.ui
    def plot_dist_dependientes_sexo():
        return ui.HTML(pio.to_html(calc_plot_dist_dependientes_sexo(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_mobility_kpis():
        df_pd, col_orig, col_dest, label_ejes = get_ole_mobility_df()
        if len(df_pd) == 0: return {"retencion": 0, "fuga": 0, "ratio": 0}

        def normalize_str(s):
            return str(s).upper().replace(".", "").replace(",", "").replace("  ", " ").strip()

        f_vals = isolated_filters()
        seleccionados = list(f_vals["municipio"] or []) if label_ejes == "Municipio" else list(f_vals["departamento"] or [])
        sel_norm = [normalize_str(x) for x in seleccionados]
        
        # Para los KPIs de Movilidad, la base son los que COTIZAN (no los graduados totales)
        col_vol = "cotizantes" 

        if not sel_norm:
            total_nacional = df_pd[col_vol].sum()
            se_quedan = df_pd[df_pd[col_orig] == df_pd[col_dest]][col_vol].sum()
            se_van = df_pd[df_pd[col_orig] != df_pd[col_dest]][col_vol].sum()
            llegan = se_van 
            
            return {
                "retencion": se_quedan / total_nacional if total_nacional else 0,
                "fuga": se_van / total_nacional if total_nacional else 0,
                "ratio": se_van / llegan if llegan else 0
            }
            
        # Normalizar las columnas del dataframe para el match
        df_pd["orig_clean"] = df_pd[col_orig].apply(normalize_str)
        df_pd["dest_clean"] = df_pd[col_dest].apply(normalize_str)
        
        is_origen_sel = df_pd["orig_clean"].isin(sel_norm)
        is_dest_sel = df_pd["dest_clean"].isin(sel_norm)
        
        total_cotizantes_zona = df_pd[is_origen_sel][col_vol].sum()
        se_quedan = df_pd[is_origen_sel & is_dest_sel][col_vol].sum()
        se_van = df_pd[is_origen_sel & ~is_dest_sel][col_vol].sum()
        llegan_de_fuera = df_pd[~is_origen_sel & is_dest_sel][col_vol].sum()
        
        return {
            "retencion": se_quedan / total_cotizantes_zona if total_cotizantes_zona else 0,
            "fuga": se_van / total_cotizantes_zona if total_cotizantes_zona else 0,
            "ratio": se_van / llegan_de_fuera if llegan_de_fuera else 0
        }

    @reactive.calc
    def calc_mobility_yoy_data():
        import pandas as pd
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0: return pd.DataFrame()
        
        # Detección de nivel geográfico
        f_vals = isolated_filters()
        mpio_filtro = f_vals["municipio"]
        if hasattr(mpio_filtro, '__iter__') and len(mpio_filtro) > 0 and mpio_filtro[0]:
            col_orig, col_dest, label_ejes = "municipio_origen", "municipio_destino", "Municipio"
        else:
            col_orig, col_dest, label_ejes = "departamento_origen", "departamento_destino", "Departamento"
            
        def normalize_str(s):
            return str(s).upper().replace(".", "").replace(",", "").replace("  ", " ").strip()

        seleccionados = list(f_vals["municipio"] or []) if label_ejes == "Municipio" else list(f_vals["departamento"] or [])
        sel_norm = [normalize_str(x) for x in seleccionados]
        
        ole_all = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) &
            (pl.col("anno_corte") >= 2016)
        )
        
        if len(ole_all) == 0: return pd.DataFrame()
        
        # Agrupar por Año, Origen y Destino
        agg = ole_all.group_by(["anno_corte", col_orig, col_dest]).agg([
            pl.col("graduados_que_cotizan").sum().alias("cotizantes")
        ]).to_pandas()
        
        # Limpieza para match
        agg["orig_clean"] = agg[col_orig].apply(normalize_str)
        agg["dest_clean"] = agg[col_dest].apply(normalize_str)
        
        res = []
        for anno in sorted(agg["anno_corte"].unique()):
            df_yr = agg[agg["anno_corte"] == anno]
            
            if not sel_norm:
                total_nac = df_yr["cotizantes"].sum()
                se_quedan = df_yr[df_yr["orig_clean"] == df_yr["dest_clean"]]["cotizantes"].sum()
                se_van = df_yr[df_yr["orig_clean"] != df_yr["dest_clean"]]["cotizantes"].sum()
                llegan_de_fuera = se_van
            else:
                is_origen_sel = df_yr["orig_clean"].isin(sel_norm)
                is_dest_sel = df_yr["dest_clean"].isin(sel_norm)
                total_nac = df_yr[is_origen_sel]["cotizantes"].sum()
                se_quedan = df_yr[is_origen_sel & is_dest_sel]["cotizantes"].sum()
                se_van = df_yr[is_origen_sel & ~is_dest_sel]["cotizantes"].sum()
                llegan_de_fuera = df_yr[~is_origen_sel & is_dest_sel]["cotizantes"].sum()
            
            retencion = se_quedan / total_nac if total_nac else 0
            ratio = se_van / llegan_de_fuera if llegan_de_fuera else 0
            res.append({"anno_corte": anno, "retencion": retencion, "ratio": ratio})
            
        return pd.DataFrame(res)

    @reactive.calc
    def calc_plot_retencion_trend():
        df_pd = calc_mobility_yoy_data()
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="retencion", text="retencion", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Tasa de Retención", tickformat=".1%", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_retencion_trend():
        return ui.HTML(pio.to_html(calc_plot_retencion_trend(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_ratio_trend():
        df_pd = calc_mobility_yoy_data()
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="ratio", text="ratio", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.2f}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Ratio Salen / Entran", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_ratio_trend():
        return ui.HTML(pio.to_html(calc_plot_ratio_trend(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_dependientes_trend():
        # Reutilizamos el motor de tendencias pero con el nuevo nombre semántico
        df_pd = create_ole_trend_df("graduados_cotizantes_dependientes", "graduados_que_cotizan", ["anno_corte", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", text="tasa", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Dependientes sobre Cotizantes", tickformat=".1%", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_dependientes_trend():
        return ui.HTML(pio.to_html(calc_plot_dependientes_trend(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_kpi_retencion():
        val = calc_mobility_kpis()["retencion"]
        return format_pct_es(val)

    @render.ui
    def kpi_retencion():
        val = calc_kpi_retencion()
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{val}</div>")
        
    @render.ui
    def kpi_fuga():
        val = calc_mobility_kpis()["fuga"]
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{format_pct_es(val)}</div>")
        
    @reactive.calc
    def calc_kpi_ratio():
        val = calc_mobility_kpis()["ratio"]
        return format_num_es(val, decimals=2)

    @render.ui
    def kpi_ratio():
        val = calc_kpi_ratio()
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{val}</div>")

    # --- SALARIO DE ENGANCHE ---
    @reactive.calc
    def filtered_ole_salario():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0:
            return pl.DataFrame()
            
        df = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))
        
        # Filtro geográfico (Origen)
        f_vals = isolated_filters()
        dept = f_vals["departamento"]
        mpio = f_vals["municipio"]
        if is_filtered(dept) or is_filtered(mpio):
            # Obtenemos los códigos DIVIPOLA numéricos de los municipios/departamentos seleccionados
            df_cob_geo = df_cobertura.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))
            if is_filtered(dept):
                df_cob_geo = df_cob_geo.filter(pl.col("departamento_oferta").is_in(dept))
            if is_filtered(mpio):
                df_cob_geo = df_cob_geo.filter(pl.col("municipio_oferta").is_in(mpio))
            
            divis_num = df_cob_geo["divipola_mpio_oferta"].drop_nulls().unique()
            # En OLE Salario cruzamos por divipola_mpio_principal (Int32)
            df = df.filter(pl.col("divipola_mpio_principal").is_in(divis_num))
            
        return df

    @reactive.calc
    def calc_kpi_salario_dependientes_sum():
        df = filtered_ole_salario()
        if len(df) == 0: return "0"
        max_yr = df["anno_corte"].max()
        total = df.filter(pl.col("anno_corte") == max_yr)["graduados_cotizantes_dependientes"].sum()
        return f"{total:,.0f}".replace(",", ".")

    @render.ui
    def kpi_salario_dependientes_sum():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_dependientes_sum()}</div>")

    def _calculate_salary_trend_data(is_constant=False):
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0: return pd.DataFrame(columns=["anno_corte", "label", "salario_pesos"])
        
        # 1. Filtro base
        df_base = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))
        
        # 2. Filtro Geográfico
        f_vals = isolated_filters()
        dept = f_vals["departamento"]
        mpio = f_vals["municipio"]
        if is_filtered(dept) or is_filtered(mpio):
            divis_num = df_cobertura.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))
            if is_filtered(dept): divis_num = divis_num.filter(pl.col("departamento_oferta").is_in(dept))
            if is_filtered(mpio): divis_num = divis_num.filter(pl.col("municipio_oferta").is_in(mpio))
            div_codes = divis_num["divipola_mpio_oferta"].drop_nulls().unique()
            df_base = df_base.filter(pl.col("divipola_mpio_principal").is_in(div_codes))
            
        if len(df_base) == 0: return pd.DataFrame(columns=["anno_corte", "label", "salario_pesos"])
        
        # 3. Join y Midpoints
        df_base = df_base.join(df_smmlv_pl, on="anno_corte", how="inner")
        
        if is_constant:
            max_data_year = df_ole_salario["anno_corte"].max()
            smmlv_ref = df_smmlv_pl.filter(pl.col("anno_corte") == max_data_year)["smmlv"]
            if smmlv_ref.len() > 0:
                latest_smmlv = smmlv_ref[0]
            else:
                latest_smmlv = df_smmlv_pl.sort("anno_corte").get_column("smmlv").tail(1).item()
            df_base = df_base.with_columns(pl.lit(latest_smmlv).alias("smmlv_calc"))
        else:
            df_base = df_base.with_columns(pl.col("smmlv").alias("smmlv_calc"))

        df_base = df_base.with_columns(
            pl.col("rango_salario").replace(SALARIO_MIDPOINTS, default=1.0).cast(pl.Float64).alias("midpoint")
        )
        
        # 4. Agregación Programa-Año-Sexo
        agg_prog = df_base.group_by(["anno_corte", "codigo_snies_del_programa", "sexo"]).agg([
            ((pl.col("midpoint") * pl.col("graduados_cotizantes_dependientes")).sum() / 
             pl.col("graduados_cotizantes_dependientes").sum() * pl.col("smmlv_calc").first()).alias("sal_prog")
        ]).filter(pl.col("sal_prog").is_not_null())
        
        # 5. Agregación Final
        agg_sexo = agg_prog.group_by(["anno_corte", "sexo"]).agg(pl.col("sal_prog").mean().alias("salario_pesos")).rename({"sexo": "label"})
        agg_total = agg_prog.group_by("anno_corte").agg(pl.col("sal_prog").mean().alias("salario_pesos")).with_columns(pl.lit("TOTAL").alias("label"))
        
        # Combinar resultados
        res_pd = pd.concat([
            agg_total.to_pandas(), 
            agg_sexo.to_pandas()
        ]).sort_values(["label", "anno_corte"])
        
        return res_pd

    @reactive.calc
    def get_salary_trend_data():
        return _calculate_salary_trend_data(is_constant=False)

    @reactive.calc
    def get_salary_trend_data_constant():
        return _calculate_salary_trend_data(is_constant=True)


    @reactive.calc
    def calc_plot_salario_evolucion_total():
        df_pd = get_salary_trend_data()
        if df_pd.empty: return go.Figure()
        df_plot = df_pd[df_pd["label"] == "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", text="salario_pesos", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario Promedio ($)", tickformat="$,.0f", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_salario_evolucion_total():
        return ui.HTML(pio.to_html(calc_plot_salario_evolucion_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_salario_evolucion_sexo():
        df_pd = get_salary_trend_data()
        if df_pd.empty: return go.Figure()
        
        df_plot = df_pd[df_pd["label"] != "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", color="label", color_discrete_map=COLOR_SEXO, text="salario_pesos", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        for trace in fig.data:
            trace.marker = dict(size=9, color="white", line=dict(width=1.5, color=trace.line.color))
            trace.line.width = 2
            
        fig.update_layout(
            legend_title_text="Sexo",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario Promedio ($)", tickformat="$,.0f", gridcolor='#EEEEEE')
        )
        return fig
        
    @render.ui
    def plot_salario_evolucion_sexo():
        return ui.HTML(pio.to_html(calc_plot_salario_evolucion_sexo(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_salario_evolucion_total_constante():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return go.Figure()
        
        df_plot = df_pd[df_pd["label"] == "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", text="salario_pesos", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario Promedio ($)", tickformat="$,.0f", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_salario_evolucion_total_constante():
        return ui.HTML(pio.to_html(calc_plot_salario_evolucion_total_constante(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_salario_evolucion_sexo_constante():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return go.Figure()
        
        df_plot = df_pd[df_pd["label"] != "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", color="label", color_discrete_map=COLOR_SEXO, text="salario_pesos", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        for trace in fig.data:
            trace.marker = dict(size=9, color="white", line=dict(width=1.5, color=trace.line.color))
            trace.line.width = 2
            
        fig.update_layout(
            legend_title_text="Sexo",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario Promedio ($)", tickformat="$,.0f", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_salario_evolucion_sexo_constante():
        return ui.HTML(pio.to_html(calc_plot_salario_evolucion_sexo_constante(), full_html=False, include_plotlyjs="cdn"))
    @reactive.calc
    def filtered_desercion():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0:
            return df_desercion.clear()
        return df_desercion.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))

    @reactive.calc
    def calc_kpi_desercion_promedio():
        df = filtered_desercion()
        if len(df) == 0: return "0%"
        max_yr = df["anno"].max()
        val = df.filter(pl.col("anno") == max_yr)["desercion_anual_mean"].mean()
        if val is None: return "0,0%"
        return format_pct_es(val)

    @render.ui
    def kpi_desercion_promedio():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_desercion_promedio()}</div>")

    @reactive.calc
    def calc_plot_dist_desercion():
        df = filtered_desercion()
        if len(df) == 0: return go.Figure()
        max_yr = df["anno"].max()
        df_plot = df.filter(pl.col("anno") == max_yr).to_pandas()
        # Caso especial: 1 solo programa o todos con el mismo valor
        if len(df_plot) == 1 or df_plot["desercion_anual_mean"].nunique() == 1:
            val = df_plot["desercion_anual_mean"].iloc[0]
            fig = go.Figure()
            fig.add_vline(x=val, line_width=4, line_color="#31497e")
            fig.add_annotation(x=val, y=0.5, yref="paper", text=f"<b>{val:.1%}</b><br>Valor del programa",
                               showarrow=True, arrowhead=2, arrowcolor="#31497e", font=dict(size=16, color="#31497e"),
                               bgcolor="white", bordercolor="#31497e", borderwidth=2)
            fig.update_layout(showlegend=False, plot_bgcolor='white', paper_bgcolor='white',
                              margin=dict(l=20, r=20, t=40, b=20),
                              xaxis=dict(title="Tasa de Deserción", tickformat=".0%",
                                         range=[max(0, val-0.1), min(1, val+0.1)], showgrid=True, gridcolor='#EEEEEE'),
                              yaxis=dict(visible=False),
                              annotations=[dict(x=0.5, y=1.05, xref="paper", yref="paper", showarrow=False,
                                               text="<i>Programa único seleccionado — se muestra valor puntual</i>",
                                               font=dict(size=12, color="#888"))])
            return fig
        fig = px.histogram(df_plot, x="desercion_anual_mean", nbins=50, histnorm='percent', text_auto='.1f')
        fig.update_traces(xbins=dict(start=0.0, end=1.0, size=0.02), marker_color="#31497e", 
                          marker_line_color="white", marker_line_width=1, textposition='outside', textfont_size=11)
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Tasa de Deserción", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="Participación de Programas (%)", ticksuffix="%", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_dist_desercion():
        return ui.HTML(pio.to_html(calc_plot_dist_desercion(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_trend_desercion():
        df = filtered_desercion()
        if len(df) == 0: return go.Figure()
        df_plot = df.group_by("anno").agg(pl.col("desercion_anual_mean").mean()).sort("anno").to_pandas()
        fig = px.line(df_plot, x="anno", y="desercion_anual_mean", text="desercion_anual_mean", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:.1%}")
        fig.update_traces(line=dict(color="#31497e", width=3), marker=dict(size=10, color="white", line=dict(width=2, color="#31497e")))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Tasa de Deserción Promedio", tickformat=".1%", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_trend_desercion():
        return ui.HTML(pio.to_html(calc_plot_trend_desercion(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_kpi_salario_promedio_total():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return "$ 0"
        max_yr = df_ole_salario["anno_corte"].max()
        val = df_pd[(df_pd["label"] == "TOTAL") & (df_pd["anno_corte"] == max_yr)]["salario_pesos"]
        s = val.iloc[0] if not val.empty else 0
        return f"$ {format_num_es(s)}"

    @render.ui
    def kpi_salario_promedio_total():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_promedio_total()}</div>")

    @reactive.calc
    def calc_kpi_salario_promedio_fem():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return "$ 0"
        max_yr = df_ole_salario["anno_corte"].max()
        val = df_pd[(df_pd["label"] == "FEMENINO") & (df_pd["anno_corte"] == max_yr)]["salario_pesos"]
        s = val.iloc[0] if not val.empty else 0
        return f"$ {format_num_es(s)}"

    @render.ui
    def kpi_salario_promedio_fem():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_promedio_fem()}</div>")

    @reactive.calc
    def calc_kpi_salario_promedio_masc():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return "$ 0"
        max_yr = df_ole_salario["anno_corte"].max()
        val = df_pd[(df_pd["label"] == "MASCULINO") & (df_pd["anno_corte"] == max_yr)]["salario_pesos"]
        s = val.iloc[0] if not val.empty else 0
        return f"$ {format_num_es(s)}"

    @render.ui
    def kpi_salario_promedio_masc():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_promedio_masc()}</div>")

    @reactive.calc
    def calc_plot_salario_dist_total():
        import pandas as pd
        df = filtered_ole_salario()
        if len(df) == 0: return go.Figure()
        max_yr = df["anno_corte"].max()
        agg = df.filter(pl.col("anno_corte") == max_yr).group_by("rango_salario").agg(
            pl.col("graduados_cotizantes_dependientes").sum().alias("cantidad")
        ).to_pandas()
        agg["rango_salario"] = pd.Categorical(agg["rango_salario"], categories=RANGO_SALARIO_ORDER, ordered=True)
        agg = agg.sort_values("rango_salario")
        total_selec = agg["cantidad"].sum()
        if total_selec > 0:
            agg["porcentaje"] = agg["cantidad"] / total_selec
        else:
            agg["porcentaje"] = 0
        fig = px.bar(agg, x="porcentaje", y="rango_salario", orientation='h', text_auto='.1%')
        fig.update_traces(marker_color="#31497e", marker_line_color="white", marker_line_width=1.5, textposition='auto', textfont_size=12)
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Participación de Graduados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=13))
        )
        return fig

    @render.ui
    def plot_salario_dist_total():
        return ui.HTML(pio.to_html(calc_plot_salario_dist_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_salario_dist_sexo():
        import pandas as pd
        df = filtered_ole_salario()
        if len(df) == 0: return go.Figure()
        max_yr = df["anno_corte"].max()
        agg = df.filter(pl.col("anno_corte") == max_yr).group_by(["rango_salario", "sexo"]).agg(
            pl.col("graduados_cotizantes_dependientes").sum().alias("cantidad")
        ).to_pandas()
        agg["rango_salario"] = pd.Categorical(agg["rango_salario"], categories=RANGO_SALARIO_ORDER, ordered=True)
        agg = agg.sort_values(["rango_salario", "sexo"])
        
        # Calcular porcentajes relativos a cada sexo (intra-grupo)
        totals_sexo = agg.groupby("sexo")["cantidad"].transform("sum")
        agg["porcentaje"] = agg["cantidad"] / totals_sexo.replace(0, 1)
        
        fig = px.bar(agg, x="porcentaje", y="rango_salario", color="sexo", orientation='h', barmode='group', color_discrete_map=COLOR_SEXO, text_auto='.1%')
        fig.update_traces(textposition='auto', textfont_size=11, marker_line_width=1, marker_line_color="white")
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Participación de Graduados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=13))
        )
        return fig

    @render.ui
    def plot_salario_dist_sexo():
        return ui.HTML(pio.to_html(calc_plot_salario_dist_sexo(), full_html=False, include_plotlyjs="cdn"))

    def get_ole_mobility_df():
        import pandas as pd
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        
        if len(snies_codigos) == 0:
            return pd.DataFrame(), "", "", ""
            
        # Nivel de agregación dinámico basado en filtros frontend
        f_vals = isolated_filters()
        mpio_filtro = f_vals["municipio"]
        if hasattr(mpio_filtro, '__iter__') and len(mpio_filtro) > 0 and mpio_filtro[0]:
            col_origen = "municipio_origen"
            col_destino = "municipio_destino"
            label_ejes = "Municipio"
        else:
            col_origen = "departamento_origen"
            col_destino = "departamento_destino"
            label_ejes = "Departamento"
            
        max_anno_corte = df_ole_m0["anno_corte"].max()
        ole_base = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) & 
            (pl.col("anno_corte") == max_anno_corte)
        )
        
        # Filtro estricto de matriz geospacial: Si el usuario selecciona Deptos o Mpios,
        # obligamos que la ruta de movilidad toque esa selección temporalmente o permanentemente (Origen O Destino).
        dept_vals = list(f_vals["departamento"] or [])
        mpio_vals = list(mpio_filtro or [])
        
        def normalize_pl(col):
            # Eliminar puntos, comas y espacios dobles para match robusto
            return col.str.to_uppercase().str.replace_all(r"[\.,]", "").str.replace_all(r"\s+", " ").str.strip_chars()

        if label_ejes == "Municipio" and len(mpio_vals) > 0:
            mpio_norm = [str(x).upper().replace(".", "").replace(",", "").replace("  ", " ").strip() for x in mpio_vals]

            df_origen = ole_base.filter(normalize_pl(pl.col("municipio_origen")).is_in(mpio_norm))
            df_destino = ole_base.filter(normalize_pl(pl.col("municipio_destino")).is_in(mpio_norm))

            ole_filtered = pl.concat([df_origen, df_destino]).unique()
            
        elif label_ejes == "Departamento" and len(dept_vals) > 0:
            dept_norm = [str(x).upper().replace(".", "").replace(",", "").replace("  ", " ").strip() for x in dept_vals]

            df_origen = ole_base.filter(normalize_pl(pl.col("departamento_origen")).is_in(dept_norm))
            df_destino = ole_base.filter(normalize_pl(pl.col("departamento_destino")).is_in(dept_norm))

            ole_filtered = pl.concat([df_origen, df_destino]).unique()
        else:
            ole_filtered = ole_base

        if len(ole_filtered) == 0:
            return pd.DataFrame(), "", "", ""
            
        # Acumular volumen de estudiantes en la matriz de cruce usando datos absolutos
        agg_df = ole_filtered.group_by([col_origen, col_destino]).agg([
            pl.col("graduados").sum().alias("volumen"),
            pl.col("graduados_que_cotizan").sum().alias("cotizantes")
        ])
        agg_df = agg_df.filter(pl.col("cotizantes") > 0).sort("cotizantes", descending=True)
        return agg_df.to_pandas(), col_origen, col_destino, label_ejes

    @reactive.calc
    def calc_plot_mobility_matrix():
        df_pd, col_orig, col_dest, label_ejes = get_ole_mobility_df()
        if len(df_pd) == 0: return go.Figure()
        
        # Detectar el elemento geográfico actualmente seleccionado para resaltarlo
        f_vals = isolated_filters()
        seleccionados = list(f_vals["municipio"] or []) if label_ejes == "Municipio" else list(f_vals["departamento"] or [])
        seleccionados_upper = [str(x).upper() for x in seleccionados]
        
        # Preprocesar códigos anómalos o faltantes
        df_pd[col_orig] = df_pd[col_orig].astype(str).replace({"1": "SIN INFORMACIÓN", "1.0": "SIN INFORMACIÓN"})
        df_pd[col_dest] = df_pd[col_dest].astype(str).replace({"1": "SIN INFORMACIÓN", "1.0": "SIN INFORMACIÓN"})
 
        # Matriz pivot pivotando origen vs destino usando COTIZANTES
        matriz = df_pd.pivot_table(index=col_orig, columns=col_dest, values="cotizantes", aggfunc="sum").fillna(0)
        
        # Ordenar ejes alfabéticamente (A-Z) para lectura estándar
        def sort_labels(labels):
            lst = sorted(list(labels))
            if "SIN INFORMACIÓN" in lst:
                lst.remove("SIN INFORMACIÓN")
                lst.append("SIN INFORMACIÓN")
            return lst
            
        matriz = matriz.loc[sort_labels(matriz.index)]
        matriz = matriz[sort_labels(matriz.columns)]
        zmax_interno = float(matriz.values.max()) if matriz.size > 0 else 1.0
        matriz["TOTAL"] = matriz.sum(axis=1)
        matriz.loc["TOTAL"] = matriz.sum(axis=0)
        idx = list(matriz.index)
        idx.remove("TOTAL")
        idx_inverted = idx[::-1]
        idx_inverted.insert(0, "TOTAL")
        cols = list(matriz.columns)
        cols.remove("TOTAL")
        cols.append("TOTAL")
        matriz = matriz.loc[idx_inverted, cols]
        
        custom_scale = [
            [0.0, '#FFFFFF'], [0.1, '#D2D2F2'], [0.3, '#A096E1'], [0.6, '#6C5CE7'], [1.0, '#31497e']
        ]
        
        text_matrix = [[f"{int(v):,}" if v > 0 else "" for v in row] for row in matriz.values]
        
        fig = go.Figure(data=go.Heatmap(
            z=matriz.values, x=matriz.columns, y=matriz.index,
            zmax=zmax_interno, colorscale=custom_scale,
            text=text_matrix, texttemplate="%{text}",
            xgap=1, ygap=1
        ))
        
        fig.data[0].textfont.color = None
        fig.data[0].hovertemplate = "Origen: %{y}<br>Destino: %{x}<br>Graduados Cotizantes: %{z:,.0f}<extra></extra>"
        
        shapes = []
        for s in seleccionados_upper:
            row_idx = None
            col_idx = None
            for i, name in enumerate(matriz.index):
                if s == str(name).upper().strip(): row_idx = i
            for i, name in enumerate(matriz.columns):
                if s == str(name).upper().strip(): col_idx = i
            if row_idx is not None:
                shapes.append(dict(
                    type="rect", xref="paper", yref="y",
                    x0=0, y0=row_idx-0.5, x1=1, y1=row_idx+0.5,
                    line=dict(color="#00B4D8", width=2),
                    fillcolor="rgba(0, 180, 216, 0.05)", layer="below"
                ))
            if col_idx is not None:
                shapes.append(dict(
                    type="rect", xref="x", yref="paper",
                    x0=col_idx-0.5, y0=0, x1=col_idx+0.5, y1=1,
                    line=dict(color="#00B4D8", width=2),
                    fillcolor="rgba(0, 180, 216, 0.05)", layer="below"
                ))

        def build_ticks(labels):
            ticktext = []
            for lab in labels:
                if str(lab) == "TOTAL":
                    ticktext.append(f"<b><span style='color:black'>{lab}</span></b>")
                    continue
                match = False
                for s in seleccionados_upper:
                    if s == str(lab).upper().strip(): match = True
                if match:
                    ticktext.append(f"<b><span style='color:#00B4D8'>{lab}</span></b>")
                else:
                    ticktext.append(str(lab))
            return ticktext

        x_ticks = build_ticks(matriz.columns)
        y_ticks = build_ticks(matriz.index)
        
        dynamic_h = max(500, len(matriz.index) * 25 + 200)
        
        fig.update_layout(
            height=dynamic_h,
            plot_bgcolor='rgba(200, 200, 200, 0.4)', paper_bgcolor='white', margin=dict(l=200, r=50, t=200, b=50),
            shapes=shapes,
            xaxis=dict(
                title=dict(text=f"{label_ejes} de Destino Laboral (Donde Cotiza)", font=dict(size=17), standoff=20), 
                tickmode='array', tickvals=list(matriz.columns), ticktext=x_ticks,
                tickfont=dict(size=12), tickangle=-90, automargin=True
            ),
            yaxis=dict(
                title=dict(text=f"{label_ejes} de Origen Académico (Donde Graduó)", font=dict(size=17), standoff=20), 
                tickmode='array', tickvals=list(matriz.index), ticktext=y_ticks,
                tickfont=dict(size=12), automargin=True
            )
        )
        return fig

    @render.ui
    def plot_mobility_matrix():
        html_code = pio.to_html(calc_plot_mobility_matrix(), full_html=False, include_plotlyjs="cdn")
        return ui.HTML(f"<div style='width: 100%; overflow: auto;'>{html_code}</div>")

    @reactive.calc
    def calc_plot_mobility_matrix_report():
        # Versión transpuesta para el reporte (Vertical)
        df_pd, col_orig, col_dest, label_ejes = get_ole_mobility_df()
        if len(df_pd) == 0: return go.Figure()
        
        # Swapping Origin and Destination for Vertical view
        # Now rows = Destination, cols = Origin
        matriz = df_pd.pivot_table(index=col_dest, columns=col_orig, values="cotizantes", aggfunc="sum").fillna(0)
        
        def sort_labels(labels):
            lst = sorted(list(labels))
            if "SIN INFORMACIÓN" in lst:
                lst.remove("SIN INFORMACIÓN")
                lst.append("SIN INFORMACIÓN")
            return lst
            
        matriz = matriz.loc[sort_labels(matriz.index)]
        matriz = matriz[sort_labels(matriz.columns)]
        zmax_interno = float(matriz.values.max()) if matriz.size > 0 else 1.0
        
        # Totales
        matriz["TOTAL"] = matriz.sum(axis=1)
        matriz.loc["TOTAL"] = matriz.sum(axis=0)
        
        # Reordenar para que TOTAL esté al final/abajo
        idx = list(matriz.index)
        idx.remove("TOTAL")
        idx_inverted = idx[::-1]
        idx_inverted.insert(0, "TOTAL")
        cols = list(matriz.columns)
        cols.remove("TOTAL")
        cols.append("TOTAL")
        matriz = matriz.loc[idx_inverted, cols]

        custom_scale = [
            [0.0, '#FFFFFF'], [0.1, '#D2D2F2'], [0.3, '#A096E1'], [0.6, '#6C5CE7'], [1.0, '#31497e']
        ]
        
        text_matrix = [[f"{int(v):,}" if v > 0 else "" for v in row] for row in matriz.values]
        
        fig = go.Figure(data=go.Heatmap(
            z=matriz.values, x=matriz.columns, y=matriz.index,
            zmax=zmax_interno, colorscale=custom_scale,
            text=text_matrix, texttemplate="%{text}",
            xgap=1, ygap=1
        ))
        
        # Ajuste dinámico de altura y tipografía (Ampliación Extrema)
        num_items = len(matriz.index)
        if num_items > 40:
            row_height = 22
            base_tick_size = 9
        elif num_items > 20:
            row_height = 28
            base_tick_size = 10
        else:
            row_height = 40
            base_tick_size = 12

        # Altura dinámica considerando márgenes masivos para nombres largos (400px a la izquierda)
        dynamic_h = max(800, num_items * row_height + 500)
        
        fig.update_layout(
            height=dynamic_h, width=1500,
            plot_bgcolor='white', paper_bgcolor='white', 
            margin=dict(l=400, r=80, t=350, b=80),
            xaxis=dict(
                title=dict(text=f"{label_ejes} de Origen Académico (Donde Graduó)", font=dict(size=14), standoff=30), 
                tickfont=dict(size=base_tick_size), tickangle=-45, side="top", automargin=True
            ),
            yaxis=dict(
                title=dict(text=f"{label_ejes} de Destino Laboral (Donde Cotiza)", font=dict(size=14), standoff=30), 
                tickfont=dict(size=base_tick_size), automargin=True
            )
        )
        return fig

    @reactive.calc
    def calc_table_graduados():
        divipolas = valid_divipolas()
        return create_gender_table(df_graduados, divipolas, "graduados_sum")

    @render.data_frame
    def table_graduados():
        df_pd = calc_table_graduados()
        return render.DataGrid(df_pd, filters=False, width="100%", selection_mode="none")

    @reactive.calc
    def calc_top_ingresos_table():
        import pandas as pd
        divipolas = valid_divipolas()
        if len(divipolas) == 0:
            return pd.DataFrame()
            
        df_filt = filtered_snies()
        if df_filt.is_empty():
            return pd.DataFrame()
            
        max_yr = int(df_pcurso["anno"].max())
        # Consolidar ingresos por programa (último año) FILTRANDO POR GEOGRAFÍA
        df_p_agg = df_pcurso.filter(
            (pl.col("anno") == max_yr) & 
            pl.col("snies_divipola").is_in(divipolas)
        ).group_by("codigo_snies_del_programa").agg(
            pl.col("primer_curso_sum").sum().alias("Ingresos")
        )
        
        # Unir con la información de SNIES filtrada
        df_res = df_filt.join(df_p_agg, on="codigo_snies_del_programa", how="inner")
        
        # Seleccionar y renombrar columnas
        df_res = df_res.select([
            "codigo_snies_del_programa",
            "nombre_institucion",
            "programa_academico",
            "sector",
            "modalidad",
            "numero_creditos",
            "costo_matricula_estud_nuevos",
            "Ingresos"
        ]).sort("Ingresos", descending=True)
        
        df_res = df_res.rename({
            "codigo_snies_del_programa": "SNIES",
            "nombre_institucion": "Institución",
            "programa_academico": "Programa Académico",
            "sector": "Sector",
            "modalidad": "Modalidad",
            "numero_creditos": "Créditos",
            "costo_matricula_estud_nuevos": "Valor Matrícula",
            "Ingresos": f"Ingresos ({max_yr})"
        })
        
        df_pd = df_res.to_pandas()
        
        # Formatear moneda para Valor Matrícula (solo si no es nulo/NaN)
        df_pd["Valor Matrícula"] = df_pd["Valor Matrícula"].apply(
            lambda x: f"$ {format_num_es(x)}" if pd.notnull(x) else ""
        )
        return df_pd

    @render.data_frame
    def prev_top_ingresos():
        df_pd = calc_top_ingresos_table()
        return render.DataGrid(df_pd, filters=True, width="100%", selection_mode="none")

    @reactive.calc
    def calc_top_ingresos_comp_table():
        import pandas as pd
        # Obtenemos la lista de divipolas del grupo comparable
        comp_divipolas = comparable_snies_list()
        if len(comp_divipolas) == 0:
            return pd.DataFrame()
            
        max_yr = int(df_pcurso["anno"].max())
        # Consolidar ingresos por programa (último año) filtrando por el GRUPO COMPARABLE
        df_p_agg = df_pcurso.filter(
            (pl.col("anno") == max_yr) & 
            pl.col("snies_divipola").is_in(comp_divipolas)
        ).group_by("codigo_snies_del_programa").agg(
            pl.col("primer_curso_sum").sum().alias("Ingresos")
        )
        
        # Cruzar con metadatos de SNIES
        df_snies_small = df_snies.select([
            "codigo_snies_del_programa", "nombre_institucion", "programa_academico", 
            "sector", "modalidad", "numero_creditos", "costo_matricula_estud_nuevos"
        ]).to_pandas()
        
        df_p_agg_pd = df_p_agg.to_pandas()
        df_res = pd.merge(df_p_agg_pd, df_snies_small, on="codigo_snies_del_programa", how="inner")
        
        # Ordenar (Sin límite para el Dashboard)
        df_res = df_res.sort_values("Ingresos", ascending=False)
        
        # Formatear
        df_res["#"] = range(1, len(df_res) + 1)
        df_res = df_res.rename(columns={
            "codigo_snies_del_programa": "SNIES",
            "nombre_institucion": "Institución",
            "programa_academico": "Programa Académico",
            "sector": "Sector",
            "modalidad": "Modalidad",
            "numero_creditos": "Créditos",
            "costo_matricula_estud_nuevos": "Valor Matrícula",
            "Ingresos": f"Ingresos {max_yr}"
        })
        
        # Formatear moneda para Valor Matrícula
        df_res["Valor Matrícula"] = df_res["Valor Matrícula"].apply(
            lambda x: f"$ {format_num_es(x)}" if pd.notnull(x) and x > 0 else ""
        )
        
        return df_res[[
            "#", "SNIES", "Institución", "Programa Académico", "Sector", "Modalidad", "Créditos", "Valor Matrícula", f"Ingresos {max_yr}"
        ]]

    @render.data_frame
    def prev_top_ingresos_comp():
        df_pd = calc_top_ingresos_comp_table()
        return render.DataGrid(df_pd, filters=True, width="100%", selection_mode="none")

    @reactive.calc
    def calc_plot_primer_curso_total():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_pcurso.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by("anno").agg(pl.col("primer_curso_sum").sum()).sort("anno")
        if len(df_filtered) == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="primer_curso_sum", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Primer Curso", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_primer_curso_total():
        return ui.HTML(pio.to_html(calc_plot_primer_curso_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_primer_curso():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_pcurso.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by(["anno", "sexo"]).agg(pl.col("primer_curso_sum").sum()).sort(["sexo", "anno"])
        if len(df_filtered) == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="primer_curso_sum", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9), line=dict(width=2))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            trace.marker.line.width = 1.5
            trace.marker.color = 'white'
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Primer Curso", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_primer_curso():
        return ui.HTML(pio.to_html(calc_plot_primer_curso(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_matriculados_total():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_matricula.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by("anno").agg(pl.col("matricula_sum").sum()).sort("anno")
        if len(df_filtered) == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="matricula_sum", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Matriculados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_matriculados_total():
        return ui.HTML(pio.to_html(calc_plot_matriculados_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_matriculados():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_matricula.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by(["anno", "sexo"]).agg(pl.col("matricula_sum").sum()).sort(["sexo", "anno"])
        if len(df_filtered) == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="matricula_sum", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9), line=dict(width=2))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            trace.marker.line.width = 1.5
            trace.marker.color = 'white'
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Matriculados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_matriculados():
        return ui.HTML(pio.to_html(calc_plot_matriculados(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_graduados_total():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_graduados.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by("anno").agg(pl.col("graduados_sum").sum()).sort("anno")
        if len(df_filtered) == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="graduados_sum", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Graduados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_graduados_total():
        return ui.HTML(pio.to_html(calc_plot_graduados_total(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_graduados():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_graduados.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by(["anno", "sexo"]).agg(pl.col("graduados_sum").sum()).sort(["sexo", "anno"])
        if len(df_filtered) == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="graduados_sum", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(marker=dict(size=9), line=dict(width=2))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            trace.marker.line.width = 1.5
            trace.marker.color = 'white'
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Graduados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_graduados():
        return ui.HTML(pio.to_html(calc_plot_graduados(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def filtered_saber():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0:
            return pl.DataFrame()
        return df_saber.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) &
            (pl.col("sexo") != "ND")
        )

    @reactive.calc
    def filtered_saber_latest():
        df = filtered_saber()
        if len(df) == 0: return pl.DataFrame()
        return df.filter(pl.col("anno") == max_anno_saber)

    def calc_saber_score(column):
        df = filtered_saber_latest()
        if len(df) == 0: return "Sin dato"
        agg = df.group_by("codigo_snies_del_programa").agg(pl.col(column).mean())
        val = agg[column].mean()
        return format_num_es(val, decimals=1) if val is not None else "Sin dato"

    @render.ui
    def kpi_saber_global():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_saber_score('pro_gen_punt_global')}</div>")

    @render.ui
    def kpi_saber_razona():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_saber_score('pro_gen_mod_razona_cuantitat_punt')}</div>")

    @render.ui
    def kpi_saber_lectura():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_saber_score('pro_gen_mod_lectura_critica_punt')}</div>")

    @render.ui
    def kpi_saber_ciuda():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_saber_score('pro_gen_mod_competen_ciudada_punt')}</div>")

    @render.ui
    def kpi_saber_ingles():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_saber_score('pro_gen_mod_ingles_punt')}</div>")

    @render.ui
    def kpi_saber_escrita():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_saber_score('pro_gen_mod_comuni_escrita_punt')}</div>")

    @reactive.calc
    def calc_plot_saber_trend():
        df = filtered_saber()
        if len(df) == 0: return go.Figure()
        
        cols_map = {
            "pro_gen_punt_global": "Puntaje Global",
            "pro_gen_mod_razona_cuantitat_punt": "Razonamiento Cuantitativo",
            "pro_gen_mod_lectura_critica_punt": "Lectura Crítica",
            "pro_gen_mod_competen_ciudada_punt": "Competencias Ciudadanas",
            "pro_gen_mod_ingles_punt": "Inglés",
            "pro_gen_mod_comuni_escrita_punt": "Comunicación Escrita"
        }
        
        # Agregación por año: promedio de los promedios de los programas
        # 1. Promedio por programa y año
        df_agg = df.group_by(["anno", "codigo_snies_del_programa"]).agg([
            pl.col(c).mean().alias(c) for c in cols_map.keys()
        ])
        # 2. Promedio de los programas por año
        df_trend = df_agg.group_by("anno").agg([
            pl.col(c).mean().alias(name) for c, name in cols_map.items()
        ]).sort("anno")
        
        df_pd = df_trend.to_pandas()
        # Convertir a formato largo para Plotly
        df_plot = df_pd.melt(id_vars="anno", var_name="Componente", value_name="Puntaje")
        
        fig = px.line(df_plot, x="anno", y="Puntaje", color="Componente", text=df_plot["Puntaje"].round(0), markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=9, color="black"))
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Puntaje Promedio", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_saber_trend():
        return ui.HTML(pio.to_html(calc_plot_saber_trend(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_saber_dist():
        df = filtered_saber_latest()
        if len(df) == 0: return go.Figure()
        
        # Agregación por programa académico
        agg = df.group_by("codigo_snies_del_programa").agg(pl.col("pro_gen_punt_global").mean())
        df_pd = agg.to_pandas()
        
        # Caso especial: 1 solo programa o todos con el mismo valor
        if len(df_pd) == 1 or df_pd["pro_gen_punt_global"].nunique() == 1:
            val = df_pd["pro_gen_punt_global"].iloc[0]
            fig = go.Figure()
            fig.add_vline(x=val, line_width=4, line_color="#31497e")
            fig.add_annotation(x=val, y=0.5, yref="paper", text=f"<b>{val:.1f} pts</b><br>Puntaje del programa",
                               showarrow=True, arrowhead=2, arrowcolor="#31497e", font=dict(size=16, color="#31497e"),
                               bgcolor="white", bordercolor="#31497e", borderwidth=2)
            fig.update_layout(showlegend=False, plot_bgcolor='white', paper_bgcolor='white',
                              margin=dict(l=20, r=20, t=40, b=20),
                              xaxis=dict(title="Puntaje Global Promedio",
                                         range=[max(0, val-20), val+20], showgrid=True, gridcolor='#EEEEEE'),
                              yaxis=dict(visible=False),
                              annotations=[dict(x=0.5, y=1.05, xref="paper", yref="paper", showarrow=False,
                                               text="<i>Programa único seleccionado — se muestra valor puntual</i>",
                                               font=dict(size=12, color="#888"))])
            return fig
        
        fig = px.histogram(df_pd, x="pro_gen_punt_global", histnorm='percent', nbins=100)
        fig.update_traces(marker_color="#31497e", marker_line_color="white", marker_line_width=1, xbins=dict(size=1))
        
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Puntaje Global Promedio", gridcolor='#EEEEEE'),
            yaxis=dict(title="Participación de Programas (%)", ticksuffix="%", gridcolor='#EEEEEE')
        )
        return fig

    @render.ui
    def plot_saber_dist():
        return ui.HTML(pio.to_html(calc_plot_saber_dist(), full_html=False, include_plotlyjs="cdn"))
    @reactive.calc
    def calc_plot_saber_count_sexo():
        df = filtered_saber()
        if len(df) == 0: return go.Figure()
        trend = df.group_by(["anno", "sexo"]).len().sort(["anno", "sexo"])
        fig = px.line(trend.to_pandas(), x="anno", y="len", color="sexo", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
                        xaxis=dict(title="Año", tickmode="linear"), yaxis=dict(title="Número de Evaluados", gridcolor='#EEEEEE'))
        return fig

    @render.ui
    def plot_saber_count_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_count_sexo(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_plot_saber_count_edad():
        df = filtered_saber()
        if len(df) == 0: return go.Figure()
        trend = df.group_by(["anno", "grupo_edad"]).len().sort(["anno", "grupo_edad"])
        fig = px.line(trend.to_pandas(), x="anno", y="len", color="grupo_edad", markers=True)
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=12, color="black"), texttemplate="%{y:,.0f}")
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
                        xaxis=dict(title="Año", tickmode="linear"), yaxis=dict(title="Número de Evaluados", gridcolor='#EEEEEE'))
        return fig

    @render.ui
    def plot_saber_count_edad(): return ui.HTML(pio.to_html(calc_plot_saber_count_edad(), full_html=False, include_plotlyjs="cdn"))
    def calc_plot_saber_trend_dim(column, dim):
        df = filtered_saber()
        if len(df) == 0: return go.Figure()
        agg = df.group_by(["anno", "codigo_snies_del_programa", dim]).agg(pl.col(column).mean())
        # Tendencia final de promedios
        trend = agg.group_by(["anno", dim]).agg(pl.col(column).mean()).sort(["anno", dim])
        
        df_pd = trend.to_pandas()
        fig = px.line(df_pd, x="anno", y=column, color=dim, markers=True, text=df_pd[column].round(0))
        
        fig.update_traces(textposition="top center", textfont=dict(size=9, color="black"))
        
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Puntaje", gridcolor='#EEEEEE'),
            legend_title=dim.capitalize()
        )
        return fig
    @render.ui
    def plot_saber_trend_global_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_punt_global", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_global_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_punt_global", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_razona_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_razona_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_lectura_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_lectura_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_ciuda_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_ciuda_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_ingles_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_ingles_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_escrita_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_trend_escrita_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    def calc_plot_saber_categorical(column, label, sort_vals=False):
        df = filtered_saber_latest()
        if len(df) == 0: return go.Figure()
        df_clean = df.with_columns(
            pl.col(column).cast(pl.Utf8).fill_null("Sin Registro")
        ).with_columns(
            pl.when(pl.col(column) == "").then(pl.lit("Sin Registro"))
            .when(pl.col(column) == "-1").then(pl.lit("Sin Registro"))
            .otherwise(pl.col(column)).alias(column)
        )
        
        # Conteo por categoría
        df_plot = df_clean.group_by(column).len()
        
        if sort_vals:
            df_plot = df_plot.sort(column)
        else:
            df_plot = df_plot.sort("len", descending=True)
            
        # Porcentaje
        total = df_plot["len"].sum()
        df_pd = df_plot.with_columns((pl.col("len") / total).alias("porcentaje")).to_pandas()
        
        fig = px.bar(df_pd, y=column, x="porcentaje", orientation='h', text_auto='.2%')
        fig.update_traces(marker_color="#31497e", marker_line_color="white", marker_line_width=1.5)
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Participación (%)", tickformat=".2%", gridcolor='#EEEEEE'),
            yaxis=dict(title=label, gridcolor='#EEEEEE', autorange="reversed"),
            separators=',.'
        )
        return fig

    @render.ui
    def plot_saber_demo_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("sexo", "Sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_demo_edad(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("grupo_edad", "Grupo de Edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_demo_trabajo(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("pro_gen_estu_horassemanatrabaja", "Horas de Trabajo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_demo_estrato(): return calc_plot_saber_categorical("pro_gen_fami_estratovivienda", "Estrato Vivienda", sort_vals=True)

    # Tendencias Socio-demográficas
    def calc_plot_saber_categorical_trend(column, label):
        df = filtered_saber()
        if len(df) == 0: return go.Figure()
        df_clean = df.with_columns(
            pl.col(column).cast(pl.Utf8).fill_null("Sin Registro")
        ).with_columns(
            pl.when(pl.col(column) == "").then(pl.lit("Sin Registro"))
            .when(pl.col(column) == "-1").then(pl.lit("Sin Registro"))
            .otherwise(pl.col(column)).alias(column)
        )
        
        # Conteo por año y categoría
        df_counts = df_clean.group_by(["anno", column]).len()
        # Totales por año para calcular participación %
        df_totals = df_clean.group_by("anno").agg(pl.len().alias("total"))
        
        df_plot = df_counts.join(df_totals, on="anno").with_columns(
            (pl.col("len") / pl.col("total")).alias("participacion")
        ).sort(["anno", column])
        
        df_pd = df_plot.to_pandas()
        
        # Definir orden de categorías si aplica
        cat_orders = {}
        if label == "Estrato":
            order = ["1", "2", "3", "4", "5", "6", "0", "Sin Registro"]
            cat_orders[column] = order
        elif label == "Trabajo":
            order = ["Menos de 10 horas", "Entre 11 y 20 horas", "Entre 21 y 30 horas", "Más de 30 horas", "No trabaja", "Sin Registro"]
            cat_orders[column] = order

        fig = px.line(df_pd, x="anno", y="participacion", color=column, 
                     text=(df_pd["participacion"] * 100).round(1).astype(str) + "%", 
                     markers=True,
                     category_orders=cat_orders)
        
        fig.update_traces(mode="lines+markers+text", textposition="top center", textfont=dict(size=9, color="black"))
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear"),
            yaxis=dict(title="Participación (%)", tickformat=".2%", gridcolor='#EEEEEE'),
            legend_title=label,
            separators=',.'
        )
            
        return fig

    @render.ui
    def plot_saber_demo_sexo_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("sexo", "Sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_demo_edad_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("grupo_edad", "Grupo Edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_demo_trabajo_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("pro_gen_estu_horassemanatrabaja", "Trabajo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def plot_saber_demo_estrato_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("pro_gen_fami_estratovivienda", "Estrato"), full_html=False, include_plotlyjs="cdn"))
    @reactive.calc
    def calc_total_evaluados_saber():
        df = filtered_saber_latest()
        return len(df)

    @render.ui
    def kpi_demo_evaluados():
        val = calc_total_evaluados_saber()
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    @reactive.calc
    def calc_total_programas_saber():
        df = filtered_saber_latest()
        if len(df) == 0: return 0
        return df["codigo_snies_del_programa"].n_unique()

    @render.ui
    def kpi_demo_programas():
        val = calc_total_programas_saber()
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    # ==========================================
    # TENDENCIA COMPARADA
    # ==========================================
    @reactive.calc
    def comp_profile_attr():
        """Retorna un diccionario con los atributos del SNIES seleccionado."""
        snies_label = input.comp_snies_base()
        if not snies_label: return None
        
        try:
            # Extraer el código del string "1234 - Nombre..."
            snies_code = int(snies_label.split(" - ")[0])
        except:
            return None
            
        df_base = df_snies.filter(pl.col("codigo_snies_del_programa") == snies_code)
        if len(df_base) == 0: return None
        
        # Unir también con cobertura para tener el departamento
        df_cob = df_cobertura.filter(pl.col("codigo_snies_del_programa") == snies_code)
        
        return {
            "codigo": snies_code,
            "nombre": df_base["programa_academico"][0],
            "institucion": df_base["nombre_institucion"][0],
            "nivel_de_formacion": df_base["nivel_de_formacion"][0] if df_base["nivel_de_formacion"].len() > 0 else None,
            "modalidad": df_base["modalidad"][0] if df_base["modalidad"].len() > 0 else None,
            "sector": df_base["sector"][0] if df_base["sector"].len() > 0 else None,
            "area_de_conocimiento": df_base["area_de_conocimiento"][0] if df_base["area_de_conocimiento"].len() > 0 else None,
            "nucleo_basico_del_conocimiento": df_base["nucleo_basico_del_conocimiento"][0] if df_base["nucleo_basico_del_conocimiento"].len() > 0 else None,
            "departamento_oferta": df_cob["departamento_oferta"][0] if len(df_cob) > 0 else None
        }

    @render.ui
    def comp_perfil_snies():
        attr = comp_profile_attr()
        if not attr:
            return ui.HTML("<i style='color: gray;'>Seleccione un programa de la lista superior para ver sus atributos.</i>")
            
        items = [
            f"<b>Institución:</b> {attr['institucion']}",
            f"<b>Nivel:</b> {attr['nivel_de_formacion']}" if attr.get('nivel_de_formacion') else "",
            f"<b>Modalidad:</b> {attr['modalidad']}" if attr.get('modalidad') else "",
            f"<b>Sector:</b> {attr['sector']}" if attr.get('sector') else "",
            f"<b>Departamento:</b> {attr['departamento_oferta']}" if attr.get('departamento_oferta') else "",
            f"<b>Estado:</b> {attr['estado_programa']}" if attr.get('estado_programa') else ""
        ]
        items_clean = [i for i in items if i]
        li_html = "".join([f"<li style='margin-bottom: 2px;'>{i}</li>" for i in items_clean])
        return ui.HTML(f"""
            <div style='margin-top: 10px; padding: 15px; background-color: #f0f4f8; border-radius: 8px; font-size: 14px; border-left: 4px solid #31497e;'>
                <ul style='margin: 0; padding-left: 20px;'>
                    {li_html}
                </ul>
            </div>
        """)

    comp_modo_manual = reactive.Value(False)
    
    @reactive.effect
    @reactive.event(input.switch_modo_manual)
    def trigger_manual_switch():
        comp_modo_manual.set(input.switch_modo_manual())

    reactive_lista_comp_snies = reactive.Value([])
    
    @reactive.effect
    @reactive.event(input.comp_criterios, input.comp_snies_base, input.switch_modo_manual)
    def auto_update_lista_snies():
        if input.switch_modo_manual():
            return
            
        attr = comp_profile_attr()
        if not attr: 
            reactive_lista_comp_snies.set([])
            return
            
        criterios = input.comp_criterios() or []
        df_comp = df_snies
        
        if "nivel_de_formacion" in criterios and attr.get("nivel_de_formacion"):
            df_comp = df_comp.filter(pl.col("nivel_de_formacion") == attr["nivel_de_formacion"])
        if "modalidad" in criterios and attr.get("modalidad"):
            df_comp = df_comp.filter(pl.col("modalidad") == attr["modalidad"])
        if "sector" in criterios and attr.get("sector"):
            df_comp = df_comp.filter(pl.col("sector") == attr["sector"])
        if "area_de_conocimiento" in criterios and attr.get("area_de_conocimiento"):
            df_comp = df_comp.filter(pl.col("area_de_conocimiento") == attr["area_de_conocimiento"])
        if "nucleo_basico_del_conocimiento" in criterios and attr.get("nucleo_basico_del_conocimiento"):
            df_comp = df_comp.filter(pl.col("nucleo_basico_del_conocimiento") == attr["nucleo_basico_del_conocimiento"])
            
        df_comp = df_comp.filter(pl.col("estado_programa") == "ACTIVO")

        valid_snies = df_comp["codigo_snies_del_programa"].unique().to_list()
        
        if "departamento_oferta" in criterios and attr.get("departamento_oferta"):
            df_cob_comp = df_cobertura.filter(
                (pl.col("codigo_snies_del_programa").is_in(valid_snies)) &
                (pl.col("departamento_oferta") == attr["departamento_oferta"])
            )
            valid_snies = df_cob_comp["codigo_snies_del_programa"].unique().to_list()
            
        reactive_lista_comp_snies.set(valid_snies)

    @reactive.calc
    def comparable_snies_list():
        """Devuelve la lista de divipolas que forman el grupo comparable (para datasets de estudiantes)."""
        snies_list = reactive_lista_comp_snies.get()
        if not snies_list: return []
        
        criterios = input.comp_criterios() or []
        attr = comp_profile_attr()
        df_cob_comp = df_cobertura.filter(pl.col("codigo_snies_del_programa").is_in(snies_list))
        
        if "departamento_oferta" in criterios and attr and attr.get("departamento_oferta"):
            df_cob_comp = df_cob_comp.filter(pl.col("departamento_oferta") == attr["departamento_oferta"])
            
        return df_cob_comp["snies_divipola"].unique().to_list()

    @reactive.calc
    def comparable_snies_codigos():
        """Devuelve la lista de codigos SNIES base que forman el grupo comparable (para OLE y SaberPRO)."""
        return reactive_lista_comp_snies.get() or []

    # --- Lógica del Modal de Selección SNIES ---
    modal_snies_activados = reactive.Value([])
    
    @reactive.effect
    @reactive.event(input.btn_abrir_modal)
    def show_modal():
        import pandas as pd
        current_list = reactive_lista_comp_snies.get()
        clean_list = [str(int(c)) for c in current_list if pd.notna(c)]
        current_str = " ".join(clean_list) if clean_list else ""
        modal_snies_activados.set(current_list)
        
        m = ui.modal(
            ui.tags.style(".modal-xl { max-width: 95vw !important; }"),
            ui.h4("Personalizar Grupo Comparable", style="color: #31497e; font-weight: bold;"),
            ui.p("Pegue los códigos SNIES de los programas con los cuales desea comparar el programa base. Al guardar, los filtros de atributos automáticos serán deshabilitados en favor de esta lista.", style="margin-bottom: 15px;"),
            ui.layout_columns(
                ui.div(
                    ui.h5("1. Lista Manual de SNIES"),
                    ui.input_text_area(
                        "txt_snies_manual", 
                        "Pegue los códigos separados por coma, espacio o tabulador", 
                        value=current_str,
                        width="100%", 
                        height="250px"
                    ),
                    ui.input_action_button("btn_aplicar_txt", "🔎 Previsualizar Selección", class_="btn-primary w-100 mb-3"),
                    ui.h5("2. Descargar Catálogo Base"),
                    ui.p("Si necesita buscar qué códigos SNIES incluir, descargue aquí el catálogo completo nacional y filtre manualmente en Excel para armar su grupo.", style="font-size: 0.8em; color: gray; margin-bottom: 5px;"),
                    ui.download_button("btn_descargar_snies", "⬇ Descargar Base Filtrada (.xlsx)", class_="btn-outline-secondary w-100")
                ),
                ui.div(
                    ui.h5("Programas Seleccionados"),
                    ui.p("Esta tabla valida si los códigos SNIES ingresados existen y cuáles son sus nombres.", style="font-size: 0.8em; color: gray; margin-bottom: 5px;"),
                    ui.output_data_frame("tabla_modal_snies")
                ),
                col_widths=(4, 8)
            ),
            title="Refinar Grupo Manualmente",
            easy_close=False,
            footer=ui.div(
                ui.input_action_button("btn_guardar_modal", "Cargar y Finalizar", class_="btn-success"),
                ui.modal_button("Cancelar")
            ),
            size="xl"
        )
        ui.modal_show(m)

    @render.download(filename="Catalogo_SNIES_Filtrado.xlsx")
    def btn_descargar_snies():
        attr = comp_profile_attr()
        criterios = input.comp_criterios() or []
        df_export = df_snies.filter(pl.col("estado_programa") == "ACTIVO")
        
        if attr:
            if "nivel_de_formacion" in criterios and attr.get("nivel_de_formacion"):
                df_export = df_export.filter(pl.col("nivel_de_formacion") == attr["nivel_de_formacion"])
            if "modalidad" in criterios and attr.get("modalidad"):
                df_export = df_export.filter(pl.col("modalidad") == attr["modalidad"])
            if "sector" in criterios and attr.get("sector"):
                df_export = df_export.filter(pl.col("sector") == attr["sector"])
            if "area_de_conocimiento" in criterios and attr.get("area_de_conocimiento"):
                df_export = df_export.filter(pl.col("area_de_conocimiento") == attr["area_de_conocimiento"])
            if "nucleo_basico_del_conocimiento" in criterios and attr.get("nucleo_basico_del_conocimiento"):
                df_export = df_export.filter(pl.col("nucleo_basico_del_conocimiento") == attr["nucleo_basico_del_conocimiento"])
                
            if "departamento_oferta" in criterios and attr.get("departamento_oferta"):
                df_cob_match = df_cobertura.filter(pl.col("departamento_oferta") == attr["departamento_oferta"])
                valid_snies_dept = df_cob_match["codigo_snies_del_programa"].unique()
                df_export = df_export.filter(pl.col("codigo_snies_del_programa").is_in(valid_snies_dept))
                
        df_export = df_export.select([
            "codigo_snies_del_programa", 
            "programa_academico", 
            "nombre_institucion", 
            "departamento_principal",
            "nivel_de_formacion",
            "modalidad",
            "sector",
            "area_de_conocimiento",
            "nucleo_basico_del_conocimiento"
        ])
        
        import io
        import pandas as pd
        output = io.BytesIO()
        df_pd = df_export.to_pandas()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_pd.to_excel(writer, index=False)
        yield output.getvalue()

    @reactive.effect
    @reactive.event(input.btn_aplicar_txt)
    def update_from_txt():
        import re
        raw_text = input.txt_snies_manual() or ""
        codes = re.findall(r'\d+', raw_text)
        codes = list(set([int(c) for c in codes]))
        modal_snies_activados.set(codes)

    @render.data_frame
    def tabla_modal_snies():
        activados = set(modal_snies_activados.get())
        if not activados:
            import pandas as pd
            return render.DataGrid(pd.DataFrame(), filters=False, width="100%", selection_mode="none")
            
        df_view = df_snies.filter(
            (pl.col("estado_programa") == "ACTIVO") & 
            (pl.col("codigo_snies_del_programa").is_in(list(activados)))
        ).select([
            "codigo_snies_del_programa", 
            "programa_academico", 
            "nombre_institucion", 
            "departamento_principal",
            "nivel_de_formacion",
            "modalidad",
            "sector",
            "area_de_conocimiento",
            "nucleo_basico_del_conocimiento"
        ])
        
        import pandas as pd
        return render.DataGrid(df_view.to_pandas(), filters=True, width="100%", selection_mode="none")

    @reactive.effect
    @reactive.event(input.btn_guardar_modal)
    def save_modal():
        import re
        raw_text = input.txt_snies_manual() or ""
        codes = re.findall(r'\d+', raw_text)
        codes = list(set([int(c) for c in codes]))
        
        reactive_lista_comp_snies.set(codes)
        ui.update_switch("switch_modo_manual", value=True)
        ui.modal_remove()

    def calc_comp_metric(df_source, metric_col):
        import pandas as pd
        attr = comp_profile_attr()
        if not attr: 
            return pd.DataFrame(), pd.DataFrame()
            
        # 1. SERIE BASE (SNIES INDIVIDUAL)
        df_snies_base = df_cobertura.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        divipolas_base = df_snies_base["snies_divipola"].unique()
        
        # Sumamos por si tiene multiples divipolas (ej. se oferta en varias sedes)
        df_base_agg = df_source.filter(
            pl.col("snies_divipola").is_in(divipolas_base) & (pl.col("anno") >= 2016)
        ).group_by("anno").agg(pl.col(metric_col).sum().alias("valor_base")).sort("anno")
        
        df_base_pd = df_base_agg.to_pandas()
        
        # 2. SERIE COMPARABLE (PROMEDIO NACIONAL/FILTRADO)
        comp_divipolas = comparable_snies_list()
        if len(comp_divipolas) == 0:
            return df_base_pd, pd.DataFrame()
            
        df_comp = df_source.filter(
            pl.col("snies_divipola").is_in(comp_divipolas) & (pl.col("anno") >= 2016)
        )
        
        # Primero sumamos a nivel de SNIES Divipola individualmente en cada año
        # (agrupando por las iteraciones que haya si las hay) para tener el valor "por programa"
        df_comp_prog = df_comp.group_by(["anno", "snies_divipola"]).agg(pl.col(metric_col).sum())
        
        # Segundo, calculamos mediana y MAD (Desviación Absoluta de la Mediana) por año
        # El MAD se escala por 1.4826 para ser comparable con la desviación estándar en distribuciones normales.
        df_comp_agg = df_comp_prog.group_by("anno").agg([
            pl.col(metric_col).median().alias("valor_comp_median"),
            ((pl.col(metric_col) - pl.col(metric_col).median()).abs().median() * 1.4826).alias("valor_comp_mad"),
            pl.col(metric_col).sum().alias("valor_comp_sum"),
            pl.col(metric_col).count().alias("n_programas")
        ]).sort("anno")
        
        df_comp_pd = df_comp_agg.to_pandas()
        
        # Manejar casos sin dispersión (n=1)
        df_comp_pd["valor_comp_mad"] = df_comp_pd["valor_comp_mad"].fillna(0)
        
        return df_base_pd, df_comp_pd

    def build_comp_plot(df_base_pd, df_comp_pd, title):
        import plotly.graph_objects as go
        fig = go.Figure()
        
        if df_comp_pd.empty and df_base_pd.empty:
            return fig
            
        color_base = "#31497e"  # Primer color: Azul
        color_comp = "#674f95"  # Segundo color: Púrpura
        color_band = "rgba(103, 79, 149, 0.15)" # Púrpura semitransparente (basado en #674f95)
        
        # Traza de Banda Sombreada (Mediana ± MAD escalado)
        if not df_comp_pd.empty:
            y_lower = (df_comp_pd["valor_comp_median"] - df_comp_pd["valor_comp_mad"]).clip(lower=0) 
            y_upper = df_comp_pd["valor_comp_median"] + df_comp_pd["valor_comp_mad"]
            
            fig.add_trace(go.Scatter(
                x=df_comp_pd["anno"],
                y=y_lower,
                marker=dict(color="#444"),
                line=dict(width=0),
                mode='lines',
                showlegend=False,
                hoverinfo='skip'
            ))
            
            # Traza de Banda Superior
            fig.add_trace(go.Scatter(
                x=df_comp_pd["anno"],
                y=y_upper,
                marker=dict(color="#444"),
                line=dict(width=0),
                mode='lines',
                fillcolor=color_band,
                fill='tonexty',
                name='Dispersión (Mediana ± 1.48 MAD)',
                hoverinfo='skip'
            ))
            
            # Traza Mediana Comparable
            fig.add_trace(go.Scatter(
                x=df_comp_pd["anno"],
                y=df_comp_pd["valor_comp_median"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:,.0f}',
                name='Mediana Comparable',
                line=dict(color=color_comp, width=3, dash='dash'),
                marker=dict(size=8, color="white", line=dict(width=2, color=color_comp)),
                hovertemplate="Año: %{x}<br>Mediana: %{y:,.0f} est.<br>N: %{customdata} prog.<extra></extra>",
                customdata=df_comp_pd["n_programas"]
            ))

        # Traza Programa Base
        if not df_base_pd.empty:
            attr = comp_profile_attr()
            prog_name = f"SNIES {attr['codigo']}" if attr else "Prog. Base"
            fig.add_trace(go.Scatter(
                x=df_base_pd["anno"],
                y=df_base_pd["valor_base"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:,.0f}',
                name=prog_name,
                line=dict(color=color_base, width=4),
                marker=dict(size=9, color="white", line=dict(width=2.5, color=color_base)),
                hovertemplate="Año: %{x}<br>Total: %{y:,.0f} est.<extra></extra>"
            ))
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear"),
            yaxis=dict(title="Número de Estudiantes", gridcolor='#EEEEEE'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            separators=',.'
        )
        return fig

    # Gráficas
    @reactive.calc
    def calc_fig_comp_pcurso():
        df_base, df_comp = calc_comp_metric(df_pcurso, "primer_curso_sum")
        return build_comp_plot(df_base, df_comp, "Primer Curso")

    @render.ui
    def plot_comp_pcurso():
        fig = calc_fig_comp_pcurso()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_matricula():
        df_base, df_comp = calc_comp_metric(df_matricula, "matricula_sum")
        return build_comp_plot(df_base, df_comp, "Matriculados")

    @render.ui
    def plot_comp_matricula():
        fig = calc_fig_comp_matricula()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_graduados():
        df_base, df_comp = calc_comp_metric(df_graduados, "graduados_sum")
        return build_comp_plot(df_base, df_comp, "Graduados")

    @render.ui
    def plot_comp_graduados():
        fig = calc_fig_comp_graduados()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    def calc_comp_ole_metric(num_col, den_col):
        import pandas as pd
        attr = comp_profile_attr()
        if not attr: 
            return pd.DataFrame(), pd.DataFrame()
            
        # 1. SERIE BASE
        df_base_filtered = df_ole_m0.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        df_base_agg = df_base_filtered.group_by("anno_corte").agg([
            pl.col(num_col).sum().alias("num"),
            pl.col(den_col).sum().alias("den")
        ]).filter(pl.col("den") > 0).sort("anno_corte")
        
        df_base_pd = df_base_agg.with_columns((pl.col("num") / pl.col("den")).alias("valor_base")).to_pandas()
        
        # 2. SERIE COMPARABLE
        comp_codigos = comparable_snies_codigos()
        if len(comp_codigos) == 0:
            return df_base_pd, pd.DataFrame()
            
        df_comp_filtered = df_ole_m0.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))
        
        df_comp_prog = df_comp_filtered.group_by(["anno_corte", "codigo_snies_del_programa"]).agg([
            pl.col(num_col).sum().alias("num"),
            pl.col(den_col).sum().alias("den")
        ]).filter(pl.col("den") > 0).with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
        
        df_comp_agg = df_comp_prog.group_by("anno_corte").agg([
            pl.col("tasa").mean().alias("valor_comp_mean"),
            pl.col("tasa").std().alias("valor_comp_std"),
            pl.col("tasa").count().alias("n_programas")
        ]).sort("anno_corte")
        
        df_comp_pd = df_comp_agg.to_pandas()
        df_comp_pd["valor_comp_std"] = df_comp_pd["valor_comp_std"].fillna(0)
        
        # Estandarizar nombre de columna de año
        if not df_base_pd.empty: df_base_pd["anno"] = df_base_pd["anno_corte"]
        if not df_comp_pd.empty: df_comp_pd["anno"] = df_comp_pd["anno_corte"]
        
        return df_base_pd, df_comp_pd

    def build_comp_plot_ole(df_base_pd, df_comp_pd, title):
        import plotly.graph_objects as go
        fig = go.Figure()
        
        if df_comp_pd.empty and df_base_pd.empty:
            return fig
            
        color_base = "#31497e"
        color_comp = "#674f95"
        color_band = "rgba(103, 79, 149, 0.15)"
        
        # Detectar columna de tiempo (anno o anno_corte)
        col_x = "anno" if "anno" in df_base_pd.columns or "anno" in df_comp_pd.columns else "anno_corte"

        if not df_comp_pd.empty:
            y_lower = (df_comp_pd["valor_comp_mean"] - df_comp_pd["valor_comp_std"]).clip(lower=0) 
            y_upper = (df_comp_pd["valor_comp_mean"] + df_comp_pd["valor_comp_std"]).clip(upper=1)
            
            fig.add_trace(go.Scatter(x=df_comp_pd[col_x], y=y_lower, marker=dict(color="#444"), line=dict(width=0), mode='lines', showlegend=False, hoverinfo='skip'))
            fig.add_trace(go.Scatter(x=df_comp_pd[col_x], y=y_upper, marker=dict(color="#444"), line=dict(width=0), mode='lines', fillcolor=color_band, fill='tonexty', name='Dispersión (Media ± 1 Std. Dev)', hoverinfo='skip'))
            
            fig.add_trace(go.Scatter(
                x=df_comp_pd[col_x],
                y=df_comp_pd["valor_comp_mean"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:.1%}',
                name='Media Comparable',
                line=dict(color=color_comp, width=3, dash='dash'),
                marker=dict(size=8, color="white", line=dict(width=2, color=color_comp)),
                hovertemplate="Año: %{x}<br>Media: %{y:.1%}<extra></extra>",
                customdata=df_comp_pd["n_programas"]
            ))

        if not df_base_pd.empty:
            attr = comp_profile_attr()
            prog_name = f"SNIES {attr['codigo']}" if attr else "Prog. Base"
            fig.add_trace(go.Scatter(
                x=df_base_pd[col_x],
                y=df_base_pd["valor_base"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:.1%}',
                name=prog_name,
                line=dict(color=color_base, width=4),
                marker=dict(size=9, color="white", line=dict(width=2.5, color=color_base)),
                hovertemplate="Año: %{x}<br>Tasa: %{y:.1%}<extra></extra>"
            ))
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Tasa", tickformat=".1%", gridcolor='#EEEEEE'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    def build_comp_plot_salario(df_base_pd, df_comp_pd, title):
        import plotly.graph_objects as go
        fig = go.Figure()
        
        if df_comp_pd.empty and df_base_pd.empty:
            return fig
            
        color_base = "#31497e"
        color_comp = "#674f95"
        color_band = "rgba(103, 79, 149, 0.15)"
        
        if not df_comp_pd.empty:
            y_lower = (df_comp_pd["valor_comp_mean"] - df_comp_pd["valor_comp_std"]).clip(lower=0) 
            y_upper = (df_comp_pd["valor_comp_mean"] + df_comp_pd["valor_comp_std"])
            
            fig.add_trace(go.Scatter(x=df_comp_pd["anno"], y=y_lower, marker=dict(color="#444"), line=dict(width=0), mode='lines', showlegend=False, hoverinfo='skip'))
            fig.add_trace(go.Scatter(x=df_comp_pd["anno"], y=y_upper, marker=dict(color="#444"), line=dict(width=0), mode='lines', fillcolor=color_band, fill='tonexty', name='Dispersión (±1 SD)', hoverinfo='skip'))
            
            fig.add_trace(go.Scatter(
                x=df_comp_pd["anno"],
                y=df_comp_pd["valor_comp_mean"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:,.0f}',
                name='Media',
                line=dict(color=color_comp, width=3, dash='dash'),
                marker=dict(size=8, color="white", line=dict(width=2, color=color_comp)),
                hovertemplate="Año: %{x}<br>Media: $%{y:,.0f}<br>N: %{customdata} prog.<extra></extra>",
                customdata=df_comp_pd["n_programas"]
            ))

        if not df_base_pd.empty:
            attr = comp_profile_attr()
            prog_name = f"SNIES {attr['codigo']}" if attr else "Prog. Base"
            fig.add_trace(go.Scatter(
                x=df_base_pd["anno"],
                y=df_base_pd["valor_base"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:,.0f}',
                name=prog_name,
                line=dict(color=color_base, width=4),
                marker=dict(size=9, color="white", line=dict(width=2.5, color=color_base)),
                hovertemplate="Año: %{x}<br>Salario: $%{y:,.0f}<extra></extra>"
            ))
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario ($)", tickformat="$,.0f", gridcolor='#EEEEEE'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    @reactive.calc
    def calc_fig_comp_ole_empleabilidad():
        df_base, df_comp = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
        return build_comp_plot_ole(df_base, df_comp, "Tasa de Empleabilidad")

    @render.ui
    def plot_comp_ole_empleabilidad():
        fig = calc_fig_comp_ole_empleabilidad()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_ole_dependientes():
        df_base, df_comp = calc_comp_ole_metric("graduados_cotizantes_dependientes", "graduados_que_cotizan")
        return build_comp_plot_ole(df_base, df_comp, "Dependientes sobre Cotizantes")

    @render.ui
    def plot_comp_ole_dependientes():
        fig = calc_fig_comp_ole_dependientes()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_comp_mobility_tables():
        import pandas as pd
        snies_base_full = input.comp_snies_base()
        if not snies_base_full:
            return pd.DataFrame(), pd.DataFrame(), "Lugar"
            
        try:
            # Extraer solo el código SNIES del string "CÓDIGO - NOMBRE"
            snies_base = int(str(snies_base_full).split(" - ")[0])
        except:
            return pd.DataFrame(), pd.DataFrame(), "Lugar"
            
        # Detectar nivel de agregación (Depto/Mpio) según filtros generales
        f_vals = isolated_filters()
        mpio_filtro = f_vals["municipio"]
        if hasattr(mpio_filtro, '__iter__') and len(mpio_filtro) > 0 and mpio_filtro[0]:
            col_o = "municipio_origen"
            col_d = "municipio_destino"
            label = "Municipio"
        else:
            col_o = "departamento_origen"
            col_d = "departamento_destino"
            label = "Departamento"
            
        max_anno = df_ole_m0["anno_corte"].max()
        # Filtro estricto para el programa base
        df_base = df_ole_m0.filter(
            (pl.col("codigo_snies_del_programa") == snies_base) & 
            (pl.col("anno_corte") == max_anno)
        ).to_pandas()
        
        if df_base.empty:
            return pd.DataFrame(), pd.DataFrame(), label
            
        # Origen
        df_o = df_base.groupby(col_o)["graduados_que_cotizan"].sum().reset_index()
        df_o.columns = [label, "Cotizantes"]
        df_o = df_o.sort_values("Cotizantes", ascending=False)
        total_o = df_o["Cotizantes"].sum()
        if total_o > 0:
            df_o["Porcentaje"] = (df_o["Cotizantes"] / total_o * 100).round(1).astype(str) + "%"
        
        # Destino
        df_d = df_base.groupby(col_d)["graduados_que_cotizan"].sum().reset_index()
        df_d.columns = [label, "Cotizantes"]
        df_d = df_d.sort_values("Cotizantes", ascending=False)
        total_d = df_d["Cotizantes"].sum()
        if total_d > 0:
            df_d["Porcentaje"] = (df_d["Cotizantes"] / total_d * 100).round(1).astype(str) + "%"
        
        return df_o, df_d, label

    @render.data_frame
    def comp_ole_top_origen():
        df_o, _, _ = calc_comp_mobility_tables()
        return render.DataGrid(df_o, filters=True, width="100%", selection_mode="none")

    @render.data_frame
    def comp_ole_top_destino():
        _, df_d, _ = calc_comp_mobility_tables()
        return render.DataGrid(df_d, filters=True, width="100%", selection_mode="none")

    @render.ui
    def comp_dist_empleabilidad_header():
        max_yr = df_ole_m0["anno_corte"].max()
        return ui.HTML(f"Distribución de <b style='color: #31497e;'>Tasa de Empleabilidad</b> ({max_yr})")

    @reactive.calc
    def get_comp_ole_dist_empleabilidad():
        import pandas as pd
        max_anno_corte = df_ole_m0["anno_corte"].max()
        
        attr = comp_profile_attr()
        df_total = pd.DataFrame()
        if attr and attr.get("nivel_de_formacion"):
            nivel = attr["nivel_de_formacion"]
            valid_snies_nivel = df_snies.filter((pl.col("nivel_de_formacion") == nivel) & (pl.col("estado_programa") == "ACTIVO"))["codigo_snies_del_programa"].unique()
            
            ole_nivel = df_ole_m0.filter(
                pl.col("codigo_snies_del_programa").is_in(valid_snies_nivel) & 
                (pl.col("anno_corte") == max_anno_corte)
            )
            
            if len(ole_nivel) > 0:
                agg_total = ole_nivel.group_by(["codigo_snies_del_programa"]).agg([
                    pl.col("graduados_que_cotizan").sum().alias("num"),
                    pl.col("graduados").sum().alias("den")
                ]).filter(pl.col("den") > 0).with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
                df_total = agg_total.to_pandas()
                df_total["grupo"] = "Mismo Nivel de Formación"
        
        comp_codigos = comparable_snies_codigos()
        df_comp = pd.DataFrame()
        if len(comp_codigos) > 0:
            ole_comp = df_ole_m0.filter(
                pl.col("codigo_snies_del_programa").is_in(comp_codigos) & 
                (pl.col("anno_corte") == max_anno_corte)
            )
            if len(ole_comp) > 0:
                agg_comp = ole_comp.group_by(["codigo_snies_del_programa"]).agg([
                    pl.col("graduados_que_cotizan").sum().alias("num"),
                    pl.col("graduados").sum().alias("den")
                ]).filter(pl.col("den") > 0).with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
                df_comp = agg_comp.to_pandas()
                df_comp["grupo"] = "Grupo Comparable"
                
        if df_total.empty and df_comp.empty:
            return pd.DataFrame()
        
        return pd.concat([df_total, df_comp], ignore_index=True)

    @reactive.calc
    def calc_plot_comp_dist_empleabilidad():
        import plotly.graph_objects as go
        df_pd = get_comp_ole_dist_empleabilidad()
        if df_pd.empty: return go.Figure()
        
        fig = px.histogram(df_pd, x="tasa", color="grupo", barmode='overlay', histnorm='percent', 
                           color_discrete_map={"Mismo Nivel de Formación": "#ced4da", "Grupo Comparable": "#674f95"})
        fig.update_traces(xbins=dict(start=0.0, end=1.0, size=0.05), marker_line_width=1.5, marker_line_color="white", opacity=0.82)
        
        # Agregar línea del programa base
        attr = comp_profile_attr()
        if attr:
            df_base, _ = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
            if not df_base.empty:
                tasa_base = df_base["valor_base"].iloc[-1]
                fig.add_vline(x=tasa_base, line_width=3.5, line_dash="dash", line_color="#31497e", 
                              annotation_text=f"Prog. Seleccionado<br>({tasa_base:.1%})", annotation_position="top",
                              annotation_font_color="#31497e", annotation_font_size=18)
                          
        fig.update_layout(
            legend_title_text="",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=80, r=60, t=120, b=70),
            font=dict(size=24, family="Montserrat"),
            xaxis=dict(
                title=dict(text="Tasa de Empleabilidad", font=dict(size=28)),
                tickformat=".0%", dtick=0.05, gridcolor='#EEEEEE', automargin=True,
                tickfont=dict(size=22)
            ),
            yaxis=dict(
                title=dict(text="Porcentaje de Programas (%)", font=dict(size=28)),
                ticksuffix="%", gridcolor='#EEEEEE', automargin=True,
                tickfont=dict(size=22)
            ),
            legend=dict(orientation="h", yanchor="bottom", y=1.12, xanchor="center", x=0.5, font=dict(size=22))
        )
        # Format tooltips
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}%<extra></extra>')
        return fig

    @render.ui
    def plot_comp_dist_empleabilidad():
        return ui.HTML(pio.to_html(calc_plot_comp_dist_empleabilidad(), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def comp_salario_evolucion_header():
        max_yr = df_ole_salario["anno_corte"].max()
        return ui.HTML(f"Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos Ctes. {max_yr})")

    @reactive.calc
    def calc_comp_salario_evolucion():
        import pandas as pd
        attr = comp_profile_attr()
        if not attr: 
            return pd.DataFrame(), pd.DataFrame()
        
        # Sincronización de Filtros Geográficos del Sidebar (para coherencia con pestaña original)
        f_vals = isolated_filters()
        dept = f_vals["departamento"]
        mpio = f_vals["municipio"]
        
        df_base = df_ole_salario.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        
        comp_codigos = comparable_snies_codigos()
        
        if len(comp_codigos) == 0:
            df_comp = pd.DataFrame()
        else:
            df_comp = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))
            
        def process_salary_df(df_in):
            if len(df_in) == 0: return pl.DataFrame()
            d = df_in.join(df_smmlv_pl, on="anno_corte", how="inner")
            
            # Pesos constantes basados en el último año de datos salariales reales
            max_data_year = df_ole_salario["anno_corte"].max()
            smmlv_ref = df_smmlv_pl.filter(pl.col("anno_corte") == max_data_year)["smmlv"]
            if smmlv_ref.len() > 0:
                latest_smmlv = smmlv_ref[0]
            else:
                latest_smmlv = df_smmlv_pl.sort("anno_corte").get_column("smmlv").tail(1).item()
                
            d = d.with_columns(pl.lit(latest_smmlv).alias("smmlv_calc"))
                
            d = d.with_columns(
                pl.col("rango_salario").replace(SALARIO_MIDPOINTS, default=1.0).cast(pl.Float64).alias("midpoint")
            )
            agg_prog_sexo = d.group_by(["anno_corte", "codigo_snies_del_programa", "sexo"]).agg([
                ((pl.col("midpoint") * pl.col("graduados_cotizantes_dependientes")).sum() / 
                 pl.col("graduados_cotizantes_dependientes").sum() * pl.col("smmlv_calc").first()).alias("sal_prog_sexo"),
                pl.col("graduados_cotizantes_dependientes").sum().alias("grad_sexo")
            ])
            
            agg_prog = agg_prog_sexo.group_by(["anno_corte", "codigo_snies_del_programa"]).agg([
                pl.col("sal_prog_sexo").mean().alias("sal_prog"),
                pl.col("grad_sexo").sum().alias("graduados_cotizantes_dependientes")
            ]).filter(pl.col("sal_prog").is_not_null())
            return agg_prog

        base_prog = process_salary_df(df_base)
        if len(base_prog) > 0:
            df_base_pd = base_prog.group_by("anno_corte").agg([
                pl.col("sal_prog").mean().alias("valor_base"),
                pl.col("graduados_cotizantes_dependientes").sum().alias("cotizantes_base")
            ]).sort("anno_corte").to_pandas()
            df_base_pd["anno"] = df_base_pd["anno_corte"]
        else:
            df_base_pd = pd.DataFrame()

        comp_prog = process_salary_df(df_comp)
        if len(comp_prog) > 0:
            df_comp_pd = comp_prog.group_by("anno_corte").agg([
                pl.col("sal_prog").mean().alias("valor_comp_mean"),
                pl.col("sal_prog").std().alias("valor_comp_std"),
                pl.col("graduados_cotizantes_dependientes").sum().alias("cotizantes_sum"),
                pl.col("sal_prog").count().alias("n_programas")
            ]).sort("anno_corte").to_pandas()
            df_comp_pd["valor_comp_std"] = df_comp_pd["valor_comp_std"].fillna(0)
            df_comp_pd["anno"] = df_comp_pd["anno_corte"]
        else:
            df_comp_pd = pd.DataFrame()
            
        return df_base_pd, df_comp_pd

    @reactive.calc
    def calc_comp_salario_dist_data():
        import pandas as pd
        attr = comp_profile_attr()
        df_base = pd.DataFrame()
        df_comp = pd.DataFrame()
        
        if attr:
            base_pl = df_ole_salario.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
            if len(base_pl) > 0:
                max_yr = base_pl["anno_corte"].max()
                agg_base = base_pl.filter(pl.col("anno_corte") == max_yr).group_by("rango_salario").agg(
                    pl.col("graduados_cotizantes_dependientes").sum().alias("cantidad")
                ).to_pandas()
                tot = agg_base["cantidad"].sum()
                agg_base["porcentaje"] = agg_base["cantidad"] / tot if tot > 0 else 0
                agg_base["grupo"] = "Programa Seleccionado"
                df_base = agg_base
                
        comp_codigos = comparable_snies_codigos()
        if len(comp_codigos) > 0:
            comp_pl = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))
            if len(comp_pl) > 0:
                max_yr_comp = comp_pl["anno_corte"].max()
                agg_comp = comp_pl.filter(pl.col("anno_corte") == max_yr_comp).group_by("rango_salario").agg(
                    pl.col("graduados_cotizantes_dependientes").sum().alias("cantidad")
                ).to_pandas()
                tot_comp = agg_comp["cantidad"].sum()
                agg_comp["porcentaje"] = agg_comp["cantidad"] / tot_comp if tot_comp > 0 else 0
                agg_comp["grupo"] = "Grupo Comparable"
                df_comp = agg_comp
                
        res = pd.concat([df_base, df_comp], ignore_index=True)
        if not res.empty:
            res["rango_salario"] = pd.Categorical(res["rango_salario"], categories=RANGO_SALARIO_ORDER, ordered=True)
            res = res.sort_values(["rango_salario", "grupo"])
        return res

    @reactive.calc
    def calc_fig_comp_salario_dist():
        import plotly.graph_objects as go
        df = calc_comp_salario_dist_data()
        if df.empty: return go.Figure()
        
        fig = px.bar(df, x="porcentaje", y="rango_salario", color="grupo", orientation='h', barmode='group', 
                     color_discrete_map={"Programa Seleccionado": "#31497e", "Grupo Comparable": "#674f95"}, 
                     text_auto='.1%')
        fig.update_traces(marker_line_width=1.5, marker_line_color="white", textposition='auto', textfont_size=12)
        fig.update_layout(
            legend_title_text="",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Participación de Graduados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=13)),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    @render.ui
    def plot_comp_salario_dist():
        return ui.HTML(pio.to_html(calc_fig_comp_salario_dist(), full_html=False, include_plotlyjs="cdn"))
        
    @reactive.calc
    def calc_fig_comp_salario_evolucion():
        df_base, df_comp = calc_comp_salario_evolucion()
        return build_comp_plot_salario(df_base, df_comp, "Salario Promedio de Enganche")

    @render.ui
    def plot_comp_salario_evolucion():
        return ui.HTML(pio.to_html(calc_fig_comp_salario_evolucion(), full_html=False, include_plotlyjs="cdn"))

    # Costos y Créditos KPIs (Comparativo)
    @render.ui
    def comp_kpi_base_promedio_matricula():
        attr = comp_profile_attr()
        if not attr: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        
        df_base = df_snies.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("costo_matricula_estud_nuevos") > 0))
        if len(df_base) == 0: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        
        val = df_base["costo_matricula_estud_nuevos"][0]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #31497e;'>${format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_promedio_matricula():
        snies_list = comparable_snies_codigos()
        if not snies_list: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        df_comp = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("sector") == "PRIVADO") & (pl.col("costo_matricula_estud_nuevos") > 0))
        if len(df_comp) == 0: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        data = df_comp["costo_matricula_estud_nuevos"].to_list()
        import numpy as np
        avg = np.mean(data)
        std = np.std(data)
        return ui.HTML(f"""
            <div style='font-size: 40px; font-weight: bold; color: #674f95; line-height: 1;'>${format_num_es(avg)}</div>
            <div style='font-size: 15px; color: #666; margin-top: 4px;'>± {format_num_es(std)} (SD)</div>
        """)

    @render.ui
    def comp_kpi_mediana_matricula():
        snies_list = comparable_snies_codigos()
        if not snies_list: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        df_comp = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("sector") == "PRIVADO") & (pl.col("costo_matricula_estud_nuevos") > 0))
        if len(df_comp) == 0: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        data = df_comp["costo_matricula_estud_nuevos"].to_list()
        import numpy as np
        median = np.median(data)
        mad = np.median([abs(x - median) for x in data]) * 1.4826
        return ui.HTML(f"""
            <div style='font-size: 40px; font-weight: bold; color: #674f95; line-height: 1;'>${format_num_es(median)}</div>
            <div style='font-size: 15px; color: #666; margin-top: 4px;'>± {format_num_es(mad)} (MAD)</div>
        """)

    @render.ui
    def comp_kpi_base_promedio_creditos():
        attr = comp_profile_attr()
        if not attr: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        df_base = df_snies.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("numero_creditos") > 0))
        if len(df_base) == 0: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["numero_creditos"][0]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #31497e;'>{val:.1f}</div>")

    @render.ui
    def comp_kpi_promedio_creditos():
        snies_list = comparable_snies_codigos()
        if not snies_list: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        df_comp = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("numero_creditos") > 0))
        if len(df_comp) == 0: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        data = df_comp["numero_creditos"].to_list()
        import numpy as np
        avg = np.mean(data)
        std = np.std(data)
        return ui.HTML(f"""
            <div style='font-size: 40px; font-weight: bold; color: #674f95; line-height: 1;'>{avg:.1f}</div>
            <div style='font-size: 15px; color: #666; margin-top: 4px;'>± {std:.1f} (SD)</div>
        """)

    @reactive.calc
    def calc_fig_comp_dist_costo():
        attr = comp_profile_attr()
        snies_list = comparable_snies_codigos()
        
        df_universe = df_snies.filter((pl.col("estado_programa") == "ACTIVO") & (pl.col("sector") == "PRIVADO") & (pl.col("costo_matricula_estud_nuevos") > 0))
        df_comp = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("sector") == "PRIVADO") & (pl.col("costo_matricula_estud_nuevos") > 0))
        
        fig = go.Figure()
        
        if len(df_universe) > 0:
            fig.add_trace(go.Histogram(
                x=df_universe["costo_matricula_estud_nuevos"].to_list(),
                histnorm='percent',
                name='Universo (Todos Activos)',
                marker_color='lightgray',
                xbins=dict(size=200000),
                opacity=0.6
            ))
            
        if len(df_comp) > 0:
            fig.add_trace(go.Histogram(
                x=df_comp["costo_matricula_estud_nuevos"].to_list(),
                histnorm='percent',
                name='Grupo Comparable',
                marker_color='#674f95',
                xbins=dict(size=200000),
                opacity=0.9
            ))
            
        fig.update_layout(
            barmode='overlay',
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Costo de Matrícula ($)", tickformat="$,.0f"),
            yaxis=dict(title="Porcentaje (%)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        if attr:
            df_base = df_snies.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("costo_matricula_estud_nuevos") > 0))
            if len(df_base) > 0:
                val = df_base["costo_matricula_estud_nuevos"][0]
                fig.add_vline(x=val, line_dash="dash", line_color="#31497e", line_width=3, annotation_text="Programa Seleccionado", annotation_position="top right", annotation_font_color="#31497e")
        return fig

    @render.ui
    def plot_comp_dist_costo_matricula():
        fig = calc_fig_comp_dist_costo()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_dist_creditos():
        attr = comp_profile_attr()
        snies_list = comparable_snies_codigos()
        
        df_universe = df_snies.filter((pl.col("estado_programa") == "ACTIVO") & (pl.col("numero_creditos") > 0))
        df_comp = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("numero_creditos") > 0))
        
        fig = go.Figure()
        
        if len(df_universe) > 0:
            fig.add_trace(go.Histogram(
                x=df_universe["numero_creditos"].to_list(),
                histnorm='percent',
                name='Universo (Todos Activos)',
                marker_color='lightgray',
                opacity=0.6
            ))
            
        if len(df_comp) > 0:
            fig.add_trace(go.Histogram(
                x=df_comp["numero_creditos"].to_list(),
                histnorm='percent',
                name='Grupo Comparable',
                marker_color='#674f95',
                opacity=0.9
            ))
            
        fig.update_layout(
            barmode='overlay',
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Número de Créditos"),
            yaxis=dict(title="Porcentaje (%)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        if attr:
            df_base = df_snies.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("numero_creditos") > 0))
            if len(df_base) > 0:
                val = df_base["numero_creditos"][0]
                fig.add_vline(x=val, line_dash="dash", line_color="#31497e", line_width=3, annotation_text="Programa Seleccionado", annotation_position="top right", annotation_font_color="#31497e")
        return fig

    @render.ui
    def plot_comp_dist_creditos():
        fig = calc_fig_comp_dist_creditos()
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))
            
        if len(df_comp) > 0:
            fig.add_trace(go.Histogram(
                x=df_comp["numero_creditos"].to_list(),
                histnorm='percent',
                name='Grupo Comparable',
                marker_color='#674f95',
                opacity=0.9
            ))
            
        fig.update_layout(
            barmode='overlay',
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Número de Créditos"),
            yaxis=dict(title="Porcentaje (%)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        if attr:
            df_base = df_snies.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("numero_creditos") > 0))
            if len(df_base) > 0:
                val = df_base["numero_creditos"][0]
                fig.add_vline(x=val, line_dash="dash", line_color="#31497e", line_width=3, annotation_text="Programa Base", annotation_position="top right", annotation_font_color="#31497e")
                
        return ui.HTML(pio.to_html(fig, full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def comp_kpi_base_pcurso():
        df_base, _ = calc_comp_metric(df_pcurso, "primer_curso_sum")
        if df_base.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["valor_base"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_base_matricula():
        df_base, _ = calc_comp_metric(df_matricula, "matricula_sum")
        if df_base.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["valor_base"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_base_graduados():
        df_base, _ = calc_comp_metric(df_graduados, "graduados_sum")
        if df_base.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["valor_base"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_universo():
        _, df_comp = calc_comp_metric(df_pcurso, "primer_curso_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>0 prog.</div>")
        n = df_comp["n_programas"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(n)} prog.</div>")

    @render.ui
    def comp_kpi_neto_pcurso():
        _, df_comp = calc_comp_metric(df_pcurso, "primer_curso_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        val = df_comp["valor_comp_sum"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_neto_matricula():
        _, df_comp = calc_comp_metric(df_matricula, "matricula_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        val = df_comp["valor_comp_sum"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_neto_graduados():
        _, df_comp = calc_comp_metric(df_graduados, "graduados_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        val = df_comp["valor_comp_sum"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_pcurso():
        _, df_comp = calc_comp_metric(df_pcurso, "primer_curso_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        median = df_comp["valor_comp_median"].iloc[-1]
        mad = df_comp["valor_comp_mad"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(median)} <span style='font-size: 18px; color: gray;'>±{format_num_es(mad)} (MAD)</span></div>")

    @render.ui
    def comp_kpi_matricula():
        _, df_comp = calc_comp_metric(df_matricula, "matricula_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        median = df_comp["valor_comp_median"].iloc[-1]
        mad = df_comp["valor_comp_mad"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(median)} <span style='font-size: 18px; color: gray;'>±{format_num_es(mad)} (MAD)</span></div>")

    @render.ui
    def comp_kpi_graduados():
        _, df_comp = calc_comp_metric(df_graduados, "graduados_sum")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        median = df_comp["valor_comp_median"].iloc[-1]
        mad = df_comp["valor_comp_mad"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_num_es(median)} <span style='font-size: 18px; color: gray;'>±{format_num_es(mad)} (MAD)</span></div>")

    @render.ui
    def comp_kpi_base_empleabilidad():
        df_base, _ = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
        if df_base.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["valor_base"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #31497e;'>{format_pct_es(val)}</div>")

    @render.ui
    def comp_kpi_empleabilidad():
        _, df_comp = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
        if df_comp.empty: return ui.HTML("<div style='font-size: 40px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        mean = df_comp["valor_comp_mean"].iloc[-1]
        std = df_comp["valor_comp_std"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 40px; font-weight: bold; color: #674f95;'>{format_pct_es(mean)} <span style='font-size: 18px; color: gray;'>±{format_pct_es(std)} (SD)</span></div>")

    @render.ui
    def comp_kpi_base_cotizantes():
        df_base, _ = calc_comp_salario_evolucion()
        if df_base.empty: return ui.HTML("<div style='font-size: 34px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["cotizantes_base"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 34px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_cotizantes():
        _, df_comp = calc_comp_salario_evolucion()
        if df_comp.empty: return ui.HTML("<div style='font-size: 34px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        val = df_comp["cotizantes_sum"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 34px; font-weight: bold; color: #674f95;'>{format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_base_salario():
        df_base, _ = calc_comp_salario_evolucion()
        if df_base.empty: return ui.HTML("<div style='font-size: 34px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_base["valor_base"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 34px; font-weight: bold; color: #31497e;'>${format_num_es(val)}</div>")

    @render.ui
    def comp_kpi_salario():
        _, df_comp = calc_comp_salario_evolucion()
        if df_comp.empty: return ui.HTML("<div style='font-size: 34px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        mean = df_comp["valor_comp_mean"].iloc[-1]
        std = df_comp["valor_comp_std"].iloc[-1]
        return ui.HTML(f"<div style='font-size: 34px; font-weight: bold; color: #674f95;'>${format_num_es(mean)} <span style='font-size: 18px; color: gray;'>±${format_num_es(std)} (SD)</span></div>")

    def _prepare_report_content(engine, p):
        # 1. Preparar Datos Generales
        p.set(2, message="Capturando indicadores...", detail="Tendencias SNIES")
        data_ctx = {
            "max_anno_snies": max_anno_snies,
            "max_anno_ole": max_anno_ole,
            "max_anno_spadies": max_anno_desercion,
            "max_anno_saber": max_anno_saber,
            "date": datetime.datetime.now().strftime("%d/%m/%Y"),
            "kpis_summary": [
                ("Instituciones", calc_total_instituciones()),
                ("Programas", calc_total_programas()),
                ("Matrícula Total", calc_total_matriculados()),
                ("Tasa Empleabilidad", calc_kpi_empleabilidad())
            ],
            "sections": []
        }
        
        # SECCIÓN 1: TENDENCIAS SNIES
        p.set(4, message="Procesando sección:", detail="Tendencias SNIES")
        snies_plots = [
            engine.export_plotly_fig(calc_plot_primer_curso_total(), "pcurso_total"),
            engine.export_plotly_fig(calc_plot_matriculados_total(), "matricula_total"),
            engine.export_plotly_fig(calc_plot_graduados_total(), "graduados_total"),
            engine.export_plotly_fig(calc_plot_primer_curso(), "pcurso_sexo"),
            engine.export_plotly_fig(calc_plot_matriculados(), "matricula_sexo"),
            engine.export_plotly_fig(calc_plot_graduados(), "graduados_sexo")
        ]
        
        data_ctx["sections"].append({
            "title": "Tendencias SNIES (Oferta y Demanda)",
            "intro": "Esta sección analiza la evolución de la matrícula, los estudiantes de primer curso y los graduados. Permite identificar el flujo de entrada y salida del sistema de educación superior.",
            "kpis": [
                ("Primer Curso", calc_total_primer_curso()),
                ("Matriculados", calc_total_matriculados()),
                ("Graduados", calc_total_graduados())
            ],
            "plots": snies_plots,
            "table": f"""
#v(1em)
== Detalle de Estudiantes de Primer Curso
{{engine.format_as_typst_table(pl.from_pandas(calc_table_pcurso()))}}

#v(1em)
== Detalle de Estudiantes Matriculados
{{engine.format_as_typst_table(pl.from_pandas(calc_table_matriculados()))}}

#v(1em)
== Detalle de Graduados
{{engine.format_as_typst_table(pl.from_pandas(calc_table_graduados()))}}
"""
        })
        
        # SECCIÓN 2: OBSERVATORIO LABORAL (OLE)
        p.set(7, message="Procesando sección:", detail="Observatorio Laboral")
        ole_plots = [
            engine.export_plotly_fig(calc_plot_empleabilidad_total(), "ole_emp_total"),
            engine.export_plotly_fig(calc_plot_dependientes_total(), "ole_dep_total"),
            engine.export_plotly_fig(calc_plot_empleabilidad_sexo(), "ole_emp_sexo"),
            engine.export_plotly_fig(calc_plot_dependientes_sexo(), "ole_dep_sexo"),
            engine.export_plotly_fig(calc_plot_dist_empleabilidad(), "ole_dist_emp"),
            engine.export_plotly_fig(calc_plot_dist_dependientes(), "ole_dist_dep"),
            engine.export_plotly_fig(calc_plot_dist_empleabilidad_sexo(), "ole_dist_emp_sexo"),
            engine.export_plotly_fig(calc_plot_dist_dependientes_sexo(), "ole_dist_dep_sexo"),
            engine.export_plotly_fig(calc_plot_mobility_matrix(), "ole_mobility")
        ]
        
        data_ctx["sections"].append({
            "title": "Observatorio Laboral para la Educación (OLE)",
            "intro": "Métricas de vinculación laboral y movilidad de los graduados. Se analiza la capacidad de inserción en el mercado formal y el comportamiento geográfico de la fuerza laboral.",
            "kpis": [
                ("Empleabilidad", calc_kpi_empleabilidad()),
                ("Retención Local", calc_kpi_retencion()),
                ("Ratio Migratorio", calc_kpi_ratio())
            ],
            "plots": ole_plots
        })

        # SECCIÓN 3: SALARIOS DE ENGANCHE
        p.set(10, message="Procesando sección:", detail="Salario de Enganche")
        salario_plots = [
            engine.export_plotly_fig(calc_plot_salario_dist_total(), "sal_dist_total"),
            engine.export_plotly_fig(calc_plot_salario_dist_sexo(), "sal_dist_sexo"),
            engine.export_plotly_fig(calc_plot_salario_evolucion_total(), "sal_evol_total"),
            # engine.export_plotly_fig(calc_plot_salario_evolucion_sexo(), "sal_evol_sexo") # Si existe
        ]
        
        data_ctx["sections"].append({
            "title": "Salarios de Enganche",
            "intro": "Análisis del ingreso de los graduados en su primer empleo formal. Se presentan distribuciones por rangos de SMMLV y evolución histórica ajustada.",
            "kpis": [
                ("Salario Promedio", calc_kpi_salario_promedio_total()),
                ("Brecha Género (F)", calc_kpi_salario_promedio_fem()),
                ("Brecha Género (M)", calc_kpi_salario_promedio_masc())
            ],
            "plots": salario_plots
        })

        # SECCIÓN 4: DESERCIÓN
        p.set(13, message="Procesando sección:", detail="Deserción")
        desercion_plots = [
            engine.export_plotly_fig(calc_plot_dist_desercion(), "des_dist"),
            engine.export_plotly_fig(calc_plot_trend_desercion(), "des_trend")
        ]
        
        data_ctx["sections"].append({
            "title": "Permanencia y Deserción (SPADIES)",
            "intro": "Análisis de la deserción anual promedio. Esta métrica es crítica para entender la eficiencia interna de los programas y la retención estudiantil.",
            "kpis": [
                ("Tasa Deserción", calc_kpi_desercion_promedio())
            ],
            "plots": desercion_plots
        })

        # SECCIÓN 5: PRUEBA SABER PRO
        p.set(14, message="Procesando sección:", detail="Prueba SABER PRO")
        saber_plots = [
            engine.export_plotly_fig(calc_plot_saber_trend(), "saber_trend"),
            engine.export_plotly_fig(calc_plot_saber_dist(), "saber_dist"),
            # Evolución de conteos
            engine.export_plotly_fig(calc_plot_saber_count_sexo(), "saber_count_sexo"),
            engine.export_plotly_fig(calc_plot_saber_count_edad(), "saber_count_edad"),
            # Detalle por dimensiones
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_punt_global", "sexo"), "saber_global_sexo"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_punt_global", "grupo_edad"), "saber_global_edad"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "sexo"), "saber_razona_sexo"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "grupo_edad"), "saber_razona_edad"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "sexo"), "saber_lectura_sexo"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "grupo_edad"), "saber_lectura_edad"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "sexo"), "saber_ciuda_sexo"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "grupo_edad"), "saber_ciuda_edad"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "sexo"), "saber_ingles_sexo"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "grupo_edad"), "saber_ingles_edad"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "sexo"), "saber_escrita_sexo"),
            engine.export_plotly_fig(calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "grupo_edad"), "saber_escrita_edad")
        ]
        
        data_ctx["sections"].append({
            "title": "Excelencia Académica (Prueba SABER PRO)",
            "intro": "Resultados de las pruebas de Estado que evalúan las competencias genéricas de los estudiantes de último año. El puntaje global es un indicador de la calidad educativa.",
            "kpis": [
                ("Puntaje Global Promedio", calc_saber_score("pro_gen_punt_global")),
                ("Razonamiento Cuantitativo", calc_saber_score("pro_gen_mod_razona_cuantitat_punt")),
                ("Lectura Crítica", calc_saber_score("pro_gen_mod_lectura_critica_punt")),
                ("Competencias Ciudadanas", calc_saber_score("pro_gen_mod_competen_ciudada_punt")),
                ("Inglés", calc_saber_score("pro_gen_mod_ingles_punt")),
                ("Comunicación Escrita", calc_saber_score("pro_gen_mod_comuni_escrita_punt"))
            ],
            "plots": saber_plots
        })

        # SECCIÓN 6: SOCIO-DEMOGRAFÍA
        p.set(14.5, message="Procesando sección:", detail="Socio-demografía")
        demo_plots = [
            engine.export_plotly_fig(calc_plot_saber_categorical("sexo", "Sexo"), "demo_sexo"),
            engine.export_plotly_fig(calc_plot_saber_categorical("grupo_edad", "Edad"), "demo_edad"),
            engine.export_plotly_fig(calc_plot_saber_categorical("pro_gen_estu_horassemanatrabaja", "Trabajo"), "demo_trabajo"),
            engine.export_plotly_fig(calc_plot_saber_categorical("pro_gen_fami_estratovivienda", "Estrato"), "demo_estrato"),
            # Evolución temporal
            engine.export_plotly_fig(calc_plot_saber_categorical_trend("sexo", "Sexo"), "demo_sexo_trend"),
            engine.export_plotly_fig(calc_plot_saber_categorical_trend("grupo_edad", "Edad"), "demo_edad_trend"),
            engine.export_plotly_fig(calc_plot_saber_categorical_trend("pro_gen_estu_horassemanatrabaja", "Trabajo"), "demo_trabajo_trend"),
            engine.export_plotly_fig(calc_plot_saber_categorical_trend("pro_gen_fami_estratovivienda", "Estrato"), "demo_estrato_trend")
        ]
        
        data_ctx["sections"].append({
            "title": "Perfil Socio-demográfico de los Evaluados",
            "intro": "Caracterización demográfica y socioeconómica de los estudiantes que presentaron la prueba en el último año. Incluye la distribución por sexo, grupo de edad, carga laboral y estrato de vivienda.",
            "kpis": [
                ("Total de Evaluados", calc_total_evaluados_saber()),
                ("Programas Académicos", calc_total_programas_saber())
            ],
            "plots": demo_plots
        })
        return data_ctx

    def wrap_kpi(val):
        return ui.HTML(f"<div style='font-size: 32px; font-weight: bold; color: #31497e;'>{val}</div>")

    @render.ui
    def prev_kpi_instituciones(): return wrap_kpi(calc_total_instituciones())
    @render.ui
    def prev_kpi_programas(): return wrap_kpi(calc_total_programas())

    @render.ui
    def prev_kpi_pcurso(): return wrap_kpi(calc_total_primer_curso())
    @render.ui
    def prev_kpi_matriculados(): return wrap_kpi(calc_total_matriculados())
    @render.ui
    def prev_kpi_graduados(): return wrap_kpi(calc_total_graduados())
    @render.ui
    def prev_kpi_emp(): return wrap_kpi(calc_kpi_empleabilidad())
    @render.ui
    def prev_kpi_dep_grad(): return wrap_kpi(calc_kpi_dependientes_graduados())
    @render.ui
    def prev_kpi_dep_cot(): return wrap_kpi(calc_kpi_cotizantes_dependientes())
    @render.ui
    def prev_kpi_ret(): return wrap_kpi(calc_kpi_retencion())
    @render.ui
    def prev_kpi_ratio(): return wrap_kpi(calc_kpi_ratio())
    @render.ui
    def prev_kpi_sal(): return wrap_kpi(calc_kpi_salario_promedio_total())
    @render.ui
    def prev_kpi_sal_f(): return wrap_kpi(calc_kpi_salario_promedio_fem())
    @render.ui
    def prev_kpi_sal_m(): return wrap_kpi(calc_kpi_salario_promedio_masc())
    @render.ui
    def prev_kpi_des(): return wrap_kpi(calc_kpi_desercion_promedio())
    @render.ui
    def prev_kpi_saber(): return wrap_kpi(calc_saber_score("pro_gen_punt_global"))
    @render.ui
    def prev_kpi_saber_razona(): return wrap_kpi(calc_saber_score("pro_gen_mod_razona_cuantitat_punt"))
    @render.ui
    def prev_kpi_saber_lectura(): return wrap_kpi(calc_saber_score("pro_gen_mod_lectura_critica_punt"))
    @render.ui
    def prev_kpi_saber_ciuda(): return wrap_kpi(calc_saber_score("pro_gen_mod_competen_ciudada_punt"))
    @render.ui
    def prev_kpi_saber_ingles(): return wrap_kpi(calc_saber_score("pro_gen_mod_ingles_punt"))
    @render.ui
    def prev_kpi_saber_escrita(): return wrap_kpi(calc_saber_score("pro_gen_mod_comuni_escrita_punt"))
    @render.ui
    def prev_kpi_evaluados(): return wrap_kpi(format_num_es(calc_total_evaluados_saber()))
    @render.ui
    def prev_kpi_progs_saber(): return wrap_kpi(format_num_es(calc_total_programas_saber()))

    @render.ui
    def prev_pcurso_total(): return ui.HTML(pio.to_html(calc_plot_primer_curso_total(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_matricula_total(): return ui.HTML(pio.to_html(calc_plot_matriculados_total(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_graduados_total(): return ui.HTML(pio.to_html(calc_plot_graduados_total(), full_html=False, include_plotlyjs="cdn"))

    def __get_sexo_counts(df_source, col_name, max_anno):
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return 0, 0
        df_sexo_raw = df_source.filter(
            pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") == max_anno)
        ).group_by("sexo").agg(pl.col(col_name).sum()).to_pandas()
        nb = 0
        tr = 0
        for i, row in df_sexo_raw.iterrows():
            if str(row["sexo"]) == "NO BINARIO": nb = int(row[col_name]) if row[col_name] else 0
            if str(row["sexo"]) == "TRANS": tr = int(row[col_name]) if row[col_name] else 0
        return nb, tr

    def dynamic_caption_sexo(df_source, col_name):
        max_anno = df_source["anno"].max()
        nb, tr = __get_sexo_counts(df_source, col_name, max_anno)
        return ui.HTML(f"Fuente: SNIES<br>Elaboración propia<br>Para el último año se reportan {nb} No binarios y {tr} trans, pero no se muestran en la gráfica.")

    @render.ui
    def prev_caption_pcurso(): return dynamic_caption_sexo(df_pcurso, "primer_curso_sum")
    @render.ui
    def prev_caption_matricula(): return dynamic_caption_sexo(df_matricula, "matricula_sum")
    @render.ui
    def prev_caption_graduados(): return dynamic_caption_sexo(df_graduados, "graduados_sum")

    def __filter_gender_fig(fig):
        new_data = [trace for trace in fig.data if str(trace.name).upper() in ['FEMENINO', 'MASCULINO']]
        fig.data = tuple(new_data)
        return fig

    @render.ui
    def prev_pcurso_sexo(): return ui.HTML(pio.to_html(__filter_gender_fig(calc_plot_primer_curso()), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_matricula_sexo(): return ui.HTML(pio.to_html(__filter_gender_fig(calc_plot_matriculados()), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_graduados_sexo(): return ui.HTML(pio.to_html(__filter_gender_fig(calc_plot_graduados()), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_emp_total(): return ui.HTML(pio.to_html(calc_plot_empleabilidad_total(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dep_total(): return ui.HTML(pio.to_html(calc_plot_dependientes_total(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_emp_sexo(): return ui.HTML(pio.to_html(calc_plot_empleabilidad_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dep_sexo(): return ui.HTML(pio.to_html(calc_plot_dependientes_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_emp(): return ui.HTML(pio.to_html(calc_plot_dist_empleabilidad(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_dep(): return ui.HTML(pio.to_html(calc_plot_dist_dependientes(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_emp_sexo(): return ui.HTML(pio.to_html(calc_plot_dist_empleabilidad_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_dep_sexo(): return ui.HTML(pio.to_html(calc_plot_dist_dependientes_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_mobility(): return ui.HTML(pio.to_html(calc_plot_mobility_matrix(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_mobility_tables():
        import pandas as pd
        mob_df_pd, col_o, col_d, label_ejes = get_ole_mobility_df()
        if mob_df_pd.empty:
            return pd.DataFrame(), pd.DataFrame(), label_ejes
        
        # En mob_df_pd la columna YA se llama 'cotizantes' por el alias en get_ole_mobility_df
        # Origen
        df_o = mob_df_pd.groupby(col_o)["cotizantes"].sum().reset_index()
        df_o.columns = [label_ejes, "Cotizantes"]
        df_o = df_o.sort_values("Cotizantes", ascending=False)
        total_o = df_o["Cotizantes"].sum()
        if total_o > 0:
            df_o["Porcentaje"] = (df_o["Cotizantes"] / total_o * 100).round(1).astype(str) + "%"
        
        # Destino
        df_d = mob_df_pd.groupby(col_d)["cotizantes"].sum().reset_index()
        df_d.columns = [label_ejes, "Cotizantes"]
        df_d = df_d.sort_values("Cotizantes", ascending=False)
        total_d = df_d["Cotizantes"].sum()
        if total_d > 0:
            df_d["Porcentaje"] = (df_d["Cotizantes"] / total_d * 100).round(1).astype(str) + "%"
        
        return df_o, df_d, label_ejes

    @render.data_frame
    def prev_ole_top_origen():
        df_o, _, _ = calc_mobility_tables()
        return render.DataGrid(df_o, filters=True, width="100%", selection_mode="none")

    @render.data_frame
    def prev_ole_top_destino():
        _, df_d, _ = calc_mobility_tables()
        return render.DataGrid(df_d, filters=True, width="100%", selection_mode="none")
    @render.ui
    def prev_sal_dist_total(): return ui.HTML(pio.to_html(calc_plot_salario_dist_total(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_sal_dist_sexo(): return ui.HTML(pio.to_html(calc_plot_salario_dist_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_sal_evol_total(): return ui.HTML(pio.to_html(calc_plot_salario_evolucion_total(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_des_dist(): return ui.HTML(pio.to_html(calc_plot_dist_desercion(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_des_trend(): return ui.HTML(pio.to_html(calc_plot_trend_desercion(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend(): return ui.HTML(pio.to_html(calc_plot_saber_trend(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_dist(): return ui.HTML(pio.to_html(calc_plot_saber_dist(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_count_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_count_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_count_edad(): return ui.HTML(pio.to_html(calc_plot_saber_count_edad(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("sexo", "Sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_edad(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("grupo_edad", "Edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_trabajo(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("pro_gen_estu_horassemanatrabaja", "Trabajo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_estrato(): return ui.HTML(pio.to_html(calc_plot_saber_categorical("pro_gen_fami_estratovivienda", "Estrato"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_sexo_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("sexo", "Sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_edad_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("grupo_edad", "Edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_emp(): return ui.HTML(pio.to_html(calc_plot_dist_empleabilidad(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_dep(): return ui.HTML(pio.to_html(calc_plot_dist_dependientes(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_emp_sexo(): return ui.HTML(pio.to_html(calc_plot_dist_empleabilidad_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_dist_dep_sexo(): return ui.HTML(pio.to_html(calc_plot_dist_dependientes_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_trend_dep(): return ui.HTML(pio.to_html(calc_plot_dependientes_trend(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_trend_ret(): return ui.HTML(pio.to_html(calc_plot_retencion_trend(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_ole_trend_ratio(): return ui.HTML(pio.to_html(calc_plot_ratio_trend(), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def prev_sal_evol_sexo(): return ui.HTML(pio.to_html(calc_plot_salario_evolucion_sexo(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_sal_evol_constante(): return ui.HTML(pio.to_html(calc_plot_salario_evolucion_total_constante(), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_sal_evol_sexo_constante(): return ui.HTML(pio.to_html(calc_plot_salario_evolucion_sexo_constante(), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def prev_saber_trend_global_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_punt_global", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_global_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_punt_global", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_razona_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_razona_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_lectura_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_lectura_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_ciuda_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_ciuda_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_ingles_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_ingles_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_escrita_sexo(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "sexo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_saber_trend_escrita_edad(): return ui.HTML(pio.to_html(calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "grupo_edad"), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def prev_demo_trabajo_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("pro_gen_estu_horassemanatrabaja", "Trabajo"), full_html=False, include_plotlyjs="cdn"))
    @render.ui
    def prev_demo_estrato_trend(): return ui.HTML(pio.to_html(calc_plot_saber_categorical_trend("pro_gen_fami_estratovivienda", "Estrato"), full_html=False, include_plotlyjs="cdn"))
    @reactive.calc
    def calc_comp_desercion_metric():
        import pandas as pd
        attr = comp_profile_attr()
        if not attr: 
            return pd.DataFrame(), pd.DataFrame()
            
        # 1. SERIE BASE
        df_base_filtered = df_desercion.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        df_base_pd = df_base_filtered.select([pl.col("anno"), pl.col("desercion_anual_mean").alias("valor_base")]).sort("anno").to_pandas()
        
        # 2. SERIE COMPARABLE
        comp_codigos = comparable_snies_codigos()
        if len(comp_codigos) == 0:
            return df_base_pd, pd.DataFrame()
            
        df_comp_filtered = df_desercion.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))
        
        df_comp_agg = df_comp_filtered.group_by("anno").agg([
            pl.col("desercion_anual_mean").mean().alias("valor_comp_mean"),
            pl.col("desercion_anual_mean").std().alias("valor_comp_std"),
            pl.col("desercion_anual_mean").count().alias("n_programas")
        ]).sort("anno")
        
        df_comp_pd = df_comp_agg.to_pandas()
        df_comp_pd["valor_comp_std"] = df_comp_pd["valor_comp_std"].fillna(0)
        
        return df_base_pd, df_comp_pd

    @reactive.calc
    def calc_plot_comp_desercion_trend():
        df_base, df_comp = calc_comp_desercion_metric()
        return build_comp_plot_ole(df_base, df_comp, "Deserción Anual")

    @render.ui
    def plot_comp_desercion_trend():
        return ui.HTML(pio.to_html(calc_plot_comp_desercion_trend(), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def comp_dist_desercion_header():
        attr = comp_profile_attr()
        yr = max_anno_desercion
        if not attr: return ui.HTML(f"Distribución Total de la <b style='color: #31497e;'>Deserción</b> ({yr})")
        return ui.HTML(f"Distribución vs Universo <b style='color: #31497e;'>{attr['nivel_de_formacion']}</b> ({yr})")

    @reactive.calc
    def calc_plot_comp_dist_desercion():
        import pandas as pd
        import plotly.graph_objects as go
        attr = comp_profile_attr()
        if not attr: return go.Figure()
        
        comp_codigos = comparable_snies_codigos()
        if len(comp_codigos) == 0: return go.Figure()
        
        max_yr = max_anno_desercion
        df_latest = df_desercion.filter(pl.col("anno") == max_yr)
        
        # Universo Base (Mismo Nivel de Formación)
        df_snies_nivel = df_snies.filter((pl.col("nivel_de_formacion") == attr["nivel_de_formacion"]) & (pl.col("estado_programa") == "ACTIVO"))
        codigos_nivel = df_snies_nivel["codigo_snies_del_programa"].unique()
        df_universo = df_latest.filter(pl.col("codigo_snies_del_programa").is_in(codigos_nivel))
        
        # Grupo Comparable
        df_grupo = df_latest.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))
        
        # Programa Base
        df_base = df_latest.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        tasa_base = df_base["desercion_anual_mean"][0] if len(df_base) > 0 else None
        
        # -- Plotly --
        fig = go.Figure()
        
        if len(df_universo) > 0:
            fig.add_trace(go.Histogram(
                x=df_universo["desercion_anual_mean"].to_pandas(), histnorm='percent', 
                name="Mismo Nivel de Formación",
                marker_color="#ced4da", opacity=0.8,
                marker_line_width=1, marker_line_color="white",
                xbins=dict(start=0.0, end=1.0, size=0.02)
            ))
            
        if len(df_grupo) > 0:
            fig.add_trace(go.Histogram(
                x=df_grupo["desercion_anual_mean"].to_pandas(), histnorm='percent', 
                name="Grupo Comparable",
                marker_color="#674f95", opacity=0.8,
                marker_line_width=1, marker_line_color="white",
                xbins=dict(start=0.0, end=1.0, size=0.02)
            ))
            
        fig.update_layout(
            barmode='overlay', plot_bgcolor='white', paper_bgcolor='white',
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Tasa de Deserción", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="Participación de Programas (%)", gridcolor='#EEEEEE'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        if tasa_base is not None:
            fig.add_vline(x=tasa_base, line_dash="dash", line_color="#00B4D8", line_width=3,
                          annotation_text=f"Prog. Base ({tasa_base:.1%})", annotation_position="top right",
                          annotation_font_color="#00B4D8")
                          
        return fig

    @render.ui
    def plot_comp_dist_desercion():
        return ui.HTML(pio.to_html(calc_plot_comp_dist_desercion(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_comp_kpi_base_desercion():
        attr = comp_profile_attr()
        if not attr: return "Sin dato"
        max_yr = max_anno_desercion
        df = df_desercion.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("anno") == max_yr))
        val = df["desercion_anual_mean"][0] if len(df) > 0 else None
        return format_pct_es(val) if val is not None else "Sin dato"

    @render.ui
    def comp_kpi_base_desercion():
        return ui.HTML(f"<div style='font-size: 44px; font-weight: bold; color: #31497e;'>{calc_comp_kpi_base_desercion()}</div>")

    @reactive.calc
    def calc_comp_kpi_desercion():
        comp_codigos = comparable_snies_codigos()
        if len(comp_codigos) == 0: return None, None
        max_yr = max_anno_desercion
        df = df_desercion.filter((pl.col("codigo_snies_del_programa").is_in(comp_codigos)) & (pl.col("anno") == max_yr))
        if len(df) == 0: return None, None
        return df["desercion_anual_mean"].mean(), df["desercion_anual_mean"].std()
        
    @reactive.calc
    def calc_comp_mobility_tables_data():
        """Calcula los datos de movilidad (Origen/Destino) para el programa base de Tendencia Comparada"""
        import pandas as pd
        attr = comp_profile_attr()
        if not attr: return None, None, "Departamento"
        
        snies_base = attr["codigo"]
        max_anno_corte = df_ole_m0["anno_corte"].max()
        
        # Determinar si usamos Depto o Mpio según filtros globales
        f_vals = isolated_filters()
        mpio_filtro = f_vals.get("municipio", [])
        if is_filtered(mpio_filtro):
            col_orig = "municipio_origen"
            col_dest = "municipio_destino"
            label = "Municipio"
        else:
            col_orig = "departamento_origen"
            col_dest = "departamento_destino"
            label = "Departamento"

        df_base = df_ole_m0.filter(
            (pl.col("codigo_snies_del_programa") == snies_base) & 
            (pl.col("anno_corte") == max_anno_corte)
        )
        
        if len(df_base) == 0: return None, None, label

        def agg_mobility(col):
            # Limpiar valores anómalos
            df_agg = df_base.with_columns(
                pl.col(col).cast(pl.Utf8).replace({"1": "SIN INFORMACIÓN", "1.0": "SIN INFORMACIÓN"})
            ).group_by(col).agg(
                pl.col("graduados_que_cotizan").sum().alias("cotizantes")
            ).filter(pl.col("cotizantes") > 0).sort("cotizantes", descending=True).to_pandas()
            
            total = df_agg["cotizantes"].sum()
            df_agg["porcentaje"] = (df_agg["cotizantes"] / total) if total > 0 else 0
            return df_agg

        return agg_mobility(col_orig), agg_mobility(col_dest), label

    @render.data_frame
    def comp_table_mobility_origen():
        import pandas as pd
        df_orig, _, label = calc_comp_mobility_tables_data()
        if df_orig is None or df_orig.empty: return pd.DataFrame()
        
        df_fmt = df_orig.copy()
        df_fmt["cotizantes"] = df_fmt["cotizantes"].apply(lambda x: f"{int(x):,}")
        df_fmt["porcentaje"] = df_fmt["porcentaje"].apply(lambda x: f"{x:.1%}")
        df_fmt.columns = [f"{label} de Origen", "Graduados Cotizantes", "%"]
        return render.DataGrid(df_fmt, filters=False, selection_mode="none")

    @render.data_frame
    def comp_table_mobility_destino():
        import pandas as pd
        _, df_dest, label = calc_comp_mobility_tables_data()
        if df_dest is None or df_dest.empty: return pd.DataFrame()
        
        df_fmt = df_dest.copy()
        df_fmt["cotizantes"] = df_fmt["cotizantes"].apply(lambda x: f"{int(x):,}")
        df_fmt["porcentaje"] = df_fmt["porcentaje"].apply(lambda x: f"{x:.1%}")
        df_fmt.columns = [f"{label} de Destino", "Graduados Cotizantes", "%"]
        return render.DataGrid(df_fmt, filters=False, selection_mode="none")

    @render.ui
    def comp_kpi_desercion():
        mean, std = calc_comp_kpi_desercion()
        if mean is None: 
            return ui.HTML("<div style='font-size: 44px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        
        formatted = format_pct_es(mean)
        if std is not None:
            formatted += f" <span style='font-size: 18px; color: gray;'>±{format_pct_es(std)} (SD)</span>"
        return ui.HTML(f"<div style='font-size: 44px; font-weight: bold; color: #674f95;'>{formatted}</div>")

    # --- TENDENCIA COMPARADA PRUEBA SABER ---
    @reactive.calc
    def _df_saber_filt_base():
        attr = comp_profile_attr()
        if not attr: return None
        return df_saber.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])

    @reactive.calc
    def _df_saber_filt_comp():
        comp_codigos = comparable_snies_codigos()
        if len(comp_codigos) == 0: return None
        return df_saber.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))

    def get_comp_saber_series(score_col):
        import pandas as pd
        df_base = _df_saber_filt_base()
        df_comp = _df_saber_filt_comp()
        
        # Base
        if df_base is None or len(df_base) == 0:
            pd_base = pd.DataFrame()
        else:
            pd_base = df_base.group_by("anno").agg([
                pl.col(score_col).mean().alias("valor_base")
            ]).drop_nulls().sort("anno").to_pandas()
            
        # Comp
        if df_comp is None or len(df_comp) == 0:
            pd_comp = pd.DataFrame()
        else:
            pd_comp = df_comp.group_by("anno").agg([
                pl.col(score_col).mean().alias("valor_comp_mean"),
                pl.col(score_col).std().alias("valor_comp_std"),
                pl.col("codigo_snies_del_programa").n_unique().alias("n_programas")
            ]).drop_nulls().sort("anno").to_pandas()
            
        return pd_base, pd_comp

    def get_saber_base_html(col):
        df_b, _ = get_comp_saber_series(col)
        if df_b.empty: return ui.HTML(f"<div style='font-size: 38px; font-weight: bold; color: #31497e;'>Sin dato</div>")
        val = df_b['valor_base'].iloc[-1]
        return ui.HTML(f"<div style='font-size: 38px; font-weight: bold; color: #31497e;'>{format_num_es(val, decimals=1)}</div>")

    def get_saber_comp_html(col):
        _, df_c = get_comp_saber_series(col)
        if df_c.empty: return ui.HTML(f"<div style='font-size: 38px; font-weight: bold; color: #674f95;'>Sin dato</div>")
        mean = df_c['valor_comp_mean'].iloc[-1]
        std = df_c['valor_comp_std'].iloc[-1]
        return ui.HTML(f"<div style='font-size: 38px; font-weight: bold; color: #674f95;'>{format_num_es(mean, decimals=1)} <span style='font-size: 18px; color: gray;'>±{format_num_es(std, decimals=1)} (SD)</span></div>")

    @render.ui
    def comp_kpi_base_saber_global(): return get_saber_base_html('pro_gen_punt_global')
    @render.ui
    def comp_kpi_saber_global(): return get_saber_comp_html('pro_gen_punt_global')
    
    @render.ui
    def comp_kpi_base_saber_razona(): return get_saber_base_html('pro_gen_mod_razona_cuantitat_punt')
    @render.ui
    def comp_kpi_saber_razona(): return get_saber_comp_html('pro_gen_mod_razona_cuantitat_punt')

    @render.ui
    def comp_kpi_base_saber_lectura(): return get_saber_base_html('pro_gen_mod_lectura_critica_punt')
    @render.ui
    def comp_kpi_saber_lectura(): return get_saber_comp_html('pro_gen_mod_lectura_critica_punt')

    @render.ui
    def comp_kpi_base_saber_ciuda(): return get_saber_base_html('pro_gen_mod_competen_ciudada_punt')
    @render.ui
    def comp_kpi_saber_ciuda(): return get_saber_comp_html('pro_gen_mod_competen_ciudada_punt')

    @render.ui
    def comp_kpi_base_saber_ingles(): return get_saber_base_html('pro_gen_mod_ingles_punt')
    @render.ui
    def comp_kpi_saber_ingles(): return get_saber_comp_html('pro_gen_mod_ingles_punt')

    @render.ui
    def comp_kpi_base_saber_escrita(): return get_saber_base_html('pro_gen_mod_comuni_escrita_punt')
    @render.ui
    def comp_kpi_saber_escrita(): return get_saber_comp_html('pro_gen_mod_comuni_escrita_punt')

    def build_comp_plot_saber(df_base_pd, df_comp_pd, title):
        import plotly.graph_objects as go
        fig = go.Figure()
        
        if df_comp_pd.empty and df_base_pd.empty:
            return fig
            
        color_base = "#31497e"
        color_comp = "#674f95"
        color_band = "rgba(103, 79, 149, 0.15)"
        
        if not df_comp_pd.empty:
            y_lower = (df_comp_pd["valor_comp_mean"] - df_comp_pd["valor_comp_std"]).clip(lower=0) 
            y_upper = (df_comp_pd["valor_comp_mean"] + df_comp_pd["valor_comp_std"])
            
            fig.add_trace(go.Scatter(x=df_comp_pd["anno"], y=y_lower, marker=dict(color="#444"), line=dict(width=0), mode='lines', showlegend=False, hoverinfo='skip'))
            fig.add_trace(go.Scatter(x=df_comp_pd["anno"], y=y_upper, marker=dict(color="#444"), line=dict(width=0), mode='lines', fillcolor=color_band, fill='tonexty', name='Dispersión (Media ± 1 SD)', hoverinfo='skip'))
            
            fig.add_trace(go.Scatter(
                x=df_comp_pd["anno"],
                y=df_comp_pd["valor_comp_mean"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:,.0f}',
                name='Media Comparable',
                line=dict(color=color_comp, width=3, dash='dash'),
                marker=dict(size=8, color="white", line=dict(width=2, color=color_comp)),
                hovertemplate="Año: %{x}<br>Media: %{y:.1f}<br>N: %{customdata} prog.<extra></extra>",
                customdata=df_comp_pd["n_programas"]
            ))

        if not df_base_pd.empty:
            attr = comp_profile_attr()
            prog_name = f"SNIES {attr['codigo']}" if attr else "Prog. Base"
            fig.add_trace(go.Scatter(
                x=df_base_pd["anno"],
                y=df_base_pd["valor_base"],
                mode='lines+markers+text', textposition='top center', textfont=dict(size=12, color='black'), texttemplate='%{y:,.0f}',
                name=prog_name,
                line=dict(color=color_base, width=4),
                marker=dict(size=9, color="white", line=dict(width=2.5, color=color_base)),
                hovertemplate="Año: %{x}<br>Puntaje: %{y:.1f}<extra></extra>"
            ))
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Puntaje", gridcolor='#EEEEEE'),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    @reactive.calc
    def calc_fig_comp_saber_trend_global():
        df_base, df_comp = get_comp_saber_series('pro_gen_punt_global')
        return build_comp_plot_saber(df_base, df_comp, "Puntaje Global")

    @render.ui
    def plot_comp_saber_trend_global():
        return ui.HTML(pio.to_html(calc_fig_comp_saber_trend_global(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_saber_trend_razona():
        df_base, df_comp = get_comp_saber_series('pro_gen_mod_razona_cuantitat_punt')
        return build_comp_plot_saber(df_base, df_comp, "Razonamiento Cuantitativo")

    @render.ui
    def plot_comp_saber_trend_razona():
        return ui.HTML(pio.to_html(calc_fig_comp_saber_trend_razona(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_saber_trend_lectura():
        df_base, df_comp = get_comp_saber_series('pro_gen_mod_lectura_critica_punt')
        return build_comp_plot_saber(df_base, df_comp, "Lectura Crítica")

    @render.ui
    def plot_comp_saber_trend_lectura():
        return ui.HTML(pio.to_html(calc_fig_comp_saber_trend_lectura(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_saber_trend_ciuda():
        df_base, df_comp = get_comp_saber_series('pro_gen_mod_competen_ciudada_punt')
        return build_comp_plot_saber(df_base, df_comp, "Competencias Ciudadanas")

    @render.ui
    def plot_comp_saber_trend_ciuda():
        return ui.HTML(pio.to_html(calc_fig_comp_saber_trend_ciuda(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_saber_trend_ingles():
        df_base, df_comp = get_comp_saber_series('pro_gen_mod_ingles_punt')
        return build_comp_plot_saber(df_base, df_comp, "Inglés")

    @render.ui
    def plot_comp_saber_trend_ingles():
        return ui.HTML(pio.to_html(calc_fig_comp_saber_trend_ingles(), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_fig_comp_saber_trend_escrita():
        df_base, df_comp = get_comp_saber_series('pro_gen_mod_comuni_escrita_punt')
        return build_comp_plot_saber(df_base, df_comp, "Comunicación Escrita")

    @render.ui
    def plot_comp_saber_trend_escrita():
        return ui.HTML(pio.to_html(calc_fig_comp_saber_trend_escrita(), full_html=False, include_plotlyjs="cdn"))

    def build_comp_saber_categorical(column_id):
        import pandas as pd
        import plotly.express as px
        import plotly.graph_objects as go
        
        attr = comp_profile_attr()
        if not attr: return go.Figure()
        
        max_yr = df_saber["anno"].max()
        df_base_raw = df_saber.filter((pl.col("codigo_snies_del_programa") == attr["codigo"]) & (pl.col("anno") == max_yr))
        
        comp_codigos = comparable_snies_codigos()
        df_comp_raw = df_saber.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos) & (pl.col("anno") == max_yr)) if comp_codigos else pl.DataFrame()
        
        def process_cat(df_raw, grupo_name):
            if len(df_raw) == 0: return pl.DataFrame()
            d_clean = df_raw.with_columns(
                pl.col(column_id).cast(pl.Utf8).fill_null("Sin Registro")
            ).with_columns(
                pl.when((pl.col(column_id) == "") | (pl.col(column_id) == "-1")).then(pl.lit("Sin Registro"))
                .otherwise(pl.col(column_id)).alias(column_id)
            )
            agg = d_clean.group_by(column_id).len()
            
            total = agg["len"].sum()
            return agg.with_columns(
                (pl.col("len") / total).alias("porcentaje"),
                pl.lit(grupo_name).alias("grupo")
            )

        df_b = process_cat(df_base_raw, "Programa Seleccionado")
        df_c = process_cat(df_comp_raw, "Grupo Comparable")
        
        if len(df_b) == 0 and len(df_c) == 0:
            return ui.HTML(pio.to_html(go.Figure(), full_html=False, include_plotlyjs="cdn"))
            
        dfs_to_concat = []
        if len(df_b) > 0: dfs_to_concat.append(df_b)
        if len(df_c) > 0: dfs_to_concat.append(df_c)
        df_comb = pl.concat(dfs_to_concat)
        
        df_pd = df_comb.to_pandas()
        
        if df_pd.empty: return ui.HTML(pio.to_html(go.Figure(), full_html=False, include_plotlyjs="cdn"))
        
        fig = px.bar(df_pd, x="porcentaje", y=column_id, color="grupo", orientation='h', barmode='group', 
                     color_discrete_map={"Programa Seleccionado": "#31497e", "Grupo Comparable": "#674f95"}, 
                     text_auto='.1%')
        fig.update_traces(marker_line_width=1.5, marker_line_color="white", textfont_size=12, textangle=0, textposition="auto", cliponaxis=False)
        fig.update_layout(
            legend_title_text="",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=40, t=20, b=20),
            xaxis=dict(title="Participación de Evaluados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=12)),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )

        # Aplicar orden específico si aplica
        if column_id == "pro_gen_estu_horassemanatrabaja":
            order = ["No trabaja", "Menos de 10 horas", "Entre 11 y 20 horas", "Entre 21 y 30 horas", "Más de 30 horas", "Sin Registro"]
            # En Plotly, el orden del array se lee de abajo hacia arriba en gráficas horizontales
            fig.update_layout(yaxis={'categoryorder':'array', 'categoryarray': order[::-1]})
        elif column_id == "pro_gen_fami_estratovivienda":
            existing_cats = df_pd[column_id].unique()
            order = []
            for e in ["1", "2", "3", "4", "5", "6"]:
                for cat in existing_cats:
                    if e in str(cat):
                        order.append(cat)
                        break
            if "Sin Registro" in existing_cats: order.append("Sin Registro")
            fig.update_layout(yaxis={'categoryorder':'array', 'categoryarray': order[::-1]})
        else:
            fig.update_layout(yaxis={'categoryorder':'total ascending'})

        return fig

    @render.ui
    def plot_comp_saber_demo_sexo():
        return ui.HTML(pio.to_html(build_comp_saber_categorical("sexo"), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def plot_comp_saber_demo_edad():
        return ui.HTML(pio.to_html(build_comp_saber_categorical("grupo_edad"), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def plot_comp_saber_demo_trabajo():
        return ui.HTML(pio.to_html(build_comp_saber_categorical("pro_gen_estu_horassemanatrabaja"), full_html=False, include_plotlyjs="cdn"))

    @render.ui
    def plot_comp_saber_demo_estrato():
        return ui.HTML(pio.to_html(build_comp_saber_categorical("pro_gen_fami_estratovivienda"), full_html=False, include_plotlyjs="cdn"))

    @reactive.calc
    def calc_all_report_data():
        """Recopila toda la información necesaria para el reporte premium en formato JSON."""
        # Helpers para evitar SilentExceptions de reactivos no inicializados
        def get_input_safe(id, default=None):
            try:
                val = getattr(input, id)()
                return val if val is not None else default
            except:
                return default

        def safe_call(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except:
                return None

        def safe_fig(fn, w=None, h=None):
            try:
                fig = fn()
                # Ajustes para reporte estático
                fig.update_layout(
                    margin=dict(l=15, r=15, t=30, b=45),
                    font=dict(family="Montserrat", size=9),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.12,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=8)
                    )
                )
                for trace in fig.data:
                    if hasattr(trace, 'marker') and isinstance(trace.marker, dict):
                        if 'size' in trace.marker: trace.marker['size'] = 6
                
                # Usar dimensiones del layout de la figura si no se especifican explícitamente
                final_w = w if w is not None else (fig.layout.width or 800)
                final_h = h if h is not None else (fig.layout.height or 450)
                return fig_to_base64(fig, width=final_w, height=final_h)
            except:
                return fig_to_base64(go.Figure(), width=(w or 800), height=(h or 450))

        def safe_kpi(fn, default="Sin dato"):
            try:
                val = fn()
                return str(val) if val is not None else default
            except:
                return default

        # Lógica inteligente de Alcance del Análisis
        # Lógica inteligente de Alcance del Análisis
        snies_sel = get_input_safe("snies_label", [])
        inst_sel = get_input_safe("institucion_label", []) or get_input_safe("nombre_institucion", [])
        dept_sel = get_input_safe("departamento", [])

        filtros_mapeo = {
            "departamento": "Departamento",
            "municipio": "Municipio",
            "modalidad": "Modalidad",
            "nivel_de_formacion": "Nivel de Formación",
            "area_de_conocimiento": "Área de Conocimiento",
            "nucleo_basico_del_conocimiento": "Núcleo Básico",
            "sector": "Sector",
            "estado_programa": "Estado"
        }
        
        filtros_activos = []
        
        # 1. Programa / SNIES
        if snies_sel:
            snies_clean = [str(s).split(" - ")[0] for s in snies_sel]
            v_snies = ", ".join(snies_clean).replace("'", "").replace("(", "").replace(")", "")
            filtros_activos.append(f"<b>Programa (SNIES)</b>: {v_snies}")
        
        # 2. Institución
        if inst_sel:
            inst_clean = [str(i).split(" - ")[-1] for i in inst_sel]
            v_inst = ", ".join(inst_clean).replace("'", "").replace("(", "").replace(")", "")
            filtros_activos.append(f"<b>Institución</b>: {v_inst}")
            
        # 2.5 Palabra Clave
        keyword_val = get_input_safe("keyword_programa", "")
        if keyword_val and str(keyword_val).strip():
            filtros_activos.append(f"<b>Palabra Clave</b>: {str(keyword_val).strip()}")
            
        # 3. Resto de filtros dinámicos
        for fid, label in filtros_mapeo.items():
            val = get_input_safe(fid, [])
            if val and len(val) > 0:
                if isinstance(val, (list, tuple)):
                    v_str = ", ".join(map(str, val))
                else:
                    v_str = str(val)
                
                # Limpiar caracteres de formato de Python
                v_str = v_str.replace("('", "").replace("',)", "").replace("'", "").replace("(", "").replace(")", "")
                
                if v_str.upper() not in ["", "TODAS", "TODOS"]:
                    filtros_activos.append(f"<b>{label}</b>: {v_str}")

        if filtros_activos:
            desc_alcance = "Este informe contempla un análisis estratégico bajo la siguiente segmentación: " + "; ".join(filtros_activos) + "."
        else:
            desc_alcance = "Este informe es un análisis a nivel nacional del ecosistema de educación superior basado en los consolidados generales."

        dept_name = ", ".join(dept_sel) if dept_sel else "Nacional"
        inst_name = ", ".join(inst_sel) if inst_sel else "Todas"
        filtros_desc = desc_alcance
        
        # SNIES Data
        snies_y = str(int(df_matricula["anno"].max()))
        ole_y = str(int(df_ole_m0["anno_corte"].max()))
        spadies_y = str(int(df_desercion["anno"].max() if "anno" in df_desercion.columns else 2023))
        icfes_y = str(int(df_saber["anno"].max() if "anno" in df_saber.columns else 2024))

        # Cargar Logo en Base64
        logo_b64 = ""
        logo_path = app_dir / "logo_symbiotic.svg"
        if logo_path.exists():
            try:
                with open(logo_path, "rb") as f:
                    logo_b64 = base64.b64encode(f.read()).decode("utf-8")
                    logo_b64 = f"data:image/svg+xml;base64,{logo_b64}"
            except: pass

        # Helper for Saber KPIs
        def get_saber_val(col):
            try:
                df_b, _ = get_comp_saber_series(col)
                if df_b is None or df_b.empty: return 0.0
                return float(df_b['valor_base'].iloc[-1])
            except:
                return 0.0

        # Preparación de datos de movilidad para las tablas de la PÁGINA 4
        try:
            mob_df_pd, col_o, col_d, _ = get_ole_mobility_df()
            mob_origin = []
            mob_destination = []
            if not mob_df_pd.empty:
                total_m = mob_df_pd["cotizantes"].sum()
                if total_m > 0:
                    # Todos los registros de Origen
                    top_o = mob_df_pd.groupby(col_o)["cotizantes"].sum().sort_values(ascending=False)
                    mob_origin = [{"name": str(n), "value": f"{v:,.0f}".replace(",", "."), "percentage": f"{(v/total_m):.1%}"} for n, v in top_o.items()]
                    # Todos los registros de Destino
                    top_d = mob_df_pd.groupby(col_d)["cotizantes"].sum().sort_values(ascending=False)
                    mob_destination = [{"name": str(n), "value": f"{v:,.0f}".replace(",", "."), "percentage": f"{(v/total_m):.1%}"} for n, v in top_d.items()]
        except Exception as e:
            print(f"Error calculando movilidad para PDF: {e}")
            mob_origin = []
            mob_destination = []

        report_kpis = {
            "instituciones": safe_kpi(calc_total_instituciones),
            "programas": safe_kpi(calc_total_programas),
            "pcurso": safe_kpi(calc_total_primer_curso),
            "matriculados": safe_kpi(calc_total_matriculados),
            "graduados": safe_kpi(calc_total_graduados),
            "vinculacion": safe_kpi(calc_kpi_empleabilidad),
            "salario": safe_kpi(calc_kpi_salario_promedio_total), 
            "retencion": safe_kpi(calc_kpi_retencion),
            "dep_grad": safe_kpi(calc_kpi_dependientes_graduados),
            "dep_cot": safe_kpi(calc_kpi_cotizantes_dependientes),
            "ratio_migratorio": safe_kpi(calc_kpi_ratio),
            "saber_global": safe_kpi(lambda: calc_saber_score('pro_gen_punt_global')),
            "sal_promedio": safe_kpi(calc_kpi_salario_promedio_total),
            "sal_femenino": safe_kpi(calc_kpi_salario_promedio_fem),
            "sal_masculino": safe_kpi(calc_kpi_salario_promedio_masc),
            "des_rate": safe_kpi(calc_kpi_desercion_promedio, default="0,0%"),
            "s_global": safe_kpi(lambda: calc_saber_score('pro_gen_punt_global')),
            "s_razona": safe_kpi(lambda: calc_saber_score('pro_gen_mod_razona_cuantitat_punt')),
            "s_lectura": safe_kpi(lambda: calc_saber_score('pro_gen_mod_lectura_critica_punt')),
            "s_ciuda": safe_kpi(lambda: calc_saber_score('pro_gen_mod_competen_ciudada_punt')),
            "s_ingles": safe_kpi(lambda: calc_saber_score('pro_gen_mod_ingles_punt')),
            "s_escrita": safe_kpi(lambda: calc_saber_score('pro_gen_mod_comuni_escrita_punt')),
            "evaluados": safe_kpi(calc_total_evaluados_saber),
            "progs_saber": safe_kpi(calc_total_programas_saber)
        }
        # --- GENERACIÓN DE GRÁFICOS EN PARALELO ---
        # Definimos las tareas de renderizado (Plot configurations)
        plot_tasks = [
            # SNIES
            ("snies", 0, "t1", "Tendencia de Estudiantes de Primer Curso", calc_plot_primer_curso_total, "Evolución histórica del ingreso."),
            ("snies", 1, "t2", "Tendencia de Estudiantes Matriculados", calc_plot_matriculados_total, "Población estudiantil activa en el sistema."),
            ("snies", 2, "t3", "Tendencia de Estudiantes Graduados", calc_plot_graduados_total, "Egresados titulados por cohorte de salida."),
            ("snies", 3, "s1", "Tendencia por Sexo de Estudiantes de Primer Curso", calc_plot_primer_curso, "Participación por género en el ingreso."),
            ("snies", 4, "s2", "Tendencia por Sexo de Estudiantes Matriculados", calc_plot_matriculados, "Permanencia desagregada por sexo."),
            ("snies", 5, "s3", "Tendencia por Sexo de Estudiantes Graduados", calc_plot_graduados, "Perfil de egreso por género."),
            # OLE
            ("ole", 0, "o1", "Tendencia de Empleabilidad", calc_plot_empleabilidad_total, "Evolución de la empleabilidad"),
            ("ole", 1, "o2", "Tendencia de la Relación Dependientes sobre Graduados", calc_plot_dependientes_total, "Relación de empleados dependientes"),
            ("ole", 2, "o3", "Empleabilidad por Sexo", calc_plot_empleabilidad_sexo, "Empleabilidad por sexo"),
            ("ole", 3, "o4", "Relación Dependientes sobre Graduados por Sexo", calc_plot_dependientes_sexo, "Relación de dependientes por sexo"),
            ("ole", 4, "o5", "Distribución de Empleabilidad (2022)", calc_plot_dist_empleabilidad, "Distribución de empleabilidad (2022)"),
            ("ole", 5, "o6", "Distribución de la Relación Dependientes sobre Graduados (2022)", calc_plot_dist_dependientes, "Distribución de la relación de dependientes"),
            ("ole", 6, "o7", "Distribución de Empleabilidad por Sexo (2022)", calc_plot_dist_empleabilidad_sexo, "Distribución de empleabilidad por sexo"),
            ("ole", 7, "o8", "Distribución de la Relación Dependientes sobre Graduados por Sexo (2022)", calc_plot_dist_dependientes_sexo, "Distribución de dependientes por sexo"),
            ("ole", 8, "o10", "Evolución de la Tasa de Retención Local", calc_plot_retencion_trend, ""),
            ("ole", 9, "o11", "Evolución del Ratio Salen / Entran", calc_plot_ratio_trend, ""),
            ("ole", 10, "o12", "Evolución de Dependientes sobre Cotizantes", calc_plot_dependientes_trend, ""),
            # Salarios
            ("salarios", 0, "v1", "Distribución por Rango Salarial (2022)", calc_plot_salario_dist_total, "Distribución por rango salarial"),
            ("salarios", 1, "v2", "Distribución Salarial por Sexo (2022)", calc_plot_salario_dist_sexo, "Distribución salarial por sexo"),
            ("salarios", 2, "v3", "Evolución del Salario Promedio Estimado (Pesos corrientes)", calc_plot_salario_evolucion_total, "Evolución del salario promedio"),
            ("salarios", 3, "v4", "Evolución Salarial por Sexo (Pesos corrientes)", calc_plot_salario_evolucion_sexo, "Evolución salarial por sexo"),
            ("salarios", 4, "v5", "Evolución de Salario Promedio Estimado (Pesos constantes - SMMLV 2026)", calc_plot_salario_evolucion_total_constante, "Salario promedio en pesos constantes"),
            ("salarios", 5, "v6", "Evolución Salarial por Sexo (Pesos constantes - SMMLV 2026)", calc_plot_salario_evolucion_sexo_constante, "Evolución salarial por sexo en pesos constantes"),
            # Spadies
            ("spadies", 0, "d1", "Distribución de la Tasa de Deserción (Último Año)", calc_plot_dist_desercion, "Variabilidad de la deserción académica."),
            ("spadies", 1, "d2", "Tendencia Histórica de Deserción Anual", calc_plot_trend_desercion, "evolución de tasa de deserción anual"),
            # Saber Pro
            ("saber", 0, "sb1", "Evolución de Competencias Genéricas (Promedio por Programa)", (lambda: calc_plot_saber_trend()), "Evolución histórica de competencias genéricas."),
            ("saber", 1, "sb2", "Distribución del Puntaje Global (2024)", (lambda: calc_plot_saber_dist()), "Distribución del puntaje global."),
            ("saber", 2, "sb3", "Cantidad de Evaluados por Sexo", (lambda: calc_plot_saber_count_sexo()), "Cantidad de evaluados por sexo."),
            ("saber", 3, "sb4", "Cantidad de Evaluados por Edad", (lambda: calc_plot_saber_count_edad()), "Cantidad de evaluados por edad."),
            ("saber", 4, "sb5", "Puntaje Global por Sexo", (lambda: calc_plot_saber_trend_dim("pro_gen_punt_global", "sexo")), "Evolución del puntaje global desagregado por sexo."),
            ("saber", 5, "sb6", "Puntaje Global por Edad", (lambda: calc_plot_saber_trend_dim("pro_gen_punt_global", "grupo_edad")), "Evolución del puntaje global desagregado por grupo de edad."),
            ("saber", 6, "sb7", "Razonamiento Cuantitativo por Sexo", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "sexo")), "Tendencia de Razonamiento Cuantitativo por sexo."),
            ("saber", 7, "sb8", "Razonamiento Cuantitativo por Edad", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "grupo_edad")), "Tendencia de Razonamiento Cuantitativo por grupo de edad."),
            ("saber", 8, "sb9", "Lectura Crítica por Sexo", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "sexo")), "Tendencia de Lectura Crítica desagregada por sexo."),
            ("saber", 9, "sb10", "Lectura Crítica por Edad", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "grupo_edad")), "Tendencia de Lectura Crítica por grupo de edad."),
            ("saber", 10, "sb11", "Competencias Ciudadanas por Sexo", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "sexo")), "Tendencia de Competencias Ciudadanas por sexo."),
            ("saber", 11, "sb12", "Competencias Ciudadanas por Edad", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "grupo_edad")), "Tendencia de Competencias Ciudadanas por grupo de edad."),
            ("saber", 12, "sb13", "Inglés por Sexo", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "sexo")), "Tendencia del módulo de Inglés desagregada por sexo."),
            ("saber", 13, "sb14", "Inglés por Edad", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "grupo_edad")), "Tendencia del módulo de Inglés por grupo de edad."),
            ("saber", 14, "sb15", "Comunicación Escrita por Sexo", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "sexo")), "Tendencia de Comunicación Escrita desagregada por sexo."),
            ("saber", 15, "sb16", "Comunicación Escrita por Edad", (lambda: calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "grupo_edad")), "Tendencia de Comunicación Escrita por grupo de edad."),
            # Demo
            ("demo", 0, "pr1", "Distribución por Sexo (2024)", (lambda: calc_plot_saber_categorical("sexo", "Sexo")), "Composición por género."),
            ("demo", 1, "pr2", "Distribución por Grupo de Edad (2024)", (lambda: calc_plot_saber_categorical("grupo_edad", "Edad")), "Composición por rangos de edad."),
            ("demo", 2, "pr3", "Distribución por Horas de Trabajo (2024)", (lambda: calc_plot_saber_categorical("pro_gen_estu_horassemanatrabaja", "Trabajo")), "Distribución por horas de trabajo"),
            ("demo", 3, "pr4", "Distribución por Estrato Social (2024)", (lambda: calc_plot_saber_categorical("pro_gen_fami_estratovivienda", "Estrato")), "Distribución por estrato social"),
            ("demo", 4, "pr5", "Evolución de Participación por Sexo", (lambda: calc_plot_saber_categorical_trend("sexo", "Sexo")), "Evolución histórica por sexo."),
            ("demo", 5, "pr6", "Evolución de Participación por Grupo de Edad", (lambda: calc_plot_saber_categorical_trend("grupo_edad", "Edad")), "Evolución histórica por grupo de edad."),
            ("demo", 6, "pr7", "Evolución de Participación por Horas de Trabajo", (lambda: calc_plot_saber_categorical_trend("pro_gen_estu_horassemanatrabaja", "Trabajo")), "Evolución de carga laboral."),
            ("demo", 7, "pr8", "Evolución de Participación por Estrato Social", (lambda: calc_plot_saber_categorical_trend("pro_gen_fami_estratovivienda", "Estrato")), "Evolución del perfil económico.")
        ]

        def exec_task(t):
            section, idx, pid, title, fn, caption = t
            return (section, idx, pid, title, safe_fig(fn), caption)

        # Renderizado SECUENCIAL: Kaleido usa un subproceso Chromium único
        # que no puede compartirse entre hilos en Windows. La ejecución en paralelo
        # causa "Couldn't close or kill browser subprocess". Se mantiene secuencial.
        print(f"INFO: Iniciando renderizado secuencial de {len(plot_tasks)} gráficos...")
        start_render = datetime.datetime.now()
        results = [exec_task(t) for t in plot_tasks]
        print(f"INFO: Renderizado completado en {(datetime.datetime.now() - start_render).total_seconds():.2f}s")

        # Inicializar estructura de report_data
        report_data = {
            "metadata": {
                "title": "Informe de Mercado de Educación Superior",
                "subtitle": "ANÁLISIS DEL SECTOR COLOMBIANO" if (not dept_sel and (not filtros_activos or (len(filtros_activos) == 1 and "Estado" in filtros_activos[0]))) else f"DEPARTAMENTO: {dept_name.upper()}",
                "date": (lambda dt: ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"][dt.month-1] + " " + str(dt.year))(datetime.datetime.now()),
                "year_start": 2018,
                "year_end": int(snies_y),
                "logo_data": logo_b64,
                "source_years": {"snies": snies_y, "ole": ole_y, "spadies": spadies_y, "icfes": icfes_y},
                "scope": {"type": "filters", "description": filtros_desc, "details": {"Departamento": dept_name, "Institución": inst_name}}
            },
            "kpis": report_kpis,
            "snies": {"technical_note": "se realizan conteos anualizados para permitir la comparacion con programas academicos de diferente periodicidad , el numero de matriculados corresponde al promedio anual.", "plots": [None]*6},
            "ole": {"technical_note": "Los datos se calculan para el siguiente año después de graduado. Los graduados se dividen en cotizantes y no cotizantes. Los cotizantes se dividen en dependientes e independientes. para calcular el Ratio se consideran exclusivamente graduados con registro de cotización dependiente. El ratio solo tiene interpretación a nivel regional (al seleccionar un departamento) y muestra la proporción entre los dependientes que salen del departamento sobre los que ingresan al departamento. La retención local corresponde a dependientes que laboran en el departamento de grado sobre el total de dependientes graduados en dicho departamento", "plots": [None]*12, "mobility": {"origin": mob_origin, "destination": mob_destination}},
            "salarios": {"technical_note": "", "plots": [None]*6},
            "spadies": {"technical_note": "Para programas de Posgrado sin registro en SPADIES, se estima un proxy de deserción empleando la fórmula: (Matriculados_{t-1} + Primer Curso_{t} - Graduados_{t} - Matriculados_{t}) / (Matriculados_{t-1} + Primer Curso_{t}).se utiliza como indicador la desercion anual que permite comparar programas acadmicos con diferente periodicidad", "plots": [None]*2},
            "saber": {"technical_note": "Fuente: ICFES, SABER PRO.", "plots": [None]*16},
            "demo": {"technical_note": "Caracterización socio-demográfica.", "plots": [None]*8, "title": "Perfil Socio-demográfico de los Evaluados"},
            "top_ranking_ingresos": []
        }

        # Población de resultados
        for section, idx, pid, title, b64, caption in results:
            report_data[section]["plots"][idx] = {"id": pid, "title": title, "b64": b64, "caption": caption}

        # Dump de datos para tabla dinámica
        report_data["raw_data_dump"] = [
            {
                "Año": int(yr),
                "Matriculados": (lambda val: int(val) if val is not None else 0)(_df_m_agg.filter((pl.col("codigo_snies_del_programa").is_in(filtered_snies()["codigo_snies_del_programa"].unique())) & (pl.col("anno") == yr))["matriculados"].sum()),
                "Graduados": (lambda val: int(val) if val is not None else 0)(_df_g_agg.filter((pl.col("codigo_snies_del_programa").is_in(filtered_snies()["codigo_snies_del_programa"].unique())) & (pl.col("anno") == yr))["graduados"].sum()),
                "Primer_Curso": (lambda val: int(val) if val is not None else 0)(_df_p_agg.filter((pl.col("codigo_snies_del_programa").is_in(filtered_snies()["codigo_snies_del_programa"].unique())) & (pl.col("anno") == yr))["primer_curso"].sum()),
                "Tasa_Vinculacion": (lambda df: f"{df['tasa'].iloc[0]:.1%}" if not df.empty else "N/A")(create_ole_trend_df("graduados_que_cotizan", "graduados", ["anno_corte"]).pipe(lambda df: df[df['anno_corte'] == yr] if 'anno_corte' in df.columns else df)),
                "Salario_Promedio": (lambda df: f"${df['salario_pesos'].iloc[0]:,.0f}" if not df.empty else "N/A")(get_salary_trend_data().pipe(lambda df: df[df['label'] == 'TOTAL'] if 'label' in df.columns else df).pipe(lambda df: df[df['anno_corte'] == yr] if 'anno_corte' in df.columns else df)),
                "Tasa_Desercion": (lambda df: f"{df['desercion_anual_mean'].iloc[0]:.1%}" if not df.empty else "N/A")(filtered_desercion().group_by("anno").agg(pl.col("desercion_anual_mean").mean()).to_pandas().pipe(lambda df: df[df['anno'] == yr] if 'anno' in df.columns else df))
            } for yr in sorted(_df_m_agg["anno"].unique())
        ]
        
        # Ranking de Competitividad por Ingresos (Top 20 para el informe)
        try:
            df_top_ing = calc_top_ingresos_table()
            if not df_top_ing.empty:
                top_df = df_top_ing.head(19)
                for i, (_, row) in enumerate(top_df.iterrows()):
                    # Formatear SNIES y Créditos para quitar el .0 si son float
                    try:
                        snies_val = str(int(float(row["SNIES"])))
                    except:
                        snies_val = str(row["SNIES"])
                    
                    try:
                        cred_val = str(int(float(row["Créditos"]))) if pd.notnull(row["Créditos"]) else "-"
                    except:
                        cred_val = str(row["Créditos"])

                    # Obtener ingresos (la columna tiene el año en el nombre, usamos iloc o búsqueda dinámica)
                    ing_val = "0"
                    for col in top_df.columns:
                        if "Ingresos" in col:
                            try:
                                ing_val = str(int(float(row[col])))
                            except:
                                ing_val = str(row[col])
                            break

                    report_data["top_ranking_ingresos"].append({
                        "pos": str(i + 1),
                        "snies": snies_val,
                        "inst": str(row["Institución"]),
                        "prog": str(row["Programa Académico"]),
                        "sector": str(row["Sector"]),
                        "mod": str(row["Modalidad"]),
                        "cred": cred_val,
                        "val": str(row["Valor Matrícula"]),
                        "ing": ing_val
                    })
        except Exception as e:
            print(f"Error generando ranking ingresos PDF: {e}")
        
        return report_data
        
        print(f"DEBUG SABER KPIS: s_global={report_data['kpis']['s_global']}, s_razona={report_data['kpis']['s_razona']}")
        print(f"DEBUG DEMO PLOT STATUS: PR1={type(report_data['demo']['plots'][0]['b64'])}")
        
        return report_data

    @reactive.effect
    @reactive.event(input.btn_preview_report)
    def handle_preview():
        """Genera la vista previa del Informe de Mercado con loader premium animado."""
        _NID = "mercado_report_loader"

        _LOADER_HTML = """
        <style>
          @keyframes mkt-spin  { to { transform: rotate(360deg); } }
          @keyframes mkt-pulse { 0%,100%{transform:scale(1);opacity:.7} 50%{transform:scale(1.25);opacity:1} }
          @keyframes mkt-slide {
            0%   { left:-55%; width:55%; }
            50%  { left:55%;  width:55%; }
            100% { left:110%; width:55%; }
          }
          @keyframes mkt-msg {
            0%           { opacity:0; transform:translateY(6px);  }
            4%           { opacity:1; transform:translateY(0);    }
            16%          { opacity:1; transform:translateY(0);    }
            20%, 100%    { opacity:0; transform:translateY(-6px); }
          }
          @keyframes mkt-bounce {
            0%,100% { transform:translateY(0);   }
            50%     { transform:translateY(-5px); }
          }
          #mercado_report_loader { min-width:390px !important; }
        </style>
        <div style="font-family:'Nunito',sans-serif;padding:6px 2px;">

          <!-- Spinner + Título -->
          <div style="display:flex;align-items:center;gap:16px;margin-bottom:18px;">
            <div style="position:relative;width:52px;height:52px;flex-shrink:0;">
              <div style="position:absolute;inset:0;border-radius:50%;border:4px solid #f1f5f9;
                          border-top:4px solid #DF6C5B;border-right:4px solid #385A64;
                          animation:mkt-spin 0.85s linear infinite;"></div>
              <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
                          width:16px;height:16px;background:#DF6C5B;border-radius:50%;
                          animation:mkt-pulse 1.6s ease-in-out infinite;"></div>
            </div>
            <div>
              <div style="font-size:1.05rem;font-weight:900;color:#385A64;letter-spacing:-0.4px;line-height:1.2;">
                Generando Informe de Mercado
              </div>
              <div style="font-size:0.68rem;color:#94a3b8;margin-top:4px;font-weight:700;
                          text-transform:uppercase;letter-spacing:0.8px;">
                SymbioTIC &nbsp;·&nbsp; Análisis Estratégico Regional
              </div>
            </div>
          </div>

          <!-- Barra deslizante indeterminada -->
          <div style="background:#e2e8f0;border-radius:12px;height:12px;overflow:hidden;
                      position:relative;margin-bottom:16px;">
            <div style="position:absolute;top:0;height:100%;
                        background:linear-gradient(90deg,#DF6C5B,#527A86,#385A64);
                        border-radius:12px;
                        animation:mkt-slide 1.7s ease-in-out infinite;"></div>
          </div>

          <!-- Mensajes ciclantes cada 3 s -->
          <div style="position:relative;height:22px;overflow:hidden;margin-bottom:14px;">
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:mkt-msg 15s linear infinite;animation-delay:0s;animation-fill-mode:both;">
              ⚙️&nbsp; Calculando KPIs de matrícula, primer curso y graduados...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:mkt-msg 15s linear infinite;animation-delay:3s;animation-fill-mode:both;">
              💼&nbsp; Procesando datos de empleabilidad y salarios del OLE...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:mkt-msg 15s linear infinite;animation-delay:6s;animation-fill-mode:both;">
              📊&nbsp; Renderizando gráficas del mercado regional en alta resolución...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:mkt-msg 15s linear infinite;animation-delay:9s;animation-fill-mode:both;">
              🏛️&nbsp; Analizando resultados Saber PRO y movilidad de egresados...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:mkt-msg 15s linear infinite;animation-delay:12s;animation-fill-mode:both;">
              📋&nbsp; Ensamblando el informe estratégico del mercado educativo...
            </div>
          </div>

          <!-- Puntos rebotando -->
          <div style="display:flex;gap:6px;justify-content:center;">
            <div style="width:8px;height:8px;border-radius:50%;background:#DF6C5B;
                        animation:mkt-bounce 1.2s ease-in-out infinite;animation-delay:0s;"></div>
            <div style="width:8px;height:8px;border-radius:50%;background:#527A86;
                        animation:mkt-bounce 1.2s ease-in-out infinite;animation-delay:0.2s;"></div>
            <div style="width:8px;height:8px;border-radius:50%;background:#385A64;
                        animation:mkt-bounce 1.2s ease-in-out infinite;animation-delay:0.4s;"></div>
          </div>
        </div>
        """

        ui.notification_show(
            ui.HTML(_LOADER_HTML),
            type="default",
            duration=None,
            id=_NID
        )

        try:
            report_data = calc_all_report_data()
            import json
            report_json = json.dumps(report_data)

            template_path = app_dir / "web_report_demo" / "viewer.html"
            if not template_path.exists():
                template_path = app_dir / "viewer.html"

            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            # Inyectar el JSON y el logo en Base64 (WYSIWYG 100% en memoria)
            injected_content = template_content.replace(
                '<script src="data_ANTIOQUIA.js"></script>',
                f'<script>window.__REPORT_DATA__ = {report_json};</script>'
            )

            # Reemplazar el logo por el Base64 inyectado (si existe en el JSON)
            logo_b64 = report_data.get("metadata", {}).get("logo_data", "")
            if logo_b64:
                injected_content = injected_content.replace('src="../logo_symbiotic.svg"', f'src="{logo_b64}"')

            ui.notification_remove(_NID)

            ui.modal_show(ui.modal(
                ui.tags.div(
                    ui.tags.iframe(
                        srcdoc=injected_content,
                        style="width: 100%; height: 85vh; border: none; background: white;"
                    ),
                    style="padding: 0; margin: -1rem; background: #f8fafc; overflow: hidden; border-radius: 4px;"
                ),
                title="Previsualización del Informe Estratégico (Vista Gráfica)",
                size="xl",
                easy_close=True,
                footer=ui.div(
                    ui.modal_button("Cerrar"),
                    style="display: flex; gap: 10px; justify-content: flex-end; width: 100%;"
                )
            ))
        except Exception as e:
            import traceback
            ui.notification_remove(_NID)
            error_details = traceback.format_exc()
            # Si el error es una SilentException de Shiny, significa que falta algún dato reactivo
            if "SilentException" in str(type(e)):
                ui.notification_show("Por favor, selecciona los filtros necesarios (Departamento/Institución) antes de generar el reporte.", type="warning", duration=15)
            else:
                ui.notification_show(f"Error en Vista Previa: {str(e)}", type="error", duration=15)
            print(f"DEBUG: Error Detallado en Vista Previa:\n{error_details}")

    @render.download(filename=lambda: f"Informe_Mercado_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    def download_pdf():
        with ui.Progress(min=0, max=1) as p:
            try:
                p.set(message="Generando Reporte...", detail="Esto puede tardar unos segundos...")
                report_data = calc_all_report_data()
                import json
                report_json = json.dumps(report_data)
                
                template_path = app_dir / "web_report_demo" / "viewer.html"
                if not template_path.exists():
                    template_path = app_dir / "viewer.html"
                
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()
                
                html_content = template_content.replace(
                    '<script src="data_ANTIOQUIA.js"></script>',
                    f'<script>window.__REPORT_DATA__ = {report_json};</script>'
                )
                
                # Inyectar logo Base64 para WeasyPrint (independencia total de archivos)
                report_obj = json.loads(report_json)
                logo_data = report_obj.get("metadata", {}).get("logo_data", "")
                if logo_data:
                    html_content = html_content.replace('src="../logo_symbiotic.svg"', f'src="{logo_data}"')
                
                pdf_buffer = io.BytesIO()
                # Pasar app_dir como base_url para que WeasyPrint encuentre el logo en dashboard/logo_symbiotic.svg
                HTML(string=html_content, base_url=str(app_dir)).write_pdf(pdf_buffer)
                pdf_buffer.seek(0)
                yield pdf_buffer.read()
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                if "SilentException" in str(type(e)):
                    ui.notification_show("No se pudo generar el PDF: faltan filtros por seleccionar.", type="warning", duration=15)
                else:
                    ui.notification_show(f"Error generando PDF: {str(e)}", type="error", duration=15)
                print(f"DEBUG: Error Detallado en PDF:\n{error_details}")
                yield b"Error"




    # ==========================================
    # LOGICA DE DESCARGA PDF TENDENCIA COMPARADA
    # ==========================================
    @reactive.calc
    def calc_all_comp_report_data():
        """Genera el JSON para el PDF de Tendencia Comparada con esquema compatible"""
        def safe_fig_comp(fn, w=800, h=450):
            try:
                fig = fn()
                # Aplicamos estilo reporte premium optimizado para exportación estática
                fig.update_layout(
                    plot_bgcolor='white', paper_bgcolor='white',
                    margin=dict(l=15, r=15, t=30, b=45), # Aumentamos margen inferior para la leyenda
                    font=dict(family="Montserrat", size=9),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.12,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=8)
                    )
                )
                # Reducir tamaño de marcadores en las trazas si existen
                for trace in fig.data:
                    if hasattr(trace, 'marker') and isinstance(trace.marker, dict):
                        if 'size' in trace.marker:
                            trace.marker['size'] = 6
                    if hasattr(trace, 'textfont') and isinstance(trace.textfont, dict):
                        trace.textfont['size'] = 8

                return fig_to_base64(fig, width=w, height=h)
            except Exception as e:
                print("Error safe_fig_comp:", e)
                return fig_to_base64(go.Figure(), width=w, height=h)

        attr = comp_profile_attr()
        if not attr: return None
        
        # Filtros e Identificación
        filtros_labels = {
            "departamento_oferta": "Mismo Departamento",
            "nivel_de_formacion": "Mismo Nivel de Formación",
            "modalidad": "Misma Modalidad",
            "sector": "Mismo Sector",
            "area_de_conocimiento": "Misma Área",
            "nucleo_basico_del_conocimiento": "Mismo NBC"
        }
        filtros_activos = [filtros_labels.get(f, f) for f in input.comp_criterios()]

        def safe_val(val, format_fn=str, default="Sin dato"):
            if val is None: return default
            try:
                # Evitar errores de numpy en tipos no numéricos
                if isinstance(val, (float, np.floating)) and np.isnan(val): return default
                return format_fn(val)
            except:
                try: return str(val) if val is not None else default
                except: return default

        # 1. SNIES (Series Temporales)
        df_b_pcurso, df_c_pcurso = calc_comp_metric(df_pcurso, "primer_curso_sum")
        df_b_matr, df_c_matr = calc_comp_metric(df_matricula, "matricula_sum")
        df_b_grad, df_c_grad = calc_comp_metric(df_graduados, "graduados_sum")

        # 2. OLE, SPADIES, SABER
        df_b_sal, df_c_sal = calc_comp_salario_evolucion()
        df_b_emp, df_c_emp = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
        df_b_des, df_c_des = calc_comp_desercion_metric()
        df_b_sb, df_c_sb = get_comp_saber_series('pro_gen_punt_global')
        
        # SNIES Costos y Créditos
        snies_list = comparable_snies_codigos()
        df_snies_base = df_snies.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        df_c_c = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("costo_matricula_estud_nuevos") > 0))
        df_c_cr = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list) & (pl.col("numero_creditos") > 0))

        def get_last_base(df): return df["valor_base"].iloc[-1] if df is not None and not df.empty else None
        def get_last_comp_sum(df): return df["valor_comp_sum"].iloc[-1] if df is not None and not df.empty else None
        def get_last_median(df): return df["valor_comp_median"].iloc[-1] if df is not None and not df.empty else None
        def get_last_mean(df): return df["valor_comp_mean"].iloc[-1] if df is not None and not df.empty else None
        def get_last_mad(df): return df["valor_comp_mad"].iloc[-1] if df is not None and not df.empty and "valor_comp_mad" in df.columns else None
        def get_last_std(df): return df["valor_comp_std"].iloc[-1] if df is not None and not df.empty and "valor_comp_std" in df.columns else None

        # Cálculos ad-hoc para SD en Costos y Créditos
        costo_vals = df_c_c["costo_matricula_estud_nuevos"].to_list() if len(df_c_c) > 0 else []
        costo_sd = np.std(costo_vals) if costo_vals else None
        costo_median = np.median(costo_vals) if costo_vals else None
        costo_mad = np.median([abs(x - costo_median) for x in costo_vals]) * 1.4826 if costo_vals else None

        creditos_vals = df_c_cr["numero_creditos"].to_list() if len(df_c_cr) > 0 else []
        creditos_sd = np.std(creditos_vals) if creditos_vals else None

        # 3. Tablas de Movilidad (Origen/Destino)
        df_orig, df_dest, label_mob = calc_comp_mobility_tables_data()
        
        def table_to_list(df, col_name):
            if df is None or df.empty: return []
            # Tomar los top 10 para no saturar el PDF si hay muchos
            df_top = df.head(10)
            return [
                {
                    "lugar": str(row[col_name]),
                    "cantidad": format_num_es(row["cotizantes"]),
                    "porcentaje": f"{row['porcentaje']:.1%}"
                }
                for _, row in df_top.iterrows()
            ]

        mobility_origen_list = table_to_list(df_orig, df_orig.columns[0] if df_orig is not None else "")
        mobility_destino_list = table_to_list(df_dest, df_dest.columns[0] if df_dest is not None else "")

        # 4. COMPETITIVIDAD (Ranking)
        df_ranking = calc_top_ingresos_comp_table().head(20)
        ranking_data = []
        if not df_ranking.empty:
            max_yr_col = [c for c in df_ranking.columns if "Ingresos" in c][0]
            for _, row in df_ranking.iterrows():
                ranking_data.append({
                    "pos": str(row["#"]),
                    "snies": str(int(row["SNIES"])),
                    "inst": str(row["Institución"]),
                    "prog": str(row["Programa Académico"]),
                    "sector": str(row["Sector"]),
                    "mod": str(row["Modalidad"]),
                    "cred": str(row["Créditos"]) if pd.notna(row["Créditos"]) else "-",
                    "val": str(row["Valor Matrícula"]) if row["Valor Matrícula"] else "-",
                    "ing": format_num_es(row[max_yr_col])
                })

        report_data = {
            "metadata": {
                "base_codigo": attr["codigo"],
                "base_nombre": attr["nombre"],
                "base_institucion": attr["institucion"],
                "base_departamento": attr["departamento_oferta"],
                "date": datetime.datetime.now().strftime("%d/%m/%Y"),
                "filtros": filtros_activos,
                "modo_manual": "Activado" if input.switch_modo_manual() else "Desactivado",
                "max_anno_snies":   int(max_anno_snies),
                "max_anno_ole":     int(max_anno_ole),
                "max_anno_spadies": int(max_anno_desercion),
                "max_anno_saber":   int(max_anno_saber)
            },
            "mobility": {
                "label": label_mob,
                "origen": mobility_origen_list,
                "destino": mobility_destino_list
            },
            "top_ranking_comp": ranking_data,
            "kpis": {
                "universo": safe_val(df_c_pcurso["n_programas"].iloc[-1] if not df_c_pcurso.empty else 0, format_num_es),
                "neto_pcurso": safe_val(get_last_comp_sum(df_c_pcurso), format_num_es),
                "neto_matricula": safe_val(get_last_comp_sum(df_c_matr), format_num_es),
                "neto_graduados": safe_val(get_last_comp_sum(df_c_grad), format_num_es),
                
                "base_pcurso": safe_val(get_last_base(df_b_pcurso), format_num_es),
                "comp_pcurso": safe_val(get_last_median(df_c_pcurso), format_num_es),
                "comp_pcurso_mad": safe_val(get_last_mad(df_c_pcurso), format_num_es),

                "base_matricula": safe_val(get_last_base(df_b_matr), format_num_es),
                "comp_matricula": safe_val(get_last_median(df_c_matr), format_num_es),
                "comp_matricula_mad": safe_val(get_last_mad(df_c_matr), format_num_es),

                "base_graduados": safe_val(get_last_base(df_b_grad), format_num_es),
                "comp_graduados": safe_val(get_last_median(df_c_grad), format_num_es),
                "comp_graduados_mad": safe_val(get_last_mad(df_c_grad), format_num_es),
                
                "base_costo": safe_val(df_snies_base["costo_matricula_estud_nuevos"][0] if len(df_snies_base) > 0 else None, lambda x: f"${format_num_es(x)}"),
                "comp_costo_avg": safe_val(df_c_c["costo_matricula_estud_nuevos"].mean() if len(df_c_c) > 0 else None, lambda x: f"${format_num_es(x)}"),
                "comp_costo_avg_sd": safe_val(costo_sd, format_num_es),
                "comp_costo_med": safe_val(df_c_c["costo_matricula_estud_nuevos"].median() if len(df_c_c) > 0 else None, lambda x: f"${format_num_es(x)}"),
                "comp_costo_med_mad": safe_val(costo_mad, format_num_es),

                "base_creditos": safe_val(df_snies_base["numero_creditos"][0] if len(df_snies_base) > 0 else None, format_num_es),
                "comp_creditos": safe_val(df_c_cr["numero_creditos"].mean() if len(df_c_cr) > 0 else None, format_num_es),
                "comp_creditos_sd": safe_val(creditos_sd, lambda x: format_num_es(x, decimals=1)),
                
                "base_emp": safe_val(get_last_base(df_b_emp), format_pct_es),
                "comp_emp": safe_val(get_last_mean(df_c_emp), format_pct_es),
                "comp_emp_sd": safe_val(get_last_std(df_c_emp), format_pct_es),

                "base_sal": safe_val(get_last_base(df_b_sal), lambda x: f"${format_num_es(x)}"),
                "comp_sal": safe_val(get_last_mean(df_c_sal), lambda x: f"${format_num_es(x)}"),
                "comp_sal_sd": safe_val(get_last_std(df_c_sal), lambda x: f"${format_num_es(x)}"),

                "base_des": safe_val(get_last_base(df_b_des), format_pct_es),
                "comp_des": safe_val(get_last_mean(df_c_des), format_pct_es),
                "comp_des_sd": safe_val(get_last_std(df_c_des), format_pct_es),

                
                # Saber PRO — Global
                "base_sb_global":    safe_val(get_last_base(df_b_sb), lambda x: format_num_es(x, decimals=1)),
                "comp_sb_global":    safe_val(get_last_mean(df_c_sb), lambda x: format_num_es(x, decimals=1)),
                "comp_sb_global_sd": safe_val(get_last_std(df_c_sb),  lambda x: format_num_es(x, decimals=1)),
                
                # Saber PRO — Dimensiones individuales
                "base_sb_razona":    safe_val(get_last_base(get_comp_saber_series("pro_gen_mod_razona_cuantitat_punt")[0]),  lambda x: format_num_es(x, decimals=1)),
                "comp_sb_razona":    safe_val(get_last_mean(get_comp_saber_series("pro_gen_mod_razona_cuantitat_punt")[1]),  lambda x: format_num_es(x, decimals=1)),
                "comp_sb_razona_sd": safe_val(get_last_std(get_comp_saber_series("pro_gen_mod_razona_cuantitat_punt")[1]),   lambda x: format_num_es(x, decimals=1)),
                
                "base_sb_lectura":    safe_val(get_last_base(get_comp_saber_series("pro_gen_mod_lectura_critica_punt")[0]),   lambda x: format_num_es(x, decimals=1)),
                "comp_sb_lectura":    safe_val(get_last_mean(get_comp_saber_series("pro_gen_mod_lectura_critica_punt")[1]),   lambda x: format_num_es(x, decimals=1)),
                "comp_sb_lectura_sd": safe_val(get_last_std(get_comp_saber_series("pro_gen_mod_lectura_critica_punt")[1]),    lambda x: format_num_es(x, decimals=1)),
                
                "base_sb_ciuda":    safe_val(get_last_base(get_comp_saber_series("pro_gen_mod_competen_ciudada_punt")[0]),  lambda x: format_num_es(x, decimals=1)),
                "comp_sb_ciuda":    safe_val(get_last_mean(get_comp_saber_series("pro_gen_mod_competen_ciudada_punt")[1]),  lambda x: format_num_es(x, decimals=1)),
                "comp_sb_ciuda_sd": safe_val(get_last_std(get_comp_saber_series("pro_gen_mod_competen_ciudada_punt")[1]),   lambda x: format_num_es(x, decimals=1)),
                
                "base_sb_ingles":    safe_val(get_last_base(get_comp_saber_series("pro_gen_mod_ingles_punt")[0]),            lambda x: format_num_es(x, decimals=1)),
                "comp_sb_ingles":    safe_val(get_last_mean(get_comp_saber_series("pro_gen_mod_ingles_punt")[1]),            lambda x: format_num_es(x, decimals=1)),
                "comp_sb_ingles_sd": safe_val(get_last_std(get_comp_saber_series("pro_gen_mod_ingles_punt")[1]),             lambda x: format_num_es(x, decimals=1)),
                
                "base_sb_escrita":    safe_val(get_last_base(get_comp_saber_series("pro_gen_mod_comuni_escrita_punt")[0]),   lambda x: format_num_es(x, decimals=1)),
                "comp_sb_escrita":    safe_val(get_last_mean(get_comp_saber_series("pro_gen_mod_comuni_escrita_punt")[1]),   lambda x: format_num_es(x, decimals=1)),
                "comp_sb_escrita_sd": safe_val(get_last_std(get_comp_saber_series("pro_gen_mod_comuni_escrita_punt")[1]),    lambda x: format_num_es(x, decimals=1))

            },
            "snies": {
                "technical_note": "La información corresponde a estadísticas descriptivas del grupo de comparación SNIES. La mediana se utiliza como referencia central y las bandas en los gráficos representan la dispersión de los datos. Las métricas se calculan con base en el último período disponible.",
                "plots": [
                    {"id": "t1", "title": "Estudiantes de Primer Curso",   "b64": safe_fig_comp(calc_fig_comp_pcurso),    "caption": "Evolución histórica consolidada de ingresos.",      "source": "Fuente: SNIES\nElaboración propia"},
                    {"id": "t2", "title": "Estudiantes Matriculados",   "b64": safe_fig_comp(calc_fig_comp_matricula), "caption": "Población estudiantil activa en el sistema.",        "source": "Fuente: SNIES\nElaboración propia"},
                    {"id": "t3", "title": "Estudiantes Graduados",      "b64": safe_fig_comp(calc_fig_comp_graduados), "caption": "Egresados titulados por cohorte de salida.",         "source": "Fuente: SNIES\nElaboración propia"}
                ]
            },
            "salarios": {
                "technical_note": "Fuente: SNIES. Costos y créditos reportados por las instituciones.",
                "plots": [
                    {"id": "v1", "title": "Distribución de Costo de Matrícula (Solo Privados)", "b64": safe_fig_comp(calc_fig_comp_dist_costo), "caption": "", "source": "Fuente: SNIES\nElaboración propia"},
                    {"id": "v2", "title": "Distribución de Número de Créditos", "b64": safe_fig_comp(calc_fig_comp_dist_creditos), "caption": "", "source": "Fuente: SNIES\nElaboración propia"}
                ]
            },
            "ole": {
                "technical_note": "Nota Técnica: Los datos se calculan para el siguiente año después de graduado. Los graduados se dividen en cotizantes y no cotizantes. Los cotizantes se dividen en dependientes e independientes. para calcular el Ratio se consideran exclusivamente graduados con registro de cotización dependiente. El ratio solo tiene interpretación a nivel regional (al seleccionar un departamento) y muestra la proporción entre los dependientes que salen del departamento sobre los que ingresan al departamento.",
                "plots": [
                    {"id": "o1", "title": "Tendencia de Empleabilidad",                "b64": safe_fig_comp(calc_fig_comp_ole_empleabilidad),    "caption": "Evolución de la empleabilidad",                        "source": "Fuente: OLE\nElaboración propia"},
                    {"id": "o2", "title": "Evolución de Dependientes sobre Cotizantes",            "b64": safe_fig_comp(calc_fig_comp_ole_dependientes),     "caption": "Proporción de empleados dependientes sobre cotizantes",          "source": "Fuente: OLE\nElaboración propia"},
                    {"id": "o3", "title": "Distribución de Tasa de Empleabilidad (2022)",          "b64": safe_fig_comp(calc_plot_comp_dist_empleabilidad, w=1100, h=620), "caption": "Distribución de empleabilidad (2022)", "source": "Fuente: OLE\nElaboración propia"},
                    {"id": "o4", "title": f"Evolución del Salario Promedio Estimado (Pesos Ctes. {max_anno_ole:.0f})", "b64": safe_fig_comp(calc_fig_comp_salario_evolucion), "caption": "Evolución del salario promedio", "source": "Fuente: OLE\nElaboración propia"},
                    {"id": "o5", "title": "Distribución por Rango Salarial (Últ. Año)", "b64": safe_fig_comp(calc_fig_comp_salario_dist),  "caption": "Distribución por rango salarial",         "source": "Fuente: OLE | Elaboración propia"}
                ]
            },
            "spadies": {
                "technical_note": "Para programas de Posgrado sin registro en SPADIES, se estima un proxy de deserción empleando la fórmula: (Matriculados_{t-1} + Primer Curso_{t} - Graduados_{t} - Matriculados_{t}) / (Matriculados_{t-1} + Primer Curso_{t}). se utiliza como indicador la deserción anual que permite comparar programas académicos con diferente periodicidad.",
                "plots": [
                    {"id": "d1", "title": "Tendencia Histórica de Deserción Anual", "b64": safe_fig_comp(calc_plot_comp_desercion_trend),      "caption": "",     "source": "Fuente: SPADIES\nElaboración propia"},
                    {"id": "d2", "title": "Distribución de la Tasa de Deserción (Último Año)",       "b64": safe_fig_comp(calc_plot_comp_dist_desercion),     "caption": "",   "source": "Fuente: SPADIES\nElaboración propia"}
                ]
            },
            "saber": {
                "technical_note": "Fuente: ICFES. Puntajes normalizados en escala 0-300.",
                "plots": [
                    {"id": "sb1", "title": "Saber PRO Global",       "b64": safe_fig_comp(calc_fig_comp_saber_trend_global),  "caption": "Evolución comparada del puntaje global.",       "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "sb2", "title": "Saber PRO Inglés",       "b64": safe_fig_comp(calc_fig_comp_saber_trend_ingles),  "caption": "Comparativa en competencias de segunda lengua.",  "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "sb3", "title": "Saber PRO Razonamiento", "b64": safe_fig_comp(calc_fig_comp_saber_trend_razona),  "caption": "Razonamiento cuantitativo comparado.",           "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "sb4", "title": "Saber PRO Lectura",      "b64": safe_fig_comp(calc_fig_comp_saber_trend_lectura), "caption": "Lectura crítica y comprensión comparada.",       "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "sb5", "title": "Saber PRO Ciudadanas",   "b64": safe_fig_comp(calc_fig_comp_saber_trend_ciuda),   "caption": "Competencias ciudadanas comparadas.",            "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "sb6", "title": "Saber PRO Escrita",      "b64": safe_fig_comp(calc_fig_comp_saber_trend_escrita), "caption": "Comunicación escrita comparada.",               "source": "Fuente: ICFES - Saber PRO\nElaboración propia"}
                ]
            },
            "demo": {
                "technical_note": "Caracterización sociodemográfica basada en formularios del ICFES.",
                "plots": [
                    {"id": "pr1", "title": "Distribución por Sexo",        "b64": safe_fig_comp(lambda: build_comp_saber_categorical("sexo")),                              "caption": "Composición por género del estudiantado.",    "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "pr2", "title": "Distribución por Grupo de Edad",        "b64": safe_fig_comp(lambda: build_comp_saber_categorical("grupo_edad")),                       "caption": "Rangos de edad prevalentes.",               "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "pr3", "title": "Distribución por Horas de Trabajo",          "b64": safe_fig_comp(lambda: build_comp_saber_categorical("pro_gen_estu_horassemanatrabaja")), "caption": "Distribución por horas de trabajo",            "source": "Fuente: ICFES - Saber PRO\nElaboración propia"},
                    {"id": "pr4", "title": "Distribución por Estrato Social", "b64": safe_fig_comp(lambda: build_comp_saber_categorical("pro_gen_fami_estratovivienda")),     "caption": "Distribución por estrato social","source": "Fuente: ICFES - Saber PRO\nElaboración propia"}
                ]
            },
            "mobility": {
                "label": label_mob,
                "origen": mobility_origen_list,
                "destino": mobility_destino_list
            }
        }
        print(f"DEBUG COMP REPORT - Universo: {report_data['kpis']['universo']}, Base Costo: {report_data['kpis']['base_costo']}")
        return report_data

    @render.download(filename=lambda: "Datos_Cluster_Comparable.xlsx")
    def btn_download_comp_excel():
        import io
        import pandas as pd
        snies_list = comparable_snies_codigos()
        if not snies_list:
            yield b""
            return
            
        output = io.BytesIO()
        
        def clean_tz(df_pd):
            # Convierte columnas datetime con zona horaria a sin zona horaria (naive) para Excel
            for col in df_pd.columns:
                if pd.api.types.is_datetime64_any_dtype(df_pd[col]):
                    if getattr(df_pd[col].dtype, 'tz', None) is not None:
                        df_pd[col] = df_pd[col].dt.tz_localize(None)
            return df_pd

        try:
            with pd.ExcelWriter(output) as writer:
                # 1. SNIES
                try:
                    df1 = df_snies.filter(pl.col("codigo_snies_del_programa").is_in(snies_list)).to_pandas()
                    df1 = clean_tz(df1)
                    df1.to_excel(writer, sheet_name="SNIES", index=False)
                except Exception as e: print("Error SNIES export:", e)
                
                # 2. OLE
                try:
                    df2 = df_ole_m0.filter(pl.col("codigo_snies_del_programa").is_in(snies_list)).to_pandas()
                    df2 = clean_tz(df2)
                    df2.to_excel(writer, sheet_name="OLE", index=False)
                except Exception as e: print("Error OLE export:", e)
                
                # 3. SPADIES (Deserción)
                try:
                    df3 = df_desercion.filter(pl.col("codigo_snies_del_programa").is_in(snies_list)).to_pandas()
                    df3 = clean_tz(df3)
                    df3.to_excel(writer, sheet_name="SPADIES", index=False)
                except Exception as e: print("Error SPADIES export:", e)
                
                # 4. SABER PRO
                try:
                    df4 = df_saber.filter(pl.col("codigo_snies_del_programa").is_in(snies_list)).to_pandas()
                    df4 = clean_tz(df4)
                    df4.to_excel(writer, sheet_name="SABER PRO", index=False)
                except Exception as e: print("Error SABER export:", e)
        except Exception as main_e:
            print("Error general al generar Excel:", main_e)
            
        yield output.getvalue()

    @reactive.effect
    @reactive.event(input.btn_preview_comp)
    def handle_preview_comp():
        """Genera la vista previa del Informe de Tendencia Comparada con loader premium animado."""
        _NID = "comp_report_loader"

        # ─── LOADER ANIMADO ───────────────────────────────────────────────────────
        # El CSS corre en el browser mientras Python computa en el servidor.
        # Los @keyframes se inyectan con <style> dentro de la notificación.
        _LOADER_HTML = """
        <style>
          @keyframes comp-spin  { to { transform: rotate(360deg); } }
          @keyframes comp-pulse { 0%,100%{transform:scale(1);opacity:.7} 50%{transform:scale(1.25);opacity:1} }
          @keyframes comp-slide {
            0%   { left:-55%; width:55%; }
            50%  { left:55%;  width:55%; }
            100% { left:110%; width:55%; }
          }
          @keyframes comp-msg {
            0%           { opacity:0; transform:translateY(6px);  }
            4%           { opacity:1; transform:translateY(0);    }
            16%          { opacity:1; transform:translateY(0);    }
            20%, 100%    { opacity:0; transform:translateY(-6px); }
          }
          @keyframes comp-bounce {
            0%,100% { transform:translateY(0);   }
            50%     { transform:translateY(-5px); }
          }
          #comp_report_loader { min-width:390px !important; }
        </style>
        <div style="font-family:'Nunito',sans-serif;padding:6px 2px;">

          <!-- Spinner + Título -->
          <div style="display:flex;align-items:center;gap:16px;margin-bottom:18px;">
            <div style="position:relative;width:52px;height:52px;flex-shrink:0;">
              <!-- Anillo exterior giratorio -->
              <div style="position:absolute;inset:0;border-radius:50%;border:4px solid #f1f5f9;
                          border-top:4px solid #DF6C5B;border-right:4px solid #674f95;
                          animation:comp-spin 0.85s linear infinite;"></div>
              <!-- Punto central pulsante -->
              <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
                          width:16px;height:16px;background:#385A64;border-radius:50%;
                          animation:comp-pulse 1.6s ease-in-out infinite;"></div>
            </div>
            <div>
              <div style="font-size:1.05rem;font-weight:900;color:#385A64;letter-spacing:-0.4px;line-height:1.2;">
                Generando Informe Comparativo
              </div>
              <div style="font-size:0.68rem;color:#94a3b8;margin-top:4px;font-weight:700;
                          text-transform:uppercase;letter-spacing:0.8px;">
                SymbioTIC &nbsp;·&nbsp; Benchmarking Estratégico
              </div>
            </div>
          </div>

          <!-- Barra de progreso indeterminada deslizante -->
          <div style="background:#e2e8f0;border-radius:12px;height:12px;overflow:hidden;
                      position:relative;margin-bottom:16px;">
            <div style="position:absolute;top:0;height:100%;
                        background:linear-gradient(90deg,#DF6C5B,#9333ea,#385A64);
                        border-radius:12px;
                        animation:comp-slide 1.7s ease-in-out infinite;"></div>
          </div>

          <!-- Mensajes ciclantes cada 3 s (duración total del ciclo: 15 s) -->
          <div style="position:relative;height:22px;overflow:hidden;margin-bottom:14px;">
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:comp-msg 15s linear infinite;animation-delay:0s;animation-fill-mode:both;">
              ⚙️&nbsp; Cargando datos de matrícula y primer curso del SNIES...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:comp-msg 15s linear infinite;animation-delay:3s;animation-fill-mode:both;">
              💼&nbsp; Calculando métricas del Observatorio Laboral para la Educación (OLE)...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:comp-msg 15s linear infinite;animation-delay:6s;animation-fill-mode:both;">
              📊&nbsp; Renderizando gráficas comparativas en alta resolución...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:comp-msg 15s linear infinite;animation-delay:9s;animation-fill-mode:both;">
              🏛️&nbsp; Procesando resultados Saber PRO y estadísticas sociodemográficas...
            </div>
            <div style="position:absolute;font-size:0.74rem;color:#475569;white-space:nowrap;
                        opacity:0;animation:comp-msg 15s linear infinite;animation-delay:12s;animation-fill-mode:both;">
              📋&nbsp; Ensamblando el informe de benchmarking estratégico del programa...
            </div>
          </div>

          <!-- Tres puntos rebotando -->
          <div style="display:flex;gap:6px;justify-content:center;">
            <div style="width:8px;height:8px;border-radius:50%;background:#DF6C5B;
                        animation:comp-bounce 1.2s ease-in-out infinite;animation-delay:0s;"></div>
            <div style="width:8px;height:8px;border-radius:50%;background:#9333ea;
                        animation:comp-bounce 1.2s ease-in-out infinite;animation-delay:0.2s;"></div>
            <div style="width:8px;height:8px;border-radius:50%;background:#385A64;
                        animation:comp-bounce 1.2s ease-in-out infinite;animation-delay:0.4s;"></div>
          </div>
        </div>
        """

        ui.notification_show(
            ui.HTML(_LOADER_HTML),
            type="default",
            duration=None,
            id=_NID
        )

        try:
            report_data = calc_all_comp_report_data()

            if not report_data:
                ui.notification_remove(_NID)
                ui.notification_show(
                    "Selecciona un programa base y criterios de comparación antes de generar el informe.",
                    type="warning", duration=12
                )
                return

            import json
            report_json = json.dumps(report_data)

            template_path = app_dir / "web_report_demo" / "viewer_comp.html"
            if not template_path.exists():
                template_path = app_dir / "viewer_comp.html"

            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()

            injected_content = template_content.replace(
                '<script src="data_ANTIOQUIA.js"></script>',
                f'<script>window.__REPORT_DATA_COMP__ = {report_json};</script>'
            )

            logo_path = app_dir / "logo_symbiotic.svg"
            if logo_path.exists():
                import base64 as _b64
                with open(logo_path, "rb") as image_file:
                    logo_data = f"data:image/svg+xml;base64,{_b64.b64encode(image_file.read()).decode()}"
                    injected_content = injected_content.replace('src="../logo_symbiotic.svg"', f'src="{logo_data}"')

            ui.notification_remove(_NID)

            ui.modal_show(ui.modal(
                ui.tags.div(
                    ui.tags.iframe(
                        srcdoc=injected_content,
                        style="width: 100%; height: 85vh; border: none; background: white;"
                    ),
                    style="padding: 0; margin: -1rem; background: #f8fafc; overflow: hidden; border-radius: 4px;"
                ),
                title="Informe de Tendencia Comparada — Benchmarking Estratégico",
                size="xl",
                easy_close=True,
                footer=ui.div(
                    ui.modal_button("Cerrar"),
                    style="display: flex; gap: 10px; justify-content: flex-end; width: 100%;"
                )
            ))

        except Exception as e:
            import traceback
            ui.notification_remove(_NID)
            error_details = traceback.format_exc()
            if "SilentException" in str(type(e)):
                ui.notification_show("Por favor, selecciona los filtros necesarios antes de generar el informe.", type="warning", duration=15)
            else:
                ui.notification_show(f"Error en Vista Previa Comparativa: {str(e)}", type="error", duration=15)
            print(f"DEBUG ERROR COMP:\n{error_details}")

    @render.download(filename=lambda: f"Informe_Propio_Bench_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    def download_pdf_comp():
        # Este disparador es para la descarga directa vía WeasyPrint si se desea, 
        # pero para gráficas complejas se recomienda el botón de Imprimir del visor.
        pass

app = App(app_ui, server)

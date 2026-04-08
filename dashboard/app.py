import faicons as fa
from pathlib import Path
import polars as pl
from shiny import App, reactive, render, ui, session
import plotly.express as px
import plotly.graph_objects as go
from shinywidgets import output_widget, render_widget
from report_engine import ReportEngine
import datetime
import base64

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
).with_columns(
    pl.col("codigo_snies_del_programa").cast(pl.Int64)
)
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
    "nombre_institucion",
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
        ui.input_selectize("nombre_institucion", "Institución", choices=valores_iniciales["nombre_institucion"], multiple=True),
        ui.input_selectize("estado_programa", "Estado del Programa", choices=valores_iniciales["estado_programa"], selected=["ACTIVO"], multiple=True),
        ui.input_selectize("modalidad", "Modalidad", choices=valores_iniciales["modalidad"], multiple=True),
        ui.input_selectize("nivel_de_formacion", "Nivel de Formación", choices=valores_iniciales["nivel_de_formacion"], multiple=True),
        ui.input_selectize("area_de_conocimiento", "Área de Conocimiento", choices=valores_iniciales["area_de_conocimiento"], multiple=True),
        ui.input_selectize("nucleo_basico_del_conocimiento", "Núcleo Básico (NBC)", choices=valores_iniciales["nucleo_basico_del_conocimiento"], multiple=True),
        ui.input_selectize("sector", "Sector", choices=valores_iniciales["sector"], multiple=True),
        ui.input_selectize("departamento", "Departamento de Oferta", choices=departamentos_oferta, multiple=True),
        ui.input_selectize("municipio", "Municipio de Oferta", choices=[], multiple=True),
        ui.input_action_button("btn_calcular", "Aplicar Filtros", class_="btn-danger w-100 mt-2 mb-2", style="font-weight: bold; font-size: 1.1em;"),
        ui.download_button("download_pdf", "Descargar Informe (PDF)", class_="btn-primary w-100 mt-2"),
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
            ui.card_header(ui.HTML("Tendencia Total de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
            output_widget("plot_primer_curso_total"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia Total de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
            output_widget("plot_matriculados_total"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia Total de Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
            output_widget("plot_graduados_total"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
            output_widget("plot_primer_curso"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
            output_widget("plot_matriculados"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        ui.card(
            ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
            output_widget("plot_graduados"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True, style="min-height: 500px;"
        ),
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(
            ui.card_header(ui.HTML("Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
            ui.output_data_frame("table_pcurso"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True
        ),
        ui.card(
            ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
            ui.output_data_frame("table_matriculados"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True
        ),
        ui.card(
            ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
            ui.output_data_frame("table_graduados"), 
            ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"),
            full_screen=True
        ),
        class_="mb-4"
    ),
    ui.layout_columns(
        ui.card(ui.card_header("Información Básica de Programas"), ui.output_data_frame("table"), full_screen=True),
        class_="mb-4"
    )
        ),
        ui.nav_panel(
            "Observatorio Laboral",
            ui.layout_columns(
                ui.value_box(
                    f"Tasa de Empleabilidad ({max_anno_ole})", 
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
                ui.card(ui.card_header(ui.HTML("Tendencia Total de <b style='color: #31497e;'>Empleabilidad</b>")), output_widget("plot_empleabilidad_total"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                ui.card(ui.card_header(ui.HTML("Tendencia Total de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b>")), output_widget("plot_dependientes_total"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Empleabilidad por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_empleabilidad_sexo"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                ui.card(ui.card_header(ui.HTML("Relación Dependientes sobre Graduados por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_dependientes_sexo"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución Total de <b style='color: #31497e;'>Empleabilidad</b> ({max_anno_ole})")), output_widget("plot_dist_empleabilidad"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución Total de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> ({max_anno_ole})")), output_widget("plot_dist_dependientes"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución de <b style='color: #31497e;'>Empleabilidad</b> por Sexo ({max_anno_ole})")), output_widget("plot_dist_empleabilidad_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> por Sexo ({max_anno_ole})")), output_widget("plot_dist_dependientes_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),

            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Matriz de <b style='color: #31497e;'>Graduados que Cotizan</b> (Origen vs Destino) - {max_anno_ole}")), 
                    output_widget("plot_mobility_matrix"), 
                    ui.card_footer(
                        ui.HTML("Sumatoria de Graduados que Cotizan según zona de estudio (Origen) vs zona de cotización laboral (Destino). Fuente: Observatorio Laboral para la Educación (OLE)<br>"),
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
                    output_widget("plot_dependientes_trend"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de la <b style='color: #31497e;'>Tasa de Retención Local</b>")), 
                    output_widget("plot_retencion_trend"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Ratio Salen / Entran</b>")), 
                    output_widget("plot_ratio_trend"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
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
                    ui.card_header(ui.HTML(f"Distribución Total por <b style='color: #31497e;'>Rango Salarial</b> ({max_anno_ole:.0f})")), 
                    output_widget("plot_salario_dist_total"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución Salarial por <b style='color: #31497e;'>Sexo</b> ({max_anno_ole:.0f})")), 
                    output_widget("plot_salario_dist_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos corrientes)")), 
                    output_widget("plot_salario_evolucion_total"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos corrientes)")), 
                    output_widget("plot_salario_evolucion_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos constantes - SMMLV {max_anno_smmlv:.0f})")), 
                    output_widget("plot_salario_evolucion_total_constante"), 
                    ui.card_footer(ui.HTML(f"Fuente: Observatorio Laboral para la Educación (OLE)<br>Ajustado a SMMLV de {max_anno_smmlv:.0f}"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos constantes - SMMLV {max_anno_smmlv:.0f})")), 
                    output_widget("plot_salario_evolucion_sexo_constante"), 
                    ui.card_footer(ui.HTML(f"Fuente: Observatorio Laboral para la Educación (OLE)<br>Ajustado a SMMLV de {max_anno_smmlv:.0f}"), style="font-size: 0.85em; color: gray;"), 
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
                    output_widget("plot_dist_desercion"), 
                    ui.card_footer(ui.HTML("Fuente: SPADIES - Ministerio de Educación Nacional<br>Distribución por programa en pasos de 2%"), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Tendencia Histórica de <b style='color: #31497e;'>Deserción Anual</b>")), 
                    output_widget("plot_trend_desercion"), 
                    ui.card_footer(ui.HTML("Fuente: SPADIES - Ministerio de Educación Nacional<br>Evolución promedio de los programas seleccionados"), style="font-size: 0.85em; color: gray;"), 
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
                    output_widget("plot_saber_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución del puntaje promedio de los programas capturados por los filtros activos."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución del <b style='color: #31497e;'>Puntaje Global</b> ({max_anno_saber:.0f})")), 
                    output_widget("plot_saber_dist"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Frecuencia relativa de los programas académicos según su puntaje promedio."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Sexo</b>")), 
                    output_widget("plot_saber_count_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución del conteo de estudiantes que presentaron la prueba."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Edad</b>")), 
                    output_widget("plot_saber_count_edad"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución del conteo de estudiantes que presentaron la prueba."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.h3("Evolución Detallada por Perfil Sociodemográfico", class_="mt-5 mb-3", style="color: #31497e; border-bottom: 2px solid #31497e; padding-bottom: 5px;"),
            
            # FILA 1: PUNTAJE GLOBAL
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_saber_trend_global_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Edad</b>")), output_widget("plot_saber_trend_global_edad"), full_screen=True),
            ),
            # FILA 2: RAZONAMIENTO CUANTITATIVO
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_saber_trend_razona_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Edad</b>")), output_widget("plot_saber_trend_razona_edad"), full_screen=True),
            ),
            # FILA 3: LECTURA CRÍTICA
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_saber_trend_lectura_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Edad</b>")), output_widget("plot_saber_trend_lectura_edad"), full_screen=True),
            ),
            # FILA 4: COMPETENCIAS CIUDADANAS
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_saber_trend_ciuda_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Edad</b>")), output_widget("plot_saber_trend_ciuda_edad"), full_screen=True),
            ),
            # FILA 5: INGLÉS
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_saber_trend_ingles_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Edad</b>")), output_widget("plot_saber_trend_ingles_edad"), full_screen=True),
            ),
            # FILA 6: COMUNICACIÓN ESCRITA
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Sexo</b>")), output_widget("plot_saber_trend_escrita_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Edad</b>")), output_widget("plot_saber_trend_escrita_edad"), full_screen=True),
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
                    output_widget("plot_saber_demo_sexo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Distribución porcentual por sexo de los evaluados en el último año."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Grupo de Edad</b> ({max_anno_saber:.0f})")), 
                    output_widget("plot_saber_demo_edad"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Distribución porcentual por rangos de edad en el último año."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Horas de Trabajo</b> ({max_anno_saber:.0f})")), 
                    output_widget("plot_saber_demo_trabajo"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Distribución porcentual de la carga laboral reportada por los estudiantes."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Estrato Social</b> ({max_anno_saber:.0f})")), 
                    output_widget("plot_saber_demo_estrato"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Distribución porcentual por estrato socioeconómico de la vivienda."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.h3("Evolución Temporal de la Socio-demografía", class_="mt-5 mb-3", style="color: #31497e; border-bottom: 2px solid #31497e; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Sexo</b>")), 
                    output_widget("plot_saber_demo_sexo_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la composición por género."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Grupo de Edad</b>")), 
                    output_widget("plot_saber_demo_edad_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la composición por rangos de edad."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Horas de Trabajo</b>")), 
                    output_widget("plot_saber_demo_trabajo_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la participación según carga laboral."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Estrato Social</b>")), 
                    output_widget("plot_saber_demo_estrato_trend"), 
                    ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la composición socioeconómica."), style="font-size: 0.85em; color: gray;"), 
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
                ui.value_box("Primer Curso", ui.output_ui("prev_kpi_pcurso"), showcase=fa.icon_svg("user-graduate", "solid")),
                ui.value_box("Matriculados", ui.output_ui("prev_kpi_matriculados"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Graduados", ui.output_ui("prev_kpi_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia Total de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), output_widget("prev_pcurso_total"), ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia Total de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), output_widget("prev_matricula_total"), ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia Total de Estudiantes <b style='color: #31497e;'>Graduados</b>")), output_widget("prev_graduados_total"), ui.card_footer(ui.HTML("Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), output_widget("prev_pcurso_sexo"), ui.output_ui("prev_caption_pcurso"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Matriculados</b>")), output_widget("prev_matricula_sexo"), ui.output_ui("prev_caption_matricula"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia por Sexo de Estudiantes <b style='color: #31497e;'>Graduados</b>")), output_widget("prev_graduados_sexo"), ui.output_ui("prev_caption_graduados"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("2. Observatorio Laboral para la Educación (OLE)", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px; mt-5"),
            ui.layout_columns(
                ui.value_box("Tasa Empleabilidad", ui.output_ui("prev_kpi_emp"), showcase=fa.icon_svg("briefcase", "solid")),
                ui.value_box("Retención Local", ui.output_ui("prev_kpi_ret"), showcase=fa.icon_svg("thumbtack", "solid")),
                ui.value_box("Ratio Migratorio", ui.output_ui("prev_kpi_ratio"), showcase=fa.icon_svg("arrow-right-arrow-left", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Tendencia Total de <b style='color: #31497e;'>Empleabilidad</b>")), output_widget("prev_ole_emp_total"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia Total de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b>")), output_widget("prev_ole_dep_total"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Empleabilidad por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_ole_emp_sexo"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Relación Dependientes sobre Graduados por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_ole_dep_sexo"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución Total de <b style='color: #31497e;'>Empleabilidad</b> ({max_anno_ole})")), output_widget("prev_ole_dist_emp"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución Total de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> ({max_anno_ole})")), output_widget("prev_ole_dist_dep"), ui.card_footer(ui.HTML("Frecuencia relativa de programas académicos. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución de <b style='color: #31497e;'>Empleabilidad</b> por Sexo ({max_anno_ole})")), output_widget("prev_ole_dist_emp_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                ui.card(ui.card_header(ui.HTML(f"Distribución de la <b style='color: #31497e;'>Relación Dependientes sobre Graduados</b> por Sexo ({max_anno_ole})")), output_widget("prev_ole_dist_dep_sexo"), ui.card_footer(ui.HTML("Frecuencia relativa de sub-grupos por programa y sexo. Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 400px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Matriz de <b style='color: #31497e;'>Graduados que Cotizan</b> (Origen vs Destino) - {max_anno_ole}")), output_widget("prev_ole_mobility"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br><small><i>Nota técnica: La matriz se construye exclusivamente sobre graduados con registro de cotización laboral.</i></small>"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 500px;"),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Dependientes sobre Cotizantes</b>")), output_widget("prev_ole_trend_dep"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                ui.card(ui.card_header(ui.HTML("Evolución de la <b style='color: #31497e;'>Tasa de Retención Local</b>")), output_widget("prev_ole_trend_ret"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                ui.card(ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Ratio Salen / Entran</b>")), output_widget("prev_ole_trend_ratio"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
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
                ui.card(ui.card_header(ui.HTML(f"Distribución Total por <b style='color: #31497e;'>Rango Salarial</b> ({max_anno_ole:.0f})")), output_widget("prev_sal_dist_total"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución Salarial por <b style='color: #31497e;'>Sexo</b> ({max_anno_ole:.0f})")), output_widget("prev_sal_dist_sexo"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos corrientes)")), output_widget("prev_sal_evol_total"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos corrientes)")), output_widget("prev_sal_evol_sexo"), ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación (OLE)<br>Elaboración propia con base en SMMLV histórico"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Evolución del <b style='color: #31497e;'>Salario Promedio Estimado</b> (Pesos constantes - SMMLV {max_anno_smmlv:.0f})")), output_widget("prev_sal_evol_constante"), ui.card_footer(ui.HTML(f"Fuente: Observatorio Laboral para la Educación (OLE)<br>Ajustado a SMMLV de {max_anno_smmlv:.0f}"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Evolución Salarial por <b style='color: #31497e;'>Sexo</b> (Pesos constantes - SMMLV {max_anno_smmlv:.0f})")), output_widget("prev_sal_evol_sexo_constante"), ui.card_footer(ui.HTML(f"Fuente: Observatorio Laboral para la Educación (OLE)<br>Ajustado a SMMLV de {max_anno_smmlv:.0f}"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("4. Permanencia y Deserción (SPADIES)", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Tasa Deserción", ui.output_ui("prev_kpi_des"), showcase=fa.icon_svg("user-minus", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Distribución de la <b style='color: #31497e;'>Tasa de Deserción</b> (Último Año)")), output_widget("prev_des_dist"), ui.card_footer(ui.HTML("Fuente: SPADIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Tendencia Histórica de <b style='color: #31497e;'>Deserción Anual</b>")), output_widget("prev_des_trend"), ui.card_footer(ui.HTML("Fuente: SPADIES<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("5. Excelencia Académica (Prueba SABER PRO)", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Puntaje Global Promedio", ui.output_ui("prev_kpi_saber"), showcase=fa.icon_svg("award", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Competencias Genéricas</b> (Promedio por Programa)")), output_widget("prev_saber_trend"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución del <b style='color: #31497e;'>Puntaje Global</b> ({max_anno_saber:.0f})")), output_widget("prev_saber_dist"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_count_sexo"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Cantidad de Evaluados por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_count_edad"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Elaboración propia"), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-5"
            ),
            ui.h4("Evolución Detallada por Perfil Sociodemográfico", class_="mt-4 mb-3", style="color: #31497e;"),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_trend_global_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Puntaje Global por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_trend_global_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_trend_razona_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Razonamiento Cuantitativo por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_trend_razona_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_trend_lectura_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Lectura Crítica por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_trend_lectura_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_trend_ciuda_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Competencias Ciudadanas por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_trend_ciuda_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_trend_ingles_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Inglés por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_trend_ingles_edad"), full_screen=True),
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_saber_trend_escrita_sexo"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Comunicación Escrita por <b style='color: #31497e;'>Edad</b>")), output_widget("prev_saber_trend_escrita_edad"), full_screen=True),
                class_="mb-5"
            ),

            ui.h3("6. Perfil Socio-demográfico de los Evaluados", style="color: #31497e; border-bottom: 2px solid #ccc; padding-bottom: 5px;"),
            ui.layout_columns(
                ui.value_box("Total de Evaluados", ui.output_ui("prev_kpi_evaluados"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Programas Académicos", ui.output_ui("prev_kpi_progs_saber"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Sexo</b> ({max_anno_saber:.0f})")), output_widget("prev_demo_sexo"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Caracterización demográfica de los estudiantes evaluados."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Grupo de Edad</b> ({max_anno_saber:.0f})")), output_widget("prev_demo_edad"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Composición por rangos de edad."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Horas de Trabajo</b> ({max_anno_saber:.0f})")), output_widget("prev_demo_trabajo"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Distribución según carga laboral."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML(f"Distribución por <b style='color: #31497e;'>Estrato Social</b> ({max_anno_saber:.0f})")), output_widget("prev_demo_estrato"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Composición socioeconómica de los evaluados."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Sexo</b>")), output_widget("prev_demo_sexo_trend"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la composición por sexo."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Grupo de Edad</b>")), output_widget("prev_demo_edad_trend"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la composición por rangos de edad."), style="font-size: 0.85em; color: gray;"), full_screen=True),
                class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Horas de Trabajo</b>")), output_widget("prev_demo_trabajo_trend"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la participación según carga laboral."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                ui.card(ui.card_header(ui.HTML("Evolución de Participación por <b style='color: #31497e;'>Estrato Social</b>")), output_widget("prev_demo_estrato_trend"), ui.card_footer(ui.HTML("Fuente: ICFES - Prueba SABER PRO<br>Evolución histórica de la composición socioeconómica."), style="font-size: 0.85em; color: gray;"), full_screen=True, style="min-height: 450px;"),
                class_="mb-5"
            )
        ),
        ui.nav_panel(
            "Tendencia Comparada",
            ui.h2("Análisis Comparativo de Tendencias", class_="mt-2 mb-3", style="color: #31497e; font-weight: bold;"),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("<b style='color: #31497e;'>1. Selección de Programa Base</b>")),
                    ui.input_selectize("comp_snies_base", "Buscar Programa Académico por SNIES o Nombre:", choices=[], multiple=False, width="100%"),
                    ui.output_ui("comp_perfil_snies"),
                    class_="mb-3"
                ),
                ui.card(
                    ui.card_header(ui.HTML("<b style='color: #31497e;'>2. Definir Grupo Comparable</b>")),
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
                        ui.HTML("<b>Nota Técnica (Estadística Robusta):</b> Se utiliza la <b>Mediana</b> y el <b>MAD</b> (Desviación Absoluta de la Mediana) porque los datos de matrícula presentan una distribución tipo Pareto (varios programas pequeños y pocos con volúmenes masivos). En estos casos, el promedio y la desviación estándar se ven fuertemente sesgados por los valores extremos. La mediana representa mejor al 'programa típico' del grupo, y el MAD escalado por 1.4826 ofrece una medida de dispersión equivalente a la desviación estándar pero protegida contra anomalías."),
                        style="font-size: 0.85em; color: #555; background-color: #fef9e7; padding: 12px; border-radius: 8px; border-left: 4px solid #ffa600; margin-top: 10px;"
                    ),
                    class_="mb-3"
                ),
                class_="mb-4",
                col_widths=(4, 8)
            ),
            ui.h3("Grupo de Comparación", class_="mt-4 mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Universo de Comparación (Últ. Año)", ui.output_ui("comp_kpi_universo"), showcase=fa.icon_svg("users-rays", "solid")),
                ui.value_box("Total Neto Primer Curso", ui.output_ui("comp_kpi_neto_pcurso"), showcase=ICONS["student"]),
                ui.value_box("Total Neto Matriculados", ui.output_ui("comp_kpi_neto_matricula"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Total Neto Graduados", ui.output_ui("comp_kpi_neto_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-5"
            ),
            ui.h3("Tendencias de Matrícula", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Primer Curso)", ui.output_ui("comp_kpi_base_pcurso"), showcase=ICONS["student"]),
                ui.value_box("Programa Seleccionado (Matriculados)", ui.output_ui("comp_kpi_base_matricula"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Programa Seleccionado (Graduados)", ui.output_ui("comp_kpi_base_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.value_box("Mediana Comparable (Primer Curso)", ui.output_ui("comp_kpi_pcurso"), showcase=ICONS["student"]),
                ui.value_box("Mediana Comparable (Matriculados)", ui.output_ui("comp_kpi_matricula"), showcase=fa.icon_svg("users", "solid")),
                ui.value_box("Mediana Comparable (Graduados)", ui.output_ui("comp_kpi_graduados"), showcase=fa.icon_svg("graduation-cap", "solid")),
                fill=False, class_="mb-4"
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Estudiantes de <b style='color: #31497e;'>Primer Curso</b>")), 
                    output_widget("plot_comp_pcurso"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br>La línea central representa la mediana y la zona sombreada el intervalo de dispersión basado en la Mediana (±1.48 MAD)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Matriculados</b>")), 
                    output_widget("plot_comp_matricula"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br>La línea central representa la mediana y la zona sombreada el intervalo de dispersión basado en la Mediana (±1.48 MAD)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Estudiantes <b style='color: #31497e;'>Graduados</b>")), 
                    output_widget("plot_comp_graduados"), 
                    ui.card_footer(ui.HTML("Fuente: SNIES.<br>La línea central representa la mediana y la zona sombreada el intervalo de dispersión basado en la Mediana (±1.48 MAD)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5"
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("Observatorio Laboral y Calidad", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Tasa Empleabilidad)", ui.output_ui("comp_kpi_base_empleabilidad"), showcase=fa.icon_svg("briefcase", "solid")),
                ui.value_box("Media Comparable (Tasa Empleabilidad)", ui.output_ui("comp_kpi_empleabilidad"), showcase=fa.icon_svg("briefcase", "solid")),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.HTML("Tendencia Total de <b style='color: #31497e;'>Empleabilidad</b>")), 
                    output_widget("plot_comp_ole_empleabilidad"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación.<br>La línea central representa el promedio y la zona sombreada la dispersión muestral (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Evolución de <b style='color: #31497e;'>Dependientes sobre Cotizantes</b>")), 
                    output_widget("plot_comp_ole_dependientes"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación.<br>La línea central representa el promedio y la zona sombreada la dispersión muestral (±1 Desviación Estándar)."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 500px;"
                ),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.output_ui("comp_dist_empleabilidad_header")), 
                    output_widget("plot_comp_dist_empleabilidad"), 
                    ui.card_footer(ui.HTML("Fuente: Observatorio Laboral para la Educación.<br><b>El fondo gris</b> representa a todos los programas del mismo Nivel de Formación.<br><b>La distribución púrpura</b> es el Grupo Comparable.<br><b>La línea azul punteada</b> marca la tasa del Programa Seleccionado."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.div(),
                class_="mb-5", col_widths=(6, 6)
            ),
            ui.hr(style="margin-top: 2rem; margin-bottom: 2rem; border-color: #31497e; opacity: 1; border-width: 3px;"),
            ui.h3("Salario de Enganche (Estimado)", class_="mb-3", style="color: #31497e; font-weight: bold; font-size: 1.5em;"),
            ui.layout_columns(
                ui.value_box("Programa Seleccionado (Salario)", ui.output_ui("comp_kpi_base_salario"), showcase=fa.icon_svg("money-bill-trend-up", "solid")),
                ui.value_box("Media Comparable (Salario)", ui.output_ui("comp_kpi_salario"), showcase=fa.icon_svg("money-bill-trend-up", "solid")),
                fill=False, class_="mb-4", col_widths=(6, 6)
            ),
            ui.layout_columns(
                ui.card(
                    ui.card_header(ui.output_ui("comp_salario_evolucion_header")), 
                    output_widget("plot_comp_salario_evolucion"), 
                    ui.card_footer(ui.HTML("Fuente: OLE.<br>Salario estimado en pesos constantes. El grupo comparable presenta la media ±1 Desviación Estándar."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                ui.card(
                    ui.card_header(ui.HTML("Distribución por <b style='color: #31497e;'>Rango Salarial</b> (Últ. Año)")), 
                    output_widget("plot_comp_salario_dist"), 
                    ui.card_footer(ui.HTML("Fuente: OLE.<br>Frecuencia de graduados por rangos salariales comparando el programa con su grupo."), style="font-size: 0.85em; color: gray;"), 
                    full_screen=True, style="min-height: 450px;"
                ),
                class_="mb-5", col_widths=(6, 6)
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
            "nombre_institucion": input.nombre_institucion() or (),
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
            "nombre_institucion": input.nombre_institucion(),
            "estado_programa": input.estado_programa(),
            "modalidad": input.modalidad(),
            "nivel_de_formacion": input.nivel_de_formacion(),
            "area_de_conocimiento": input.area_de_conocimiento(),
            "nucleo_basico_del_conocimiento": input.nucleo_basico_del_conocimiento(),
            "sector": input.sector(),
            "departamento": input.departamento(),
            "municipio": input.municipio()
        }

    @reactive.calc
    def filtered_snies():
        df = df_snies
        f_vals = isolated_filters()
        
        for col in filtros_cols:
            val = f_vals[col]
            if is_filtered(val): 
                df = df.filter(pl.col(col).is_in(val))
        
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
        if snies_filtered.height == 0:
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
        if ole_filtered.height == 0: return "Sin dato"
        df_snies_agg = ole_filtered.group_by("codigo_snies_del_programa").agg([pl.col("graduados_que_cotizan").sum().alias("cotizan"), pl.col("graduados").sum().alias("total")])
        df_snies_agg = df_snies_agg.filter(pl.col("total") > 0)
        if df_snies_agg.height == 0: return "0%"
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
        if ole_filtered.height == 0: return "0%"
        df_snies_agg = ole_filtered.group_by("codigo_snies_del_programa").agg([pl.col("graduados_cotizantes_dependientes").sum().alias("dependientes"), pl.col("graduados_que_cotizan").sum().alias("total_cotizan")])
        df_snies_agg = df_snies_agg.filter(pl.col("total_cotizan") > 0)
        if df_snies_agg.height == 0: return "0%"
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
        if ole_filtered.height == 0: return "0%"
        df_snies_agg = ole_filtered.group_by("codigo_snies_del_programa").agg([pl.col("graduados_cotizantes_dependientes").sum().alias("dependientes"), pl.col("graduados").sum().alias("total_graduados")]).filter(pl.col("total_graduados") > 0)
        if df_snies_agg.height == 0: return "0%"
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
        
        if df_sexo.height == 0:
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
            return pd.DataFrame()
            
        ole_filtered = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) & 
            (pl.col("anno_corte") >= 2016)
        )
        if ole_filtered.height == 0:
            return pd.DataFrame()
            
        agg_df = ole_filtered.group_by(group_by_cols).agg([
            pl.col(measure_numerator).sum().alias("num"),
            pl.col(measure_denominator).sum().alias("den")
        ])
        
        agg_df = agg_df.filter(pl.col("den") > 0)
        if agg_df.height == 0:
            return pd.DataFrame()
            
        agg_df = agg_df.with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
        
        final_group_cols = [c for c in group_by_cols if c != "codigo_snies_del_programa"]
        
        res_df = agg_df.group_by(final_group_cols).agg(pl.col("tasa").mean()).sort(final_group_cols)
        return res_df.to_pandas()

    @reactive.calc
    def calc_plot_empleabilidad_total():
        df_pd = create_ole_trend_df("graduados_que_cotizan", "graduados", ["anno_corte", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año de Corte", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Tasa de Empleabilidad", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=".1%", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_empleabilidad_total():
        return calc_plot_empleabilidad_total()

    @reactive.calc
    def calc_plot_dependientes_total():
        df_pd = create_ole_trend_df("graduados_cotizantes_dependientes", "graduados", ["anno_corte", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año de Corte", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Relación Dependientes sobre Graduados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=".1%", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_dependientes_total():
        return calc_plot_dependientes_total()

    @reactive.calc
    def calc_plot_empleabilidad_sexo():
        df_pd = create_ole_trend_df("graduados_que_cotizan", "graduados", ["anno_corte", "sexo", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
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

    @render_widget
    def plot_empleabilidad_sexo():
        return calc_plot_empleabilidad_sexo()

    @reactive.calc
    def calc_plot_dependientes_sexo():
        df_pd = create_ole_trend_df("graduados_cotizantes_dependientes", "graduados", ["anno_corte", "sexo", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
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

    @render_widget
    def plot_dependientes_sexo():
        return calc_plot_dependientes_sexo()

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
        if ole_filtered.height == 0:
            return pd.DataFrame()
            
        agg_df = ole_filtered.group_by(group_by_cols).agg([
            pl.col(measure_numerator).sum().alias("num"),
            pl.col(measure_denominator).sum().alias("den")
        ])
        
        agg_df = agg_df.filter(pl.col("den") > 0)
        if agg_df.height == 0:
            return pd.DataFrame()
            
        agg_df = agg_df.with_columns((pl.col("num") / pl.col("den")).alias("tasa"))
        return agg_df.to_pandas()

    @reactive.calc
    def calc_plot_dist_empleabilidad():
        df_pd = get_ole_distribution_df("graduados_que_cotizan", "graduados")
        if df_pd.empty: return go.Figure()
        fig = px.histogram(df_pd, x="tasa", histnorm='percent')
        fig.update_traces(marker=dict(color="#31497e"), xbins=dict(start=0.0, end=1.0, size=0.05), marker_line_width=1, marker_line_color="white")
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
        # Format tooltips
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}% de programas<extra></extra>')
        return fig

    @render_widget
    def plot_dist_empleabilidad():
        return calc_plot_dist_empleabilidad()

    @reactive.calc
    def calc_plot_dist_dependientes():
        df_pd = get_ole_distribution_df("graduados_cotizantes_dependientes", "graduados")
        if df_pd.empty: return go.Figure()
        fig = px.histogram(df_pd, x="tasa", histnorm='percent')
        fig.update_traces(marker=dict(color="#31497e"), xbins=dict(start=0.0, end=1.0, size=0.05), marker_line_width=1, marker_line_color="white")
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

    @render_widget
    def plot_dist_dependientes():
        return calc_plot_dist_dependientes()

    @reactive.calc
    def calc_plot_dist_empleabilidad_sexo():
        df_pd = get_ole_distribution_df("graduados_que_cotizan", "graduados", ["codigo_snies_del_programa", "sexo"])
        if df_pd.empty: return go.Figure()
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
        # Format tooltips
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}% de sub-grupos<extra></extra>')
        return fig

    @render_widget
    def plot_dist_empleabilidad_sexo():
        return calc_plot_dist_empleabilidad_sexo()

    @reactive.calc
    def calc_plot_dist_dependientes_sexo():
        df_pd = get_ole_distribution_df("graduados_cotizantes_dependientes", "graduados", ["codigo_snies_del_programa", "sexo"])
        if df_pd.empty: return go.Figure()
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

    @render_widget
    def plot_dist_dependientes_sexo():
        return calc_plot_dist_dependientes_sexo()

    @reactive.calc
    def calc_mobility_kpis():
        df_pd, col_orig, col_dest, label_ejes = get_ole_mobility_df()
        if len(df_pd) == 0: return {"retencion": 0, "fuga": 0, "ratio": 0}

        def normalize_str(s):
            return str(s).upper().replace(".", "").replace(",", "").replace("  ", " ").strip()

        seleccionados = list(input.municipio() or []) if label_ejes == "Municipio" else list(input.departamento() or [])
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
        mpio_filtro = input.municipio()
        if hasattr(mpio_filtro, '__iter__') and len(mpio_filtro) > 0 and mpio_filtro[0]:
            col_orig, col_dest, label_ejes = "municipio_origen", "municipio_destino", "Municipio"
        else:
            col_orig, col_dest, label_ejes = "departamento_origen", "departamento_destino", "Departamento"
            
        def normalize_str(s):
            return str(s).upper().replace(".", "").replace(",", "").replace("  ", " ").strip()

        seleccionados = list(input.municipio() or []) if label_ejes == "Municipio" else list(input.departamento() or [])
        sel_norm = [normalize_str(x) for x in seleccionados]
        
        ole_all = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) &
            (pl.col("anno_corte") >= 2016)
        )
        
        if ole_all.height == 0: return pd.DataFrame()
        
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
        fig = px.line(df_pd, x="anno_corte", y="retencion", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Tasa de Retención", tickformat=".1%", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_retencion_trend():
        return calc_plot_retencion_trend()

    @reactive.calc
    def calc_plot_ratio_trend():
        df_pd = calc_mobility_yoy_data()
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="ratio", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Ratio Salen / Entran", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_ratio_trend():
        return calc_plot_ratio_trend()

    @reactive.calc
    def calc_plot_dependientes_trend():
        # Reutilizamos el motor de tendencias pero con el nuevo nombre semántico
        df_pd = create_ole_trend_df("graduados_cotizantes_dependientes", "graduados_que_cotizan", ["anno_corte", "codigo_snies_del_programa"])
        if df_pd.empty: return go.Figure()
        fig = px.line(df_pd, x="anno_corte", y="tasa", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Dependientes sobre Cotizantes", tickformat=".1%", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_dependientes_trend():
        return calc_plot_dependientes_trend()

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
        dept = input.departamento()
        mpio = input.municipio()
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
        if df.height == 0: return "0"
        max_yr = df["anno_corte"].max()
        total = df.filter(pl.col("anno_corte") == max_yr)["graduados_cotizantes_dependientes"].sum()
        return f"{total:,.0f}".replace(",", ".")

    @render.ui
    def kpi_salario_dependientes_sum():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_dependientes_sum()}</div>")

    def _calculate_salary_trend_data(is_constant=False):
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0: return pd.DataFrame()
        
        # 1. Filtro base
        df_base = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))
        
        # 2. Filtro Geográfico
        dept = input.departamento()
        mpio = input.municipio()
        if is_filtered(dept) or is_filtered(mpio):
            divis_num = df_cobertura.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))
            if is_filtered(dept): divis_num = divis_num.filter(pl.col("departamento_oferta").is_in(dept))
            if is_filtered(mpio): divis_num = divis_num.filter(pl.col("municipio_oferta").is_in(mpio))
            div_codes = divis_num["divipola_mpio_oferta"].drop_nulls().unique()
            df_base = df_base.filter(pl.col("divipola_mpio_principal").is_in(div_codes))
            
        if df_base.height == 0: return pd.DataFrame()
        
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
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario Promedio ($)", tickformat="$,.0f", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_salario_evolucion_total():
        return calc_plot_salario_evolucion_total()

    @reactive.calc
    def calc_plot_salario_evolucion_sexo():
        df_pd = get_salary_trend_data()
        if df_pd.empty: return go.Figure()
        
        df_plot = df_pd[df_pd["label"] != "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", color="label", color_discrete_map=COLOR_SEXO, markers=True)
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
        
    @render_widget
    def plot_salario_evolucion_sexo():
        return calc_plot_salario_evolucion_sexo()

    @reactive.calc
    def calc_plot_salario_evolucion_total_constante():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return go.Figure()
        
        df_plot = df_pd[df_pd["label"] == "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año de Corte", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Salario Promedio ($)", tickformat="$,.0f", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_salario_evolucion_total_constante():
        return calc_plot_salario_evolucion_total_constante()

    @reactive.calc
    def calc_plot_salario_evolucion_sexo_constante():
        df_pd = get_salary_trend_data_constant()
        if df_pd.empty: return go.Figure()
        
        df_plot = df_pd[df_pd["label"] != "TOTAL"]
        fig = px.line(df_plot, x="anno_corte", y="salario_pesos", color="label", color_discrete_map=COLOR_SEXO, markers=True)
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

    @render_widget
    def plot_salario_evolucion_sexo_constante():
        return calc_plot_salario_evolucion_sexo_constante()

    # --- SECCIÓN DESERCIÓN (SPADIES) ---
    @reactive.calc
    def filtered_desercion():
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        if len(snies_codigos) == 0:
            return pl.DataFrame()
        return df_desercion.filter(pl.col("codigo_snies_del_programa").is_in(snies_codigos))

    @reactive.calc
    def calc_kpi_desercion_promedio():
        df = filtered_desercion()
        if df.height == 0: return "0%"
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
        if df.height == 0: return go.Figure()
        max_yr = df["anno"].max()
        df_plot = df.filter(pl.col("anno") == max_yr).to_pandas()
        fig = px.histogram(df_plot, x="desercion_anual_mean", nbins=50, histnorm='percent')
        fig.update_traces(xbins=dict(start=0.0, end=1.0, size=0.02), marker_color="#31497e", marker_line_color="white", marker_line_width=1)
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Tasa de Deserción", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="Participación de Programas (%)", ticksuffix="%", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_dist_desercion():
        return calc_plot_dist_desercion()

    @reactive.calc
    def calc_plot_trend_desercion():
        df = filtered_desercion()
        if df.height == 0: return go.Figure()
        df_plot = df.group_by("anno").agg(pl.col("desercion_anual_mean").mean()).sort("anno").to_pandas()
        fig = px.line(df_plot, x="anno", y="desercion_anual_mean", markers=True)
        fig.update_traces(line=dict(color="#31497e", width=3), marker=dict(size=10, color="white", line=dict(width=2, color="#31497e")))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Tasa de Deserción Promedio", tickformat=".1%", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_trend_desercion():
        return calc_plot_trend_desercion()

    @reactive.calc
    def calc_kpi_salario_promedio_total():
        df_pd = get_salary_trend_data()
        if df_pd.empty: return "$ 0"
        val = df_pd[(df_pd["label"] == "TOTAL") & (df_pd["anno_corte"] == 2022)]["salario_pesos"]
        s = val.iloc[0] if not val.empty else 0
        return f"$ {format_num_es(s)}"

    @render.ui
    def kpi_salario_promedio_total():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_promedio_total()}</div>")

    @reactive.calc
    def calc_kpi_salario_promedio_fem():
        df_pd = get_salary_trend_data()
        if df_pd.empty: return "$ 0"
        val = df_pd[(df_pd["label"] == "FEMENINO") & (df_pd["anno_corte"] == 2022)]["salario_pesos"]
        s = val.iloc[0] if not val.empty else 0
        return f"$ {format_num_es(s)}"

    @render.ui
    def kpi_salario_promedio_fem():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_promedio_fem()}</div>")

    @reactive.calc
    def calc_kpi_salario_promedio_masc():
        df_pd = get_salary_trend_data()
        if df_pd.empty: return "$ 0"
        val = df_pd[(df_pd["label"] == "MASCULINO") & (df_pd["anno_corte"] == 2022)]["salario_pesos"]
        s = val.iloc[0] if not val.empty else 0
        return f"$ {format_num_es(s)}"

    @render.ui
    def kpi_salario_promedio_masc():
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{calc_kpi_salario_promedio_masc()}</div>")

    @reactive.calc
    def calc_plot_salario_dist_total():
        import pandas as pd
        df = filtered_ole_salario()
        if df.height == 0: return go.Figure()
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
        fig.update_traces(marker_color="#31497e", marker_line_color="white", marker_line_width=1.5)
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Participación de Graduados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=13))
        )
        return fig

    @render_widget
    def plot_salario_dist_total():
        return calc_plot_salario_dist_total()

    @reactive.calc
    def calc_plot_salario_dist_sexo():
        import pandas as pd
        df = filtered_ole_salario()
        if df.height == 0: return go.Figure()
        max_yr = df["anno_corte"].max()
        agg = df.filter(pl.col("anno_corte") == max_yr).group_by(["rango_salario", "sexo"]).agg(
            pl.col("graduados_cotizantes_dependientes").sum().alias("cantidad")
        ).to_pandas()
        agg["rango_salario"] = pd.Categorical(agg["rango_salario"], categories=RANGO_SALARIO_ORDER, ordered=True)
        agg = agg.sort_values(["rango_salario", "sexo"])
        total_global = agg["cantidad"].sum()
        if total_global > 0:
            agg["porcentaje"] = agg["cantidad"] / total_global
        else:
            agg["porcentaje"] = 0
        fig = px.bar(agg, x="porcentaje", y="rango_salario", color="sexo", orientation='h', barmode='group', color_discrete_map=COLOR_SEXO, text_auto='.1%')
        fig.update_layout(
            legend_title_text="Sexo", plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Participación de Graduados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=13))
        )
        return fig

    @render_widget
    def plot_salario_dist_sexo():
        return calc_plot_salario_dist_sexo()

    def get_ole_mobility_df():
        import pandas as pd
        fs = filtered_snies()
        snies_codigos = fs["codigo_snies_del_programa"].unique()
        
        if len(snies_codigos) == 0:
            return pd.DataFrame(), "", "", ""
            
        # Nivel de agregación dinámico basado en filtros frontend
        mpio_filtro = input.municipio()
        if hasattr(mpio_filtro, '__iter__') and len(mpio_filtro) > 0 and mpio_filtro[0]:
            col_origen = "municipio_origen"
            col_destino = "municipio_destino"
            label_ejes = "Municipio"
        else:
            col_origen = "departamento_origen"
            col_destino = "departamento_destino"
            label_ejes = "Departamento"
            
        max_anno_corte = df_ole_m0["anno_corte"].max()
        ole_filtered = df_ole_m0.filter(
            pl.col("codigo_snies_del_programa").is_in(snies_codigos) & 
            (pl.col("anno_corte") == max_anno_corte)
        )
        
        # Filtro estricto de matriz geospacial: Si el usuario selecciona Deptos o Mpios,
        # obligamos que la ruta de movilidad toque esa selección temporalmente o permanentemente (Origen O Destino).
        dept_vals = list(input.departamento() or [])
        mpio_vals = list(input.municipio() or [])
        
        def normalize_pl(col):
            # Eliminar puntos, comas y espacios dobles para match robusto
            return col.str.to_uppercase().str.replace_all(r"[\.,]", "").str.replace_all(r"\s+", " ").str.strip_chars()

        if label_ejes == "Municipio" and len(mpio_vals) > 0:
            mpio_norm = [str(x).upper().replace(".", "").replace(",", "").replace("  ", " ").strip() for x in mpio_vals]
            ole_filtered = ole_filtered.filter(
                normalize_pl(pl.col("municipio_origen")).is_in(mpio_norm) | 
                normalize_pl(pl.col("municipio_destino")).is_in(mpio_norm)
            )
        elif label_ejes == "Departamento" and len(dept_vals) > 0:
            dept_norm = [str(x).upper().replace(".", "").replace(",", "").replace("  ", " ").strip() for x in dept_vals]
            ole_filtered = ole_filtered.filter(
                normalize_pl(pl.col("departamento_origen")).is_in(dept_norm) | 
                normalize_pl(pl.col("departamento_destino")).is_in(dept_norm)
            )

        if ole_filtered.height == 0:
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
        
        # Simplificación estética de nombres largos en la capa de presentación
        long_name_san_andres = "ARCHIPIELAGO DE SAN ANDRES PROVIDENCIA Y SANTA CATALINA"
        df_pd[col_orig] = df_pd[col_orig].astype(str).str.replace(long_name_san_andres, "SAN ANDRES ISLAS").str.replace("ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA CATALINA", "SAN ANDRES ISLAS")
        df_pd[col_dest] = df_pd[col_dest].astype(str).str.replace(long_name_san_andres, "SAN ANDRES ISLAS").str.replace("ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA CATALINA", "SAN ANDRES ISLAS")
        
        # Detectar el elemento geográfico actualmente seleccionado para resaltarlo
        seleccionados = list(input.municipio() or []) if label_ejes == "Municipio" else list(input.departamento() or [])
        seleccionados_upper = [str(x).upper().replace(long_name_san_andres, "SAN ANDRES ISLAS").replace("ARCHIPIELAGO DE SAN ANDRES, PROVIDENCIA Y SANTA CATALINA", "SAN ANDRES ISLAS") for x in seleccionados]
        
        # Poda logística para no atorar el render web (Top 40 orígenes y destinos con más flujo de cotizantes)
        top_origenes = set(df_pd.groupby(col_orig)["cotizantes"].sum().nlargest(40).index)
        top_destinos = set(df_pd.groupby(col_dest)["cotizantes"].sum().nlargest(40).index)
        
        # Forzar que la zona seleccionada SIEMPRE se muestre (independientemente del Top 40)
        for s in seleccionados_upper:
            for v in df_pd[col_orig].unique():
                if s in str(v).upper(): top_origenes.add(v)
            for v in df_pd[col_dest].unique():
                if s in str(v).upper(): top_destinos.add(v)
                
        df_pd = df_pd[df_pd[col_orig].isin(top_origenes) & df_pd[col_dest].isin(top_destinos)]
        
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
        
        fig = go.Figure(data=go.Heatmap(
            z=matriz.values, x=matriz.columns, y=matriz.index,
            zmax=zmax_interno, colorscale=custom_scale,
            text=matriz.values, texttemplate="%{text:,.0f}",
            xgap=1, ygap=1
        ))
        
        fig.data[0].textfont.color = None
        fig.data[0].hovertemplate = "Origen: %{y}<br>Destino: %{x}<br>Graduados Cotizantes: %{z:,.0f}<extra></extra>"
        
        shapes = []
        for s in seleccionados_upper:
            row_idx = None
            col_idx = None
            for i, name in enumerate(matriz.index):
                if s in str(name).upper(): row_idx = i
            for i, name in enumerate(matriz.columns):
                if s in str(name).upper(): col_idx = i
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
                    if s in str(lab).upper(): match = True
                if match:
                    ticktext.append(f"<b><span style='color:#00B4D8'>{lab}</span></b>")
                else:
                    ticktext.append(str(lab))
            return ticktext

        x_ticks = build_ticks(matriz.columns)
        y_ticks = build_ticks(matriz.index)
        
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=10, r=10, t=20, b=10),
            shapes=shapes,
            xaxis=dict(
                title=dict(text=f"{label_ejes} de Destino Laboral (Donde Cotiza)", font=dict(size=17), standoff=20), 
                tickmode='array', tickvals=list(matriz.columns), ticktext=x_ticks,
                tickfont=dict(size=12), tickangle=-90
            ),
            yaxis=dict(
                title=dict(text=f"{label_ejes} de Origen Académico (Donde Graduó)", font=dict(size=17), standoff=20), 
                tickmode='array', tickvals=list(matriz.index), ticktext=y_ticks,
                tickfont=dict(size=12)
            )
        )
        return fig

    @render_widget
    def plot_mobility_matrix():
        return calc_plot_mobility_matrix()

    @reactive.calc
    def calc_table_graduados():
        divipolas = valid_divipolas()
        return create_gender_table(df_graduados, divipolas, "graduados_sum")

    @render.data_frame
    def table_graduados():
        df_pd = calc_table_graduados()
        return render.DataGrid(df_pd, filters=False, width="100%", selection_mode="none")

    @reactive.calc
    def calc_plot_primer_curso_total():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_pcurso.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by("anno").agg(pl.col("primer_curso_sum").sum()).sort("anno")
        if df_filtered.height == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="primer_curso_sum", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Primer Curso", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_primer_curso_total():
        return calc_plot_primer_curso_total()

    @reactive.calc
    def calc_plot_primer_curso():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_pcurso.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by(["anno", "sexo"]).agg(pl.col("primer_curso_sum").sum()).sort(["sexo", "anno"])
        if df_filtered.height == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="primer_curso_sum", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
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

    @render_widget
    def plot_primer_curso():
        return calc_plot_primer_curso()

    @reactive.calc
    def calc_plot_matriculados_total():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_matricula.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by("anno").agg(pl.col("matricula_sum").sum()).sort("anno")
        if df_filtered.height == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="matricula_sum", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Matriculados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_matriculados_total():
        return calc_plot_matriculados_total()

    @reactive.calc
    def calc_plot_matriculados():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_matricula.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by(["anno", "sexo"]).agg(pl.col("matricula_sum").sum()).sort(["sexo", "anno"])
        if df_filtered.height == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="matricula_sum", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
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

    @render_widget
    def plot_matriculados():
        return calc_plot_matriculados()

    @reactive.calc
    def calc_plot_graduados_total():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_graduados.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by("anno").agg(pl.col("graduados_sum").sum()).sort("anno")
        if df_filtered.height == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="graduados_sum", markers=True)
        fig.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text="Graduados", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_graduados_total():
        return calc_plot_graduados_total()

    @reactive.calc
    def calc_plot_graduados():
        divipolas = valid_divipolas()
        if len(divipolas) == 0: return go.Figure()
        df_filtered = df_graduados.filter(pl.col("snies_divipola").is_in(divipolas) & (pl.col("anno") >= 2016)).group_by(["anno", "sexo"]).agg(pl.col("graduados_sum").sum()).sort(["sexo", "anno"])
        if df_filtered.height == 0: return go.Figure()
        fig = px.line(df_filtered.to_pandas(), x="anno", y="graduados_sum", color="sexo", color_discrete_map=COLOR_SEXO, markers=True)
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

    @render_widget
    def plot_graduados():
        return calc_plot_graduados()

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
        if df.height == 0: return pl.DataFrame()
        return df.filter(pl.col("anno") == max_anno_saber)

    def calc_saber_score(column):
        df = filtered_saber_latest()
        if df.height == 0: return "Sin dato"
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
        if df.height == 0: return go.Figure()
        
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
        
        fig = px.line(df_plot, x="anno", y="Puntaje", color="Componente", markers=True)
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Puntaje Promedio", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_saber_trend():
        return calc_plot_saber_trend()

    @reactive.calc
    def calc_plot_saber_dist():
        df = filtered_saber_latest()
        if df.height == 0: return go.Figure()
        
        # Agregación por programa académico
        agg = df.group_by("codigo_snies_del_programa").agg(pl.col("pro_gen_punt_global").mean())
        df_pd = agg.to_pandas()
        
        fig = px.histogram(df_pd, x="pro_gen_punt_global", histnorm='percent', nbins=100)
        fig.update_traces(marker_color="#31497e", marker_line_color="white", marker_line_width=1, xbins=dict(size=1))
        
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Puntaje Global Promedio", gridcolor='#EEEEEE'),
            yaxis=dict(title="Participación de Programas (%)", ticksuffix="%", gridcolor='#EEEEEE')
        )
        return fig

    @render_widget
    def plot_saber_dist():
        return calc_plot_saber_dist()

    # --- Conteos Demográficos ---
    @reactive.calc
    def calc_plot_saber_count_sexo():
        df = filtered_saber()
        if df.height == 0: return go.Figure()
        trend = df.group_by(["anno", "sexo"]).len().sort(["anno", "sexo"])
        fig = px.line(trend.to_pandas(), x="anno", y="len", color="sexo", markers=True)
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
                        xaxis=dict(title="Año", tickmode="linear"), yaxis=dict(title="Número de Evaluados", gridcolor='#EEEEEE'))
        return fig

    @render_widget
    def plot_saber_count_sexo(): return calc_plot_saber_count_sexo()

    @reactive.calc
    def calc_plot_saber_count_edad():
        df = filtered_saber()
        if df.height == 0: return go.Figure()
        trend = df.group_by(["anno", "grupo_edad"]).len().sort(["anno", "grupo_edad"])
        fig = px.line(trend.to_pandas(), x="anno", y="len", color="grupo_edad", markers=True)
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
                        xaxis=dict(title="Año", tickmode="linear"), yaxis=dict(title="Número de Evaluados", gridcolor='#EEEEEE'))
        return fig

    @render_widget
    def plot_saber_count_edad(): return calc_plot_saber_count_edad()

    # --- Ayudante de Tendencias Detalladas ---
    def calc_plot_saber_trend_dim(column, dim):
        df = filtered_saber()
        if df.height == 0: return go.Figure()
        
        # Agregación por año, programa y dimensión
        agg = df.group_by(["anno", "codigo_snies_del_programa", dim]).agg(pl.col(column).mean())
        # Tendencia final de promedios
        trend = agg.group_by(["anno", dim]).agg(pl.col(column).mean()).sort(["anno", dim])
        
        fig = px.line(trend.to_pandas(), x="anno", y=column, color=dim, markers=True)
        fig.update_traces(line=dict(width=3), marker=dict(size=8, color="white", line=dict(width=2)))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(title="Año", tickmode="linear", gridcolor='#EEEEEE'),
            yaxis=dict(title="Puntaje", gridcolor='#EEEEEE'),
            legend_title=dim.capitalize()
        )
        return fig

    # Global
    @render_widget
    def plot_saber_trend_global_sexo(): return calc_plot_saber_trend_dim("pro_gen_punt_global", "sexo")
    @render_widget
    def plot_saber_trend_global_edad(): return calc_plot_saber_trend_dim("pro_gen_punt_global", "grupo_edad")
    # Razonamiento
    @render_widget
    def plot_saber_trend_razona_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "sexo")
    @render_widget
    def plot_saber_trend_razona_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "grupo_edad")
    # Lectura
    @render_widget
    def plot_saber_trend_lectura_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "sexo")
    @render_widget
    def plot_saber_trend_lectura_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "grupo_edad")
    # Ciudadanas
    @render_widget
    def plot_saber_trend_ciuda_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "sexo")
    @render_widget
    def plot_saber_trend_ciuda_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "grupo_edad")
    # Inglés
    @render_widget
    def plot_saber_trend_ingles_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "sexo")
    @render_widget
    def plot_saber_trend_ingles_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "grupo_edad")
    # Escrita
    @render_widget
    def plot_saber_trend_escrita_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "sexo")
    @render_widget
    def plot_saber_trend_escrita_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "grupo_edad")

    # --- Socio-demografía (SaberPRO) ---
    def calc_plot_saber_categorical(column, label, sort_vals=False):
        df = filtered_saber_latest()
        if df.height == 0: return go.Figure()
        
        # Limpieza básica
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

    @render_widget
    def plot_saber_demo_sexo(): return calc_plot_saber_categorical("sexo", "Sexo")
    @render_widget
    def plot_saber_demo_edad(): return calc_plot_saber_categorical("grupo_edad", "Grupo de Edad")
    @render_widget
    def plot_saber_demo_trabajo(): return calc_plot_saber_categorical("pro_gen_estu_horassemanatrabaja", "Horas de Trabajo")
    @render_widget
    def plot_saber_demo_estrato(): return calc_plot_saber_categorical("pro_gen_fami_estratovivienda", "Estrato Vivienda", sort_vals=True)

    # Tendencias Socio-demográficas
    def calc_plot_saber_categorical_trend(column, label):
        df = filtered_saber()
        if df.height == 0: return go.Figure()
        
        # Limpieza básica
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
        fig = px.line(df_pd, x="anno", y="participacion", color=column, markers=True)
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

    @render_widget
    def plot_saber_demo_sexo_trend(): return calc_plot_saber_categorical_trend("sexo", "Sexo")
    @render_widget
    def plot_saber_demo_edad_trend(): return calc_plot_saber_categorical_trend("grupo_edad", "Grupo Edad")
    @render_widget
    def plot_saber_demo_trabajo_trend(): return calc_plot_saber_categorical_trend("pro_gen_estu_horassemanatrabaja", "Trabajo")
    @render_widget
    def plot_saber_demo_estrato_trend(): return calc_plot_saber_categorical_trend("pro_gen_fami_estratovivienda", "Estrato")

    # KPIs Socio-demográficos
    @reactive.calc
    def calc_total_evaluados_saber():
        df = filtered_saber_latest()
        return df.height

    @render.ui
    def kpi_demo_evaluados():
        val = calc_total_evaluados_saber()
        return ui.HTML(f"<div style='font-size: 48px; font-weight: bold; color: #31497e;'>{format_num_es(val)}</div>")

    @reactive.calc
    def calc_total_programas_saber():
        df = filtered_saber_latest()
        if df.height == 0: return 0
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
        if df_base.height == 0: return None
        
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
            "departamento_oferta": df_cob["departamento_oferta"][0] if df_cob.height > 0 else None
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

    @reactive.calc
    def comparable_snies_list():
        """Devuelve la lista de divipolas/snies que forman el grupo comparable."""
        attr = comp_profile_attr()
        if not attr: return []
        
        criterios = input.comp_criterios() or []
        
        # Empezamos con todos los SNIES (sin aplicar los filtros globales del sidebar)
        df_comp = df_snies
        df_cob_comp = df_cobertura
        
        # Aplicamos los filtros locales basados en checkboxes marcados
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
            
        # Filtrar por defecto solo por programas en estado ACTIVO
        df_comp = df_comp.filter(pl.col("estado_programa") == "ACTIVO")

        # Filtro de Cobertura (Depto) para Divipolas
        valid_snies = df_comp["codigo_snies_del_programa"].unique()
        df_cob_comp = df_cob_comp.filter(pl.col("codigo_snies_del_programa").is_in(valid_snies))
        
        if "departamento_oferta" in criterios and attr.get("departamento_oferta"):
            df_cob_comp = df_cob_comp.filter(pl.col("departamento_oferta") == attr["departamento_oferta"])
            
        return df_cob_comp["snies_divipola"].unique().to_list()

    @reactive.calc
    def comparable_snies_codigos():
        """Devuelve la lista de codigos SNIES que forman el grupo comparable (para OLE)."""
        attr = comp_profile_attr()
        if not attr: return []
        
        criterios = input.comp_criterios() or []
        df_comp = df_snies
        df_cob_comp = df_cobertura
        
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

        valid_snies = df_comp["codigo_snies_del_programa"].unique()
        df_cob_comp = df_cob_comp.filter(pl.col("codigo_snies_del_programa").is_in(valid_snies))
        
        if "departamento_oferta" in criterios and attr.get("departamento_oferta"):
            df_cob_comp = df_cob_comp.filter(pl.col("departamento_oferta") == attr["departamento_oferta"])
            
        return df_cob_comp["codigo_snies_del_programa"].unique().to_list()
        # De esta forma, si configuras un grupo idéntico a lo que harías en el sidebar,
        # la masa total y los elementos cuadran de manera exacta.
        # Retornamos los códigos Divipola unicos para el filtro de dfs (pcurso, matriculados)
        return df_cob_comp["snies_divipola"].unique().to_list()

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
                mode='lines+markers',
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
                mode='lines+markers',
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
    @render_widget
    def plot_comp_pcurso():
        df_base, df_comp = calc_comp_metric(df_pcurso, "primer_curso_sum")
        return build_comp_plot(df_base, df_comp, "Primer Curso")

    @render_widget
    def plot_comp_matricula():
        df_base, df_comp = calc_comp_metric(df_matricula, "matricula_sum")
        return build_comp_plot(df_base, df_comp, "Matriculados")

    @render_widget
    def plot_comp_graduados():
        df_base, df_comp = calc_comp_metric(df_graduados, "graduados_sum")
        return build_comp_plot(df_base, df_comp, "Graduados")

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
        
        if not df_comp_pd.empty:
            y_lower = (df_comp_pd["valor_comp_mean"] - df_comp_pd["valor_comp_std"]).clip(lower=0) 
            y_upper = (df_comp_pd["valor_comp_mean"] + df_comp_pd["valor_comp_std"]).clip(upper=1)
            
            fig.add_trace(go.Scatter(x=df_comp_pd["anno"], y=y_lower, marker=dict(color="#444"), line=dict(width=0), mode='lines', showlegend=False, hoverinfo='skip'))
            fig.add_trace(go.Scatter(x=df_comp_pd["anno"], y=y_upper, marker=dict(color="#444"), line=dict(width=0), mode='lines', fillcolor=color_band, fill='tonexty', name='Dispersión (Media ± 1 Std. Dev)', hoverinfo='skip'))
            
            fig.add_trace(go.Scatter(
                x=df_comp_pd["anno"],
                y=df_comp_pd["valor_comp_mean"],
                mode='lines+markers',
                name='Media Comparable',
                line=dict(color=color_comp, width=3, dash='dash'),
                marker=dict(size=8, color="white", line=dict(width=2, color=color_comp)),
                hovertemplate="Año: %{x}<br>Media: %{y:.1%}<br>N: %{customdata} prog.<extra></extra>",
                customdata=df_comp_pd["n_programas"]
            ))

        if not df_base_pd.empty:
            attr = comp_profile_attr()
            prog_name = f"SNIES {attr['codigo']}" if attr else "Prog. Base"
            fig.add_trace(go.Scatter(
                x=df_base_pd["anno"],
                y=df_base_pd["valor_base"],
                mode='lines+markers',
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
                mode='lines+markers',
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
                mode='lines+markers',
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

    @render_widget
    def plot_comp_ole_empleabilidad():
        df_base, df_comp = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
        return build_comp_plot_ole(df_base, df_comp, "Tasa de Empleabilidad")

    @render_widget
    def plot_comp_ole_dependientes():
        df_base, df_comp = calc_comp_ole_metric("graduados_cotizantes_dependientes", "graduados_que_cotizan")
        return build_comp_plot_ole(df_base, df_comp, "Dependientes sobre Cotizantes")

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
            
            if ole_nivel.height > 0:
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
            if ole_comp.height > 0:
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
        fig.update_traces(xbins=dict(start=0.0, end=1.0, size=0.05), marker_line_width=1, marker_line_color="white", opacity=0.8)
        
        # Agregar línea del programa base
        attr = comp_profile_attr()
        if attr:
            df_base, _ = calc_comp_ole_metric("graduados_que_cotizan", "graduados")
            if not df_base.empty:
                tasa_base = df_base["valor_base"].iloc[-1]
                fig.add_vline(x=tasa_base, line_width=3, line_dash="dash", line_color="#31497e", 
                              annotation_text=f"Prog. Seleccionado<br>({tasa_base:.1%})", annotation_position="top",
                              annotation_font_color="#31497e")
                          
        fig.update_layout(
            legend_title_text="",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(title="Tasa de Empleabilidad", tickformat=".0%", dtick=0.05, gridcolor='#EEEEEE', automargin=True),
            yaxis=dict(title="Porcentaje de Programas (%)", ticksuffix="%", gridcolor='#EEEEEE', automargin=True),
            legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5)
        )
        # Format tooltips
        fig.update_traces(hovertemplate='Tasa: %{x}<br>Frecuencia: %{y:.1f}%<extra></extra>')
        return fig

    @render_widget
    def plot_comp_dist_empleabilidad():
        return calc_plot_comp_dist_empleabilidad()

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
        
        df_base = df_ole_salario.filter(pl.col("codigo_snies_del_programa") == attr["codigo"])
        comp_codigos = comparable_snies_codigos()
        
        if len(comp_codigos) == 0:
            df_comp = pd.DataFrame()
        else:
            df_comp = df_ole_salario.filter(pl.col("codigo_snies_del_programa").is_in(comp_codigos))
            
        def process_salary_df(df_in):
            if df_in.height == 0: return pl.DataFrame()
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
            agg_prog = d.group_by(["anno_corte", "codigo_snies_del_programa"]).agg([
                ((pl.col("midpoint") * pl.col("graduados_cotizantes_dependientes")).sum() / 
                 pl.col("graduados_cotizantes_dependientes").sum() * pl.col("smmlv_calc").first()).alias("sal_prog")
            ]).filter(pl.col("sal_prog").is_not_null())
            return agg_prog

        base_prog = process_salary_df(df_base)
        if base_prog.height > 0:
            df_base_pd = base_prog.group_by("anno_corte").agg(pl.col("sal_prog").mean().alias("valor_base")).sort("anno_corte").to_pandas()
            df_base_pd["anno"] = df_base_pd["anno_corte"]
        else:
            df_base_pd = pd.DataFrame()

        comp_prog = process_salary_df(df_comp)
        if comp_prog.height > 0:
            df_comp_pd = comp_prog.group_by("anno_corte").agg([
                pl.col("sal_prog").mean().alias("valor_comp_mean"),
                pl.col("sal_prog").std().alias("valor_comp_std"),
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
            if base_pl.height > 0:
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
            if comp_pl.height > 0:
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
    def calc_plot_comp_salario_dist():
        import plotly.graph_objects as go
        df = calc_comp_salario_dist_data()
        if df.empty: return go.Figure()
        
        fig = px.bar(df, x="porcentaje", y="rango_salario", color="grupo", orientation='h', barmode='group', 
                     color_discrete_map={"Programa Seleccionado": "#31497e", "Grupo Comparable": "#674f95"}, 
                     text_auto='.1%')
        fig.update_traces(marker_line_width=1.5, marker_line_color="white")
        fig.update_layout(
            legend_title_text="",
            plot_bgcolor='white', paper_bgcolor='white', margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(title="Participación de Graduados (%)", tickformat=".0%", gridcolor='#EEEEEE'),
            yaxis=dict(title="", tickfont=dict(size=13)),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        return fig

    @render_widget
    def plot_comp_salario_dist():
        return calc_plot_comp_salario_dist()
        
    @render_widget
    def plot_comp_salario_evolucion():
        df_base, df_comp = calc_comp_salario_evolucion()
        return build_comp_plot_salario(df_base, df_comp, "Salario Promedio de Enganche")

    # KPIs
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
                ("Tasa Empleabilidad", calc_kpi_empleabilidad()),
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
                ("Puntaje Global Promedio", calc_saber_score("pro_gen_punt_global"))
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
    def prev_kpi_pcurso(): return wrap_kpi(calc_total_primer_curso())
    @render.ui
    def prev_kpi_matriculados(): return wrap_kpi(calc_total_matriculados())
    @render.ui
    def prev_kpi_graduados(): return wrap_kpi(calc_total_graduados())
    @render.ui
    def prev_kpi_emp(): return wrap_kpi(calc_kpi_empleabilidad())
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
    def prev_kpi_evaluados(): return wrap_kpi(format_num_es(calc_total_evaluados_saber()))
    @render.ui
    def prev_kpi_progs_saber(): return wrap_kpi(format_num_es(calc_total_programas_saber()))

    @render_widget
    def prev_pcurso_total(): return calc_plot_primer_curso_total()
    @render_widget
    def prev_matricula_total(): return calc_plot_matriculados_total()
    @render_widget
    def prev_graduados_total(): return calc_plot_graduados_total()

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
        return ui.HTML(f"Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia<br>Para el último año se reportan {nb} No binarios y {tr} trans, pero no se muestran en la gráfica.")

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

    @render_widget
    def prev_pcurso_sexo(): return __filter_gender_fig(calc_plot_primer_curso())
    @render_widget
    def prev_matricula_sexo(): return __filter_gender_fig(calc_plot_matriculados())
    @render_widget
    def prev_graduados_sexo(): return __filter_gender_fig(calc_plot_graduados())
    @render_widget
    def prev_ole_emp_total(): return calc_plot_empleabilidad_total()
    @render_widget
    def prev_ole_dep_total(): return calc_plot_dependientes_total()
    @render_widget
    def prev_ole_emp_sexo(): return calc_plot_empleabilidad_sexo()
    @render_widget
    def prev_ole_dep_sexo(): return calc_plot_dependientes_sexo()
    @render_widget
    def prev_ole_dist_emp(): return calc_plot_dist_empleabilidad()
    @render_widget
    def prev_ole_dist_dep(): return calc_plot_dist_dependientes()
    @render_widget
    def prev_ole_dist_emp_sexo(): return calc_plot_dist_empleabilidad_sexo()
    @render_widget
    def prev_ole_dist_dep_sexo(): return calc_plot_dist_dependientes_sexo()
    @render_widget
    def prev_ole_mobility(): return calc_plot_mobility_matrix()
    @render_widget
    def prev_sal_dist_total(): return calc_plot_salario_dist_total()
    @render_widget
    def prev_sal_dist_sexo(): return calc_plot_salario_dist_sexo()
    @render_widget
    def prev_sal_evol_total(): return calc_plot_salario_evolucion_total()
    @render_widget
    def prev_des_dist(): return calc_plot_dist_desercion()
    @render_widget
    def prev_des_trend(): return calc_plot_trend_desercion()
    @render_widget
    def prev_saber_trend(): return calc_plot_saber_trend()
    @render_widget
    def prev_saber_dist(): return calc_plot_saber_dist()
    @render_widget
    def prev_saber_count_sexo(): return calc_plot_saber_count_sexo()
    @render_widget
    def prev_saber_count_edad(): return calc_plot_saber_count_edad()
    @render_widget
    def prev_demo_sexo(): return calc_plot_saber_categorical("sexo", "Sexo")
    @render_widget
    def prev_demo_edad(): return calc_plot_saber_categorical("grupo_edad", "Edad")
    @render_widget
    def prev_demo_trabajo(): return calc_plot_saber_categorical("pro_gen_estu_horassemanatrabaja", "Trabajo")
    @render_widget
    def prev_demo_estrato(): return calc_plot_saber_categorical("pro_gen_fami_estratovivienda", "Estrato")
    @render_widget
    def prev_demo_sexo_trend(): return calc_plot_saber_categorical_trend("sexo", "Sexo")
    @render_widget
    def prev_demo_edad_trend(): return calc_plot_saber_categorical_trend("grupo_edad", "Edad")

    # Missing render functions mapping
    @render_widget
    def prev_ole_dist_emp(): return calc_plot_dist_empleabilidad()
    @render_widget
    def prev_ole_dist_dep(): return calc_plot_dist_dependientes()
    @render_widget
    def prev_ole_dist_emp_sexo(): return calc_plot_dist_empleabilidad_sexo()
    @render_widget
    def prev_ole_dist_dep_sexo(): return calc_plot_dist_dependientes_sexo()
    @render_widget
    def prev_ole_trend_dep(): return calc_plot_dependientes_trend()
    @render_widget
    def prev_ole_trend_ret(): return calc_plot_retencion_trend()
    @render_widget
    def prev_ole_trend_ratio(): return calc_plot_ratio_trend()

    @render_widget
    def prev_sal_evol_sexo(): return calc_plot_salario_evolucion_sexo()
    @render_widget
    def prev_sal_evol_constante(): return calc_plot_salario_evolucion_total_constante()
    @render_widget
    def prev_sal_evol_sexo_constante(): return calc_plot_salario_evolucion_sexo_constante()

    @render_widget
    def prev_saber_trend_global_sexo(): return calc_plot_saber_trend_dim("pro_gen_punt_global", "sexo")
    @render_widget
    def prev_saber_trend_global_edad(): return calc_plot_saber_trend_dim("pro_gen_punt_global", "grupo_edad")
    @render_widget
    def prev_saber_trend_razona_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "sexo")
    @render_widget
    def prev_saber_trend_razona_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_razona_cuantitat_punt", "grupo_edad")
    @render_widget
    def prev_saber_trend_lectura_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "sexo")
    @render_widget
    def prev_saber_trend_lectura_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_lectura_critica_punt", "grupo_edad")
    @render_widget
    def prev_saber_trend_ciuda_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "sexo")
    @render_widget
    def prev_saber_trend_ciuda_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_competen_ciudada_punt", "grupo_edad")
    @render_widget
    def prev_saber_trend_ingles_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "sexo")
    @render_widget
    def prev_saber_trend_ingles_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_ingles_punt", "grupo_edad")
    @render_widget
    def prev_saber_trend_escrita_sexo(): return calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "sexo")
    @render_widget
    def prev_saber_trend_escrita_edad(): return calc_plot_saber_trend_dim("pro_gen_mod_comuni_escrita_punt", "grupo_edad")

    @render_widget
    def prev_demo_trabajo_trend(): return calc_plot_saber_categorical_trend("pro_gen_estu_horassemanatrabaja", "Trabajo")
    @render_widget
    def prev_demo_estrato_trend(): return calc_plot_saber_categorical_trend("pro_gen_fami_estratovivienda", "Estrato")

    @render.download(filename=lambda: f"Informe_Educacion_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    def download_pdf():
        with ui.Progress(min=1, max=15) as p:
            p.set(message="Iniciando generación de informe...", detail="Preparando motor de reportes")
            engine = ReportEngine(app_dir)
            try:
                data_ctx = _prepare_report_content(engine, p)
                p.set(15, message="Compilando informe...", detail="Cerrando archivos y generando PDF final")
                pdf_path = engine.generate_report(data_ctx)
                return pdf_path
            finally:
                # El motor se encarga de limpiar los temporales
                # engine.cleanup() 
                pass

app = App(app_ui, server, static_assets={"/temp_report": app_dir / "temp_report"})

import os
import base64
import json
import plotly.express as px
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "data_ANTIOQUIA.js"

def fig_to_base64(fig):
    img_bytes = fig.to_image(format="png", engine="kaleido", scale=2)
    return base64.b64encode(img_bytes).decode('utf-8')

def export_dummy_antioquia():
    print("Generando datos dummy (Sección 1 Completa) para ANTIOQUIA...")
    
    # Datos base para gráficos totales
    df_trend = pd.DataFrame({
        "Año": [2018, 2019, 2020, 2021, 2022, 2023],
        "Primer Curso": [12000, 12500, 11000, 13000, 14000, 14500],
        "Matriculados": [45000, 47500, 46000, 48000, 51000, 52300],
        "Graduados": [8000, 8500, 9000, 8200, 9500, 10200]
    })
    
    COLOR_SEXO = {"Femenino": "#db4a39", "Masculino": "#0097a8"}
    
    # helper de estilo
    def apply_style(fig, title_y="Cantidad"):
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17), standoff=20), tickfont=dict(size=15), tickmode="linear", automargin=True, showgrid=True, gridcolor='#EEEEEE'),
            yaxis=dict(title=dict(text=title_y, font=dict(size=17), standoff=20), tickfont=dict(size=15), tickformat=",.0f", automargin=True, showgrid=True, gridcolor='#EEEEEE')
        )
        return fig

    # 1. Total Primer Curso
    fig_pc_t = px.line(df_trend, x="Año", y="Primer Curso", title="Tendencia: Primer Curso", markers=True)
    fig_pc_t = apply_style(fig_pc_t, "Primer Curso")
    fig_pc_t.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
    
    # 2. Total Matriculados
    fig_ma_t = px.line(df_trend, x="Año", y="Matriculados", title="Tendencia: Matriculados", markers=True)
    fig_ma_t = apply_style(fig_ma_t, "Matriculados")
    fig_ma_t.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))

    # 3. Total Graduados
    fig_gr_t = px.line(df_trend, x="Año", y="Graduados", title="Tendencia: Graduados", markers=True)
    fig_gr_t = apply_style(fig_gr_t, "Graduados")
    fig_gr_t.update_traces(marker=dict(size=9, color="white", line=dict(width=1.5, color="#31497e")), line=dict(width=2, color="#31497e"))
    
    # Datos base para gráficos por sexo
    df_sexo = pd.DataFrame({
        "Año": [2018, 2018, 2019, 2019, 2020, 2020, 2021, 2021, 2022, 2022, 2023, 2023],
        "Sexo": ["Femenino", "Masculino"] * 6,
        "Primer Curso": [6500, 5500, 6800, 5700, 6000, 5000, 7000, 6000, 7500, 6500, 7800, 6700],
        "Matriculados": [24000, 21000, 25500, 22000, 25000, 21000, 26000, 22000, 27500, 23500, 28000, 24300],
        "Graduados": [4300, 3700, 4600, 3900, 4800, 4200, 4500, 3700, 5200, 4300, 5800, 4400]
    })
    
    def apply_sex_style(fig):
        fig.update_traces(marker=dict(size=9), line=dict(width=2))
        for trace in fig.data:
            trace.marker.line.color = trace.line.color
            trace.marker.line.width = 1.5
            trace.marker.color = 'white'
        fig.update_layout(legend_title_text="Sexo", legend=dict(orientation="h", y=-0.2))
        return fig
    
    # 4. Primer Curso por Sexo
    fig_pc_s = px.line(df_sexo, x="Año", y="Primer Curso", color="Sexo", title="Primer Curso por Sexo", color_discrete_map=COLOR_SEXO, markers=True)
    fig_pc_s = apply_sex_style(apply_style(fig_pc_s, "Primer Curso"))

    # 5. Matriculados por Sexo
    fig_ma_s = px.line(df_sexo, x="Año", y="Matriculados", color="Sexo", title="Matriculados por Sexo", color_discrete_map=COLOR_SEXO, markers=True)
    fig_ma_s = apply_sex_style(apply_style(fig_ma_s, "Matriculados"))

    # 6. Graduados por Sexo
    fig_gr_s = px.line(df_sexo, x="Año", y="Graduados", color="Sexo", title="Graduados por Sexo", color_discrete_map=COLOR_SEXO, markers=True)
    fig_gr_s = apply_sex_style(apply_style(fig_gr_s, "Graduados"))


    report_data = {
        "metadata": {
            "title": "Informe de Mercado de Educación Superior",
            "subtitle": "DEPARTAMENTO: ANTIOQUIA",
            "date": "Abril 2026",
            "scope": {
                "type": "aggregate",
                "description": "Filtros aplicados para el Departamento de ANTIOQUIA. Cobertura de todas las IES y Programas activos en la región."
            }
        },
        "kpis": {
            "instituciones": "152",
            "programas": "1.489",
            "pcurso": "14.500",
            "matriculados": "52.300",
            "graduados": "10.200",
        },
        "snies": {
            "technical_note": "La tendencia muestra la evolución histórica de acuerdo con los registros del SNIES para el área seleccionada y asume que el reporte captura la distribución estandarizada de todos los programas bajo la categoría de 'Activos'.",
            "plots": [
                {
                    "id": "t1",
                    "title": "Tendencia Total de Estudiantes de Primer Curso",
                    "b64": fig_to_base64(fig_pc_t),
                    "caption": "Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"
                },
                {
                    "id": "t2",
                    "title": "Tendencia Total de Estudiantes Matriculados",
                    "b64": fig_to_base64(fig_ma_t),
                    "caption": "Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"
                },
                {
                    "id": "t3",
                    "title": "Tendencia Total de Estudiantes Graduados",
                    "b64": fig_to_base64(fig_gr_t),
                    "caption": "Fuente: Ministerio de Educación Nacional (SNIES)<br>Elaboración propia"
                },
                {
                    "id": "s1",
                    "title": "Tendencia por Sexo (Primer Curso)",
                    "b64": fig_to_base64(fig_pc_s),
                    "caption": "Frecuencia por género reportada. Fuente: SNIES."
                },
                {
                    "id": "s2",
                    "title": "Tendencia por Sexo (Matriculados)",
                    "b64": fig_to_base64(fig_ma_s),
                    "caption": "Matrícula total desglosada por Femenino/Masculino. Fuente: SNIES."
                },
                {
                    "id": "s3",
                    "title": "Tendencia por Sexo (Graduados)",
                    "b64": fig_to_base64(fig_gr_s),
                    "caption": "Frecuencia histórica de titulación por género. Fuente: SNIES."
                }
            ]
        }
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.__REPORT_DATA__ = ")
        json.dump(report_data, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"Reporte Dummy SECCIÓN 1 generado en: {OUTPUT_FILE}")

if __name__ == "__main__":
    export_dummy_antioquia()

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
    print("Generando datos dummy para ANTIOQUIA (Secciones 1-6)...")
    
    df_trend = pd.DataFrame({
        "Año": [2018, 2019, 2020, 2021, 2022, 2023],
        "Primer Curso": [12000, 12500, 11000, 13000, 14000, 14500],
        "Matriculados": [45000, 47500, 46000, 48000, 51000, 52300],
        "Graduados": [8000, 8500, 9000, 8200, 9500, 10200]
    })
    
    def apply_style(fig, title_y="Cantidad"):
        fig.update_layout(
            plot_bgcolor='white', paper_bgcolor='white', separators=",.",
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis=dict(title=dict(text="Año", font=dict(size=17)), tickfont=dict(size=14)),
            yaxis=dict(title=dict(text=title_y, font=dict(size=17)), tickfont=dict(size=14), tickformat=",.0f")
        )
        return fig

    # Gráficos base
    fig_line = apply_style(px.line(df_trend, x="Año", y="Primer Curso", title="Tendencia"))
    fig_bar = apply_style(px.bar(x=["A", "B", "C"], y=[10, 20, 30], title="Distribución"))
    
    report_data = {
        "metadata": {
            "title": "Informe de Mercado de Educación Superior",
            "subtitle": "DEPARTAMENTO: ANTIOQUIA",
            "date": "Abril 2026",
            "year_start": 2018, "year_end": 2024,
            "source_years": {"snies": "2024", "ole": "2022", "spadies": "2023", "icfes": "2024"}
        },
        "kpis": {
            "instituciones": "152", "programas": "1.489", "pcurso": "14.500", "matriculados": "52.300", "graduados": "10.200",
            "vinculacion": "86.4%", "salario": "4.2 SMMLV", "retencion": "12.8%", "saber_global": "158.4",
            "sal_promedio": "$3.850.000", "sal_femenino": "$3.420.000", "sal_masculino": "$4.180.000",
            "des_rate": "12.8%",
            "s_global": "158.4", "s_razona": "162.1", "s_lectura": "155.8", "s_ciuda": "152.3", "s_ingles": "165.4", "s_escrita": "148.9",
            "evaluados": "24.500", "progs_saber": "452"
        },
        "snies": {
            "technical_note": "Fuente: SNIES.",
            "plots": [
                {"id": "t1", "title": "PC", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "t2", "title": "MA", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "t3", "title": "GR", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "s1", "title": "PC Sexo", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "s2", "title": "MA Sexo", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "s3", "title": "GR Sexo", "b64": fig_to_base64(fig_line), "caption": ""}
            ]
        },
        "ole": {
            "technical_note": "Fuente OLE.",
            "plots": [
                {"id": i, "title": f"OLE {i}", "b64": fig_to_base64(fig_line), "caption": ""} for i in ["o1", "o2", "o3", "o4", "o5", "o6", "o7", "o8", "o10", "o11", "o12"]
            ]
        },
        "salarios": {
            "technical_note": "Fuente OLE.",
            "plots": [
                {"id": i, "title": f"SAL {i}", "b64": fig_to_base64(fig_line), "caption": ""} for i in ["v1", "v2", "v5", "v6"]
            ]
        },
        "spadies": {
            "technical_note": "Fuente SPADIES.",
            "plots": [
                {"id": "d1", "title": "Dist", "b64": fig_to_base64(fig_bar), "caption": ""},
                {"id": "d2", "title": "Tend", "b64": fig_to_base64(fig_line), "caption": ""}
            ]
        },
        "saber": {
            "technical_note": "Fuente ICFES.",
            "plots": [
                {"id": f"sb{i}", "title": f"SABER {i}", "b64": fig_to_base64(fig_line), "caption": ""} for i in range(1, 17)
            ]
        },
        "demo": {
            "technical_note": "Fuente ICFES - Perfil Socio-demográfico.",
            "plots": [
                {"id": "pr1", "title": "Sexo", "b64": fig_to_base64(fig_bar), "caption": ""},
                {"id": "pr2", "title": "Edad", "b64": fig_to_base64(fig_bar), "caption": ""},
                {"id": "pr3", "title": "Trabajo", "b64": fig_to_base64(fig_bar), "caption": ""},
                {"id": "pr4", "title": "Estrato", "b64": fig_to_base64(fig_bar), "caption": ""},
                {"id": "pr5", "title": "Sexo Trend", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "pr6", "title": "Edad Trend", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "pr7", "title": "Trabajo Trend", "b64": fig_to_base64(fig_line), "caption": ""},
                {"id": "pr8", "title": "Estrato Trend", "b64": fig_to_base64(fig_line), "caption": ""}
            ]
        }
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.__REPORT_DATA__ = ")
        json.dump(report_data, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"Reporte Dummy SECCIÓN 1-6 generado.")

if __name__ == "__main__":
    export_dummy_antioquia()

import os
import typst
import plotly.express as px
import pandas as pd
from pathlib import Path

# Configuración de rutas
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "temp_report"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def generate_dummy_plots():
    # 1. Gráfico de Tendencia
    df_trend = pd.DataFrame({
        "Año": [2018, 2019, 2020, 2021, 2022],
        "Estudiantes": [100, 120, 110, 130, 150]
    })
    fig1 = px.line(df_trend, x="Año", y="Estudiantes", title="Tendencia de Matrícula")
    fig1.update_layout(margin=dict(l=20, r=20, t=40, b=20))
    fig1.write_image(OUTPUT_DIR / "plot1.png", engine="kaleido")

    # 2. Gráfico de Barras
    df_bar = pd.DataFrame({
        "Categoría": ["A", "B", "C", "D"],
        "Valor": [45, 78, 32, 91]
    })
    fig2 = px.bar(df_bar, x="Categoría", y="Valor", title="Distribución por Área")
    fig2.update_layout(margin=dict(l=20, r=20, t=40, b=20))
    fig2.write_image(OUTPUT_DIR / "plot2.png", engine="kaleido")

    # 3. Gráfico de Pastel
    fig3 = px.pie(values=[400, 500, 300], names=['Bogotá', 'Medellín', 'Cali'], title="Sedes")
    fig3.update_layout(margin=dict(l=20, r=20, t=40, b=20))
    fig3.write_image(OUTPUT_DIR / "plot3.png", engine="kaleido")

def create_typ_content():
    content = f"""
#import "report_template.typ": *

#show: template.with(
  title: "Informe Diagnóstico Sectorial",
  subtitle: "Análisis de Mercado Educación Superior",
  date: "30 de Marzo, 2026",
  logo: "logo_symbiotic.svg"
)

= Resumen Ejecutivo

Este informe presenta un análisis detallado del comportamiento de las instituciones de educación superior en Colombia. Symbiotic, como startup de UNIMINUTO, provee estas herramientas para la transformación digital del sector.

#v(1em)

#kpi_grid(
  kpi_box("Total Programas", "1.245"),
  kpi_box("Matrícula Total", "45.670"),
  kpi_box("Tasa de Empleabilidad", "82,5%")
)

#v(1em)

#technical_note([
  Los indicadores presentados se basan en el cruce de bases de datos oficiales del SNIES y el OLE. La tasa de empleabilidad se calcula sobre el total de graduados que registran cotización al sistema de seguridad social.
])

= Tendencias SNIES

== Evolución Institucional

La tendencia muestra un crecimiento sostenido en la oferta académica de nivel profesional. A continuación se presentan las visualizaciones clave del comportamiento histórico.

#v(1em)

#grid(
  columns: (1fr, 1fr),
  gutter: 10pt,
  image("temp_report/plot1.png", width: 100%),
  image("temp_report/plot2.png", width: 100%)
)

#v(1em)

== Distribución Territorial

La concentración de la oferta académica en las principales áreas metropolitanas sigue siendo un factor determinante en el acceso a la educación superior.

#align(center, image("temp_report/plot3.png", width: 60%))

= Observatorio Laboral

== Tasa de Cotización

La relación entre graduados y cotizantes activos en el mercado laboral formal permite identificar la pertinencia de los programas académicos.

#technical_note([
  Las notas técnicas aquí explican la metodología de cálculo de los salarios de enganche y la movilidad inter-departamental.
])

#v(2em)

#align(center, text(10pt, gray, [--- Fin del Informe ---]))
"""
    with open(BASE_DIR / "report_instance.typ", "w", encoding="utf-8") as f:
        f.write(content)

def main():
    print("Generando gráficos dummy...")
    generate_dummy_plots()
    
    print("Creando contenido Typst...")
    create_typ_content()
    
    print("Compilando PDF...")
    try:
        typst.compile(str(BASE_DIR / "report_instance.typ"), output=str(BASE_DIR / "informe_dummy_symbiotic.pdf"))
        print(f"¡Éxito! El informe se ha generado en: {BASE_DIR / 'informe_dummy_symbiotic.pdf'}")
    except Exception as e:
        print(f"Error al compilar Typst: {e}")

if __name__ == "__main__":
    main()

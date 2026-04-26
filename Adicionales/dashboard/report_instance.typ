
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

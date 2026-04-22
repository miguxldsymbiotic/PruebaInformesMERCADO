# Resolviendo los KPIs '0' y gráficos vacíos en la Sección 5 (Saber Pro)

## El Problema Original
Al generar el PDF del "Informe de Mercado V2", las gráficas a partir de las tendencias por Sexo y Edad en la sección 5 (Saber Pro) no se mostraban. Adicional a esto, **absolutamente todos los KPIs de la evaluación** (Puntaje Global, Razonamiento, Lectura, Ciudadanía, etc.) renderizaban estáticamente el valor `"0"`.

## Diagnóstico y Análisis

1.  **Investigación Backend (`app.py`):** Inicialmente analizamos la función encartada de capturar los datos (`calc_saber_score`). Encontramos que las variables de retención no estaban devolviendo directamente `0`, y si no había datos arrojaban una alerta visual de `"Sin dato"`. Tampoco había errores en consola procedentes del servidor Python al presionar "Generar Informe".
2.  **Mapeo PDF vs UI UI:** Identificamos que las funciones que se enviaban para llenar las gráficas `sb1`, `sb2`, `sb3` y `sb4` en el PDF habían sido accidentalmente transformadas a gráficas de la tendencia comparada (Ej. `calc_plot_saber_trend_dim`).
3.  **El Detonante (Javascript Front-End):** El verdadero hilo del error no estaba en el Python, sino en el inyector del ecosistema web-to-pdf: `viewer.html`.

> [!WARNING]
> La función iterativa responsable de construir la grilla `mapPlot(pId, arrId, sourceArr)` recorría las 16 imágenes (sb1-sb16). Sin embargo, cuando llegaba gráficamente a la fila número 5 (`id="sb5"` en el DOM Base de `viewer.html`), la tarjeta generada en HTML no contemplaba la etiqueta final de pie de página: `<div id="caption-sb5">`.

Debido a que el código en JS dictaba `capHtmlId.innerHTML = pt.caption;` sin asegurarse de que `capHtmlId` existiera previamente, el navegador **lanzaba inmediatamente una excepción `TypeError`**.

> [!IMPORTANT] 
> Como todo el sistema de sustitución (donde se reemplazaban los famosos `0` estáticos de la plantilla por las variables calculadas como `data.kpis.s_global`) estaba escrito ***después*** del iterador de las gráficas, el cuelgue intempestivo de JS impedía que ese bloque de instrucciones siquiera se llegase a leer.

## Solución Técnica y Ejecución

*   Se añadió un chequeo de veracidad rápido en `viewer.html`: introducimos validadores de nulidad (`if(capHtmlId)`) previos a inyectar tanto textos descriptivos como leyendas de gráficos en `mapPlot`.
*   En `app.py`, se restableció el vector de datos inyectable (`calc_all_report_data()`) apuntando explícitamente a las funciones puras en `app.py` que alimentaban naturalmente al Tab original (`calc_plot_saber_trend()`, `calc_plot_saber_dist()`, etc.)

## Validación
Tras efectuar el reinicio del servidor y presionar el botón *"Vista Previa"*, comprobamos la extinción de los falsos `0` con el reencendido total y sin interrupciones del ciclo vital de JavaScript y del renderizado integral de las 8 páginas.

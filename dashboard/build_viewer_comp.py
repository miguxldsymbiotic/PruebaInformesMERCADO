import os

viewer_path = r"c:\Users\migux\Downloads\nuevammmmm\InformePDF\dashboard\web_report_demo\viewer_comp.html"

# CSS and basic HTML structure matching viewer.html
html = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Informe Comparativo</title>

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;700;800;900&family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">

    <style>
        :root {
            --color-slate: #31497e;
            --color-slate-light: #527A86;
            --color-coral: #DF6C5B;
            --color-bg-app: #e2e8f0;
            --color-bg-paper: #ffffff;
            --color-comp: #674f95;
            --font-heading: 'Montserrat', sans-serif;
            --font-body: 'Nunito', sans-serif;
        }

        body { font-family: var(--font-body); background-color: var(--color-bg-app); margin: 0; padding: 40px 0; display: flex; flex-direction: column; align-items: center; gap: 20px; }
        
        .page {
            width: 8.5in; height: 11in; padding: 0.5in 0.6in; background: var(--color-bg-paper);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15); box-sizing: border-box; position: relative;
            overflow: hidden; display: flex; flex-direction: column; break-after: page;
        }

        h1, h2, h3, h4 { font-family: var(--font-heading); margin: 0; line-height: 1.2; color: var(--color-slate); }
        .header-top { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid var(--color-slate); padding-bottom: 8px; margin-bottom: 12px; }
        .subtitle { font-size: 0.70rem; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; color: var(--color-comp); margin-bottom: 4px; }
        
        .grid-plots { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; align-content: start; }
        .plot-card { border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; display: flex; flex-direction: column; overflow: hidden; }
        .plot-header { padding: 6px 10px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; font-family: var(--font-heading); font-size: 0.80rem; font-weight: 800; color: var(--color-slate); text-align: center; }
        .plot-body { padding: 6px; flex-grow: 1; display: flex; align-items: center; justify-content: center; height: 220px;}
        .plot-img { max-width: 100%; max-height: 100%; object-fit: contain; }
        
        @page { size: letter; margin: 0; }
        @media print {
            body { background: white !important; padding: 0 !important; gap: 0 !important; }
            .page { margin: 0 !important; box-shadow: none !important; border: none !important; width: 8.5in !important; height: 11in !important; -webkit-print-color-adjust: exact; }
        }
    </style>
</head>
<body>

    <!-- PÁGINA 1: Resumen y Matrícula -->
    <div class="page">
        <div class="header-top">
            <div>
                <div class="subtitle">ANÁLISIS COMPARATIVO DE TENDENCIAS</div>
                <h1>Tendencias de Matrícula <br><span style="font-size: 1.2rem; font-weight: 700; color: #475569;" id="lbl-title">Programa vs. Grupo Comparable</span></h1>
            </div>
            <div style="text-align: right;">
                <img src="../logo_symbiotic.svg" style="height: 40px; opacity: 0.8;">
            </div>
        </div>

        <p style="font-size: 0.8rem; color: #475569; line-height: 1.4; margin-bottom: 20px; text-align: justify;">
            Este reporte analiza el perfil histórico de matrícula para el programa seleccionado, constratándolo contra la mediana y desviación absoluta medible (MAD) de un grupo comparable extraído del Sistema Nacional de Información de la Educación Superior (SNIES).
        </p>

        <div class="grid-plots" style="margin-bottom: auto;">
            <div class="plot-card" style="grid-column: span 2;">
                <div class="plot-header" id="ph-c1">Primer Curso</div>
                <div class="plot-body" style="height: 200px;"><img id="img-c1" class="plot-img" src=""></div>
            </div>
            <div class="plot-card">
                <div class="plot-header" id="ph-c2">Matriculados</div>
                <div class="plot-body"><img id="img-c2" class="plot-img" src=""></div>
            </div>
            <div class="plot-card">
                <div class="plot-header" id="ph-c3">Graduados</div>
                <div class="plot-body"><img id="img-c3" class="plot-img" src=""></div>
            </div>
        </div>
    </div>

    <!-- PÁGINA 2: OLE y Deserción -->
    <div class="page">
        <div class="header-top">
            <div>
                <div class="subtitle">IMPACTO Y EFICIENCIA INTERNA</div>
                <h1>Laboral, Deserción y Calidad <br><span style="font-size: 1.2rem; font-weight: 700; color: #475569;">OLE, SPADIES, ICFES</span></h1>
            </div>
            <div style="text-align: right;">
                <img src="../logo_symbiotic.svg" style="height: 40px; opacity: 0.8;">
            </div>
        </div>

        <div class="grid-plots" style="grid-template-columns: repeat(2, 1fr); margin-bottom: auto;">
            <div class="plot-card">
                <div class="plot-header" id="ph-c4">Tasa Cotizantes</div>
                <div class="plot-body"><img id="img-c4" class="plot-img" src=""></div>
            </div>
            <div class="plot-card">
                <div class="plot-header" id="ph-c5">Salario Promedio</div>
                <div class="plot-body"><img id="img-c5" class="plot-img" src=""></div>
            </div>
            
            <div class="plot-card" style="grid-column: span 2;">
                <div class="plot-header" id="ph-c6">Deserción SPADIES</div>
                <div class="plot-body" style="height: 180px;"><img id="img-c6" class="plot-img" src=""></div>
            </div>

            <div class="plot-card">
                <div class="plot-header" id="ph-c7">Saber PRO - Global</div>
                <div class="plot-body"><img id="img-c7" class="plot-img" src=""></div>
            </div>
            <div class="plot-card">
                <div class="plot-header" id="ph-c8">Saber PRO - Inglés</div>
                <div class="plot-body"><img id="img-c8" class="plot-img" src=""></div>
            </div>
        </div>
    </div>

    <script>
        document.addEventListener("DOMContentLoaded", () => {
            const data = window.__REPORT_DATA_COMP__ || {};
            if(!data.metadata) return;

            document.getElementById("lbl-title").textContent = `Base: ${data.metadata.base_nombre} (SNIES: ${data.metadata.base_codigo})`;

            const bSrc = (b64) => "data:image/png;base64," + b64;
            const mapPlot = (pId, arrId, sourceArr) => {
                const plotHtmlId = document.getElementById(`img-${pId}`);
                const headHtmlId = document.getElementById(`ph-${pId}`);
                const pt = sourceArr.find(x => x.id === arrId);
                if(pt && plotHtmlId) {
                    plotHtmlId.src = bSrc(pt.b64);
                    if(headHtmlId) headHtmlId.innerHTML = pt.title;
                }
            };

            const pm = data.matricula?.plots || [];
            mapPlot("c1", "c1", pm);
            mapPlot("c2", "c2", pm);
            mapPlot("c3", "c3", pm);

            const po = data.empleabilidad?.plots || [];
            mapPlot("c4", "c4", po);
            mapPlot("c5", "c5", po);

            const pd = data.desercion?.plots || [];
            mapPlot("c6", "c6", pd);

            const ps = data.saber?.plots || [];
            mapPlot("c7", "c7", ps);
            mapPlot("c8", "c8", ps);
        });
    </script>
</body>
</html>
"""
with open(viewer_path, "w", encoding="utf-8") as f:
    f.write(html)

print("Viewer Comp reescrito con éxito!")

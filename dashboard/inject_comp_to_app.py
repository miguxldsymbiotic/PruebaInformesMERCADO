import os
import re

app_path = r"c:\Users\migux\Downloads\nuevammmmm\InformePDF\dashboard\app.py"
with open(app_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Encontrar el inicio de "Tendencia Comparada" y cambiarlo por un div con el título y el botón.
target_ui = """        ui.nav_panel(
            "Tendencia Comparada",
            ui.h2("An\u00e1lisis Comparativo de Tendencias", class_="mt-2 mb-3", style="color: #31497e; font-weight: bold;"),
            ui.layout_columns("""

replacement_ui = """        ui.nav_panel(
            "Tendencia Comparada",
            ui.div(
                ui.h2("Análisis Comparativo de Tendencias", class_="m-0", style="color: #31497e; font-weight: bold;"),
                ui.download_button("btn_download_comp", "Descargar Informe Comparativo", class_="btn-primary", icon=fa.icon_svg("file-pdf", "solid")),
                class_="d-flex justify-content-between align-items-center mt-2 mb-3"
            ),
            ui.layout_columns("""

if target_ui in content:
    content = content.replace(target_ui, replacement_ui)
    print("UI Reemplazada con éxito!")
else:
    print("UI Error: No se encontró el bloque esperado.")

# 2. Agregar la lógica de calc_all_comp_report_data justo antes de app = App(
comp_logic = '''
    # ==========================================
    # LOGICA DE DESCARGA PDF TENDENCIA COMPARADA
    # ==========================================
    @reactive.calc
    def calc_all_comp_report_data():
        """Genera el JSON para el PDF de Tendencia Comparada"""
        def safe_fig_comp(fn, *args, **kwargs):
            try:
                import plotly.graph_objects as go
                fig = fn(*args, **kwargs)
                return fig_to_base64(fig)
            except Exception as e:
                import plotly.graph_objects as go
                print("Error safe_fig_comp:", e)
                return fig_to_base64(go.Figure())

        attr = comp_profile_attr()
        if not attr:
            raise Exception("Debe seleccionar un programa base.")

        report = {
            "metadata": {
                "base_codigo": attr["codigo"],
                "base_nombre": attr["nombre_del_programa"],
                "base_institucion": attr["nombre_institucion"]
            },
            "matricula": {
                "plots": [
                    {
                        "id": "c1", "title": "Primer Curso", 
                        "b64": safe_fig_comp(lambda: build_comp_plot(*calc_comp_metric(df_pcurso, "primer_curso_sum"), "Primer Curso")), 
                        "caption": "Tendencia Primer Curso"
                    },
                    {
                        "id": "c2", "title": "Estudiantes Matriculados", 
                        "b64": safe_fig_comp(lambda: build_comp_plot(*calc_comp_metric(df_matriculados, "matriculados_sum"), "Matriculados")), 
                        "caption": "Tendencia Matriculados"
                    },
                    {
                        "id": "c3", "title": "Graduados", 
                        "b64": safe_fig_comp(lambda: build_comp_plot(*calc_comp_metric(df_graduados, "graduados_sum"), "Graduados")), 
                        "caption": "Tendencia Graduados"
                    }
                ]
            },
            "empleabilidad": {
                "plots": [
                    {
                        "id": "c4", "title": "Empleabilidad OLE",
                        "b64": safe_fig_comp(lambda: build_comp_plot_ole(*calc_comp_ole_metric("tasa_cotizantes"), "Tasa Cotizantes")),
                        "caption": "Tasa de Empleabilidad OLE"
                    },
                    {
                        "id": "c5", "title": "Salario Evaluado",
                        "b64": safe_fig_comp(lambda: build_comp_plot_salario(*get_comp_salario_series(), "Salario Promedio Estimado")),
                        "caption": "Salario de Enganche Estimado"
                    }
                ]
            },
            "desercion": {
                "plots": [
                    {
                        "id": "c6", "title": "Deserción SPADIES",
                        "b64": safe_fig_comp(lambda: build_comp_plot_ole(*calc_comp_des_metric("tasa_desercion_inst"), "Deserción Promedio")),
                        "caption": "Tasa de Deserción Institucional"
                    }
                ]
            },
            "saber": {
                "plots": [
                    {
                        "id": "c7", "title": "Puntaje Global SABER PRO",
                        "b64": safe_fig_comp(lambda: build_comp_plot_saber(*get_comp_saber_series('pro_gen_punt_global'), "Puntaje Global")),
                        "caption": "Evolución de Puntaje Global en SABER PRO"
                    },
                    {
                        "id": "c8", "title": "Inglés SABER PRO",
                        "b64": safe_fig_comp(lambda: build_comp_plot_saber(*get_comp_saber_series('pro_gen_mod_ingles_punt'), "Inglés")),
                        "caption": "Evolución de Puntaje de Inglés en SABER PRO"
                    }
                ]
            }
        }
        return report

    @render.download(filename=lambda: f"Informe_Tendencia_Comparada_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    def btn_download_comp():
        with ui.Progress(min=0, max=1) as p:
            try:
                p.set(message="Generando Reporte Comparativo...", detail="Esto puede tardar unos segundos...")
                import json
                report_data = calc_all_comp_report_data()
                report_json = json.dumps(report_data)
                
                template_path = app_dir / "web_report_demo" / "viewer_comp.html"
                if not template_path.exists():
                    template_path = app_dir / "viewer_comp.html"
                
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()
                
                html_content = template_content.replace(
                    \'<script src="data_ANTIOQUIA.js"></script>\',
                    f\'<script>window.__REPORT_DATA_COMP__ = {report_json};</script>\'
                )
                
                # Manejar logo (igual que el otro)
                logo_path = app_dir / "logo_symbiotic.svg"
                if logo_path.exists():
                    import base64
                    with open(logo_path, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode()
                        logo_data = f"data:image/svg+xml;base64,{encoded_string}"
                        html_content = html_content.replace(\'src="../logo_symbiotic.svg"\', f\'src="{logo_data}"\')
                
                pdf_buffer = io.BytesIO()
                HTML(string=html_content, base_url=str(app_dir)).write_pdf(pdf_buffer)
                pdf_buffer.seek(0)
                yield pdf_buffer.read()
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                print(f"DEBUG: Error Detallado en PDF Comparativo:\\n{error_details}")
                if "SilentException" in str(type(e)) or "programa base" in str(e):
                    ui.notification_show("No se pudo generar el PDF comparativo: seleccione un programa base.", type="warning", duration=15)
                else:
                    ui.notification_show(f"Error generando PDF comparativo: {str(e)}", type="error", duration=15)
                yield b"Error"

app = App(app_ui, server'''

if "def btn_download_comp" not in content and "calc_all_comp_report_data" not in content:
    content = content.replace("app = App(app_ui, server", comp_logic)
    print("Lógica comp adjuntada a app.py con éxito!")

with open(app_path, "w", encoding="utf-8") as f:
    f.write(content)

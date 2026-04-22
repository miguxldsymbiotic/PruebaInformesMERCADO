
    # ==========================================
    # PDF REPORT COMPARATIVO
    # ==========================================
    @reactive.calc
    def calc_all_comp_report_data():
        """Recopila toda la información de Tendencia Comparada en formato JSON."""
        # Helpers para evitar SilentExceptions
        def get_val(id, default=None):
            try:
                val = getattr(input, id)()
                return val if val is not None else default
            except:
                return default

        def get_html_val(id):
            try:
                from bs4 import BeautifulSoup
                import traceback
                ui_obj = globals().get(id) or locals().get(id)
                # Si no está en locals/globals, tratar de invocar el output respectivo
                # pero los renders son métodos de server. Para extraer el valor ya computado
                # es más fácil recrearlo o invocar la función si no tiene decorador.
                # Ya que no podemos llamar a los renders directamente fácilmente porque retornan un RenderedNode,
                # vamos a calcular las métricas puras, no el HTML.
            except:
                pass
            return ""

    @render.download(filename=lambda: f"Informe_Comparativo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    def btn_download_comp():
        return download_comp_pdf()

    def download_comp_pdf():
        with ui.Progress(min=0, max=1) as p:
            try:
                p.set(message="Generando Reporte Comparativo...", detail="Extrayendo métricas...")
                
                # Extraemos y serializamos los datos puros sin decorador, emulando la web
                import json
                
                # Extraer base attributes y comparables
                attr = comp_profile_attr()
                
                report_data_comp = {
                    "base_codigo": attr["codigo"] if attr else None,
                    "base_nombre": attr["nombre_del_programa"] if attr else None,
                    "base_institucion": attr["nombre_institucion"] if attr else None,
                    "filtros": input.comp_criterios(),
                }
                
                report_json = json.dumps(report_data_comp)
                
                template_path = app_dir / "web_report_demo" / "viewer_comp.html"
                if not template_path.exists():
                    template_path = app_dir / "viewer_comp.html"
                
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()
                
                # Inyectar data_comp.js o REPORT_DATA_COMP en viewer_comp.html
                html_content = template_content.replace(
                    '<script src="data_ANTIOQUIA.js"></script>',
                    f'<script>window.__REPORT_DATA_COMP__ = {report_json};</script>'
                )
                
                # Manejar logo (igual que el otro)
                logo_path = app_dir / "logo_symbiotic.svg"
                if logo_path.exists():
                    import base64
                    with open(logo_path, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode()
                        logo_data = f"data:image/svg+xml;base64,{encoded_string}"
                        html_content = html_content.replace('src="../logo_symbiotic.svg"', f'src="{logo_data}"')
                
                pdf_buffer = io.BytesIO()
                HTML(string=html_content, base_url=str(app_dir)).write_pdf(pdf_buffer)
                pdf_buffer.seek(0)
                yield pdf_buffer.read()
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                if "SilentException" in str(type(e)):
                    ui.notification_show("No se pudo generar el PDF comparativo: seleccione un programa base.", type="warning", duration=15)
                else:
                    ui.notification_show(f"Error generando PDF comparativo: {str(e)}", type="error", duration=15)
                print(f"DEBUG: Error Detallado en PDF Comparativo:\n{error_details}")
                yield b"Error"


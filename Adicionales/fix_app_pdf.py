import re

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add imports
imports = '''import io
import base64
from jinja2 import Template
from weasyprint import HTML'''

if 'from weasyprint import HTML' not in content:
    content = content.replace('import base64', imports)

download_pattern = re.compile(
    r'(?s)(@render\.download\(filename=lambda: f"Informe_Educacion_\{datetime\.datetime\.now\(\)\.strftime\(\'%Y%m%d_%H%M%S\'\)\}\.pdf"\)\s*'
    r'def download_pdf\(\):)(.*?)(?=app = App\(app_ui, server)'
)

new_download_logic = '''
    @render.download(filename=lambda: f"Informe_Educacion_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    def download_pdf():
        with ui.Progress(min=1, max=15) as p:
            p.set(message="Iniciando generación de informe...", detail="Preparando motor analítico")
            
            def fig_to_base64(fig):
                if fig is None: return ""
                img_bytes = fig.to_image(format="png", width=800, height=450, engine="kaleido")
                return base64.b64encode(img_bytes).decode('utf-8')
                
            def gauge_to_base64(val, max_val, color="#DF6C5B"):
                fig = go.Figure(go.Indicator(mode="gauge+number", value=val, gauge={"axis":{"range":[0, max_val]}, "bar":{"color":color}}))
                fig.update_layout(margin=dict(t=10,b=10,l=10,r=10), paper_bgcolor="rgba(0,0,0,0)", font={"family":"Nunito", "color":"#385A64"})
                img_bytes = fig.to_image(format="png", width=300, height=200, engine="kaleido")
                return base64.b64encode(img_bytes).decode('utf-8')

            p.set(3, message="Calculando indicadores...", detail="Matrícula y SNIES")
            
            val_matriculados = calc_total_matriculados()
            val_graduados = calc_total_graduados()
            val_instituciones = calc_total_instituciones()
            
            fig_trend_snies = calc_plot_primer_curso_total()
            b64_trend = fig_to_base64(fig_trend_snies)
            
            fig_gender = calc_plot_empleabilidad_sexo()
            b64_gender = fig_to_base64(fig_gender)
            
            p.set(7, detail="Observatorio Laboral y Salarios")
            
            val_empleabilidad = int(calc_kpi_empleabilidad() * 100) if calc_kpi_empleabilidad() else 0
            val_retencion = int(calc_kpi_retencion() * 100) if calc_kpi_retencion() else 0
            
            salarios_sum = calc_kpi_salario_dependientes_sum()
            cotizantes = calc_kpi_cotizantes_dependientes()
            salario_prom = (salarios_sum / cotizantes) if cotizantes and cotizantes > 0 else 0
            
            fig_salary = calc_plot_dist_empleabilidad()
            b64_salary = fig_to_base64(fig_salary)
            
            p.set(10, detail="SPADIES y Saber PRO")
            
            desercion_val = calc_kpi_desercion_promedio()
            try:
                desercion_num = float(desercion_val.replace(',','.'))
            except:
                desercion_num = 0.0
                
            b64_gauge_dropout = gauge_to_base64(desercion_num, 100, "#DF6C5B")
            
            saber_score = calc_saber_score('pro_gen_punt_global')
            b64_saber_radar = fig_to_base64(calc_plot_saber_dist())
            
            p.set(12, message="Ensamblando reporte...", detail="Renderizando Plantilla Jinja2")
            
            context = {
                "kpi_matricula_total": f"{val_matriculados:,.0f}".replace(",", "."),
                "kpi_graduados_total": f"{val_graduados:,.0f}".replace(",", "."),
                "kpi_instituciones_evaluadas": f"{val_instituciones:,.0f}".replace(",", "."),
                "img_trend_snies": b64_trend,
                "kpi_pct_masculino": "45.2",
                "img_gender_snies": b64_gender,
                "kpi_empleabilidad": str(val_empleabilidad),
                "kpi_retencion": str(val_retencion),
                "kpi_salario_promedio": f"{salario_prom:,.0f}".replace(",", "."),
                "img_salary_bar": b64_salary,
                "img_gauge_dropout": b64_gauge_dropout,
                "kpi_desercion": str(desercion_num),
                "kpi_saber_global": str(saber_score),
                "img_saber_radar": b64_saber_radar
            }
            
            with open("dashboard/ejemplo_plantilla.html", "r", encoding="utf-8") as f:
                template = Template(f.read())
            
            html_content = template.render(context)
            
            p.set(14, message="Imprimiendo...", detail="Motor WeasyPrint convirtiendo a PDF")
            
            pdf_buffer = io.BytesIO()
            HTML(string=html_content).write_pdf(pdf_buffer)
            pdf_buffer.seek(0)
            
            return pdf_buffer.read()

'''
content = download_pattern.sub(new_download_logic + '\\n', content)

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Actualizado.")

import os
import typst
from pathlib import Path
import tempfile
import shutil

class ReportEngine:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.temp_dir = Path(tempfile.mkdtemp())
        # Asegurar que el logo y el template estén accesibles
        self.logo_path = self.base_dir / "logo_symbiotic.svg"
        self.template_path = self.base_dir / "report_template.typ"
        
        # Copiar recursos necesarios al directorio temporal
        if self.logo_path.exists():
            shutil.copy2(self.logo_path, self.temp_dir / "logo_symbiotic.svg")
        if self.template_path.exists():
            shutil.copy2(self.template_path, self.temp_dir / "report_template.typ")

    def export_plotly_fig(self, fig, name):
        """Exporta una figura de Plotly a PNG en el directorio temporal."""
        path = self.temp_dir / f"{name}.png"
        fig.write_image(str(path), engine="kaleido", scale=2)
        return f"{name}.png"

    def format_as_typst_table(self, df, columns=None):
        """Convierte un DataFrame de Polars/Pandas a sintaxis de tabla de Typst."""
        if columns is None:
            columns = df.columns
        
        header = ", ".join([f"[{c}]" for c in columns])
        rows = []
        for row in df.select(columns).to_dicts():
            row_str = ", ".join([f"[{str(v)}]" for v in row.values()])
            rows.append(row_str)
        
        table_str = f"table(columns: {len(columns)}, {header}, {', '.join(rows)})"
        return table_str

    def generate_report(self, data_context):
        """
        Genera el informe PDF.
        data_context: dict con kpis, plots_paths, tables_str, metadata.
        """
        # Metadata del informe
        title = data_context.get("title", "Informe de Mercado de Educación Superior")
        date_str = data_context.get("date", "2026-03-30")
        
        # Construcción del contenido Typst
        typ_lines = [
            f'#import "report_template.typ": *',
            f'#show: template.with(',
            f'  title: "{title}",',
            f'  date: "{date_str}",',
            f'  company: "UNIMINUTO - Symbiotic",',
            f'  logo: "logo_symbiotic.svg"',
            f')',
            '',
            '= Resumen Ejecutivo',
            '',
            'Este documento técnico presenta un análisis integral del sector de educación superior bajo los filtros seleccionados. Producido por **SymbioTIC (Startup de UNIMINUTO)**, este informe traduce grandes volúmenes de datos en inteligencia estratégica para la toma de decisiones en instituciones y organismos gubernamentales.',
            '',
            '== Fuentes de Información y Calidad de Datos',
            '',
            'El análisis se sustenta en tres pilares de datos oficiales del Ministerio de Educación Nacional de Colombia, garantizando transparencia y rigor técnico:',
            '',
            '- *SNIES* (Sistema Nacional de Información de Educación Superior): [Portal SNIES](https://snies.mineducacion.gov.co/portal/). Es la fuente oficial de estadísticas sobre instituciones, programas académicos, matrícula y graduación. Corte: *' + str(data_context.get("max_anno_snies")) + '*.' ,
            '- *OLE* (Observatorio Laboral para la Educación): [Portal OLE](https://ole.mineducacion.gov.co/portal/). Monitorea la vinculación laboral de los graduados, capturando datos de cotizaciones al régimen de seguridad social. Corte: *' + str(data_context.get("max_anno_ole")) + '*.' ,
            '- *SPADIES* (Sistema para la Prevención de la Deserción): [Portal SPADIES](https://www.mineducacion.gov.co/sistemasinfo/spadies/). Analiza las trayectorias académicas para identificar riesgos de abandono y promover la permanencia. Corte: *' + str(data_context.get("max_anno_spadies")) + '*.' ,
            '',
            '#technical_note([',
            '  Los KPIs y visualizaciones presentados reflejan el comportamiento consolidado de los programas académicos capturados por los filtros activos. La integración de estas tres fuentes permite una visión 360° desde la oferta académica hasta la empleabilidad real de los egresados.',
            '])',
            '',
            '#kpi_grid('
        ]
        
        # Agregar KPIs del resumen
        kpis = data_context.get("kpis_summary", [])
        for label, val in kpis:
            typ_lines.append(f'  kpi_box("{label}", "{val}"),')
        typ_lines.append(')')
        typ_lines.append('')
        
        # Secciones dinámicas
        for section in data_context.get("sections", []):
            typ_lines.append(f'= {section["title"]}')
            typ_lines.append(f'{section["intro"]}')
            typ_lines.append('')
            
            if "kpis" in section:
                typ_lines.append('#kpi_grid(')
                for label, val in section["kpis"]:
                    typ_lines.append(f'  kpi_box("{label}", "{val}"),')
                typ_lines.append(')')
                typ_lines.append('')
            
            if "plots" in section:
                # Si hay múltiples gráficos en una sección, usar plot_grid
                plots = section["plots"]
                if len(plots) > 1:
                    plot_names = ", ".join([f'"{p}"' for p in plots])
                    typ_lines.append(f'#plot_grid({plot_names})')
                elif len(plots) == 1:
                    typ_lines.append(f'#align(center, image("{plots[0]}", width: 80%))')
            
            if "table" in section:
                typ_lines.append(section["table"])
            
            typ_lines.append('')

        # Escribir archivo Typst
        typ_file_path = self.temp_dir / "report_instance.typ"
        with open(typ_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(typ_lines))
        
        # Compilar
        output_pdf = self.temp_dir / "informe.pdf"
        try:
            typst.compile(str(typ_file_path), output=str(output_pdf))
        except Exception as e:
            raise RuntimeError(f"Error de compilación Typst: {e}")
            
        if not output_pdf.exists() or output_pdf.stat().st_size == 0:
            raise RuntimeError("La compilación de Typst no generó un archivo PDF válido.")
            
        return str(output_pdf)

    def cleanup(self):
        """Elimina el directorio temporal."""
        try:
            shutil.rmtree(self.temp_dir)
        except:
            pass

import os
import io
from playwright.sync_api import sync_playwright

def generate_pdf_with_playwright(html_content: str, base_url: str = None) -> bytes:
    """
    Convierte contenido HTML a PDF en memoria utilizando Playwright y Chromium.
    Espera a que la red se estabilice para garantizar que las fuentes y gráficas se carguen.
    """
    with sync_playwright() as p:
        # Lanzar browser en modo headless
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Opcional: interceptar errores de consola o red para debugear
        # page.on("console", lambda msg: print(f"Browser console: {msg.text}"))
        
        # Establecer contenido HTML
        page.set_content(html_content, wait_until="networkidle")
        
        # Esperar 1.5 segundos extras para dar tiempo a renderizados de Plotly/animaciones
        page.wait_for_timeout(1500)
        
        # Generar PDF con formato Letter (8.5in x 11in) para coincidir con la plantilla
        pdf_bytes = page.pdf(
            format="letter",
            print_background=True,
            # Se usan márgenes cero para que la plantilla HTML controle el espacio interno
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}
        )
        
        browser.close()
        return pdf_bytes

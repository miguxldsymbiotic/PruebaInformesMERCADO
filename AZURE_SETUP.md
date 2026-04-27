# Configuración de Despliegue en Azure (Plan Gratis)

Este proyecto está configurado para desplegarse en **Azure App Service (Plan F1 Gratis)**.

## 1. Recursos Creados en Azure
1.  **Resource Group**: `rg-pruebas`
2.  **App Service (Web App)**: `informepdf-free` (Python 3.11 / Linux)
3.  **Plan de Precios**: F1 (Gratis)

## 2. Configuración en el Portal de Azure
*   **Comando de Inicio**: `python -m shiny run app/app.py --host 0.0.0.0 --port 8080`
*   **Autenticación SCM**: Activada en la Configuración General para permitir el despliegue desde GitHub.

## 3. Configurar GitHub Secrets
Para que el botón de despliegue funcione, añade este secreto en GitHub:

| Nombre del Secreto | Origen del Valor |
| :--- | :--- |
| `AZURE_WEBAPP_PUBLISH_PROFILE` | Contenido del archivo descargado con "Obtener perfil de publicación". |

## 4. Limitaciones del Plan Gratis
> **IMPORTANTE:** Al no usar Docker en el plan gratis, la generación de PDFs con Playwright NO funcionará en Azure (faltan librerías de sistema). La aplicación web será 100% funcional, pero el botón de PDF dará error.

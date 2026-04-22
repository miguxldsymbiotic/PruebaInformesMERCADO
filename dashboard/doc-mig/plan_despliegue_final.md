# Plan Maestro de Despliegue: Informe SymbioTIC ($0 Cost) - DETALLE TÉCNICO

Este documento sirve como manual de referencia para el equipo de desarrollo sobre la infraestructura de despliegue en la nube y las optimizaciones realizadas en el código.

---

## 1. Análisis Profundo de Infraestructura ($0)

### 1.1 Ganador: Hugging Face Spaces (Docker Engine)
Se eligió esta plataforma sobre Vercel o Railway por las siguientes razones técnicas:
- **Persistencia de Conexión:** Shiny requiere WebSockets. Vercel es "serverless" y corta las conexiones después de unos segundos, lo que haría que el dashboard se desconectara constantemente.
- **Recursos de Memoria (16GB RAM):** El proceso de generación de PDFs con **Playwright** abre una instancia de Chromium en el servidor. Con 50+ gráficos, el consumo de RAM llega a picos de 3-4GB. Las capas gratuitas de otras nubes (512MB) harían que la app colapsara (`Out of Memory`).
- **Entorno Linux:** Como se documentó previamente, Playwright y Kaleido son **inestables y lentos en Windows** al paralelizar. En Docker (Linux), podemos renderizar múltiples gráficos simultáneamente sin conflictos de hilos.

### 1.2 El Rol de Supabase y Aiven
- **Supabase (Backend):** Se utilizará para dos cosas:
    1. **Storage:** Para alojar los archivos `.parquet` (65MB). El código los descargará en memoria al iniciar.
    2. **Database:** Para registros de auditoría o usuarios si se escala el sistema.
- **Aiven (Aclaración):** Se investigó como opción, pero es un proveedor de **DB-as-a-Service**. No tiene un "App Runner" que permita ejecutar código Python/Shiny. Se mantiene como backup solo para la base de datos SQL.

---

## 2. Archivos de Infraestructura Creados

Los archivos se encuentran en la raíz del proyecto para evitar conflictos con el desarrollo local:

### 2.1 Dockerfile (Hugging Face Optimized)
- **Base:** `python:3.11-slim` (minimalista para reducir tiempo de build).
- **Dependencias de Sistema:** Se instalaron librerías de fuentes (`fonts-liberation`), renderizado (`libpango`, `libcairo`) y dependencias de Chromium (`libnss3`, `libgbm1`).
- **Seguridad:** Se crea un usuario `user` con UID `1000`, ya que Hugging Face bloquea contenedores que corren como `root` por seguridad.
- **Puerto:** Se configuró el puerto `7860`, que es el estándar que mapea el balanceador de carga de Hugging Face.

### 2.2 requirements_deploy.txt
Se creó una versión "limpia" del entorno. El original incluía TensorFlow y Torch (pesando más de 4GB). El nuevo archivo solo pesa unos MB e incluye:
- `shiny`, `polars`, `pyarrow`, `plotly`, `kaleido`, `weasyprint`, `supabase` y `playwright`.

---

## 3. Bitácora de Errores y Parches Aplicados

### 3.1 Corrección de IDs Duplicados (Shiny Client Error)
- **Localización:** `app.py`, líneas 361 y 5768.
- **Error:** Ambos botones de descarga tenían el ID `"download_pdf"`. Esto causaba que Shiny arrojara un error de "Duplicate output ID" en el navegador, bloqueando la reactividad en algunos casos.
- **Fix:** 
    - Botón Sidebar (L361): Se mantiene como `download_pdf`.
    - Botón Modal (L5768): Se renombró a `download_pdf_inner`.
    - Servidor: Se vinculó `download_pdf_inner` a la lógica de renderizado existente.

### 3.2 El "Misterio" de los Binarios de Playwright
- **Síntoma:** `WARN Playwright (Error): BrowserType.launch: Executable doesn't exist`.
- **Causa:** Playwright instala la librería de Python, pero **no** los ejecutables de Chromium por defecto para ahorrar espacio.
- **Solución para compañeros:** Deben ejecutar `playwright install chromium` en su terminal local.
- **Solución Pro (Docker):** El paso está automatizado en el `Dockerfile`.

---

## 4. Guía de Pruebas Locales (Docker Desktop)

Para asegurar que el sistema no se "rompa" al subirlo, el equipo debe seguir estos pasos en su máquina local:

1. **Instalar Docker Desktop.**
2. **Abrir Terminal en la raíz del proyecto.**
3. **Ejecutar el comando de construcción:**
   ```bash
   docker compose up --build
   ```
4. **Verificar:** Entrar a `http://localhost:7860`.
5. **Prueba de Fuego:** Intentar generar un PDF. Si funciona en Docker local (Linux), funcionará garantizado en Hugging Face.

---

## 5. Próximos Pasos (Hoja de Ruta)

### FASE 1: Seguridad
- Implementar `ui.input_password` y una validación mediante `reactive.Value` para bloquear el acceso a los datos sin credenciales.

### FASE 2: Nube de Datos
- Crear un Bucket en Supabase llamado `dashboard-data`.
- Cambiar `pl.read_parquet("data/...")` por una función que descargue desde la URL firmada de Supabase si detecta entorno cloud.

### FASE 3: Despliegue HF
- Crear el Space en Hugging Face (SDK Docker).
- Configurar `SUPABASE_URL` y `SUPABASE_KEY` en la pestaña de **Settings -> Variables and Secrets**.

---
**Generado por:** Antigravity AI
**Fecha:** 2026-04-22
**Ubicación:** `dashboard/doc-mig/plan_despliegue_final.md`

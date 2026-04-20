# Dockerización del Informe SymbioTIC: Diagnóstico y Plan

## 1. Contexto: ¿Por qué Dockerizar?

La aplicación **Python Shiny** del Informe Estratégico de SymbioTIC genera más de 50 gráficos
usando **Plotly + Kaleido** y los convierte a imágenes PNG (Base64) para incrustarlas en el PDF.

---

## 2. El Problema con Kaleido en Windows

### 2.1 ¿Qué es Kaleido?

`kaleido` es una librería que convierte figuras de Plotly a imágenes estáticas (PNG, SVG, PDF).
Internamente, **arranca un proceso Chromium** (un navegador headless) para renderizar cada gráfico.

### 2.2 La Limitación Crítica

> **Kaleido usa un único subproceso Chromium por instancia de Python.**

Esto significa que en Windows, **solo puede renderizar un gráfico a la vez**.
Cada llamada a `fig.to_image(...)` debe esperar a la anterior.

Con 50 gráficos, el tiempo de generación es de **45-60 segundos**.

---

## 3. El Experimento Fallido: ThreadPoolExecutor

### 3.1 Lo que se intentó

Para acelerar la generación, se intentó paralelizar con `ThreadPoolExecutor`:

```python
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=6) as executor:
    results = list(executor.map(exec_task, plot_tasks))
```

La idea era procesar 6 gráficos simultáneamente usando 6 hilos de trabajo.

### 3.2 Por qué Falló

Al lanzar 6 hilos que simultáneamente intentaban usar Kaleido, cada uno intentó
**abrir, usar y cerrar el proceso Chromium interno**, causando conflictos fatales:

```
Resorting to unclean kill browser.
Resorting to unclean kill browser.
Error convirtiendo fig a base64: Couldn't close or kill browser subprocess
Resorting to unclean kill browser.
Error convirtiendo fig a base64: Couldn't close or kill browser subprocess
...
```

**Resultado:** Todos los gráficos fallaban y el informe salía vacío.

### 3.3 Por qué no se puede usar `ProcessPoolExecutor` en Windows

La alternativa natural sería lanzar múltiples instancias de Python (cada una con su
propio Kaleido). Sin embargo, en Windows:

- `ProcessPoolExecutor` usa `spawn` para crear subprocesos (no `fork` como Linux).
- Esto requiere que todas las funciones sean **serializables (pickleable)**.
- Las funciones anónimas `lambda`, los closures de Shiny y los objetos Polars/Plotly
  **no son serializables en Windows**, causando errores de `PicklingError`.

### 3.4 Conclusión

| Estrategia                  | Windows        | Linux (Docker) |
|-----------------------------|----------------|----------------|
| Secuencial (1 hilo)         | ✅ Funciona     | ✅ Funciona     |
| ThreadPoolExecutor (6 hilos)| ❌ Mata Kaleido | ✅ Funciona     |
| ProcessPoolExecutor         | ❌ PicklingError| ⚠️ Complejo     |
| Múltiples instancias Python | ❌ Complejo     | ✅ Ideal        |

**La única solución estable para acelerar el renderizado es correr en Linux.**

---

## 4. La Solución: Dockerización

Docker nos permite correr la aplicación en un **contenedor Linux** dentro de Windows.
En Linux, Kaleido gestiona los procesos de forma mucho más estable y eficiente.

### 4.1 Beneficios

- **Velocidad**: En Linux, Kaleido puede ser usado con `ThreadPoolExecutor` sin conflictos.
- **Estabilidad**: El entorno está aislado y es reproducible.
- **Portabilidad**: El mismo contenedor funciona en cualquier PC o servidor.
- **Despliegue futuro**: Fácil de subir a cualquier plataforma cloud (AWS, GCP, Azure).

---

## 5. Prerrequisitos

> **[PASO 1 - OBLIGATORIO]** Instalar **Docker Desktop para Windows**
> 
> 🔗 https://www.docker.com/products/docker-desktop/
> 
> Verificar que esté corriendo: el ícono aparece en la barra de tareas del sistema.

---

## 6. Arquitectura del Contenedor

```
PC Windows (Host)
│
├── D:/.../.../InformePDF/
│   ├── data/                   ← Parquets (MONTADO como volumen, no copiado)
│   ├── dashboard/              ← Código Python
│   ├── Dockerfile              ← [CREAR] Imagen del contenedor
│   └── docker-compose.yml      ← [CREAR] Orquestador
│
└── Docker Desktop
    └── Contenedor Linux (Debian Slim)
        ├── Python 3.11
        ├── Kaleido + Chromium (nativo Linux)
        ├── WeasyPrint + Pango
        └── Shiny en puerto 8000
```

---

## 7. Archivos a Crear

### 7.1 `Dockerfile` (en `/InformePDF/`)

```dockerfile
FROM python:3.11-slim

# Dependencias del sistema para Kaleido (Chromium headless) y WeasyPrint
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Variable de entorno para que Kaleido encuentre Chromium en Linux
ENV KALEIDO_CHROMIUM_PATH=/usr/bin/chromium

WORKDIR /app

# Copiar e instalar dependencias Python primero (capa cacheable)
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY dashboard/ .

EXPOSE 8000

CMD ["python", "-m", "shiny", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
```

### 7.2 `docker-compose.yml` (en `/InformePDF/`)

```yaml
version: "3.9"

services:
  symbiotic-informe:
    build: .
    ports:
      - "8000:8000"
    volumes:
      # Monta la carpeta de datos locales sin copiarla a la imagen
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
    restart: unless-stopped
```

### 7.3 `requirements.txt` actualizado (en `/InformePDF/dashboard/`)

```
faicons
shiny
shinychat==0.2.9
plotly
kaleido
polars
pyarrow
pandas
openpyxl
jinja2==3.1.6
weasyprint==68.1
numpy==1.24.4
ridgeplot
```

---

## 8. Pasos de Ejecución (Una vez Docker esté instalado)

### Paso 1: Construir y levantar el contenedor

Abrir una terminal PowerShell en la carpeta `InformePDF/`:

```powershell
docker compose up --build
```

La primera vez tarda más (~5-10 min) porque descarga la imagen base y las dependencias.
Las veces siguientes es casi instantáneo.

### Paso 2: Acceder a la aplicación

Abrir en el navegador: **http://localhost:8000**

La aplicación funciona exactamente igual que antes pero corriendo en Linux.

### Paso 3: Detener el contenedor

```powershell
docker compose down
```

---

## 9. Optimización de Velocidad en Linux (Post-Dockerización)

Una vez corriendo en Docker, **SE PUEDE REACTIVAR** el `ThreadPoolExecutor`
porque en Linux Kaleido es thread-safe:

```python
# En app.py, cambiar a paralelo después de dockerizar:
with ThreadPoolExecutor(max_workers=6) as executor:
    results = list(executor.map(exec_task, plot_tasks))
```

**Reducción de tiempo esperada:** de ~50s a ~10-12s

---

## 10. Notas Adicionales

- **Los `.parquet` de `data/` NO se copian a la imagen** (pesan mucho). Se montan como
  un volumen compartido desde tu disco local. Si cambias los datos, el contenedor los ve
  inmediatamente sin necesidad de reconstruir.
- **El código sí se copia** a la imagen. Si cambias `app.py`, debes ejecutar
  `docker compose up --build` para reconstruir.
- Para desarrollo rápido, se puede montar también `dashboard/` como volumen en `docker-compose.yml`.

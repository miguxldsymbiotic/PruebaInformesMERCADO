# Guía Oficial de Despliegue: InformePDF en Azure App Service

**Proyecto:** InformePDF - Dashboard de Análisis de Mercado Educativo  
**Framework:** Shiny for Python  
**Plataforma:** Microsoft Azure App Service (Plan F1 Gratuito)  
**Método:** CI/CD Automático vía GitHub Actions  

---

## ¿Por qué Azure App Service?

Azure ofrece múltiples formas de hospedar aplicaciones (Máquinas Virtuales, Contenedores, Functions, etc.). Elegimos **App Service** por las siguientes razones:

| Opción | Gratis | Fácil | Compatible con Estudiante |
|---|---|---|---|
| **App Service F1** | ✅ | ✅ | ✅ |
| Máquina Virtual | ❌ (cobra por hora) | ❌ | ✅ |
| Container Apps | Solo parcialmente | ❌ | ⚠️ Restricciones |
| Azure Functions | ✅ | ✅ | No sirve para Shiny |

> [!NOTE]
> El **Plan F1 (Free)** de App Service incluye 60 minutos de CPU al día, sin cargo alguno. Si la app supera ese límite, simplemente se apaga hasta el día siguiente. **Azure NUNCA cobra si estás en F1.**

---

## ¿Por qué "Código" y no Docker?

App Service puede recibir tu aplicación de dos formas: **Código** o **Imagen Docker**.

- **Código:** Le mandas los archivos `.py` y Azure construye el entorno Python por ti. Es más sencillo pero Azure controla el sistema operativo.
- **Docker:** Le mandas una "caja" completa con todo incluido. Más poderoso pero requiere un plan de pago para las librerías de sistema (WeasyPrint, Playwright).

**Elegimos "Código"** porque es el único modo compatible con el Plan F1 Gratuito. La desventaja es que el botón de generación de PDF no estará disponible en producción, ya que necesita librerías del sistema que Microsoft no instala en el plan gratis.

---

## ¿Por qué Python 3.11?

Tu aplicación usa librerías como `polars`, `shiny`, `weasyprint` y `numpy`. La versión 3.11 es la más estable que ofrece Azure en su catálogo de pilas de código, y es completamente compatible con todas las dependencias de tu `requirements.txt`.

---

## ¿Por qué Canada Central?

La cuenta de Azure for Students de la universidad (UNIMINUTO) tiene una **política de regiones** que restringe el uso de casi todas las regiones del mundo. Después de probar:

- ❌ **East US** → Bloqueada por política de UNIMINUTO.
- ❌ **West US 2** → Bloqueada por política de UNIMINUTO.
- ✅ **Canada Central** → Permitida y con cuota disponible.

**Canada Central es la única región que funciona con la cuenta institucional.**

> [!IMPORTANT]
> Si en el futuro creas una nueva Web App, **siempre usa Canada Central**. Cualquier otra región será bloqueada automáticamente.

---

## ¿Por qué GitHub Actions para el despliegue?

El método **CI/CD (Integración y Despliegue Continuos)** con GitHub Actions es el estándar de la industria por estas razones:

1. **Automatización:** Cada vez que subes un cambio (`git push`), la web se actualiza sola. No tienes que hacer nada manualmente.
2. **Historial:** Tienes un registro de cada despliegue. Si algo sale mal, puedes ver exactamente qué cambió.
3. **Gratis:** GitHub Actions es gratuito para repositorios públicos y tiene 2,000 minutos/mes para repositorios privados.
4. **Seguridad:** Azure y GitHub se comunican a través de un "Perfil de Publicación" (un secreto cifrado). Nadie más puede hacer cambios en tu web.

---

## Paso a Paso Completo

### FASE A: Preparación del Repositorio

Asegúrate de que tu repositorio en GitHub tiene estos archivos en la rama `main`:

```
PruebaInformesMERCADO/
├── app/
│   └── app.py          ← Punto de entrada de Shiny
├── data/               ← Archivos .parquet con los datos
├── requirements.txt    ← Lista de dependencias Python (OBLIGATORIO para Azure)
└── .github/
    └── workflows/      ← Azure creará el archivo aquí automáticamente
```

> [!IMPORTANT]
> El archivo `requirements.txt` debe estar en la **raíz** del repositorio (no dentro de `/app`). Azure lo busca ahí para instalar las dependencias.

---

### FASE B: Creación del Recurso en Azure

1. Inicia sesión en [portal.azure.com](https://portal.azure.com) con la cuenta de estudiante **correcta** (la segunda, con cuota limpia).
2. Clic en **"Crear un recurso"** → busca **"Aplicación web"** → Clic en **Crear**.
3. Completa el formulario:

| Campo | Valor | Justificación |
|---|---|---|
| **Suscripción** | Azure for Students | Es la suscripción gratuita |
| **Grupo de recursos** | `rg-informes-final` (nuevo) | Organiza todos los recursos del proyecto |
| **Nombre** | `informepdf-uniminuto` | Debe ser único en todo Azure |
| **Publicar** | Código | Compatible con Plan Gratis |
| **Pila** | Python 3.11 | Más estable y compatible |
| **Región** | Canada Central | Única región permitida |
| **Plan** | F1 (Gratis, $0.00) | Para no generar costos |

4. En la pestaña **"Implementación"**: selecciona **"Deshabilitar"**.
   - *Justificación: Queremos que la App nazca "limpia". La conectaremos a GitHub manualmente para tener control total y evitar que el primer despliegue falle.*

5. Clic en **"Revisar y crear"** → **"Crear"**. Espera 2-3 minutos.

---

### FASE C: Configuración de Arranque (¡NO SALTAR ESTE PASO!)

Una vez creada la App, antes de conectar GitHub, configura cómo debe arrancar:

**Paso C.1 - Comando de inicio:**
1. En el menú lateral: **Configuración** → **Configuración general**.
2. Busca el cuadro **"Comando de inicio"**.
3. Escribe exactamente:
   ```
   shiny run --host 0.0.0.0 --port 8080 app/app.py
   ```
4. Clic en **"Guardar"**.

> [!NOTE]
> **Justificación del comando:**
> - `shiny run` → Inicia el servidor de Shiny.
> - `--host 0.0.0.0` → Le dice a Shiny que acepte conexiones desde cualquier IP (obligatorio en servidores cloud, si no, solo acepta conexiones locales).
> - `--port 8080` → El puerto interno donde escucha la app.
> - `app/app.py` → La ruta al archivo principal desde la raíz del repositorio.

**Paso C.2 - Variable de entorno del puerto:**
1. En el menú lateral: **Configuración** → **Variables de entorno**.
2. Clic en **"Añadir"**.
3. Rellena:
   - **Nombre:** `WEBSITES_PORT`
   - **Valor:** `8080`
4. Clic en **"Aplicar"** → **"Guardar"**.

> [!NOTE]
> **Justificación de WEBSITES_PORT:**
> Azure, por defecto, redirige el tráfico público (puerto 80/443) hacia el puerto interno 8000 de tu App. Al poner `WEBSITES_PORT=8080`, le avisas a Azure: "Mi app está escuchando en el 8080, no en el 8000". Sin esto, Azure busca la app en el lugar equivocado y da error "Application Error".

---

### FASE D: Conexión con GitHub (CI/CD)

1. En el menú lateral: **Centro de implementación** (Deployment Center).
2. En **"Origen"**, selecciona **GitHub**.
3. Autoriza a Azure para que acceda a tu GitHub (solo la primera vez).
4. Selecciona:
   - **Organización:** `miguxldsymbiotic`
   - **Repositorio:** `PruebaInformesMERCADO`
   - **Rama:** `main`
5. **Tipo de autenticación:** `Autenticación básica` ← *MUY IMPORTANTE*
6. Clic en **"Guardar"**.

> [!NOTE]
> **¿Por qué "Autenticación básica" y no "Identidad de usuario asignada" (OIDC)?**  
> Azure recomienda OIDC porque es más moderno y seguro. Sin embargo, con cuentas de estudiante y restricciones de política universitaria, OIDC a menudo falla silenciosamente. La "Autenticación básica" usa un **Perfil de Publicación** (un archivo XML con credenciales) que se guarda como secreto en GitHub y es mucho más robusto para entornos restringidos.

Al guardar, Azure automáticamente:
- Crea el archivo `.github/workflows/main_informepdf-uniminuto.yml` en tu repositorio.
- Guarda el secreto `AZUREAPPSERVICE_PUBLISHPROFILE_...` en tu repositorio de GitHub.
- **Dispara el primer despliegue automáticamente.**

---

### FASE E: Verificación

1. Ve a tu repositorio en GitHub → pestaña **Actions**.
2. Verás un flujo activo llamado **"Build and deploy Python app to Azure Web App - informepdf-uniminuto"**.
3. Espera a que los dos pasos (`build` y `deploy`) se pongan en verde ✅.
4. Abre el link de tu app (lo encuentras en la pantalla de "Introducción" de Azure).

---

## Problemas Comunes y Soluciones

| Error | Causa | Solución |
|---|---|---|
| `Application Error` | Shiny no arrancó | Verifica el Comando de inicio y WEBSITES_PORT |
| `Site Disabled (403)` | Cuota de la región agotada | Cambiar a otra cuenta o esperar hasta el día siguiente |
| `Quota 0` | Solo se permite 1 plan gratis por región | Borra los Planes de App Service anteriores |
| `Policy Disallowed` | Tu universidad bloqueó esa región | Usar Canada Central obligatoriamente |
| `Publish profile is invalid` | El nombre de la app no coincide | Reconectar el GitHub en el Centro de implementación |

---

## Flujo Futuro: Cómo Actualizar la App

Una vez desplegada, el flujo de trabajo es:

```
1. Modificas el código en tu PC
2. git add .
3. git commit -m "descripción del cambio"
4. git push origin main
5. GitHub Actions se activa automáticamente
6. En 2-3 minutos, la web ya tiene los cambios
```

**¡No necesitas tocar Azure para actualizar la app!**

# Guía Oficial de Despliegue: InformePDF en Azure Container Apps (ACA)

**Proyecto:** InformePDF - Dashboard de Análisis de Mercado Educativo  
**Framework:** Shiny for Python + WeasyPrint  
**Plataforma:** Azure Container Apps (Plan de Consumo)  
**Método:** CI/CD vía GitHub Actions (Docker)

---

## ¿Por qué Azure Container Apps?

Originalmente usamos App Service F1 (Gratis), pero WeasyPrint y Playwright (necesarios para el PDF) requieren librerías de sistema que Microsoft no permite instalar en el plan gratuito. 

Al usar **Azure Container Apps** con tus créditos de estudiante ($100 USD):
1. **Todo Incluido:** El `Dockerfile` instala todas las librerías necesarias.
2. **Generación de PDF:** El botón de "Descargar PDF" funcionará perfectamente en producción.
3. **Escalabilidad:** Se puede apagar cuando no se usa para ahorrar crédito.

---

## Requisitos Previos

1. **Suscripción:** Azure for Students activa.
2. **Región:** **Canada Central** (OBLIGATORIO por política de UNIMINUTO).
3. **Azure CLI:** Instalado en tu PC (opcional, pero recomendado).

---

## Paso a Paso: Configuración de Recursos

Ejecuta estos comandos en tu terminal (Powershell o Bash) para preparar el entorno:

### 1. Variables de Entorno (Personaliza si quieres)
```bash
# Define nombres únicos
RES_GROUP="rg-informes-final"
LOCATION="canadacentral"
ACR_NAME="acrinformepdf$(date +%s)" # Nombre único para el registro
ACA_ENV="env-informes-pdf"
APP_NAME="informepdf-app"
```

### 2. Crear Grupo de Recursos y Registro (ACR)
```bash
az group create --name $RES_GROUP --location $LOCATION
az acr create --resource-group $RES_GROUP --name $ACR_NAME --sku Basic --admin-enabled true
```

### 3. Crear Entorno de Container Apps
```bash
az containerapp env create --name $ACA_ENV --resource-group $RES_GROUP --location $LOCATION
```

---

## Configuración de Secrets en GitHub

Para que el "Botón" de despliegue automático funcione, ve a tu repositorio en GitHub:
**Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**.

Añade estos 4 secretos:

1. `AZURE_CREDENTIALS`: El JSON resultante de ejecutar:  
   `az ad sp create-for-rbac --name "sp-deploy-pdf" --role contributor --scopes /subscriptions/<TU_SUBSCRIPTION_ID>/resourceGroups/rg-informes-final --json-auth`
2. `ACR_NAME`: El nombre de tu Azure Container Registry (ej: `acrinformepdf12345`).
3. `CONTAINER_APP_NAME`: El nombre de tu app (ej: `informepdf-app`).
4. `RESOURCE_GROUP`: El nombre del grupo (ej: `rg-informes-final`).

---

## Estructura del Proyecto

Asegúrate de que tu `Dockerfile` expone el puerto **8080**:
```dockerfile
EXPOSE 8080
CMD ["python", "-m", "shiny", "run", "app/app.py", "--host", "0.0.0.0", "--port", "8080"]
```

---

## ¿Cómo desplegar?

1. Guarda todos tus cambios localmente.
2. Sube los cambios a GitHub:
   ```bash
   git add .
   git commit -m "Configuración para Container Apps"
   git push origin main
   ```
3. Ve a la pestaña **Actions** en GitHub para ver el progreso.

Una vez finalizado, Azure te dará una URL (ej: `informepdf-app.xxxx.canadacentral.azurecontainerapps.io`). ¡Tu app estará lista con generación de PDF activa!

# Guía de Configuración en Azure

Para que el "Botón de Sincronización" funcione, necesitas configurar estos recursos en Azure y conectarlos con GitHub.

## 1. Crear Recursos en Azure
Necesitas tener creados los siguientes recursos (puedes usar la capa gratuita/consumo):
1.  **Resource Group** (Ej: `rg-informepdf`)
2.  **Azure Container Registry** (ACR) - Es donde se guardan las imágenes. (Ej: `acrinformepdf`)
3.  **Azure Container App** (ACA) - Es donde corre la aplicación.

## 2. Crear las "Llaves" (Service Principal)
Ejecuta el siguiente comando en tu terminal de Azure (Cloud Shell) para generar las credenciales que GitHub usará:

```bash
az ad sp create-for-rbac --name "github-actions-sp" --role contributor \
  --scopes /subscriptions/TU_SUBSCRIPTION_ID/resourceGroups/TU_GRUPO_DE_RECURSOS \
  --sdk-auth
```
*Copia el resultado JSON que te devuelva.*

## 3. Configurar GitHub Secrets
Ve a tu repositorio en GitHub -> **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**.

Añade los siguientes secretos:

| Nombre del Secreto | Valor |
| :--- | :--- |
| `AZURE_CREDENTIALS` | Pega aquí el JSON completo del paso 2. |
| `ACR_NAME` | El nombre de tu registro (ej: `acrinformepdf`). |
| `CONTAINER_APP_NAME` | El nombre de tu Container App. |
| `RESOURCE_GROUP` | El nombre de tu grupo de recursos. |

## 4. ¡Listo!
A partir de ahora, cada vez que hagas `git push`, la aplicación se actualizará sola.

> **Nota:** La primera vez que el robot corra, tardará unos 5-8 minutos porque debe instalar Playwright y todas las dependencias. Las siguientes veces será más rápido.

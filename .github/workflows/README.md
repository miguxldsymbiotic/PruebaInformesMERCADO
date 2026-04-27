# ¿Cómo funciona este "Botón"?

Este archivo `deploy.yml` es el motor de la sincronización automática entre GitHub y Azure.

### ¿Cuándo se activa?
1.  **Automáticamente:** Cada vez que haces un `git push` a la rama `main`.
2.  **Manualmente:** En la pestaña **Actions** de tu repositorio en GitHub, puedes seleccionar "Azure Deployment" y darle al botón "Run workflow".

### ¿Qué hace el robot exactamente?
1.  **Descarga tu código:** Incluye las carpetas de datos que no están en el `.dockerignore`.
2.  **Se conecta a Azure:** Usa el secreto `AZURE_CREDENTIALS`.
3.  **Empaqueta (Build):** Lee tu `Dockerfile`, instala todo (incluyendo Chromium para Playwright) y crea la imagen.
4.  **Sube (Push):** Envía la imagen a tu Azure Container Registry.
5.  **Despliega (Deploy):** Actualiza tu Azure Container App para que use la nueva imagen.

### Requisitos
Para que funcione, debes configurar los **Secrets** en GitHub (ver archivo `AZURE_SETUP.md` en la raíz del proyecto).

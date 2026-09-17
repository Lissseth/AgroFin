# AgroFin Web

AgroFin es una plataforma web para llevar el control financiero y productivo de cultivos. El proyecto se trabajara primero como sitio web responsive; una futura app movil podra reutilizar la API existente cuando el flujo web este validado.

## Alcance actual

- Panel web responsive para escritorio y movil.
- Registro de cultivos y siembras.
- Registro de gastos, compras, produccion y ventas.
- Indicadores de costos, ingresos, utilidad y margen.
- Reportes filtrables con exportacion CSV e impresion a PDF.
- Historial de movimientos.
- Persistencia persistente con SQLite local y soporte a PostgreSQL.
- Registro y actualización del perfil del productor.
- Registro e inicio de sesión con contraseña y sesiones de servidor.
- Datos separados por cuenta autenticada.
- Respaldo local en el navegador para continuar trabajando sin conexion.
- Encabezados de seguridad y opcion de forzar HTTPS en produccion.

## Estructura

```text
AgroFin/
|-- app.py                    # Servidor Flask y API REST
|-- wsgi.py                   # Entrada WSGI para producción
|-- Procfile                  # Arranque en hosts compatibles
|-- Dockerfile                # Imagen de producción
|-- fly.toml                  # Configuración de despliegue en Fly.io
|-- agrofin.db                # Base SQLite creada al ejecutar el servidor
|-- agrofin-reporte.csv       # Archivo de reporte de ejemplo
|-- requirements.txt          # Dependencias del sitio web
|-- templates/
|   `-- index.html             # Panel web y experiencia responsive
`-- tests/
    `-- test_api.py            # Pruebas de la API
```

## Qué subir a GitHub

Si quieres dejar el proyecto listo para desplegar en Render y conectar Google Search Console, el repositorio mínimo recomendado es este:

```text
AgroFin/
|-- app.py
|-- wsgi.py
|-- Procfile
|-- Dockerfile
|-- render.yaml
|-- requirements.txt
|-- .gitignore
|-- .env.example
|-- googleca945794cfdc0b13.html
|-- robots.txt
|-- sitemap.xml
|-- templates/
|   `-- index.html
|   `-- privacy.html
|   `-- terms.html
|-- static/
|   `-- logo-agrofin.svg
|-- tests/
|   `-- test_api.py
`-- README.md
```

No hace falta subir archivos locales de desarrollo como:

- `venv/`
- `__pycache__/`
- `agrofin.db`
- `agrofin-reporte.csv`
- cualquier copia de caché o artefactos temporales

## Ejecutar localmente

En PowerShell, desde la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Abre `http://127.0.0.1:5000` en el navegador.

Para ejecutar las pruebas:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

## API disponible

- `GET /api/perfil`
- `PUT /api/perfil`

- `GET /api/cultivos`
- `POST /api/cultivos`
- `PUT /api/cultivos/<id>`
- `DELETE /api/cultivos/<id>`
- `GET /api/movimientos`
- `POST /api/movimientos`
- `DELETE /api/movimientos/<id>`

## Seguimiento web

### Hecho

- [x] Crear el panel principal de AgroFin.
- [x] Conectar formularios con la API Flask.
- [x] Agregar respaldo local y estado de conexion.
- [x] Agregar reportes y exportacion CSV.
- [x] Preparar instalacion reproducible y documentacion web.

### Siguiente etapa

- [ ] Separar estilos y JavaScript en archivos estaticos mantenibles.
- [x] Agregar autenticacion y cuentas de productores.
- [ ] Definir roles y permisos para equipos de trabajo.
- [ ] Agregar edicion de movimientos desde el historial.
- [ ] Probar la experiencia con productores reales y ajustar el lenguaje.
- [x] Preparar despliegue estándar con HTTPS y almacenamiento persistente.
- [ ] Evaluar PWA o aplicacion movil despues de validar el sitio web.

## Variables de entorno

Copia `.env.example` a `.env` y completa tus valores reales antes de desplegar:

- `FLASK_DEBUG=1`: activa el modo debug durante desarrollo.
- `FORCE_HTTPS=1`: redirige solicitudes HTTP a HTTPS cuando el sitio esta detras de un proxy seguro.
- `HOST=0.0.0.0`: escucha conexiones de otros dispositivos de la red.
- `PORT=5000`: cambia el puerto del servidor.
- `DATABASE_URL`: cadena de conexión PostgreSQL para cualquier hosting con base de datos persistente.
- `DATABASE_PATH`: ruta para SQLite; en Render o Fly.io suele apuntar a un volumen persistente.
- `PUBLIC_URL`: URL pública del sitio, por ejemplo `https://agrofin.onrender.com`.
- `GOOGLE_CLIENT_ID`: ID del cliente OAuth de Google.
- `GOOGLE_CLIENT_SECRET`: secreto OAuth de Google.
- `GOOGLE_REDIRECT_URI`: callback de Google, normalmente `https://TU-SITIO/auth/google/callback`.

Los botones de Google y Facebook quedan preparados visualmente, pero requieren registrar la aplicación en cada proveedor y configurar sus credenciales OAuth antes de habilitar ese acceso.

## Render + Google Search Console

1. Sube este repositorio a GitHub.
2. En Render, crea un servicio Web desde ese repositorio y usa `render.yaml` como base.
3. Define los secretos de entorno en Render; al menos `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` y `PUBLIC_URL`.
4. Cuando el servicio quede activo, prueba `https://TU-PAGINA.onrender.com/health`.
5. En Google Search Console, añade tu dominio o URL y sube el archivo `googleca945794cfdc0b13.html` si usas verificación por HTML.
6. Después de verificar el sitio, entrega el sitemap: `https://TU-PAGINA.onrender.com/sitemap.xml`.

El archivo `robots.txt` ya está preparado para permitir indexación y apuntar al sitemap. Si usas un dominio personalizado, actualiza también la URL pública en `PUBLIC_URL` y en la config de Google.

## Despliegue en Fly.io

`fly.toml` configura la imagen de `Dockerfile`, una máquina con un volumen persistente montado en `/data`, HTTPS automático y el health check `/health`. El nombre `agrofin` es el identificador de la aplicación y debe ser único en Fly.io.

Revisa las condiciones actuales de tu cuenta antes de desplegar: Fly.io puede solicitar verificación de pago y sus créditos o límites gratuitos pueden cambiar. El volumen persistente y una máquina siempre activa pueden generar cargos si superan el crédito disponible.

1. Instala `flyctl`, inicia sesión con `fly auth login` y ejecuta `fly launch --no-deploy` desde la carpeta del proyecto. Si pregunta por crear configuración, conserva el `fly.toml` existente.
2. Cambia `app = "agrofin"` por un nombre disponible si hace falta.
3. Define los secretos: `fly secrets set SECRET_KEY="una-cadena-aleatoria-larga"`. Para Google añade `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` y `GOOGLE_REDIRECT_URI`.
4. Crea el volumen en la región configurada: `fly volumes create agrofin_data --region mia --size 1`.
5. Despliega con `fly deploy` y prueba `https://TU-APP.fly.dev/health`; debe responder `{"status":"ok"}`.

Fly asigna HTTPS automáticamente. Para Google OAuth, registra `https://TU-APP.fly.dev/auth/google/callback` en Google Cloud Console y usa exactamente esa URL en `GOOGLE_REDIRECT_URI`. Un volumen de Fly pertenece a una región y a una máquina: conserva una sola máquina para este SQLite y configura copias de seguridad periódicas del volumen.

### Después del despliegue

- Revisa `fly logs` y `fly status` después de cada despliegue.
- Conserva un solo worker; varios procesos escribiendo SQLite aumentan la posibilidad de bloqueos.
- Usa consultas paginadas cuando crezca el historial y evita cargar grandes reportes completos en una sola respuesta.
- Mantén `PIP_NO_CACHE_DIR=1`, `PYTHONDONTWRITEBYTECODE=1` y el contenedor sin herramientas de desarrollo.

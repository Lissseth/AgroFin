# AgroFin

AgroFin es una aplicación web para que productores agrícolas creen una cuenta y registren sus cultivos, siembras, ingresos y egresos. La interfaz usa la API Flask del mismo servicio; cada registro queda asociado a la cuenta autenticada.

## Funciones disponibles

- Registro e inicio de sesión con correo y contraseña.
- Perfil básico de productor (nombre, teléfono, país y zona horaria).
- Alta, consulta, edición y eliminación de cultivos.
- Registro, consulta y eliminación de movimientos financieros.
- Resumen de ingresos, egresos y balance a partir de los movimientos guardados.
- Sesiones firmadas por Flask y contraseñas almacenadas con hash.
- Almacenamiento SQLite para desarrollo y PostgreSQL para despliegues persistentes.

La gestión de tareas, reportes exportables, recuperación de contraseña y uso sin conexión todavía no están implementados. No se muestran datos ficticios como si fueran registros de una cuenta.

## Ejecutar localmente

En PowerShell, desde la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = "una-clave-local-larga-y-aleatoria"
python app.py
```

Abre `http://127.0.0.1:5000`. La base SQLite se crea como `agrofin.db` en el directorio del proyecto. Para ejecutar las pruebas:

```powershell
python -m unittest discover -v
```

## API

Las operaciones de cuenta:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

Requieren sesión, salvo registro, inicio y consulta de sesión:

- `GET`, `PUT /api/perfil`
- `GET`, `POST /api/cultivos`
- `PUT`, `DELETE /api/cultivos/<id>`
- `GET`, `POST /api/movimientos`
- `DELETE /api/movimientos/<id>`

## Despliegue

El repositorio incluye `Dockerfile`, `Procfile` y `render.yaml`. Antes de abrir el registro al público:

1. Configura un `SECRET_KEY` largo y aleatorio en el proveedor de hosting.
2. Configura `DATABASE_URL` con una base PostgreSQL persistente. El SQLite local del contenedor no debe usarse para datos de producción porque el disco puede ser efímero.
3. Mantén `FORCE_HTTPS=1` detrás del proxy HTTPS del proveedor.
4. Verifica `https://TU-SITIO/health` y crea una cuenta de prueba desde la página principal.

La autenticación con Google no aparece en el formulario de acceso; si se habilita en otra interfaz, requiere configurar las credenciales OAuth y la URL de retorno correspondientes.

## Archivos principales

- `app.py`: aplicación Flask, autenticación, persistencia y API.
- `templates/index.html`: interfaz responsive conectada a la API.
- `templates/privacy.html`, `templates/terms.html`: páginas legales.
- `test_api.py`: pruebas de registro, sesión, aislamiento de cuentas y API.
- `render.yaml`, `Dockerfile`, `Procfile`: configuración de despliegue.

No subas `.env`, `agrofin.db`, `__pycache__` ni archivos de caché al repositorio.

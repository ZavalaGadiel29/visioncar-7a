# VisionCar API (FastAPI + PostgreSQL en Supabase)

API en la nube del carrito autónomo. El carrito registra cada decisión y 4° la consulta desde la web, el dashboard y la app.

- **Guía para 4°:** [API.md](API.md)
- **Diagrama de la base de datos (PostgreSQL):** [DIAGRAMA_BD.md](DIAGRAMA_BD.md) · versión MongoDB: [DIAGRAMA.md](DIAGRAMA.md)
- **Documentación interactiva:** `/docs` (se genera sola con FastAPI)

## Archivos

| Archivo | Qué hace |
|---|---|
| `app/main.py` | Arma la aplicación: protecciones, CORS, documentación y rutas |
| `app/configuracion.py` | Lee `.env` y las variables de entorno |
| `app/modelos.py` | Contrato de datos (Pydantic); rechaza valores no permitidos con 422 |
| `app/seguridad.py` | Tokens Bearer y permisos de cada rol (401/403) |
| `app/limites.py` | Límite de peticiones por IP (429) y tamaño máximo del cuerpo (413) |
| `app/fechas.py` | Manejo de fechas (hora local del carrito) |
| `app/almacen_memoria.py` | Guardado en memoria (MODO_DEMO=1) |
| `app/almacen_postgres.py` | Guardado en PostgreSQL (Supabase), si existe `DATABASE_URL` |
| `app/almacen_mongo.py` | Guardado en MongoDB Atlas e índices |
| `app/catalogo.py` | Lee el catálogo `componentes.json` |
| `sql/esquema.sql` | Tablas, restricciones e índices de PostgreSQL (la API lo ejecuta al arrancar) |
| `app/rutas/*.py` | Endpoints: eventos, estadísticas, control, componentes, demo y salud |
| `app/datos/componentes.json` | Catálogo de piezas del carrito (se edita aquí y se hace push) |
| `tests/` | Pruebas con pytest |
| `render.yaml` | Configuración para publicar en Render |
| `.env.example` | Plantilla de variables (el `.env` real no se sube) |

## 1. Ejecutar en tu laptop (Windows, PowerShell)

```powershell
cd C:\Users\gadie\Desktop\CARRITO\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
notepad .env          # cambia los 3 tokens (déjalo con MODO_DEMO=1)
uvicorn app.main:crear_app --factory --reload
```

Abre **http://127.0.0.1:8000/docs**, presiona **Authorize** y pega un token.

- Si PowerShell no deja activar el entorno: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (solo una vez).
- Generar un token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Para que otros dispositivos de tu red la vean: agrega `--host 0.0.0.0` y usa la IP de tu laptop.

**Pruebas:**
```powershell
python -m pytest -v
```
No necesitan base de datos ni internet: usan el modo demo.

Pruebas con PostgreSQL (opcionales; usa una base **de pruebas**, no la de producción):
```powershell
$env:TEST_DATABASE_URL = "postgresql://usuario:contraseña@host:5432/postgres"
python -m pytest tests/test_postgres.py -v
```
Sin `TEST_DATABASE_URL` esas pruebas se saltan solas.

## 2. Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `TOKEN_CARRITO` | sí | Token del carrito (`POST /eventos`, `GET /control`) |
| `TOKEN_LECTURA` | sí | Token de la web y el dashboard (solo `GET`) |
| `TOKEN_APP` | sí | Token de la app móvil (`GET` y `PUT /control`) |
| `DATABASE_URL` | no | Cadena de PostgreSQL (Supabase). Si tiene valor, se usa PostgreSQL y el modo demo se apaga solo |
| `MODO_DEMO` | no | Solo sin `DATABASE_URL`: `1` = memoria, `0` = MongoDB (por defecto `0`) |
| `DEMO_EVENTOS_INICIALES` | no | Eventos de ejemplo al arrancar en modo demo (100) |
| `MONGODB_URI` | si `MODO_DEMO=0` y no hay `DATABASE_URL` | Cadena de conexión de Atlas |
| `MONGODB_BASE` | no | Nombre de la base (`visioncar`) |
| `CORS_ORIGENES` | no | Dominios de la web de 4°, separados por coma (`*` = todos) |
| `LIMITE_POR_SEGUNDO` | no | Peticiones por segundo por IP (20) |
| `TAMANO_MAXIMO_BYTES` | no | Tamaño máximo del cuerpo (524288 = 512 KB) |
| `SEGUNDOS_VIGENCIA_MANUAL` | no | Segundos que dura una orden manual sin renovar (2) |
| `ZONA_HORARIA` | no | Zona del carrito (`America/Mexico_City`) |

Los tokens deben tener al menos 16 caracteres y ser distintos entre sí. Si no, la API no arranca y explica por qué.

## 3. Publicar en Render (desde GitHub)

El repositorio es `CARRITO` completo; la API está en la subcarpeta `api/`.

1. Sube los cambios a GitHub (desde `C:\Users\gadie\Desktop\CARRITO`):
   ```powershell
   git add api .gitignore
   git commit -m "API VisionCar"
   git push
   ```
   Antes de hacer push, revisa con `git status` que **no** aparezca ningún `.env`.
2. Entra a https://dashboard.render.com, inicia sesión con GitHub y elige **New → Web Service**.
3. Conecta el repositorio `CARRITO` y llena:
   - **Name:** `visioncar-api` (la URL será `https://visioncar-api.onrender.com`; si el nombre está ocupado, Render agrega letras)
   - **Language:** Python 3
   - **Branch:** `main`
   - **Root Directory:** `api`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:crear_app --factory --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"`
   - **Instance Type:** Free
4. En **Environment Variables** agrega:
   `PYTHON_VERSION=3.12.8`, `MODO_DEMO=1`, `TOKEN_CARRITO`, `TOKEN_LECTURA` y `TOKEN_APP` (con tokens nuevos, no los de tu laptop).
5. En **Advanced → Health Check Path** pon `/api/v1/health`.
6. Presiona **Deploy Web Service** y espera a que diga **Live** (2-4 min).
7. Prueba: `https://TU-SERVICIO.onrender.com/api/v1/health` y `https://TU-SERVICIO.onrender.com/docs`.

**Alternativa con Blueprint:** el archivo `render.yaml` ya trae todo esto, y genera los tokens solo. En Render: **New → Blueprint**, elige el repositorio e indica la ruta `api/render.yaml` (Render busca `render.yaml` en la raíz si no se le indica otra). Después copia los tokens generados desde la pestaña **Environment** del servicio.

Cada `git push` a `main` vuelve a publicar la API automáticamente.

**Plan gratuito:** el servicio se duerme tras 15 min sin uso y tarda ~50 s en despertar. En MODO_DEMO, al dormirse o reiniciarse **se borran los datos** (vuelven los 100 eventos de ejemplo); con PostgreSQL (sección 4) los datos se conservan.

## 4. Base de datos PostgreSQL en Supabase (datos reales)

Con `DATABASE_URL` la API guarda en PostgreSQL. Las rutas, los tokens y las respuestas son los mismos que en modo demo: 4°A no tiene que cambiar nada.

1. Crea una cuenta en https://supabase.com y un proyecto (**New project**). Elige una región cercana (ej. *East US*) y una **contraseña de base de datos** (guárdala; usa solo letras y números para no tener que codificar símbolos en la URL).
2. En el proyecto presiona **Connect** (arriba).
3. En **Connection String → URI**, elige **Session pooler** (método *Session*). No uses *Direct connection*: solo funciona con IPv6 y Render no lo tiene.
4. Copia la cadena. Se ve así:
   `postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres`
5. Cambia `[YOUR-PASSWORD]` por tu contraseña (sin los corchetes) y agrega al final `?sslmode=require`.
6. Ponla como `DATABASE_URL`: en tu laptop en `api/.env`, y en Render en la pestaña **Environment** (como secreto). **Nunca** la pongas en un archivo que se suba a GitHub.
7. Al arrancar, la API crea sola las tablas `eventos`, `control` y `componentes` (con el SQL de [sql/esquema.sql](sql/esquema.sql)) y carga el catálogo de componentes si la tabla está vacía. Puedes verlas en Supabase → **Table Editor**.
8. Revisa `/api/v1/health`: debe decir `"base_datos": "postgresql"` y `"conexion": "ok"`. Si dice `"error"`, revisa la contraseña de la cadena.

Diagrama y explicación de las tablas: [DIAGRAMA_BD.md](DIAGRAMA_BD.md).

> Si cambias algo en `app/datos/componentes.json` cuando la tabla ya tiene datos, edita la fila en Supabase (Table Editor) o vacía la tabla (`DELETE FROM componentes;`) y reinicia la API para que la vuelva a cargar.

## 4b. (Alternativa) Base gratuita en MongoDB Atlas

1. Crea una cuenta en https://www.mongodb.com/cloud/atlas/register.
2. **Create → Cluster → M0 (Free)**. Elige AWS y una región cercana (ej. `us-east-1`). Nombre: `Cluster0`.
3. **Database Access → Add New Database User**: usuario `visioncar_api`, contraseña generada (guárdala), rol *Read and write to any database*.
4. **Network Access → Add IP Address → Allow access from anywhere** (`0.0.0.0/0`). Es necesario porque Render gratis no tiene IP fija; la protección la da el usuario y la contraseña.
5. **Database → Connect → Drivers → Python**: copia la cadena
   `mongodb+srv://visioncar_api:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`
   y cambia `<password>` por la contraseña. Si la contraseña tiene símbolos como `@`, `:` o `/`, genera otra solo con letras y números.
6. En Render (pestaña **Environment**): `MONGODB_URI=<la cadena>`, `MODO_DEMO=0` y **sin** `DATABASE_URL` (si existe, gana PostgreSQL). Guarda y Render vuelve a publicar.
7. Revisa `/api/v1/health`: debe decir `"base_datos": "ok"`. Los índices se crean solos. Ve el diagrama en [DIAGRAMA.md](DIAGRAMA.md).

## 5. Conectar el carrito

En la carpeta del carrito (`CARRITO/.env`) pon `TOKEN_CARRITO=<el mismo de Render>`, y en `config.json → nube.api_url` la URL de Render (sin `/api/v1`).

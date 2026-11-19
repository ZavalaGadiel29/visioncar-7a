# API VisionCar: guía para 4°

Esta API guarda cada decisión que toma el carrito autónomo. Ustedes la usan desde la **página web**, el **dashboard en vivo** y la **app móvil**.

- **URL base:** `https://visioncar-api.onrender.com/api/v1`
- **Documentación interactiva:** `https://visioncar-api.onrender.com/docs`. Ahí pueden probar cada endpoint con el botón **Try it out**.
- **Formato:** JSON (UTF-8).
- **Fechas:** hora local del carrito, sin zona horaria: `"2026-12-04T10:15:32"`.

> **Importante (plan gratuito de Render):** si nadie usa la API durante 15 minutos, se "duerme". La primera petición después de eso tarda unos **50 segundos**. Muestren un "cargando..." y no la den por caída.
> **Modo demo:** mientras el carrito no esté listo, la API corre en modo demo: los datos son de ejemplo y **se borran cuando la API se reinicia**. Al arrancar siempre se crean 100 eventos de ejemplo del carrito `celula-1`.

---

## 1. Cómo mandar el token

Cada petición (excepto `/health`) lleva este encabezado:

```
Authorization: Bearer <token>
```

| Token | Para quién | Puede usar |
|---|---|---|
| `TOKEN_LECTURA` | Página web y dashboard | Todos los `GET` (incluye `/componentes`) |
| `TOKEN_APP` | App móvil | Todos los `GET` + `PUT /control` + `POST /demo/generar` |
| `TOKEN_CARRITO` | Solo el carrito | `POST /eventos` y `GET /control` (ustedes no lo necesitan) |

Los tokens se los pasa el grupo del carrito por un medio privado. **No los suban a GitHub.**
Ojo: un token puesto en el JavaScript de una página pública lo puede ver cualquiera. Por eso la web usa `TOKEN_LECTURA`, que solo puede leer.

**JavaScript (web):**
```js
const URL_BASE = "https://visioncar-api.onrender.com/api/v1";
const TOKEN = "pega-aqui-TOKEN_LECTURA";

async function api(ruta, opciones = {}) {
  const r = await fetch(URL_BASE + ruta, {
    ...opciones,
    headers: { "Authorization": `Bearer ${TOKEN}`, "Content-Type": "application/json", ...(opciones.headers || {}) },
  });
  if (!r.ok) throw new Error(`${r.status}: ${JSON.stringify(await r.json())}`);
  return r.json();
}

const ultimo = await api("/eventos/ultimo?carrito_id=celula-1");
```

**curl:**
```bash
curl -H "Authorization: Bearer TOKEN_LECTURA" "https://visioncar-api.onrender.com/api/v1/eventos?limite=5"
```

---

## 2. El evento (contrato de datos)

```json
{"carrito_id": "celula-1", "fecha": "2026-12-04T10:15:32", "senal": "alto", "confianza": 0.93, "accion": "detener", "modo": "autonomo"}
```

| Campo | Tipo | Valores |
|---|---|---|
| `carrito_id` | texto | ej. `celula-1` |
| `fecha` | texto (fecha) | `AAAA-MM-DDTHH:MM:SS`, hora local |
| `senal` | texto | `alto`, `avanzar`, `izquierda`, `derecha`, `ninguna`, `peaton` |
| `confianza` | número | de `0` a `1` (en `ninguna` el carrito manda `0`) |
| `accion` | texto | `avanzar`, `detener`, `girar_izq`, `girar_der` |
| `modo` | texto | `autonomo`, `manual` |

Al consultar, cada evento trae además `"id"` (texto único).

---

## 3. Endpoints

### `GET /health`: estado de la API (sin token)
```json
// 200
{"estado": "ok", "base_datos": "memoria", "modo_demo": true, "version": "1.0.0", "hora_servidor": "2026-12-04T10:15:40"}
```
`base_datos`: `ok` (MongoDB funciona), `memoria` (modo demo) o `error` (responde **503**).

### `GET /eventos`: historial (LECTURA o APP)
Del **más reciente al más antiguo**. Todos los parámetros son opcionales:

| Parámetro | Ejemplo | Descripción |
|---|---|---|
| `carrito_id` | `celula-1` | Solo ese carrito |
| `senal` | `alto` | Solo esa señal |
| `accion` | `detener` | Solo esa acción |
| `desde` | `2026-12-04T10:00:00` | Desde esa fecha (incluida) |
| `hasta` | `2026-12-04T11:00:00` | Hasta esa fecha (incluida) |
| `limite` | `100` | Cuántos devolver: 1 a **500** (por defecto 50) |

```
GET /api/v1/eventos?carrito_id=celula-1&senal=alto&limite=2
```
```json
// 200
[
  {"carrito_id": "celula-1", "fecha": "2026-12-04T10:15:32", "senal": "alto", "confianza": 0.93, "accion": "detener", "modo": "autonomo", "id": "675012ab34cd56ef7890abcd"},
  {"carrito_id": "celula-1", "fecha": "2026-12-04T10:02:11", "senal": "alto", "confianza": 0.88, "accion": "detener", "modo": "autonomo", "id": "675012ab34cd56ef7890abc1"}
]
```
Si no hay resultados responde `[]`. Si mandan `desde` con "Z" (por ejemplo, de `new Date().toISOString()`), se convierte a la hora local.

### `GET /eventos/ultimo?carrito_id=celula-1`: último evento (LECTURA o APP)
Ideal para el **dashboard en vivo**: consúltenlo cada 1-2 segundos.
```json
// 200
{"carrito_id": "celula-1", "fecha": "2026-12-04T10:15:32", "senal": "alto", "confianza": 0.93, "accion": "detener", "modo": "autonomo", "id": "675012ab34cd56ef7890abcd"}
```
```json
// 404 si todavía no hay eventos
{"detail": "No hay eventos registrados."}
```

### `GET /estadisticas?carrito_id=celula-1`: resumen (LECTURA o APP)
Sin `carrito_id`, se calcula con todos los carritos.
```json
// 200
{
  "carrito_id": "celula-1",
  "total_eventos": 120,
  "conteo_por_senal": {"alto": 15, "avanzar": 20, "izquierda": 10, "derecha": 11, "ninguna": 56, "peaton": 8},
  "conteo_por_accion": {"avanzar": 70, "detener": 23, "girar_izq": 14, "girar_der": 13},
  "confianza_promedio_por_senal": {"alto": 0.8812, "avanzar": 0.8301, "izquierda": 0.79, "derecha": 0.8122, "ninguna": 0.0, "peaton": 0.8455},
  "senal_mas_detectada": "avanzar",
  "evento_menor_confianza": {"carrito_id": "celula-1", "fecha": "2026-12-04T10:05:12", "senal": "ninguna", "confianza": 0.0, "accion": "avanzar", "modo": "autonomo", "id": "675012ab34cd56ef7890ab99"}
}
```
- `senal_mas_detectada` **no cuenta** `ninguna`. Es `null` si no hay datos.
- Si una señal nunca apareció, su promedio es `null`.

### `GET /control/celula-1`: modo actual (LECTURA, APP o CARRITO)
```json
// 200
{"modo": "manual", "accion": "girar_izq", "actualizado": "2026-12-04T10:15:32"}
```
Si nunca se ha configurado: `{"modo": "autonomo", "accion": "detener", "actualizado": null}`.

### `PUT /control/celula-1`: tomar o soltar el control (solo APP)
```json
// Petición: tomar el control
{"modo": "manual", "accion": "avanzar"}
```
```json
// 200
{"modo": "manual", "accion": "avanzar", "actualizado": "2026-12-04T10:15:32"}
```
```json
// Petición: devolverle el control al carrito
{"modo": "autonomo"}
```
**Regla de seguridad:** mientras estén en modo manual, **repitan el PUT cada 0.5-1 segundos**, aunque el usuario no cambie de botón. Si pasan más de **2 segundos** sin un PUT, la API le dice al carrito `detener`. Así, si la app se cierra o se va el internet, el carrito se para.

```js
// App: mientras un botón esté presionado
const TOKEN_APP = "pega-aqui-TOKEN_APP";
let accionActual = "detener";
const latido = setInterval(() => {
  fetch(`${URL_BASE}/control/celula-1`, {
    method: "PUT",
    headers: { "Authorization": `Bearer ${TOKEN_APP}`, "Content-Type": "application/json" },
    body: JSON.stringify({ modo: "manual", accion: accionActual }),
  });
}, 500);
// al salir del modo manual: clearInterval(latido) y PUT {"modo": "autonomo"}
```

### `GET /componentes`: piezas del carrito (LECTURA o APP)
Catálogo fijo, en el mismo orden siempre. Útil para la página web ("¿de qué está hecho el carrito?").

| Parámetro | Ejemplo | Descripción |
|---|---|---|
| `tipo` | `energía` | Opcional. `control`, `sensor`, `comunicación`, `actuador`, `energía` o `estructura` (con acento; `fetch` lo codifica solo). Otro valor da **422** |

```
GET /api/v1/componentes?tipo=actuador
```
```json
// 200
[
  {"id": "l298n", "nombre": "Driver de motores L298N", "tipo": "actuador", "voltaje": "5–35 V (en el carrito 7.4 V)", "cantidad": 1, "funcion": "Da la potencia a los motores y controla su sentido"},
  {"id": "motor_tt", "nombre": "Motor DC TT con reductora", "tipo": "actuador", "voltaje": "3–6 V", "cantidad": 2, "funcion": "Mueven las ruedas traseras"}
]
```
`voltaje` es `null` en las piezas que no llevan electricidad (chasis, ruedas, antena).

Ids disponibles: `esp32cam`, `ov2640`, `antena`, `l298n`, `motor_tt`, `hw131`, `bateria_18650`, `portapilas`, `chasis`, `ruedas`, `rueda_loca`.

### `GET /componentes/{id}`: una pieza (LECTURA o APP)
```
GET /api/v1/componentes/esp32cam
```
```json
// 200
{"id": "esp32cam", "nombre": "ESP32-CAM AI-Thinker", "tipo": "control", "voltaje": "5 V (opera a 3.3 V)", "cantidad": 1, "funcion": "Transmite el video y mueve los motores según los comandos"}
```
```json
// 404 si el id no existe
{"detail": "No existe el componente 'motor_x'."}
```

```js
// Web: tabla de componentes
const piezas = await api("/componentes");
const energia = await api("/componentes?tipo=" + encodeURIComponent("energía"));
```

### `POST /demo/generar`: crear datos de ejemplo (solo APP, solo en modo demo)
```json
// Petición (todos los campos son opcionales)
{"carrito_id": "celula-1", "cantidad": 50, "minutos": 10}
```
```json
// 201
{"generados": 50, "carrito_id": "celula-1"}
```
`cantidad`: 1 a 500. `minutos`: los eventos se reparten en los últimos N minutos. Si la API no está en modo demo, responde 404.

### `POST /eventos`: registrar eventos (solo CARRITO)
Lo usa el carrito, no ustedes. Acepta un evento o una lista. Responde **201** `{"guardados": 1, "ids": ["..."]}`.

---

## 4. Códigos de respuesta

| Código | Significa | Qué hacer |
|---|---|---|
| 200 / 201 | Todo bien | — |
| 307 | La raíz `/` redirige a `/docs` | — |
| **401** | Falta el token o es incorrecto | Revisar el encabezado `Authorization: Bearer ...` |
| **403** | El token es válido pero no tiene permiso (ej. LECTURA haciendo PUT) | Usar el token correcto |
| 404 | No hay eventos todavía, o la ruta no existe | Mostrar "sin datos" |
| 413 | Cuerpo demasiado grande (más de 512 KB) | Mandar menos datos |
| **422** | Los datos no cumplen el contrato | Leer `detail`: dice qué campo falló |
| **429** | Más de 20 peticiones por segundo desde la misma IP | Esperar 1 s; consultar con menos frecuencia |
| 503 | La base de datos no responde | Reintentar más tarde |

Ejemplo de error 422:
```json
{
  "mensaje": "Los datos no cumplen el contrato de la API. Revisa 'detail'.",
  "detail": [{"type": "literal_error", "loc": ["body", "modo"], "msg": "Input should be 'autonomo' or 'manual'", "input": "turbo", "ctx": {"expected": "'autonomo' or 'manual'"}}]
}
```
Ejemplo de error 401 o 403:
```json
{"detail": "Token incorrecto."}
```

> El límite de 20 peticiones por segundo es **por IP**. Si muchos celulares o computadoras usan la misma red (por ejemplo, el wifi de la escuela), comparten ese límite. Un dashboard que consulta cada 1-2 s no tiene problema.

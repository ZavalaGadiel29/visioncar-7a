# Diagrama de la base de datos (MongoDB Atlas)

Base de datos: `visioncar` · Colecciones: `eventos` y `control`.

```mermaid
erDiagram
    EVENTOS {
        ObjectId _id PK "lo asigna MongoDB; la API lo devuelve como 'id' (texto)"
        string carrito_id "ej. celula-1 (letras, números, - y _; máx. 50)"
        date fecha "hora local del carrito, sin zona (2026-12-04T10:15:32)"
        string senal "alto | avanzar | izquierda | derecha | ninguna | peaton"
        double confianza "0 a 1"
        string accion "avanzar | detener | girar_izq | girar_der"
        string modo "autonomo | manual"
        date recibido "hora UTC en que llegó a la API (interno, no se devuelve)"
    }
    CONTROL {
        string _id PK "carrito_id"
        string modo "autonomo | manual"
        string accion "avanzar | detener | girar_izq | girar_der"
        date actualizado "hora UTC del último PUT de la app"
    }
    CONTROL ||--o{ EVENTOS : "carrito_id"
```

## Índices de `eventos`

| Nombre | Campos | Para qué |
|---|---|---|
| `_id_` | `_id` | Automático de MongoDB |
| `idx_carrito_fecha` | `carrito_id` ↑, `fecha` ↓ | Historial y último evento de un carrito |
| `idx_fecha` | `fecha` ↓ | Historial de todos los carritos y filtros `desde`/`hasta` |
| `idx_senal_fecha` | `senal` ↑, `fecha` ↓ | Filtro por señal |
| `idx_carrito_confianza` | `carrito_id` ↑, `confianza` ↑ | Evento de menor confianza en estadísticas |

Los índices se crean solos cuando arranca la API.
La colección `control` solo usa el índice automático de `_id` (hay un documento por carrito).

## Ejemplo de documento en `eventos`

```json
{
  "_id": {"$oid": "675012ab34cd56ef7890abcd"},
  "carrito_id": "celula-1",
  "fecha": {"$date": "2026-12-04T10:15:32.000Z"},
  "senal": "alto",
  "confianza": 0.93,
  "accion": "detener",
  "modo": "autonomo",
  "recibido": {"$date": "2026-12-04T16:15:33.120Z"}
}
```

> En Atlas, `fecha` aparece con "Z" porque MongoDB siempre muestra las fechas así, pero el valor es la hora local del carrito (10:15:32). `recibido` sí es UTC.

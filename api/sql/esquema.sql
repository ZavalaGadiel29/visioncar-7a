-- =====================================================================
-- esquema.sql — Base de datos de VisionCar en PostgreSQL (Supabase)
-- La API ejecuta este archivo al arrancar: todo usa IF NOT EXISTS, así
-- que correrlo varias veces no borra ni duplica nada.
-- Diagrama y explicación: api/DIAGRAMA_BD.md
-- =====================================================================

-- ---------------------------------------------------------------------
-- eventos: cada decisión que toma el carrito (una fila por decisión)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eventos (
    id           SERIAL           PRIMARY KEY,
    carrito_id   VARCHAR(50)      NOT NULL,
    fecha        TIMESTAMP(3)     NOT NULL,              -- hora local del carrito, sin zona
    senal        VARCHAR(20)      NOT NULL,
    confianza    DOUBLE PRECISION NOT NULL,
    accion       VARCHAR(20)      NOT NULL,
    modo         VARCHAR(20)      NOT NULL,
    recibido_en  TIMESTAMPTZ      NOT NULL DEFAULT now(), -- cuándo llegó a la API

    CONSTRAINT eventos_carrito_id_valido CHECK (carrito_id ~ '^[A-Za-z0-9_-]{1,50}$'),
    CONSTRAINT eventos_senal_valida      CHECK (senal IN ('alto', 'avanzar', 'izquierda', 'derecha', 'ninguna', 'peaton')),
    CONSTRAINT eventos_confianza_valida  CHECK (confianza >= 0 AND confianza <= 1),
    CONSTRAINT eventos_accion_valida     CHECK (accion IN ('avanzar', 'detener', 'girar_izq', 'girar_der')),
    CONSTRAINT eventos_modo_valido       CHECK (modo IN ('autonomo', 'manual'))
);

-- Historial de un carrito, del más reciente al más antiguo
CREATE INDEX IF NOT EXISTS idx_eventos_carrito_fecha ON eventos (carrito_id, fecha DESC);
-- Historial de todos los carritos y filtros por fecha (desde / hasta)
CREATE INDEX IF NOT EXISTS idx_eventos_fecha ON eventos (fecha DESC);

-- ---------------------------------------------------------------------
-- control: el modo y la acción manual que la app le manda a cada carrito
-- (una fila por carrito; se sobrescribe en cada PUT)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control (
    carrito_id   VARCHAR(50)  PRIMARY KEY,
    modo         VARCHAR(20)  NOT NULL,
    accion       VARCHAR(20)  NOT NULL,
    actualizado  TIMESTAMPTZ  NOT NULL,

    CONSTRAINT control_carrito_id_valido CHECK (carrito_id ~ '^[A-Za-z0-9_-]{1,50}$'),
    CONSTRAINT control_modo_valido       CHECK (modo IN ('autonomo', 'manual')),
    CONSTRAINT control_accion_valida     CHECK (accion IN ('avanzar', 'detener', 'girar_izq', 'girar_der'))
);

-- ---------------------------------------------------------------------
-- componentes: catálogo de piezas del carrito (solo lectura para la API)
-- Se llena desde app/datos/componentes.json cuando la tabla está vacía.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS componentes (
    id        VARCHAR(40)   PRIMARY KEY,
    nombre    VARCHAR(100)  NOT NULL,
    tipo      VARCHAR(20)   NOT NULL,
    voltaje   VARCHAR(100),                 -- NULL si la pieza no usa electricidad
    cantidad  INTEGER       NOT NULL,
    funcion   VARCHAR(255)  NOT NULL,
    orden     INTEGER       NOT NULL,       -- posición en el catálogo (para listarlas igual que el JSON)

    CONSTRAINT componentes_id_valido       CHECK (id ~ '^[a-z0-9_]{1,40}$'),
    CONSTRAINT componentes_tipo_valido     CHECK (tipo IN ('control', 'sensor', 'comunicación', 'actuador', 'energía', 'estructura')),
    CONSTRAINT componentes_cantidad_valida CHECK (cantidad >= 1)
);

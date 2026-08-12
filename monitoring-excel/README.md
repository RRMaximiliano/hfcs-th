# Excel de monitoreo para campo

Este generador descarga los tres formularios de Kobo y crea **un archivo independiente por provincia** para compartir con cada equipo de campo. Cada libro incluye:

- un resumen de estudiantes, docentes y directivos;
- conteos por provincia, escuela, grado y rol;
- todos los envíos recibidos, ordenados para poder filtrarlos;
- todos los registros que pertenecen a posibles grupos duplicados;
- carga y alertas por aplicador;
- columnas editables para documentar cuál envío debe conservarse.

## Privacidad

El Excel contiene identificadores y nombres/etiquetas tal como llegan de Kobo. **No debe publicarse en GitHub Pages ni enviarse fuera del equipo autorizado.** Los libros generados, las vistas previas y `node_modules` están excluidos de Git.

La detección marca casos para revisión; no modifica ni elimina respuestas en Kobo. Un grupo duplicado no implica automáticamente que el envío más reciente sea incorrecto.

## Generar el archivo

El script lee el token y los UIDs desde `../.env`, igual que el tablero.

```bash
cd Analysis/HFCs/monitoring-excel
node build_monitoring_excel.mjs
```

El comando genera o reemplaza únicamente los archivos actuales en:

```text
outputs/provinces/current/
├── monitoreo_la-rioja.xlsx
├── monitoreo_mendoza.xlsx
└── monitoreo_tucuman.xlsx
```

La lista se amplía automáticamente cuando Kobo recibe datos de una provincia nueva. Los códigos técnicos como `la_rioja` se normalizan al nombre legible `La Rioja` dentro del tablero y de los libros.

Opciones útiles:

```bash
# Crear solo una provincia
node build_monitoring_excel.mjs --province Mendoza

# Elegir el archivo de salida para una sola provincia
node build_monitoring_excel.mjs --province Mendoza --output outputs/provinces/current/monitoreo_mendoza.xlsx

# Crear libros de prueba fuera de la carpeta actual
node build_monitoring_excel.mjs --sample --output-dir .qa/sample-outputs

# Guardar vistas de control visual fuera de outputs
node build_monitoring_excel.mjs --preview-dir .qa/previews
```

`--by-province` todavía se acepta por compatibilidad, pero ya no es necesario: la generación provincial es el comportamiento predeterminado. Cada libro contiene únicamente los registros de su provincia y vuelve a calcular sus posibles duplicados localmente.

El entorno de Codex ya contiene `@oai/artifact-tool`. Fuera de Codex, instale esa dependencia en este directorio antes de ejecutar el generador.

## Regla de duplicados

- Estudiantes: mismo identificador final en más de un envío.
- Docentes: mismo identificador final y misma escuela en más de un envío.
- Directivos: mismo identificador final y misma escuela en más de un envío.

Para revisar, use la hoja **Posibles duplicados**. Cada envío del grupo aparece en una fila. Complete `Estado revisión`, `¿Conservar?`, `Confirmado por`, `Fecha revisión` y `Observaciones`. Guarde el archivo revisado con otro nombre antes de volver a generar una actualización.

## Códigos de grado

- Estudiantes: `1 = 2.º`, `2 = 3.º`, `3 = 4.º`.
- Docentes: `1 = 2.º`, `2 = 3.º`, `3 = 4.º`, `4 = Multigrado`, `5 = Otro`.

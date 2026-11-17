# Tablero de monitoreo HFC

Tablero estático para revisar la recolección de Todos Humanos. `index.html` muestra el **cierre (post)**; `linea-de-base.html` conserva el tablero de línea de base.

## Cierre (post), noviembre de 2026

Dos capas, con reglas de privacidad distintas:

| Capa | Qué contiene | Dónde corre | Quién lo ve |
|---|---|---|---|
| Tablero web (`index.html`, `data/endline.json`) | Solo agregados por escuela y aplicador seudonimizado: seguimiento de línea de base, registros nuevos, duplicados, entrevistas no completas, aplicación no individual, aplicador que conocía el grupo, duraciones fuera de rango | GitHub Actions, todos los días a las 12:00 y 20:30 (hora Argentina) | Público (GitHub Pages) |
| Excel confidencial (`scripts/endline_field_report.py`) | Nombres, DNI e IDs: duplicados con columnas de decisión, posibles personas de línea de base registradas como nuevas, pendientes por escuela, alertas por entrevista, aplicadores | Localmente; escribe en Dropbox (`<TH_PROJECT_ROOT>/Analysis/Data/HFC/Endline/`), nunca en el repositorio | Solo el equipo autorizado |

El tablero de cierre **nunca** publica el grupo de tratamiento, las respuestas del cuestionario (incluidas exposición y recuerdo de Todos Humanos), nombres ni DNI. `tests/test_pipeline.py` lo verifica.

### Instalación local

El repositorio vive fuera de Dropbox (`~/Developer/GitHub/hfcs-th`). Los datos confidenciales siguen en Dropbox: en `.env` (no se sube a Git) defina `TH_PROJECT_ROOT` con la carpeta del proyecto, además de `KOBO_API_TOKEN` (ver `.env.example`).

### Rutina diaria

```bash
cd hfcs-th
# Excel confidencial + resumen agregado en Markdown para el reporte diario
python3 scripts/endline_field_report.py
```

El mismo comando escribe `decisiones_cierre.csv`, que lee la limpieza de Stata (`Analysis/Code/Endline/` en Dropbox). Las decisiones escritas en las columnas amarillas de `Duplicados` y `Posibles_no_vinculados` se conservan al regenerar (se cruzan por UUID del envío, como `ieduplicates`). Cada versión anterior queda en `archivo/`. El proceso no modifica ni borra nada en Kobo.

### Configuración

- `config/forms_endline.json`: proyectos de Kobo del cierre (los UID no son secretos; el token sí), campos y umbrales de duración (estudiantes: menos de 15 o más de 120 minutos; adultos: menos de 8).
- `config/targets_endline.csv`: participantes de línea de base por escuela, el denominador del seguimiento. Se genera localmente desde el marco confidencial con `python3 scripts/build_endline_targets.py`; vuelva a generarlo si cambia la versión del marco.
- Seguimiento = personas de línea de base encontradas en el cierre con el mismo ID (`frame_baseline_participant = 1`) sobre participantes de línea de base de la escuela.

## Línea de base (agosto de 2026)

Las secciones siguientes describen el tablero de línea de base. Resume estudiantes, docentes y directivos por escuela y grado; señala posibles duplicados; muestra la carga por aplicador; compara el total descargado con el total informado por Kobo; y expone envíos recientes sin publicar DNI ni respuestas individuales.

## Qué se necesita de Kobo

Los tres enlaces públicos de Enketo sirven para responder los formularios, pero no contienen el identificador de proyecto que exige la API. Para conectar el tablero hacen falta:

1. Un token de API de Kobo con permiso de lectura sobre los tres proyectos.
2. El `asset UID` de cada proyecto: estudiantes, docentes y directivos.
3. Opcionalmente, una cadena secreta aleatoria para seudonimizar los identificadores, guardada como `HFC_HASH_SALT`. Si se omite, el proceso usa el token como clave estable sin publicarlo.

El token se obtiene en `https://eu.kobotoolbox.org/token/?format=json` después de iniciar sesión. El `asset UID` aparece en la URL del proyecto, después de `/forms/`, por ejemplo:

```text
https://eu.kobotoolbox.org/#/forms/aBcDeFg123/summary
                                      ^^^^^^^^^^^
                                      asset UID
```

No pegue el token en `forms.json`, en el código o en un commit. Para GitHub, agréguelo directamente en **Settings > Secrets and variables > Actions**.

### Secretos de GitHub

| Tipo | Nombre | Contenido |
|---|---|---|
| Secret | `KOBO_API_TOKEN` | Token personal de Kobo |
| Secret opcional | `HFC_HASH_SALT` | Cadena aleatoria de al menos 32 caracteres |
| Variable | `KOBO_STUDENT_ASSET_UID` | UID del proyecto de estudiantes |
| Variable | `KOBO_TEACHER_ASSET_UID` | UID del proyecto de docentes |
| Variable | `KOBO_DIRECTOR_ASSET_UID` | UID del proyecto de directivos |

El tablero usa `https://eu.kobotoolbox.org/api/v2/assets/{uid}/data/`, sigue todas las páginas de resultados y consulta `deployment__submission_count` para contrastar el total de Kobo con los registros descargados.

## Ejecutar localmente

La versión incluida se abre con datos de muestra:

```bash
cd hfcs-th
python3 scripts/build_dashboard_data.py --sample
python3 -m http.server 8000
```

Abra `http://localhost:8000`. No abra `index.html` directamente con `file://`, porque el navegador bloqueará la lectura del archivo JSON.

Para actualizar desde Kobo en una terminal:

```bash
cd hfcs-th
export KOBO_API_TOKEN="..."
export HFC_HASH_SALT="..."
export KOBO_STUDENT_ASSET_UID="..."
export KOBO_TEACHER_ASSET_UID="..."
export KOBO_DIRECTOR_ASSET_UID="..."
python3 scripts/build_dashboard_data.py
```

## Usar exportaciones XLSX

También se puede construir el tablero desde los archivos XLSX descargados manualmente. La configuración recomendada en Kobo es **valores XML y encabezados XML**, porque coincide con los nombres estables del XLSForm (`student_id`, `school_id`, `grade`, etc.). El lector usa la primera hoja, que en una exportación estándar contiene los registros principales.

```bash
cd hfcs-th
export HFC_HASH_SALT="una-cadena-secreta-larga-y-estable"
python3 scripts/build_dashboard_data.py \
  --xlsx students=/ruta/estudiantes.xlsx \
  --xlsx teachers=/ruta/docentes.xlsx \
  --xlsx directors=/ruta/directivos.xlsx
```

El repositorio ignora archivos `.xlsx`, `.xls`, las carpetas `raw/` y `private/`, y archivos `.env` para reducir el riesgo de publicar datos identificables.

## Excel confidencial para el equipo de campo

El generador local en `monitoring-excel/build_monitoring_excel.mjs` crea un libro independiente por provincia con todos los envíos recibidos, conteos por escuela y grado, carga por aplicador y una hoja editable para revisar posibles duplicados. A diferencia del tablero público, estos archivos incluyen identificadores y nombres/etiquetas para que el equipo pueda confirmar cuál envío debe conservarse.

```bash
cd monitoring-excel
node build_monitoring_excel.mjs
```

Los libros actuales se guardan en `monitoring-excel/outputs/provinces/current/` y están excluidos de Git. Consulte `monitoring-excel/README.md` antes de compartirlos.

## Agregar metas por escuela

Edite `config/targets.csv` sin cambiar los encabezados:

```csv
school_id,school_name,province,target_students,target_teachers,target_directors
LR-001,Escuela Primaria Norte,La Rioja,40,4,1
```

Cuando hay metas, el tablero calcula avance total y por escuela. Sin metas, muestra los conteos observados y marca el avance como no disponible.

## Publicar en GitHub Pages

Esta carpeta está preparada para funcionar como la raíz de un repositorio independiente:

1. Cree un repositorio y copie el contenido de `Analysis/HFCs/` a su raíz.
2. Agregue `KOBO_API_TOKEN`, las tres variables y, si desea una clave separada, `HFC_HASH_SALT`.
3. En **Settings > Pages > Build and deployment**, seleccione **GitHub Actions**.
4. Haga un push a `main` o ejecute manualmente **Actualizar tablero HFC** desde la pestaña Actions.

La acción corre pruebas, descarga los datos y publica una nueva versión cada 10 minutos. GitHub puede demorar las ejecuciones programadas durante períodos de alta demanda. Si todavía faltan credenciales, publica la muestra y deja una advertencia en el registro.

## Definición de duplicado

- Estudiantes: mismo identificador final en más de un envío, sin importar la escuela declarada.
- Docentes: mismo identificador final y misma escuela en más de un envío.
- Directivos: mismo identificador final y misma escuela en más de un envío.

Un caso marcado es una señal para revisión. El proceso no borra ni modifica respuestas en Kobo.

## Codificación de grados

- Estudiantes: `1 = 2.º`, `2 = 3.º`, `3 = 4.º`.
- Docentes: `1 = 2.º`, `2 = 3.º`, `3 = 4.º`, `4 = Multigrado`, `5 = Otro`.

La misma transformación se usa en el tablero web y en los libros provinciales.

## Privacidad

El trabajo de GitHub Actions recibe el token como secreto y lo usa únicamente para leer la API. `data/dashboard.json` contiene agregados, seudónimos HMAC truncados y hasta 250 registros recientes sin DNI ni respuestas del cuestionario. Si GitHub Pages será público, revise igualmente que los nombres de escuela, fechas y volúmenes sean apropiados para publicación.

## Pruebas

```bash
cd hfcs-th
python3 -m unittest discover -s tests -v
```

El proyecto usa solamente la biblioteca estándar de Python y JavaScript del navegador. No requiere instalar paquetes.

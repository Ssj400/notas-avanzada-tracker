# Notas Avanzada Tracker · IIC2233

Versiones **web y PyQt6** para consultar la [planilla pública de calificaciones](https://docs.google.com/spreadsheets/d/1xOywnk0h-9JFxPDQKpZmB0jL-rQOKxdeBnGg3V4KBEA/edit?gid=579970152), explorar rankings y destacar tu posición. Herramienta personal, no una plataforma oficial del curso. **Nunca modifica Google Sheets.**

## Web — GitHub Pages

**https://ssj400.github.io/notas-avanzada-tracker/**

Sin instalación, contraseña, backend, claves de Google ni dependencias JavaScript. El navegador descarga directamente el CSV público de Google; **no se copia la planilla al repositorio ni se incluye una descarga de notas en el sitio publicado**. Cada visita consulta la fuente, con actualización manual o cada cinco minutos mientras la pestaña esté abierta.

- En cada apertura eliges tu número de alumno; se precarga el último usado. Puedes **Cambiar usuario** durante el uso o mirar el ranking sin perfil. Esto no autentica ni verifica identidad.
- Nombres personalizados, perfil, preferencia de actualización y última copia de notas viven en `localStorage` del navegador, bajo el prefijo `notas-avanzada-tracker:v1:`. Si no se puede guardar, se advierte y se continúa en memoria.
- La copia local se marca **desactualizada** al abrir o si falla una consulta; no se ocultan los fallos detrás de datos aparentemente recientes.
- Puedes importar/exportar solo nombres en JSON. El formato es compatible con PyQt6, pero **no hay sincronización automática** entre aplicaciones, navegadores o dispositivos.
- Si Google cambia la estructura, revoca el acceso público o deja de permitir la lectura desde el navegador, se muestra un error. No se utilizan proxies de terceros para sortearlo.

Para probar la web localmente:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory web
```

Abre `http://127.0.0.1:8000/`. No abras `index.html` como `file://`: los módulos JavaScript necesitan HTTP. Los datos del navegador local no se trasladan automáticamente a la dirección de GitHub Pages.

## Escritorio — PyQt6

La versión nativa no necesita navegador ni servidor web.

Python **3.10 o superior** y PyQt6. Si tu Python ya tiene PyQt6, basta con:

```bash
python3 desktop.py
```

Para instalarlo en un entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python desktop.py
```

La descarga necesita conexión. Después de una consulta exitosa puedes ver la copia local sin conexión, señalada como desactualizada. Para usar otra carpeta de datos:

```bash
python3 desktop.py --data-dir /ruta/a/mis-datos
```

**En cada inicio aparece una pantalla para elegir tu número de alumno**, con el último usado precargado. Pulsa **Entrar** para continuar. Durante el uso puedes pulsar **Cambiar usuario…**: las tarjetas, tu fila destacada y **Ver mi posición** pasan al nuevo número, sin reiniciar ni volver a descargar la planilla. Cancelar el cambio conserva el perfil actual. Es un selector local, **no autenticación con contraseña**, pues los datos de origen son públicos.

## Funciones

- Rankings de nota final, Banner, presentación, Cátedra y evaluaciones escritas con los valores **calculados por la planilla**.
- Ranking por actividad, interrogación y examen cuando se detectan resultados.
- Promedio de **puntos** de actividades disponibles, distinto de la nota oficial de Cátedra.
- Búsqueda, filtros Top, posición del usuario elegido y nombres editables por alumno. `26663279` aparece inicialmente como **José Garrillo**, incluso si estás viendo otro perfil.
- Último usuario recordado en `.data/profile.json`; puedes confirmarlo o cambiarlo cada vez que entres y desde el panel.
- Nombres persistentes en `.data/aliases.json`, con importación/exportación JSON para respaldarlos.
- Actualización manual o automática cada cinco minutos **mientras la aplicación esté abierta**.
- Cambios numéricos respecto de la consulta anterior que detectó modificaciones. No es un historial completo ni indica la hora de edición del profesor: las fechas corresponden a consultas de la aplicación.

## Cómo interpretar los datos

1. **Totales provisionales:** no se infieren notas futuras, ponderaciones ni aprobación. Los valores de categorías y totales se leen directamente de Google.
2. Una columna sin valores positivos se marca **sin resultados detectados**. Es una heurística: no permite distinguir una evaluación pendiente de una con todos los puntajes en cero. Se habilita al detectar un resultado positivo.
3. Los ceros individuales en columnas disponibles **sí cuentan**. Los vacíos no son ceros; no se rankean. Si falta un dato de una actividad disponible, no se calcula ese promedio para el alumno.
4. Empates con posiciones **1, 2, 2, 4**. Buscar o filtrar Top no cambia las posiciones globales. Se comparan los valores recibidos, no lo mostrado redondeado en pantalla.
5. El promedio de actividades es una media simple de puntos de las AC con algún valor positivo; no convierte a escala 1–7 ni sustituye la nota oficial de Cátedra.

## Organización — patrones del curso

- `desktop.py`: widgets de PyQt6, señales/slots y descarga en un hilo separado para no bloquear la ventana.
- `grades.py`: lectura CSV, validación y normalización de la planilla.
- `tracker.py`: descarga HTTP con `urllib`, caché JSON y detección de cambios.
- `rankings.py`: funciones puras para ranking, empates y validación de nombres.
- `test_*.py`: pruebas independientes de la fuente real.
- `web/index.html`, `web/style.css`, `web/app.js`: interfaz web estática y perfiles locales.
- `web/core.js`: lectura CSV, validación y rankings equivalentes a Python, sin dependencias.
- `web/core.test.mjs`: pruebas Node nativas, sin acceso a la fuente real.
- `.github/workflows/pages.yml`: prueba ambas versiones y despliega **solo `web/`** en GitHub Pages.

Sin frameworks web, analítica ni servicios adicionales. La única dependencia externa de Python es **PyQt6**. La web no requiere instalar Python ni Node para usarla.

## Publicación

El repositorio es [Ssj400/notas-avanzada-tracker](https://github.com/Ssj400/notas-avanzada-tracker). Los pushes a `main` ejecutan pruebas Python/Qt y JavaScript antes de publicar en Pages. Los pull requests ejecutan pruebas sin desplegar. También se puede lanzar el workflow manualmente desde **Actions**.

GitHub Pages debe usar **GitHub Actions** como origen de publicación. El código web usa rutas relativas y funciona bajo `/notas-avanzada-tracker/`. El despliegue no publica `.data/`, entornos virtuales ni archivos de configuración local.

## Datos locales y privacidad

En **PyQt6**, los nombres y la copia de notas se guardan solo en tu equipo, en `.data/` (o `--data-dir`), no en Google. En la **web**, se guardan solo en el navegador. No se sincronizan entre dispositivos. Exporta los nombres para trasladarlos; en escritorio, borrar `aliases.json` elimina las personalizaciones, y borrar `grades.json` elimina la copia de notas. El nombre inicial de José vuelve a aparecer por defecto.

`profile.json` guarda únicamente el último número seleccionado. Cambiar de perfil no cambia ni borra los nombres, que siempre están asociados al número de cada alumno. No se almacenan contraseñas ni se crea una sesión de Google.

La carpeta de datos está ignorada por Git y los archivos se guardan con permisos restrictivos. No la compartas ni publiques sus notas. La fuente es una planilla pública; esta aplicación no le añade controles de acceso.

## Pruebas

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest -v
npm test
node --check web/app.js
```

Las pruebas usan datos de prueba: CSV, comas decimales, encabezados multilínea, pendientes, ceros, empates, nombres inválidos, perfiles, caché incompatible, cambios y fallos de conexión. `offscreen` permite probar PyQt6 sin abrir ventanas. Para las pruebas web se necesita Node 18 o superior; no hace falta `npm install`.

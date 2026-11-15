# librepcb-mcp — Servidor MCP para LibrePCB

Servidor [MCP (Model Context Protocol)](https://modelcontextprotocol.io) que permite a Claude
diseñar, revisar, **rutear** y dejar listas para fabricar placas hechas en
[LibrePCB](https://librepcb.org), sin pasar por la interfaz gráfica para las tareas repetitivas.

Trabaja directamente sobre los archivos del proyecto (formato S-expression de LibrePCB) y usa
`librepcb-cli` para el ERC, el DRC y las salidas de fabricación. Para el ruteo automático usa
[Freerouting](https://github.com/freerouting/freerouting) con un Java portátil propio.

- **Versión probada:** LibrePCB 2.1.1 (formato de archivo 2), macOS (Intel y Apple Silicon), Python 3.10+.
- **Fabricante por defecto:** JLCPCB (perfil de reglas, piezas básicas, BOM y CPL de montaje).
- **¿Términos que no conoces?** Ver el [glosario](glossary.md).

---

## Índice

1. [Qué hace](#1-qué-hace)
2. [Requisitos](#2-requisitos)
3. [Instalación](#3-instalación)
4. [Configuración](#4-configuración)
5. [Flujo de trabajo recomendado](#5-flujo-de-trabajo-recomendado)
6. [Referencia de herramientas](#6-referencia-de-herramientas)
7. [Cómo funciona el ruteo automático](#7-cómo-funciona-el-ruteo-automático)
8. [Archivos que crea en tu proyecto](#8-archivos-que-crea-en-tu-proyecto)
9. [Estructura del repositorio](#9-estructura-del-repositorio)
10. [Solución de problemas](#10-solución-de-problemas)
11. [Limitaciones conocidas](#11-limitaciones-conocidas)
12. [Historial de cambios](#12-historial-de-cambios)
13. [Referencias de LibrePCB](#13-referencias-de-librepcb)
14. [Otras referencias](#14-otras-referencias)

---

## 1. Qué hace

| Área | Qué puedes pedirle a Claude |
|---|---|
| Proyectos | Listar, crear desde una plantilla, leer archivos del proyecto |
| Revisión | ERC, DRC, revisión previa al ruteo (`preflight`) y revisión final (`production_check`) |
| Placa | Contorno, agujeros de montaje, fiduciales, planos de GND, zonas prohibidas, clases de red, ubicación de piezas |
| Piezas | MPN, fabricante y código LCSC; piezas **básicas** de JLCPCB automáticas; marcar "no montar" (DNP) |
| Ruteo | Ruteo automático de punta a punta con verificación pin por pin y escritura directa en la placa |
| Serigrafía | Nombres sin choques, quitar valores, engrosar líneas por debajo del mínimo |
| Fabricación | Gerber + taladros (ZIP), BOM, pick & place, iBOM, IPC-D-356, STEP; BOM y CPL en formato JLCPCB |
| Seguridad | Toda modificación guarda una copia y se puede deshacer (`undo_last_change`) |

Principio de diseño: **nada se entrega sin verificarse primero**. El ruteo se revisa antes de
escribirse en la placa, y cada herramienta que modifica archivos se niega a hacerlo si LibrePCB
tiene el proyecto abierto, para que un guardado desde la GUI no pise los cambios.

---

## 2. Requisitos

| Requisito | Notas |
|---|---|
| LibrePCB 2.x | Incluye `librepcb-cli`. En macOS está en `/Applications/LibrePCB.app/Contents/MacOS/librepcb-cli` |
| Python ≥ 3.10 | `start.sh` crea su propio entorno virtual (`.venv`) |
| SDK de MCP para Python | `mcp>=1.2` (se instala solo) |
| Claude Desktop | Cliente MCP |
| Java 21 + Freerouting 2.0.1 | Solo para el ruteo. `./start.sh --install-freerouting` los deja en `tools/` sin tocar el Java del sistema |
| Conexión a GitHub | Solo durante la instalación de Java y Freerouting |

---

## 3. Instalación

```bash
# 1. Ubica la carpeta del servidor (por ejemplo en tu carpeta personal)
cd ~/librepcb-mcp
chmod +x start.sh route_worker.py

# 2. Entorno de Python + configuración de Claude Desktop
./start.sh --install

# 3. Ruteo automático: Freerouting 2.0.1 y Java 21 portátil en tools/
./start.sh --install-freerouting

# 4. Comprobar
./start.sh --check
```

`--check` debe mostrar la versión de `librepcb-cli` y una línea como:

```
Ruteo: Java 21 + freerouting-2.0.1.jar
```

5. En LibrePCB crea un proyecto vacío llamado `plantilla` dentro de la carpeta de proyectos
   (lo usa `create_project`).
6. Cierra Claude Desktop con **⌘Q** y vuelve a abrirlo. Las herramientas nuevas aparecen en las
   conversaciones que inicies después.

### Opciones de `start.sh`

| Opción | Qué hace |
|---|---|
| *(sin opción)* | Arranca el servidor (lo usa Claude Desktop; habla MCP por stdout) |
| `--setup` | Crea o actualiza el entorno virtual |
| `--install` | Escribe la entrada `librepcb` en `claude_desktop_config.json` (guarda un `.bak`) |
| `--install-freerouting` | Descarga Freerouting 2.0.1 y ejecuta `--install-java` |
| `--install-java` | Descarga y descomprime un Java 21 (Temurin) portátil para tu plataforma en `tools/` |
| `--config` | Imprime el bloque JSON para pegarlo a mano en la configuración |
| `--check` | Verifica Python, el SDK, `librepcb-cli` y el ruteador |

---

## 4. Configuración

`./start.sh --install` agrega esto a
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "librepcb": {
      "command": "/Users/<tu_usuario>/librepcb-mcp/start.sh",
      "env": {
        "LIBREPCB_PROJECTS_DIR": "/Users/<tu_usuario>/LibrePCB-Workspace/projects",
        "LIBREPCB_TEMPLATE": "/Users/<tu_usuario>/LibrePCB-Workspace/projects/plantilla"
      }
    }
  }
}
```

### Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `LIBREPCB_PROJECTS_DIR` | `~/LibrePCB-Workspace/projects` | Dónde se buscan y crean los proyectos |
| `LIBREPCB_TEMPLATE` | `<proyectos>/plantilla` | Proyecto que copia `create_project` |
| `LIBREPCB_CLI` | Ruta estándar de la app en macOS | Ruta a `librepcb-cli` si está en otro lugar |
| `FREEROUTING_JAR` | `tools/freerouting-2.0.1.jar` | Otro `.jar` de Freerouting |
| `JAVA` | Java de `tools/`, luego el del sistema | Java a usar para Freerouting |

### Tabla de piezas básicas propia (opcional)

`assign_basic_parts` usa una tabla verificada en jlcpcb.com. Para ampliarla crea
`tools/jlc_basic.json` (valor en ohm o farad):

```json
[
  {"type": "R", "size": "0603", "value": 2200, "mpn": "<MPN>",
   "manufacturer": "<fabricante>", "lcsc": "<código LCSC>"}
]
```

> Verifica cada código en jlcpcb.com antes de agregarlo: la tabla se aplica tal cual.

---

## 5. Flujo de trabajo recomendado

Con **LibrePCB cerrado**, pídele a Claude cada paso, o todo de una vez:

| # | Paso | Herramientas |
|---|---|---|
| 0 | Estado del proyecto | `project_lock_status` |
| 1 | Revisión previa | `preflight`: ocupación, potencia en pads finos, puentes de cobre, piezas sin parte |
| 2 | Piezas | `assign_basic_parts` → `set_component_part` (lo que falte) → `set_dnp` |
| 3 | Reglas y placa | `apply_fab_profile` → `set_board_outline` → `add_mounting_holes` → `add_fiducials` → `add_keepout_zone` → `add_ground_planes` → `set_net_class` |
| 4 | Ubicación | `get_board_layout` / `move_components` hasta que no haya superposiciones |
| 5 | Ruteo | `autoroute` → `autoroute_status`, hasta 0 sin rutear y 0 problemas |
| 6 | Revisión | `run_drc` → `fix_silkscreen` → `run_drc` |
| 7 | Cierre | `production_check` → `export_production(fab="jlcpcb")` |

Resultado en `output/produccion/`:

- `<proyecto>_gerber.zip`: se sube en jlcpcb.com
- `jlcpcb/BOM.csv` y `jlcpcb/CPL.csv`: montaje (PCB Assembly)
- `asm/<proyecto>_iBOM.html`: BOM interactiva para ubicar piezas

> En la vista previa de montaje de JLCPCB revisa siempre la rotación de las piezas: algunas
> librerías usan un 0° distinto al de JLCPCB.

---

## 6. Referencia de herramientas

En todas, `project` acepta el nombre del proyecto, su carpeta o la ruta al `.lpp`, y `board`
(opcional) el nombre de la placa (por defecto la primera).

### Proyectos y archivos

| Herramienta | Parámetros | Descripción |
|---|---|---|
| `list_projects` | — | Proyectos `.lpp` en la carpeta de proyectos |
| `create_project` | `name`, `template_path?` | Crea un proyecto copiando la plantilla |
| `list_project_files` | `project` | Archivos del proyecto |
| `read_project_file` | `project`, `file` | Lee un archivo de texto del proyecto |
| `project_lock_status` | `project`, `remove_if_stale=false` | Dice si LibrePCB tiene el proyecto abierto o si el bloqueo es viejo (proceso inexistente); puede quitarlo |
| `undo_last_change` | `project` | Restaura la última copia de seguridad |
| `cli_command` | `args` | Ejecuta cualquier comando de `librepcb-cli` |

### Revisión

| Herramienta | Parámetros | Descripción |
|---|---|---|
| `run_erc` | `project` | Electrical Rule Check |
| `run_drc` | `project`, `board?` | Design Rule Check |
| `preflight` | `project`, `board?` | Revisión **antes de rutear** |
| `production_check` | `project`, `board?` | Lista final: ERC, DRC (errores y avisos por separado), contorno, superposiciones, planos, pistas, agujeros, fiduciales, piezas sin MPN/LCSC (sin contar las DNP) y bloqueo |

### Placa

| Herramienta | Parámetros | Descripción |
|---|---|---|
| `apply_fab_profile` | `profile="jlcpcb"\|"pcbway"\|"conservador"`, `thickness=1.6` | Reglas de DRC del fabricante |
| `set_board_outline` | `width`, `height`, `corner_radius`, `x`, `y` | Contorno rectangular con esquinas redondeadas |
| `add_mounting_holes` | `diameter`, `inset` | Cuatro agujeros en las esquinas |
| `add_fiducials` | `inset` | Tres fiduciales para montaje SMT |
| `add_ground_planes` | `net`, `layers`, `clearance`, `board_clearance`, `connect_style` | Planos de cobre |
| `add_keepout_zone` | `x1, y1, x2, y2`, `layers?`, `no_copper`, `no_planes`, `no_devices`, `no_exposure` | Zona prohibida (p. ej. bajo una antena). Solo admite capas que existen en la placa |
| `set_net_class` | `name`, `nets`, `trace_width`, `clearance`, `via_drill` | Clase de red (p. ej. potencia a 0.5 mm) |
| `get_board_layout` | `project` | Posición, rotación y superposiciones |
| `move_components` | `moves` | Mueve o gira piezas |

### Piezas

| Herramienta | Parámetros | Descripción |
|---|---|---|
| `set_component_part` | `designators`, `mpn`, `manufacturer`, `lcsc` | Asigna la parte a una o más piezas |
| `list_parts` | `project` | Tabla de piezas; marca las "NO MONTAR" |
| `assign_basic_parts` | `dry_run=false`, `overwrite=false` | Piezas básicas de JLCPCB para resistencias y condensadores según valor y tamaño |
| `set_dnp` | `designators`, `dnp=true` | Marca o desmarca "no montar" (atributo `DNP` del componente) |

### Ruteo

| Herramienta | Parámetros | Descripción |
|---|---|---|
| `autoroute` | `apply=true`, `clearance=0.15`, `signal_width=0.2`, `allow_rotate=[]`, `timeout_minutes=20` | Ruteo completo en segundo plano (ver §7) |
| `autoroute_status` | `project` | Avance o resultado del último ruteo |
| `export_dsn` | `clearance`, `signal_width` | Genera `output/route/board.dsn` desde los archivos, sin la GUI |
| `fix_dsn` | `dsn_file`, `clearance` | Corrige un DSN exportado desde la GUI y escribe `*-fixed.dsn` |
| `verify_ses` | `ses_file?` | Verifica un ruteo: conexiones pin por pin, separación pista↔pad, anchos, borde, taladros y giros |
| `apply_ses` | `ses_file?`, `force=false` | Escribe un ruteo en `board.lp` (borra el anterior y conserva los fiduciales) |
| `clear_routing` | `project` | Borra pistas, vías y uniones (conserva los fiduciales) |

### Serigrafía y salidas

| Herramienta | Parámetros | Descripción |
|---|---|---|
| `fix_silkscreen` | `remove_values=true`, `hide_unplaced=true`, `fix_legend_widths=true` | Nombres en lugares libres, sin valores, líneas al mínimo |
| `export` | `schematics_pdf`, `bom_csv`, `netlist`, `gerbers`, `pick_and_place` | Salidas sueltas |
| `export_production` | `fab="jlcpcb"`, `include_step=true` | Todo para fabricar y montar |

---

## 7. Cómo funciona el ruteo automático

`autoroute` lanza `route_worker.py` en segundo plano y responde de inmediato. El avance queda en
`output/route/<fecha>/status.json` y se consulta con `autoroute_status`.

1. **DSN completo y corregido** (`librepcb_route.export_dsn`), generado desde `circuit.lp`, la
   librería local y `board.lp`:
   - clases de red con su ancho (p. ej. potencia a 0.5 mm);
   - separación global igual a la mayor entre la pedida, la del DRC y la de cada clase;
   - GND y las demás redes con plano se rutean **con pistas**, porque las pistas cortan los
     planos y estos no garantizan la conexión;
   - formas reales de los pads, incluidas las ranuras;
   - zonas sin cobre, agujeros de placa y de footprint, y margen de 0.5 mm en los fiduciales;
   - franja sin vías (0.8 mm) y sin cobre junto al borde;
   - separación entre vías calculada a partir de la distancia mínima entre taladros.
2. **Etapa 1:** Freerouting con los anchos completos.
3. **Etapa 2:** si faltan conexiones, la etapa 1 queda fija y se completa con pistas finas.
4. **Etapa 3:** se rehace el ruteo solo en un radio de 3 y luego 6 mm alrededor de los pines que faltan.
5. **Variantes:** separación 0.15/0.14/0.16 mm y señales de 0.2/0.15 mm. Se queda con la que
   deja menos conexiones sin rutear y, a igualdad, menos problemas de reglas.
6. **Giros, solo con permiso:** con `allow_rotate=["U6"]` o `["auto"]` prueba girar piezas de
   las redes que faltan, empezando por las de dos pads. Descarta los giros que chocan con piezas
   o pads vecinos.
7. **Verificación** (`verify_routing`): conectividad pin por pin, separación pista↔pad con la
   forma real del pad (esquinas redondeadas incluidas), anchos mínimos por clase, distancia de
   pistas y vías al borde, y distancia entre taladros de vías.
8. **Ajustes finos** (`nudge_fix`): separa vías y aleja puntos del borde hasta 0.12 mm.
9. **Escritura en la placa** (`apply_ses_to_board`), si `apply=true` y LibrePCB está cerrado:
   - agrupa pistas, vías y uniones en segmentos de red conectados;
   - divide correctamente las uniones en "T";
   - si una pista termina dentro de un pad pero no en su centro, agrega un tramo corto dentro
     del pad, porque LibrePCB llevaría el extremo al centro y movería la pista;
   - elimina las vías que no conectan pistas en ambas capas;
   - aplica los giros de piezas del resultado.

Freerouting 2.0.x a veces no escribe el resultado o queda colgado después de escribirlo. El
servidor cierra el proceso apenas el archivo queda estable, detecta errores de carga y reintenta
hasta 3 veces. También quita las definiciones de vía duplicadas que LibrePCB rechazaría al importar.

---

## 8. Archivos que crea en tu proyecto

```
<proyecto>/
├── output/
│   ├── mcp-backups/           copias antes de cada cambio (para undo_last_change)
│   ├── route/<fecha>/         job.json, status.json, *.dsn, *.ses, best.ses, worker.log
│   ├── route/board.dsn        lo genera export_dsn
│   └── produccion/            Gerber ZIP, asm/, jlcpcb/BOM.csv, jlcpcb/CPL.csv
└── circuit/circuit.lp         atributo DNP en las piezas marcadas
```

---

## 9. Estructura del repositorio

| Archivo | Contenido |
|---|---|
| `librepcb_mcp.py` | Servidor MCP (FastMCP) y todas las herramientas |
| `librepcb_route.py` | Motor de placa sin dependencias de MCP: modelo del proyecto, DSN, SES, verificación, escritura en `board.lp`, serigrafía, revisión previa, piezas básicas, Java y Freerouting |
| `route_worker.py` | Proceso en segundo plano de `autoroute` |
| `start.sh` | Arranque, instalación y diagnóstico |
| `requirements.txt` | `mcp>=1.2` |
| `SKILL.md` | Procedimiento para Claude (skill "librepcb-a-jlcpcb") |
| `glossary.md` | Glosario de términos técnicos y siglas |
| `tools/` | `freerouting-*.jar`, `jre21-*` y `jlc_basic.json` opcional (no van en el ZIP; los instala `start.sh`) |

---

## 10. Solución de problemas

| Síntoma | Causa y solución |
|---|---|
| Las herramientas nuevas no aparecen | Reinicia Claude Desktop (⌘Q) y abre una conversación nueva |
| El servidor no arranca | Revisa `~/Library/Logs/Claude/mcp-server-librepcb.log`, corre `./start.sh --check` y verifica que `start.sh` sea ejecutable (`chmod +x start.sh`) |
| "El proyecto está abierto en LibrePCB" | Cierra LibrePCB (⌘Q). Si persiste: `project_lock_status(remove_if_stale=true)` |
| "No hay un Java compatible con Freerouting" | `./start.sh --install-java` |
| `autoroute_status` deja conexiones sin rutear | Revisa las piezas sugeridas y relanza con `allow_rotate=[...]`, o agranda la placa (ver `preflight`) |
| DRC: "Unknown layer: inner" | Zona creada en una capa interna en una placa de 2 capas (versiones anteriores del servidor). Quita `(layer inner)` del `board.lp` o recrea la zona |
| DRC con vías juntas o pistas al borde por pocas centésimas | Relanza `autoroute`; los ajustes finos lo corrigen |
| Quiero volver atrás | `undo_last_change`, o copia el archivo desde `output/mcp-backups/` |

---

## 11. Limitaciones conocidas

- Pensado y probado para placas de **2 capas** en macOS. En Linux debería funcionar
  (`--install-java` descarga el Java de Linux), pero no está probado.
- `export_dsn` aproxima con un rectángulo los pads de forma poligonal personalizada y los de
  esquinas redondeadas parciales.
- `autoroute` no mueve piezas: solo las gira, y solo con permiso. Las placas muy ocupadas (más
  del 50 % según `preflight`) pueden necesitar más espacio.
- La tabla de piezas básicas solo incluye valores verificados (resistencias y condensadores
  0603/0805 comunes). El resto se asigna con `set_component_part`.
- La marca DNP es un atributo propio (`DNP=true`) que interpreta este servidor; no usa las
  variantes de montaje nativas de LibrePCB.
- La GUI de LibrePCB no recarga los archivos cambiados por fuera: ciérrala antes de usar las
  herramientas que modifican y no guardes encima.

---

## 12. Historial de cambios

### 2026-11-15
- **Nuevo motor `librepcb_route.py`** y nuevas herramientas: `export_dsn`, `fix_dsn`,
  `autoroute_status`, `verify_ses`, `apply_ses`, `clear_routing`, `set_dnp`,
  `assign_basic_parts`, `preflight` y `project_lock_status`.
- `autoroute`: ruteo de punta a punta en segundo plano (etapas 1-2-3, variantes, giros
  opcionales, verificación, ajustes finos y escritura en la placa), con Java 21 portátil y
  Freerouting 2.0.1.
- `add_keepout_zone`: ya no usa la capa `inner` en placas de 2 capas (rompía el DRC).
- `apply_fab_profile("jlcpcb")`: ranura metalizada mínima de 0.5 mm.
- `fix_silkscreen`: ubicación sin choques; quita valores, oculta los nombres que no caben y
  engrosa las líneas finas de los footprints.
- `production_check`: separa errores de avisos, ignora las piezas DNP y avisa del bloqueo.
- `export_production`: saca las piezas DNP de la BOM y el CPL de JLCPCB y rellena la columna Comment.
- Mensaje de bloqueo: distingue entre LibrePCB abierto y bloqueo viejo.
- La versión anterior quedó en `backups/2026-11-14/`.

---

## 13. Referencias de LibrePCB

### Sitio y documentación de usuario
- Sitio oficial: <https://librepcb.org>
- Documentación completa en PDF, versión 2.1.1: <https://download.librepcb.org/releases/2.1.1/librepcb-documentation-2.1.1.pdf>

### Interfaz de línea de comandos (`librepcb-cli`), usada para ERC, DRC y salidas
- Introducción a la CLI: <https://librepcb.org/docs/cli>
- Instalación (ruta del binario en macOS, AppImage, Docker): <https://librepcb.org/docs/cli/installation/>
- Texto de ayuda y lista de comandos: <https://librepcb.org/docs/cli/help>
- Comando `open-project` (`--erc`, `--drc`, `--run-jobs`): <https://librepcb.org/docs/cli/open-project>
- Comando `open-library`: <https://librepcb.org/docs/cli/open-library>
- Página de la función CLI: <https://librepcb.org/features/cli/>

### Formato de archivos (base de las herramientas que editan los `.lp`)
- Formato de archivo (S-expressions; estable dentro de cada versión mayor): <https://librepcb.org/features/file-format/>
- Documentación para desarrolladores: <https://developers.librepcb.org/>
- Especificación de proyecto (incluye el bloqueo del directorio, `.lock`): <https://developers.librepcb.org/d5/d75/doc_project.html>
- Especificación de librería (estructura de carpetas y archivos `.lp`): <https://developers.librepcb.org/df/d4f/doc_library.html>
- Versionado del formato de archivos: <https://developers.librepcb.org/d1/d69/doc_versioning.html>
- Clase `SExpression` (ejemplo de `netsegment` y `via`): <https://developers.librepcb.org/dc/da5/classlibrepcb_1_1_s_expression.html>

### Ruteo con Specctra DSN / SES
- LibrePCB 1.2.0, que agregó la exportación DSN y la importación SES (#1457): <https://librepcb.org/blog/2024-12-01_release_1.2.0>
- LibrePCB 2.1.1, con correcciones en la exportación DSN (#1844) y en la importación SES con versiones recientes de Freerouting (#1842): <https://librepcb.org/blog/2026-06-12_release_2.1.1/>

### Código fuente
- Repositorio de LibrePCB: <https://github.com/LibrePCB/LibrePCB>
- Repositorio de la documentación: <https://github.com/LibrePCB/librepcb-doc>

---

## 14. Otras referencias

- Freerouting (autorouter Specctra DSN/SES): <https://github.com/freerouting/freerouting>
- Integraciones de Freerouting con otros programas: <https://github.com/freerouting/freerouting/blob/master/docs/integrations.md>
- Java Temurin 21 (Adoptium): <https://github.com/adoptium/temurin21-binaries/releases>
- Model Context Protocol: <https://modelcontextprotocol.io>
- JLCPCB: <https://jlcpcb.com>

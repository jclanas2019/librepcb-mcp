---
name: librepcb-a-jlcpcb
description: Lleva un proyecto de LibrePCB hasta archivos listos para fabricar y montar en JLCPCB usando el servidor MCP de LibrePCB, con verificación antes de cada entrega y sin pedirle al usuario pasos en la GUI.
---

# LibrePCB → JLCPCB sin sobre-iterar

Usa las herramientas del servidor MCP `librepcb`. Regla central: **nunca entregues al usuario
un archivo o una placa sin haberlo verificado tú primero**. Solo pregúntale lo que es decisión
suya (tamaño de placa, mover o girar piezas, quitar un puente). Todo lo demás se resuelve solo.

## 0. Antes de tocar nada
1. `project_lock_status`. Si LibrePCB tiene el proyecto abierto, pide UNA vez: "Cierra LibrePCB
   con ⌘Q (sin guardar si no cambiaste nada)". Si el bloqueo es viejo, `remove_if_stale=true`.
   No modifiques archivos con el proyecto abierto: un guardado desde la GUI pisa tus cambios.
2. `preflight`. Resuelve o pregunta, en una sola tanda, lo que marque:
   - **Ocupación > 50 %**: proponer agrandar la placa AHORA (no después de rutear).
   - **Potencia que no cabe en pads finos**: es esperado; el ruteo hará el tramo final más delgado.
   - **Cobre propio en footprints (puentes cerrados)**: decidir con el usuario (abrir el puente o unir redes).
   - **Piezas sin MPN/LCSC**: pasos 1.

## 1. Piezas
1. `assign_basic_parts(dry_run=true)` → revisar → `assign_basic_parts`.
2. Lo que quede: buscar en jlcpcb.com (preferir **Basic**) y `set_component_part`.
3. `set_dnp` para pines que se sueldan a mano, puentes de soldadura y pads de prueba.
4. `list_parts` para confirmar.

## 2. Placa
`apply_fab_profile("jlcpcb")`, `set_board_outline`, `add_mounting_holes`, `add_fiducials`
(inset distinto al de los agujeros), `add_keepout_zone` (antena), `add_ground_planes`,
`set_net_class` (p. ej. potencia 0.5 mm con separación ≤ 0.15 mm). Ubicar con
`get_board_layout` / `move_components` hasta "Sin superposiciones".

## 3. Ruteo (todo dentro del servidor)
1. `autoroute` (apply=true). Corre en segundo plano: consulta `autoroute_status` cada 1–2 min.
   El servidor ya hace: DSN corregido, etapas 1-2-3, variantes de separación y de ancho,
   verificación pin por pin, ajustes finos de vías/borde y escritura en la placa.
2. Si quedan conexiones sin rutear, `autoroute_status` dice qué piezas permitirían cerrarlas.
   Pregunta al usuario si autoriza girar esas piezas y relanza con `allow_rotate=[...]`
   (las rotaciones que chocan con otras piezas se descartan solas).
3. **No** pidas exportar DSN ni importar SES en la GUI: `export_dsn`, `verify_ses` y `apply_ses`
   lo reemplazan. Si el usuario trae un DSN de la GUI, `fix_dsn` antes de rutear.

## 4. Cierre
1. `run_drc`: 0 errores. Si hay errores de vías juntas o pistas al borde de pocas centésimas,
   relanza `autoroute` (los ajustes finos los corrigen) en vez de editar a mano.
2. `fix_silkscreen` (quita valores, nombres sin choques, líneas al mínimo). Vuelve a `run_drc`.
3. `production_check` → debe decir "LISTA PARA FABRICAR".
4. `export_production(fab="jlcpcb")` → ZIP de Gerbers + `jlcpcb/BOM.csv` + `jlcpcb/CPL.csv`.
5. Recuérdale al usuario revisar la rotación de las piezas en la vista previa de montaje de JLCPCB.

## Cómo informar
- Una línea de qué se hizo y el resultado verificado (DRC, conexiones), no la lista de pasos.
- Si algo necesita al usuario, una sola pregunta concreta con la opción recomendada.
- Cuenta honesta de iteraciones si las pide; reconoce errores propios sin rodeos.

#!/usr/bin/env python3
"""
Servidor MCP para LibrePCB.

Expone herramientas para listar, crear (desde plantilla), inspeccionar,
verificar (ERC/DRC) y exportar proyectos LibrePCB usando librepcb-cli.

Requisitos:
    pip install mcp          (funciona con mcp 1.x y 2.x)
    librepcb-cli en el PATH (o variable LIBREPCB_CLI con la ruta)

Variables de entorno (opcionales):
    LIBREPCB_PROJECTS_DIR   Carpeta raíz de proyectos (default: ~/LibrePCB-Workspace/projects)
    LIBREPCB_CLI            Ruta al ejecutable librepcb-cli (default: "librepcb-cli")
    LIBREPCB_TEMPLATE       Proyecto plantilla para create_project (carpeta con un .lpp)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import librepcb_route as R  # noqa: E402  (motor de placa: DSN/SES, verificación, serigrafía)

try:  # SDK mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # SDK mcp 1.x
    from mcp.server.fastmcp import FastMCP

# === Configuración ===

PROJECTS_DIR = Path(
    os.environ.get("LIBREPCB_PROJECTS_DIR", "~/LibrePCB-Workspace/projects")
).expanduser().resolve()
def _default_cli() -> str:
    """Busca librepcb-cli en el PATH y, en macOS, dentro de LibrePCB.app."""
    if shutil.which("librepcb-cli"):
        return "librepcb-cli"
    for app in ("/Applications/LibrePCB.app", "~/Applications/LibrePCB.app"):
        candidate = Path(app).expanduser() / "Contents" / "MacOS" / "librepcb-cli"
        if candidate.exists():
            return str(candidate)
    return "librepcb-cli"


CLI = os.environ.get("LIBREPCB_CLI") or _default_cli()
TEMPLATE = os.environ.get("LIBREPCB_TEMPLATE")
CLI_TIMEOUT = 300  # segundos
MAX_READ_CHARS = 100_000

mcp = FastMCP("librepcb")


# === Utilidades ===

def _safe_path(p: str) -> Path:
    """Resuelve una ruta (relativa a PROJECTS_DIR) y evita salirse de la raíz."""
    path = Path(p).expanduser()
    if not path.is_absolute():
        path = PROJECTS_DIR / path
    path = path.resolve()
    if path != PROJECTS_DIR and PROJECTS_DIR not in path.parents:
        raise ValueError(f"Ruta fuera de la carpeta de proyectos ({PROJECTS_DIR}): {path}")
    return path


def _find_lpp(project: str) -> Path:
    """Acepta un nombre de proyecto, su carpeta o la ruta al .lpp, y devuelve el .lpp."""
    path = _safe_path(project)
    if path.is_file() and path.suffix == ".lpp":
        return path
    if path.is_dir():
        lpps = sorted(path.glob("*.lpp"))
        if lpps:
            return lpps[0]
    raise FileNotFoundError(f"No encontré un archivo .lpp en: {path}")


def _run_cli(args: list[str]) -> str:
    cmd = [CLI, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        return (f"No encontré '{CLI}'. Instala LibrePCB y agrega librepcb-cli al PATH, "
                "o define la variable LIBREPCB_CLI con su ruta.")
    except subprocess.TimeoutExpired:
        return f"librepcb-cli superó el tiempo límite ({CLI_TIMEOUT}s)."
    out = (r.stdout + r.stderr).strip()
    return f"$ {' '.join(cmd)}\n[exit {r.returncode}]\n{out or '(sin salida)'}"


# === Herramientas ===

@mcp.tool()
def list_projects() -> str:
    """Lista los proyectos LibrePCB (archivos .lpp) dentro de la carpeta de proyectos."""
    if not PROJECTS_DIR.exists():
        return f"La carpeta de proyectos no existe: {PROJECTS_DIR}"
    lpps = sorted(PROJECTS_DIR.rglob("*.lpp"))
    if not lpps:
        return f"No hay proyectos en {PROJECTS_DIR}"
    return "\n".join(str(p.relative_to(PROJECTS_DIR)) for p in lpps)


@mcp.tool()
def create_project(name: str, template_path: str | None = None) -> str:
    """Crea un proyecto nuevo copiando un proyecto plantilla y renombrando su .lpp.

    LibrePCB no permite crear proyectos válidos desde el CLI, así que se parte
    de una plantilla: un proyecto vacío creado una vez desde la GUI de LibrePCB.

    Args:
        name: Nombre del nuevo proyecto (también será el nombre de la carpeta).
        template_path: Carpeta del proyecto plantilla. Si se omite, usa LIBREPCB_TEMPLATE.
    """
    src = template_path or TEMPLATE
    if not src:
        return ("Falta una plantilla. Crea un proyecto vacío en LibrePCB (File > New Project) "
                "y pasa su carpeta en template_path o en la variable LIBREPCB_TEMPLATE.")
    src_dir = Path(src).expanduser().resolve()
    src_lpps = list(src_dir.glob("*.lpp"))
    if not src_lpps:
        return f"La plantilla no contiene un .lpp: {src_dir}"

    dst_dir = _safe_path(name)
    if dst_dir.exists():
        return f"Ya existe: {dst_dir}"
    shutil.copytree(src_dir, dst_dir, ignore=shutil.ignore_patterns("output", ".lock", "*~"))
    (dst_dir / src_lpps[0].name).rename(dst_dir / f"{name}.lpp")
    return (f"Proyecto '{name}' creado en {dst_dir}\n"
            "Nota: el nombre interno (metadata) sigue siendo el de la plantilla; "
            "cámbialo en LibrePCB en Project > Project Setup si lo necesitas.")


@mcp.tool()
def list_project_files(project: str) -> str:
    """Lista los archivos de un proyecto (circuitos, esquemáticos, placas, librería local).

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
    """
    root = _find_lpp(project).parent
    files = sorted(p for p in root.rglob("*") if p.is_file() and "output" not in p.parts)
    return "\n".join(f"{p.relative_to(root)}  ({p.stat().st_size} B)" for p in files)


@mcp.tool()
def read_project_file(project: str, file: str) -> str:
    """Lee un archivo de texto del proyecto (p. ej. circuit/circuit.lp, boards/*/board.lp).

    Los archivos .lp son S-expressions legibles; sirven para inspeccionar
    componentes, redes y atributos.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        file: Ruta relativa dentro del proyecto.
    """
    root = _find_lpp(project).parent
    path = (root / file).resolve()
    if root not in path.parents:
        return "El archivo debe estar dentro del proyecto."
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n... (truncado, {len(text)} caracteres en total)"
    return text


@mcp.tool()
def run_erc(project: str) -> str:
    """Ejecuta el Electrical Rule Check (ERC) del proyecto y devuelve los avisos.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
    """
    return _run_cli(["open-project", "--erc", str(_find_lpp(project))])


@mcp.tool()
def run_drc(project: str, board: str | None = None) -> str:
    """Ejecuta el Design Rule Check (DRC) de las placas del proyecto.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        board: Nombre de una placa concreta (opcional; por defecto todas).
    """
    args = ["open-project", "--drc"]
    if board:
        args.append(f"--board={board}")
    return _run_cli([*args, str(_find_lpp(project))])


@mcp.tool()
def export(
    project: str,
    schematics_pdf: bool = False,
    bom_csv: bool = False,
    netlist: bool = False,
    gerbers: bool = False,
    pick_and_place: bool = False,
    board: str | None = None,
) -> str:
    """Exporta salidas del proyecto a su carpeta output/.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        schematics_pdf: Exporta los esquemáticos a PDF.
        bom_csv: Exporta la lista de materiales (BOM) en CSV.
        netlist: Exporta el netlist en formato D-356.
        gerbers: Genera los datos de fabricación (Gerber + Excellon).
        pick_and_place: Exporta los archivos pick&place (top y bottom).
        board: Limita las exportaciones de placa a una placa concreta (opcional).
    """
    lpp = _find_lpp(project)
    out = lpp.parent / "output" / "mcp"
    args = ["open-project"]
    if schematics_pdf:
        args.append(f"--export-schematics={out / 'schematics.pdf'}")
    if bom_csv:
        args.append(f"--export-bom={out / 'bom.csv'}")
    if netlist:
        args.append(f"--export-netlist={out / '{{BOARD}}_netlist.d356'}")
    if gerbers:
        args.append("--export-pcb-fabrication-data")
    if pick_and_place:
        args.append(f"--export-pnp-top={out / '{{BOARD}}_pnp_top.csv'}")
        args.append(f"--export-pnp-bottom={out / '{{BOARD}}_pnp_bottom.csv'}")
    if len(args) == 1:
        return "No elegiste nada que exportar."
    if board:
        args.append(f"--board={board}")
    out.mkdir(parents=True, exist_ok=True)
    output_root = lpp.parent / "output"
    before = {p: p.stat().st_mtime for p in output_root.rglob("*") if p.is_file()}
    result = _run_cli([*args, str(lpp)])
    changed = sorted(
        p for p in output_root.rglob("*")
        if p.is_file() and before.get(p) != p.stat().st_mtime
    )
    if changed:
        files = "\n".join(str(p) for p in changed)
        return f"{result}\n\nArchivos generados ({len(changed)}):\n{files}"
    return f"{result}\n\nNo se generó ningún archivo nuevo en {output_root}."


# === Edición de placa (board.lp) ===
# LibrePCB guarda la placa como S-expressions. Se parsea con posiciones de
# caracteres para editar solo los números necesarios y no tocar el resto.

import math
import time


class _Node:
    __slots__ = ("items", "start", "end", "text", "quoted")

    def __init__(self, start, text=None, quoted=False):
        self.items = None if text is not None else []
        self.start, self.end, self.text, self.quoted = start, start, text, quoted

    @property
    def is_list(self):
        return self.items is not None

    @property
    def name(self):
        if self.is_list and self.items and not self.items[0].is_list:
            return self.items[0].text
        return None

    def child(self, name):
        for it in self.items or []:
            if it.is_list and it.name == name:
                return it
        return None

    def children(self, name):
        return [it for it in (self.items or []) if it.is_list and it.name == name]

    def arg(self, i=1):
        return self.items[i].text


def _parse_sexp(text: str) -> _Node:
    root, stack, i, n = _Node(0), [], 0, len(text)
    stack.append(root)
    while i < n:
        c = text[i]
        if c == "(":
            node = _Node(i)
            stack[-1].items.append(node)
            stack.append(node)
            i += 1
        elif c == ")":
            node = stack.pop()
            node.end = i + 1
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            node = _Node(i, "".join(buf), True)
            node.end = j + 1
            stack[-1].items.append(node)
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            node = _Node(i, text[i:j])
            node.end = j
            stack[-1].items.append(node)
            i = j
    return root.items[0] if root.items else root


def _fmt(v: float) -> str:
    s = f"{round(v, 6):.6f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _xy(node: _Node) -> tuple[float, float]:
    return float(node.arg(1)), float(node.arg(2))


def _rot(x, y, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def _to_world(px, py, pos, rot, flip):
    if flip:
        px = -px
    x, y = _rot(px, py, rot)
    return x + pos[0], y + pos[1]


def _to_local(wx, wy, pos, rot, flip):
    x, y = _rot(wx - pos[0], wy - pos[1], -rot)
    return (-x if flip else x), y


def _board_file(lpp: Path, board: str | None) -> Path:
    files = sorted((lpp.parent / "boards").glob("*/board.lp"))
    if not files:
        raise FileNotFoundError("El proyecto no tiene placas.")
    if board is None:
        return files[0]
    for f in files:
        name = _parse_sexp(f.read_text(encoding="utf-8")).child("name")
        if f.parent.name == board or (name and name.arg() == board):
            return f
    raise FileNotFoundError(f"No existe la placa '{board}'. Placas: "
                            + ", ".join(f.parent.name for f in files))


def _footprint_bboxes(project_dir: Path) -> dict[str, tuple]:
    """uuid de footprint -> (minx, miny, maxx, maxy) en coordenadas locales."""
    result = {}
    for pkg in (project_dir / "library" / "pkg").glob("*/package.lp"):
        root = _parse_sexp(pkg.read_text(encoding="utf-8"))
        for fp in root.children("footprint"):
            court, other = [], []
            for poly in fp.children("polygon"):
                layer = poly.child("layer").arg()
                pts = [_xy(v.child("position")) for v in poly.children("vertex")]
                (court if "courtyard" in layer else other).extend(pts)
            for circ in fp.children("circle"):
                cx, cy = _xy(circ.child("position"))
                r = float(circ.child("diameter").arg()) / 2
                layer = circ.child("layer").arg()
                pts = [(cx - r, cy - r), (cx + r, cy + r)]
                (court if "courtyard" in layer else other).extend(pts)
            for pad in fp.children("pad"):
                px, py = _xy(pad.child("position"))
                w, h = _xy(pad.child("size"))
                rot = float(pad.child("rotation").arg())
                for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
                    rx, ry = _rot(dx, dy, rot)
                    other.append((px + rx, py + ry))
            pts = court or other
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                result[fp.arg()] = (min(xs), min(ys), max(xs), max(ys))
    return result


def _load_board(project: str, board: str | None):
    lpp = _find_lpp(project)
    bfile = _board_file(lpp, board)
    text = bfile.read_text(encoding="utf-8")
    root = _parse_sexp(text)

    circuit = _parse_sexp((lpp.parent / "circuit" / "circuit.lp").read_text(encoding="utf-8"))
    nets = {n.arg(): n.child("name").arg() for n in circuit.children("net")}
    comps = {}
    for c in circuit.children("component"):
        signals = {nets.get(s.child("net").arg()) for s in c.children("signal")}
        signals.discard(None)
        comps[c.arg()] = {
            "name": c.child("name").arg(),
            "value": c.child("value").arg() if c.child("value") else "",
            "nets": sorted(signals),
        }

    bboxes = _footprint_bboxes(lpp.parent)
    devices = []
    for d in root.children("device"):
        info = comps.get(d.arg(), {"name": d.arg()[:8], "value": "", "nets": []})
        pos = _xy(d.child("position"))
        rot = float(d.child("rotation").arg())
        flip = d.child("flip").arg() == "true"
        lock = d.child("lock").arg() == "true" if d.child("lock") else False
        local = bboxes.get(d.child("lib_footprint").arg(), (-0.5, -0.5, 0.5, 0.5))
        corners = [_to_world(x, y, pos, rot, flip)
                   for x in (local[0], local[2]) for y in (local[1], local[3])]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        devices.append({
            **info, "node": d, "pos": pos, "rot": rot, "flip": flip, "lock": lock,
            "local_bbox": local, "bbox": (min(xs), min(ys), max(xs), max(ys)),
        })

    outline = []
    for poly in root.children("polygon"):
        if poly.child("layer").arg() == "brd_outlines":
            outline += [_xy(v.child("position")) for v in poly.children("vertex")]
    obox = None
    if outline:
        obox = (min(p[0] for p in outline), min(p[1] for p in outline),
                max(p[0] for p in outline), max(p[1] for p in outline))
    return lpp, bfile, text, devices, obox


def _placement_problems(devices, obox, clearance=0.2) -> list[str]:
    probs = []
    for i, a in enumerate(devices):
        if obox:
            b = a["bbox"]
            if b[0] < obox[0] or b[1] < obox[1] or b[2] > obox[2] or b[3] > obox[3]:
                probs.append(f"{a['name']} se sale del contorno de la placa")
        for b in devices[i + 1:]:
            if a["flip"] != b["flip"]:
                continue  # en caras distintas pueden superponerse
            A, B = a["bbox"], b["bbox"]
            if (A[0] < B[2] + clearance and B[0] < A[2] + clearance
                    and A[1] < B[3] + clearance and B[1] < A[3] + clearance):
                probs.append(f"{a['name']} se superpone con {b['name']}")
    return probs


def _layout_report(devices, obox) -> str:
    lines = []
    if obox:
        lines.append(f"Contorno de la placa: x {_fmt(obox[0])}..{_fmt(obox[2])}, "
                     f"y {_fmt(obox[1])}..{_fmt(obox[3])} mm "
                     f"({_fmt(obox[2] - obox[0])} x {_fmt(obox[3] - obox[1])} mm)")
    else:
        lines.append("La placa no tiene contorno (capa brd_outlines).")
    lines.append("Coordenadas en mm, eje Y hacia arriba, rotación en grados antihorario.")
    lines.append("Componente | valor | x | y | rot | cara | tamaño ancho x alto (a rot 0) "
                 "| centro del tamaño respecto al origen | ocupa (xmin,ymin,xmax,ymax) | redes")
    for d in sorted(devices, key=lambda d: d["name"]):
        lb, b = d["local_bbox"], d["bbox"]
        lines.append(
            f"{d['name']} | {d['value']} | {_fmt(d['pos'][0])} | {_fmt(d['pos'][1])} | "
            f"{_fmt(d['rot'])} | {'bottom' if d['flip'] else 'top'}{' (bloqueado)' if d['lock'] else ''} | "
            f"{_fmt(lb[2] - lb[0])} x {_fmt(lb[3] - lb[1])} | "
            f"({_fmt((lb[0] + lb[2]) / 2)}, {_fmt((lb[1] + lb[3]) / 2)}) | "
            f"({_fmt(b[0])}, {_fmt(b[1])}, {_fmt(b[2])}, {_fmt(b[3])}) | "
            f"{', '.join(d['nets'])}")
    probs = _placement_problems(devices, obox)
    lines.append("")
    lines.append(f"Problemas de ubicación ({len(probs)}):" if probs else "Sin superposiciones ni piezas fuera de la placa.")
    lines += [f"- {p}" for p in probs]
    return "\n".join(lines)


def _project_locked(lpp: Path) -> bool:
    return (lpp.parent / ".lock").exists()


@mcp.tool()
def get_board_layout(project: str, board: str | None = None) -> str:
    """Muestra la ubicación de cada componente en la placa: posición, rotación,
    cara, tamaño real (courtyard o pads), área que ocupa y redes a las que conecta.
    También lista superposiciones y piezas fuera del contorno.

    Úsala antes de move_components para planear la ubicación.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        board: Nombre de la placa (opcional; por defecto la primera).
    """
    _, bfile, _, devices, obox = _load_board(project, board)
    return f"Placa: {bfile.parent.name}\n" + _layout_report(devices, obox)


@mcp.tool()
def move_components(project: str, moves: list[dict], board: str | None = None) -> str:
    """Mueve y/o rota componentes en la placa (edita board.lp).

    LibrePCB debe tener el proyecto CERRADO; si está abierto, no se modifica nada.
    Se guarda una copia en output/mcp-backups/ antes de cada cambio.
    Los textos (nombre y valor) se mueven y rotan junto con su componente.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        moves: Lista de movimientos, cada uno {"name": "R1", "x": 10.0, "y": 5.0, "rotation": 90}.
            x/y son la posición del origen del componente en mm (la misma que
            muestra get_board_layout). rotation es opcional (grados antihorario).
        board: Nombre de la placa (opcional; por defecto la primera).
    """
    lpp, bfile, text, devices, obox = _load_board(project, board)
    if _project_locked(lpp):
        return ("El proyecto está abierto en LibrePCB (existe .lock). "
                "Ciérralo en LibrePCB y vuelve a intentarlo.")
    by_name = {d["name"]: d for d in devices}
    edits, errors, done = [], [], []
    for m in moves:
        name = str(m.get("name", ""))
        d = by_name.get(name)
        if d is None:
            errors.append(f"{name}: no existe en la placa")
            continue
        if d["lock"]:
            errors.append(f"{name}: está bloqueado en LibrePCB")
            continue
        try:
            new_pos = (float(m.get("x", d["pos"][0])), float(m.get("y", d["pos"][1])))
            new_rot = float(m.get("rotation", d["rot"])) % 360
        except (TypeError, ValueError):
            errors.append(f"{name}: x, y o rotation no son números")
            continue
        node, old_pos, old_rot, flip = d["node"], d["pos"], d["rot"], d["flip"]
        p = node.child("position")
        edits.append((p.items[1].start, p.items[1].end, _fmt(new_pos[0])))
        edits.append((p.items[2].start, p.items[2].end, _fmt(new_pos[1])))
        r = node.child("rotation")
        edits.append((r.items[1].start, r.items[1].end, _fmt(new_rot)))
        for st in node.children("stroke_text"):
            sp = st.child("position")
            lx, ly = _to_local(*_xy(sp), old_pos, old_rot, flip)
            wx, wy = _to_world(lx, ly, new_pos, new_rot, flip)
            edits.append((sp.items[1].start, sp.items[1].end, _fmt(wx)))
            edits.append((sp.items[2].start, sp.items[2].end, _fmt(wy)))
            sr = st.child("rotation")
            a = float(sr.arg())
            rel = (old_rot - a) if flip else (a - old_rot)
            na = (new_rot - rel) if flip else (new_rot + rel)
            edits.append((sr.items[1].start, sr.items[1].end, _fmt(na % 360)))
        done.append(name)

    if not done:
        return "No se movió nada.\n" + "\n".join(errors)

    backup_name = _backup_and_write(lpp, bfile, _apply_edits(text, edits))

    _, _, _, devices, obox = _load_board(project, board)
    probs = _placement_problems(devices, obox)
    out = [f"Movidos {len(done)} componentes: {', '.join(done)}",
           f"Copia de seguridad: {backup_name}"]
    if errors:
        out.append("No se pudieron mover:\n" + "\n".join(f"- {e}" for e in errors))
    out.append(f"Problemas de ubicación que quedan ({len(probs)}):" if probs
               else "Sin superposiciones ni piezas fuera de la placa.")
    out += [f"- {p}" for p in probs]
    return "\n".join(out)


@mcp.tool()
def undo_last_change(project: str) -> str:
    """Deshace el último cambio hecho por el servidor (mover componentes, contorno,
    planos, zonas, agujeros, fiduciales, serigrafía, clases de red o partes),
    restaurando la copia de seguridad más reciente. Se puede llamar varias veces.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    bdir = lpp.parent / "output" / "mcp-backups"
    backups = sorted(bdir.rglob("*.bak-*"), key=lambda p: p.name.split(".bak-")[-1])
    if not backups:
        return "No hay cambios para deshacer."
    last = backups[-1]
    rel = last.relative_to(bdir)
    target = lpp.parent / rel.parent / last.name.split(".bak-")[0]
    shutil.copy2(last, target)
    last.unlink()
    return f"Deshecho: restauré {target.relative_to(lpp.parent)} ({len(backups) - 1} cambios anteriores aún se pueden deshacer)."


# === Utilidades de edición ===

import csv
import io
import re
import uuid as _uuidlib


def _new_uuid() -> str:
    return str(_uuidlib.uuid4())


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + new + text[end:]
    return text


def _stamp() -> str:
    """Marca de tiempo ordenable y única (con microsegundos)."""
    t = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f"-{int(t * 1e6) % 1_000_000:06d}"


def _backup_and_write(lpp: Path, path: Path, new_text: str) -> str:
    """Guarda copia en output/mcp-backups/ y escribe. Devuelve la ruta de la copia."""
    rel = path.relative_to(lpp.parent)
    bdir = lpp.parent / "output" / "mcp-backups" / rel.parent
    bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / f"{path.name}.bak-{_stamp()}"
    shutil.copy2(path, backup)
    path.write_text(new_text, encoding="utf-8")
    return str(backup.relative_to(lpp.parent))


def _locked_msg(lpp: Path) -> str | None:
    li = R.lock_info(lpp.parent)
    if not li["locked"]:
        return None
    if li.get("alive") is False:
        return ("Hay un bloqueo viejo de LibrePCB (.lock) pero el programa ya no lo tiene abierto. "
                "Usa project_lock_status con remove_if_stale=true para quitarlo y vuelve a intentarlo.")
    return ("El proyecto está abierto en LibrePCB (existe .lock). "
            "Ciérralo en LibrePCB (⌘Q, sin guardar si no hiciste cambios) y vuelve a intentarlo.")


def _set_atom(node: _Node | None, key_path: list[str], value: str, edits: list) -> bool:
    """Cambia el primer valor de una clave anidada, p. ej. ['via_annular_ring', 'min']."""
    for k in key_path:
        if node is None:
            return False
        node = node.child(k)
    if node is None or len(node.items) < 2 or node.items[1].is_list:
        return False
    edits.append((node.items[1].start, node.items[1].end, value))
    return True


def _vertices(points: list[tuple[float, float, float]], indent: str) -> str:
    return "".join(f"\n{indent}(vertex (position {_fmt(x)} {_fmt(y)}) (angle {_fmt(a)}))"
                   for x, y, a in points)


def _rounded_rect(x0, y0, x1, y1, r) -> list[tuple[float, float, float]]:
    """Contorno cerrado antihorario con esquinas redondeadas (arcos de 90°)."""
    if r <= 0:
        return [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0), (x0, y0, 0)]
    return [
        (x0 + r, y0, 0), (x1 - r, y0, 90), (x1, y0 + r, 0), (x1, y1 - r, 90),
        (x1 - r, y1, 0), (x0 + r, y1, 90), (x0, y1 - r, 0), (x0, y0 + r, 90),
        (x0 + r, y0, 0),
    ]


def _circuit(lpp: Path):
    path = lpp.parent / "circuit" / "circuit.lp"
    text = path.read_text(encoding="utf-8")
    return path, text, _parse_sexp(text)


def _net_uuid(circuit_root: _Node, name: str) -> str | None:
    for n in circuit_root.children("net"):
        if n.child("name").arg() == name:
            return n.arg()
    return None


# === Perfiles de fabricante ===
# Valores típicos para placas de 2 capas y cobre de 1 oz. Verifica siempre las
# capacidades actuales del fabricante antes de pedir.
FAB_PROFILES = {
    "jlcpcb": {
        "descripcion": "JLCPCB estándar 2 capas, 1 oz",
        "default_trace_width": 0.25, "default_via_drill_diameter": 0.3,
        "min_copper_copper_clearance": 0.127, "min_copper_width": 0.127,
        "min_copper_board_clearance": 0.3, "min_copper_npth_clearance": 0.25,
        "min_drill_drill_clearance": 0.5, "min_drill_board_clearance": 0.5,
        "min_annular_ring": 0.13, "min_pth_drill_diameter": 0.3,
        "min_npth_drill_diameter": 0.5, "min_pth_slot_width": 0.5,
        "min_npth_slot_width": 1.0, "min_silkscreen_width": 0.153,
        "min_silkscreen_text_height": 1.0, "min_silkscreen_stopmask_clearance": 0.127,
        "min_outline_tool_diameter": 1.0, "via_annular_ring_min": 0.15,
        "pad_annular_ring_min": 0.25,
    },
    "pcbway": {
        "descripcion": "PCBWay estándar 2 capas, 1 oz",
        "default_trace_width": 0.25, "default_via_drill_diameter": 0.3,
        "min_copper_copper_clearance": 0.127, "min_copper_width": 0.127,
        "min_copper_board_clearance": 0.3, "min_copper_npth_clearance": 0.25,
        "min_drill_drill_clearance": 0.5, "min_drill_board_clearance": 0.5,
        "min_annular_ring": 0.15, "min_pth_drill_diameter": 0.3,
        "min_npth_drill_diameter": 0.5, "min_pth_slot_width": 0.5,
        "min_npth_slot_width": 0.8, "min_silkscreen_width": 0.15,
        "min_silkscreen_text_height": 0.8, "min_silkscreen_stopmask_clearance": 0.15,
        "min_outline_tool_diameter": 1.0, "via_annular_ring_min": 0.15,
        "pad_annular_ring_min": 0.25,
    },
    "conservador": {
        "descripcion": "Reglas holgadas (0.2/0.2 mm): cualquier fabricante, mejor rendimiento",
        "default_trace_width": 0.3, "default_via_drill_diameter": 0.4,
        "min_copper_copper_clearance": 0.2, "min_copper_width": 0.2,
        "min_copper_board_clearance": 0.4, "min_copper_npth_clearance": 0.3,
        "min_drill_drill_clearance": 0.5, "min_drill_board_clearance": 0.6,
        "min_annular_ring": 0.2, "min_pth_drill_diameter": 0.3,
        "min_npth_drill_diameter": 0.6, "min_pth_slot_width": 0.8,
        "min_npth_slot_width": 1.0, "min_silkscreen_width": 0.18,
        "min_silkscreen_text_height": 1.0, "min_silkscreen_stopmask_clearance": 0.15,
        "min_outline_tool_diameter": 1.5, "via_annular_ring_min": 0.2,
        "pad_annular_ring_min": 0.25,
    },
}


@mcp.tool()
def apply_fab_profile(project: str, profile: str = "jlcpcb", thickness: float = 1.6,
                      board: str | None = None) -> str:
    """Aplica las reglas de diseño (ancho de pista, vías, anillos) y las reglas del DRC
    de un fabricante a la placa, para que el DRC detecte lo que el fabricante rechazaría.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        profile: "jlcpcb", "pcbway" o "conservador".
        thickness: Grosor de la placa en mm (por defecto 1.6).
        board: Nombre de la placa (opcional).
    """
    p = FAB_PROFILES.get(profile.lower())
    if p is None:
        return f"Perfil desconocido. Opciones: {', '.join(FAB_PROFILES)}"
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    bfile = _board_file(lpp, board)
    text = bfile.read_text(encoding="utf-8")
    root = _parse_sexp(text)
    edits, missing = [], []

    def put(path, val):
        if not _set_atom(root, path, _fmt(val), edits):
            missing.append("/".join(path))

    put(["thickness"], thickness)
    dr = ["design_rules"]
    put(dr + ["default_trace_width"], p["default_trace_width"])
    put(dr + ["default_via_drill_diameter"], p["default_via_drill_diameter"])
    put(dr + ["via_annular_ring", "min"], p["via_annular_ring_min"])
    put(dr + ["pad_annular_ring", "min"], p["pad_annular_ring_min"])
    drc = ["design_rule_check"]
    for key in ("min_copper_copper_clearance", "min_copper_width", "min_copper_board_clearance",
                "min_copper_npth_clearance", "min_drill_drill_clearance",
                "min_drill_board_clearance", "min_annular_ring", "min_pth_drill_diameter",
                "min_npth_drill_diameter", "min_pth_slot_width", "min_npth_slot_width",
                "min_silkscreen_width", "min_silkscreen_text_height",
                "min_silkscreen_stopmask_clearance", "min_outline_tool_diameter"):
        put(drc + [key], p[key])
    backup = _backup_and_write(lpp, bfile, _apply_edits(text, edits))
    out = [f"Perfil aplicado: {p['descripcion']} (grosor {thickness} mm).",
           f"Pista por defecto {p['default_trace_width']} mm, vía {p['default_via_drill_diameter']} mm, "
           f"separación mínima {p['min_copper_copper_clearance']} mm.",
           f"Copia de seguridad: {backup}",
           "Verifica las capacidades actuales del fabricante antes de pedir."]
    if missing:
        out.append("No encontré en la placa (sin cambios): " + ", ".join(missing))
    return "\n".join(out)


@mcp.tool()
def set_board_outline(project: str, width: float, height: float, corner_radius: float = 1.0,
                      x: float = 0.0, y: float = 0.0, board: str | None = None) -> str:
    """Define el contorno de la placa como un rectángulo con esquinas redondeadas.
    Reemplaza el contorno actual (capa brd_outlines).

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        width: Ancho en mm.
        height: Alto en mm.
        corner_radius: Radio de las esquinas en mm (0 = esquinas rectas).
        x: Esquina inferior izquierda, X en mm.
        y: Esquina inferior izquierda, Y en mm.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    if width <= 0 or height <= 0 or corner_radius * 2 > min(width, height):
        return "Medidas inválidas: el radio no puede superar la mitad del lado menor."
    bfile = _board_file(lpp, board)
    text = bfile.read_text(encoding="utf-8")
    root = _parse_sexp(text)
    edits = []
    for poly in root.children("polygon"):
        if poly.child("layer").arg() == "brd_outlines":
            edits.append((poly.start, poly.end, ""))
    pts = _rounded_rect(x, y, x + width, y + height, corner_radius)
    block = (f"(polygon {_new_uuid()} (layer brd_outlines)\n"
             f"  (width 0.0) (fill false) (grab_area false) (lock false)"
             f"{_vertices(pts, '  ')}\n )")
    edits.append((root.end - 1, root.end - 1, f" {block}\n"))
    backup = _backup_and_write(lpp, bfile, _apply_edits(text, edits))
    return (f"Contorno: {width} x {height} mm, radio {corner_radius} mm, desde ({x}, {y}).\n"
            f"Copia de seguridad: {backup}")


@mcp.tool()
def add_ground_planes(project: str, net: str = "GND", layers: list[str] | None = None,
                      clearance: float = 0.3, board_clearance: float = 0.5,
                      connect_style: str = "thermal", board: str | None = None) -> str:
    """Agrega planos de cobre (relleno) conectados a una red, cubriendo toda la placa.
    LibrePCB los calcula al abrir el proyecto y respeta las zonas sin cobre.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        net: Nombre de la red (por defecto GND).
        layers: Capas, por defecto ["top_cu", "bot_cu"].
        clearance: Separación del plano al resto del cobre, en mm.
        board_clearance: Separación del plano al borde de la placa, en mm.
        connect_style: "thermal" (recomendado para soldar), "solid" o "none".
        board: Nombre de la placa (opcional).
    """
    layers = layers or ["top_cu", "bot_cu"]
    if connect_style not in ("thermal", "solid", "none"):
        return "connect_style debe ser thermal, solid o none."
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    _, _, croot = _circuit(lpp)
    net_uuid = _net_uuid(croot, net)
    if net_uuid is None:
        names = sorted(n.child("name").arg() for n in croot.children("net"))
        return f"No existe la red '{net}'. Redes: {', '.join(names)}"
    _, bfile, text, _, obox = _load_board(project, board)
    if obox is None:
        return "La placa no tiene contorno. Usa primero set_board_outline."
    root = _parse_sexp(text)
    existing = {(p.child("layer").arg(), p.child("net").arg()) for p in root.children("plane")}
    m = 0.01
    pts = [(obox[0] - m, obox[1] - m, 0), (obox[2] + m, obox[1] - m, 0),
           (obox[2] + m, obox[3] + m, 0), (obox[0] - m, obox[3] + m, 0)]
    blocks, skipped = [], []
    for layer in layers:
        if (layer, net_uuid) in existing:
            skipped.append(layer)
            continue
        blocks.append(
            f" (plane {_new_uuid()} (layer {layer})\n"
            f"  (net {net_uuid}) (priority 0) (min_width 0.25)\n"
            f"  (min_copper_clearance {_fmt(clearance)}) (min_board_clearance {_fmt(board_clearance)})"
            f" (min_npth_clearance {_fmt(board_clearance)})\n"
            f"  (connect_style {connect_style}) (thermal_gap 0.3) (thermal_spoke 0.3)\n"
            f"  (keep_islands false) (lock false){_vertices(pts, '  ')}\n )\n")
    if not blocks:
        return f"Ya existen planos de {net} en: {', '.join(skipped)}."
    backup = _backup_and_write(lpp, bfile, _apply_edits(text, [(root.end - 1, root.end - 1, "".join(blocks))]))
    out = [f"Planos de {net} agregados en {', '.join(l for l in layers if l not in skipped)}.",
           "LibrePCB los rellena al abrir la placa (o con 'Rebuild planes').",
           f"Copia de seguridad: {backup}"]
    if skipped:
        out.append(f"Ya existían en: {', '.join(skipped)}")
    return "\n".join(out)


@mcp.tool()
def add_keepout_zone(project: str, x1: float, y1: float, x2: float, y2: float,
                     layers: list[str] | None = None, no_copper: bool = True,
                     no_planes: bool = True, no_devices: bool = False,
                     no_exposure: bool = False, board: str | None = None) -> str:
    """Agrega una zona prohibida rectangular (por ejemplo bajo la antena del ESP32:
    sin cobre ni planos en ninguna capa).

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        x1, y1, x2, y2: Esquinas opuestas del rectángulo en mm.
        layers: Capas: "top_cu", "bot_cu" y, solo en placas multicapa, "inner".
            Por defecto todas las capas de cobre que tiene la placa.
        no_copper: Prohíbe pistas y cobre.
        no_planes: Prohíbe rellenos de cobre.
        no_devices: Prohíbe componentes.
        no_exposure: Prohíbe aperturas de máscara.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    bfile = _board_file(lpp, board)
    text = bfile.read_text(encoding="utf-8")
    root = _parse_sexp(text)
    lay = root.child("layers")
    inner = int(lay.child("inner").arg()) if lay is not None and lay.child("inner") else 0
    valid = {"top_cu", "bot_cu"} | ({"inner"} if inner > 0 else set())
    layers = layers or sorted(valid, key=["top_cu", "inner", "bot_cu"].index)
    if not set(layers) <= valid:
        return (f"Capas válidas en esta placa ({inner} capas internas): {', '.join(sorted(valid))}. "
                "Una capa inexistente hace fallar el DRC.")
    xa, xb, ya, yb = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
    pts = [(xa, ya, 0), (xb, ya, 0), (xb, yb, 0), (xa, yb, 0)]
    b = lambda v: "true" if v else "false"
    layer_lines = "".join(f"\n  (layer {l})" for l in ("top_cu", "inner", "bot_cu") if l in layers)
    block = (f" (zone {_new_uuid()}\n"
             f"  (no_copper {b(no_copper)}) (no_planes {b(no_planes)}) "
             f"(no_exposure {b(no_exposure)}) (no_devices {b(no_devices)})"
             f"{layer_lines}\n  (lock false){_vertices(pts, '  ')}\n )\n")
    backup = _backup_and_write(lpp, bfile, _apply_edits(text, [(root.end - 1, root.end - 1, block)]))
    return (f"Zona prohibida agregada: ({_fmt(xa)}, {_fmt(ya)}) a ({_fmt(xb)}, {_fmt(yb)}) mm "
            f"en {', '.join(layers)}.\nCopia de seguridad: {backup}")


@mcp.tool()
def add_mounting_holes(project: str, diameter: float = 3.2, inset: float = 3.5,
                       board: str | None = None) -> str:
    """Agrega 4 agujeros de montaje sin metalizar (NPTH) en las esquinas de la placa.
    3.2 mm sirve para tornillos M3.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        diameter: Diámetro del agujero en mm.
        inset: Distancia del centro del agujero a cada borde, en mm.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    _, bfile, text, devices, obox = _load_board(project, board)
    if obox is None:
        return "La placa no tiene contorno. Usa primero set_board_outline."
    root = _parse_sexp(text)
    pts = [(obox[0] + inset, obox[1] + inset), (obox[2] - inset, obox[1] + inset),
           (obox[2] - inset, obox[3] - inset), (obox[0] + inset, obox[3] - inset)]
    r = diameter / 2 + 1.0
    warnings = [f"El agujero en ({_fmt(px)}, {_fmt(py)}) choca con {d['name']}"
                for px, py in pts for d in devices
                if d["bbox"][0] < px + r and px - r < d["bbox"][2]
                and d["bbox"][1] < py + r and py - r < d["bbox"][3]]
    blocks = "".join(
        f" (hole {_new_uuid()} (diameter {_fmt(diameter)})\n"
        f"  (stop_mask auto) (lock false)\n"
        f"  (vertex (position {_fmt(px)} {_fmt(py)}) (angle 0.0))\n )\n" for px, py in pts)
    backup = _backup_and_write(lpp, bfile, _apply_edits(text, [(root.end - 1, root.end - 1, blocks)]))
    out = [f"4 agujeros de {diameter} mm agregados a {inset} mm de los bordes.",
           f"Copia de seguridad: {backup}"]
    if warnings:
        out.append("Atención, mueve estos componentes:\n" + "\n".join(f"- {w}" for w in warnings))
    return "\n".join(out)


@mcp.tool()
def add_fiducials(project: str, inset: float = 4.0, board: str | None = None) -> str:
    """Agrega 3 fiduciales globales (cara superior) para el montaje automático (SMT):
    círculos de cobre de 1 mm con apertura de máscara de 2 mm, en 3 esquinas.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        inset: Distancia de cada fiducial a los bordes, en mm. Usa un valor distinto
            del de los agujeros de montaje para que no coincidan.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    _, bfile, text, devices, obox = _load_board(project, board)
    if obox is None:
        return "La placa no tiene contorno. Usa primero set_board_outline."
    root = _parse_sexp(text)
    pts = [(obox[0] + inset, obox[1] + inset), (obox[2] - inset, obox[1] + inset),
           (obox[0] + inset, obox[3] - inset)]
    warnings = [f"El fiducial en ({_fmt(px)}, {_fmt(py)}) choca con {d['name']}"
                for px, py in pts for d in devices if not d["flip"]
                and d["bbox"][0] < px + 1.5 and px - 1.5 < d["bbox"][2]
                and d["bbox"][1] < py + 1.5 and py - 1.5 < d["bbox"][3]]
    blocks = "".join(
        f" (netsegment {_new_uuid()}\n  (net none)\n"
        f"  (pad {_new_uuid()} (side top) (shape roundrect)\n"
        f"   (position {_fmt(px)} {_fmt(py)}) (rotation 0.0) (size 1.0 1.0) (radius 1.0)\n"
        f"   (stop_mask 0.5) (solder_paste off) (clearance 0.5) (function global_fiducial)\n"
        f"   (lock false)\n  )\n )\n" for px, py in pts)
    backup = _backup_and_write(lpp, bfile, _apply_edits(text, [(root.end - 1, root.end - 1, blocks)]))
    out = ["3 fiduciales agregados (cara superior).", f"Copia de seguridad: {backup}"]
    if warnings:
        out.append("Atención:\n" + "\n".join(f"- {w}" for w in warnings))
    return "\n".join(out)


@mcp.tool()
def fix_silkscreen(project: str, remove_values: bool = True, hide_unplaced: bool = True,
                   fix_legend_widths: bool = True, board: str | None = None) -> str:
    """Ordena la serigrafía sin choques: busca para cada nombre (R1, U3...) un lugar libre
    junto a su pieza que no toque pads, vías, agujeros, fiduciales, zonas prohibidas
    ni otros textos. Prueba arriba, abajo, a los lados y desplazado; si no hay lugar
    con letra normal prueba más chica (sin bajar del mínimo del DRC).

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        remove_values: Quita los valores impresos ("100nF", MPN...). Siguen en la BOM.
        hide_unplaced: Oculta el nombre de las piezas donde no cabe ninguno (quedan en la
            BOM interactiva).
        fix_legend_widths: Engrosa las líneas de serigrafía de los footprints de la
            librería local que estén bajo el mínimo del DRC.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    bfile = _board_file(lpp, board)
    prj = R.Project(lpp, bfile)
    text, st = R.place_silkscreen(prj, remove_values=remove_values, hide_unplaced=hide_unplaced)
    backup = _backup_and_write(lpp, bfile, text)
    out = [f"Nombres reubicados sin choques: {st['moved']}.",
           f"Valores quitados de la serigrafía: {st['values_removed']}." if remove_values else "",
           (f"Sin espacio, nombre oculto ({len(st['hidden'])}): {', '.join(sorted(st['hidden']))}."
            if st["hidden"] else "Todas las piezas conservan su nombre."),
           f"Copia de seguridad: {backup}"]
    if fix_legend_widths:
        changed = R.fix_legend_widths(prj, write=lambda f, t: _backup_and_write(lpp, f, t))
        if changed:
            out.append(f"Líneas de serigrafía engrosadas al mínimo en: {', '.join(changed)}.")
    return "\n".join(l for l in out if l)


@mcp.tool()
def set_net_class(project: str, name: str, nets: list[str], trace_width: float,
                  clearance: float = 0.2, via_drill: float | None = None) -> str:
    """Crea o actualiza una clase de red (ancho de pista y separación) y le asigna redes.
    Útil para pistas de potencia más anchas (VBUS, VBAT, 3V3, GND).

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        name: Nombre de la clase, p. ej. "potencia".
        nets: Redes a asignar, p. ej. ["VBUS", "VBAT", "3V3"].
        trace_width: Ancho de pista por defecto para estas redes, en mm.
        clearance: Separación mínima a otro cobre, en mm.
        via_drill: Diámetro de taladro de vía (opcional; por defecto el de la placa).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    cpath, text, root = _circuit(lpp)
    nc = next((c for c in root.children("netclass") if c.child("name").arg() == name), None)
    edits = []
    via_val = _fmt(via_drill) if via_drill else "inherit"
    if nc is None:
        nc_uuid = _new_uuid()
        block = (f" (netclass {nc_uuid} (name {_q(name)})\n"
                 f"  (default_trace_width {_fmt(trace_width)})\n"
                 f"  (default_via_drill_diameter {via_val})\n"
                 f"  (min_copper_copper_clearance {_fmt(clearance)})\n"
                 f"  (min_copper_width {_fmt(min(trace_width, 0.2))})\n"
                 f"  (min_via_drill_diameter 0.0)\n )\n")
        anchor = root.children("netclass")[-1].end if root.children("netclass") else root.items[1].end
        edits.append((anchor, anchor, "\n" + block.rstrip("\n")))
    else:
        nc_uuid = nc.arg()
        _set_atom(nc, ["default_trace_width"], _fmt(trace_width), edits)
        _set_atom(nc, ["default_via_drill_diameter"], via_val, edits)
        _set_atom(nc, ["min_copper_copper_clearance"], _fmt(clearance), edits)
    found, missing = [], []
    by_name = {n.child("name").arg(): n for n in root.children("net")}
    for net in nets:
        node = by_name.get(net)
        if node is None:
            missing.append(net)
            continue
        if _set_atom(node, ["netclass"], nc_uuid, edits):
            found.append(net)
    backup = _backup_and_write(lpp, cpath, _apply_edits(text, edits))
    out = [f"Clase '{name}': pista {trace_width} mm, separación {clearance} mm.",
           f"Redes asignadas: {', '.join(found) or 'ninguna'}", f"Copia de seguridad: {backup}"]
    if missing:
        out.append(f"No existen: {', '.join(missing)}. Redes disponibles: {', '.join(sorted(by_name))}")
    out.append("Las pistas nuevas usarán este ancho; las ya dibujadas no cambian.")
    return "\n".join(out)


def _component_parts(root: _Node) -> dict[str, dict]:
    out = {}
    for c in root.children("component"):
        dev = c.child("device")
        parts = dev.children("part") if dev else []
        attrs = {}
        mpn = manu = ""
        if parts:
            p = parts[0]
            mpn = p.arg()
            manu = p.child("manufacturer").arg() if p.child("manufacturer") else ""
            for a in p.children("attribute"):
                attrs[a.arg()] = a.child("value").arg()
        for a in c.children("attribute"):
            attrs.setdefault(a.arg(), a.child("value").arg())
        dnp = any(a.arg() == "DNP" and a.child("value").arg().strip().lower() in ("true", "1", "yes", "si", "sí")
                  for a in c.children("attribute"))
        out[c.child("name").arg()] = {
            "value": c.child("value").arg() if c.child("value") else "",
            "mpn": mpn, "manufacturer": manu, "attrs": attrs, "has_device": dev is not None,
            "dnp": dnp,
        }
    return out


@mcp.tool()
def set_component_part(project: str, designators: list[str], mpn: str,
                       manufacturer: str = "", lcsc: str = "") -> str:
    """Asigna número de parte (MPN), fabricante y código LCSC a uno o más componentes,
    para que la BOM sirva para comprar o para el montaje en JLCPCB.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        designators: Componentes, p. ej. ["C1", "C2", "C5"].
        mpn: Número de parte del fabricante, p. ej. "CL05B104KO5NNNC".
        manufacturer: Fabricante, p. ej. "Samsung Electro-Mechanics".
        lcsc: Código LCSC para JLCPCB, p. ej. "C1525" (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    cpath, text, root = _circuit(lpp)
    comps = {c.child("name").arg(): c for c in root.children("component")}
    attr = (f"\n    (attribute \"LCSC\" (type string) (unit none) (value {_q(lcsc)}))"
            if lcsc else "")
    part = f"(part {_q(mpn)} (manufacturer {_q(manufacturer)}){attr}\n   )"
    edits, done, errors = [], [], []
    for des in designators:
        c = comps.get(des)
        if c is None:
            errors.append(f"{des}: no existe")
            continue
        dev = c.child("device")
        if dev is None:
            errors.append(f"{des}: no tiene dispositivo asignado en el esquemático")
            continue
        old = dev.children("part")
        if old:
            edits.append((old[0].start, old[-1].end, part))
        else:
            edits.append((dev.end - 1, dev.end - 1, f"\n   {part}\n  "))
        done.append(des)
    if not done:
        return "Sin cambios.\n" + "\n".join(errors)
    backup = _backup_and_write(lpp, cpath, _apply_edits(text, edits))
    out = [f"Parte {mpn}{' / ' + lcsc if lcsc else ''} asignada a: {', '.join(done)}",
           f"Copia de seguridad: {backup}"]
    if errors:
        out.append("\n".join(f"- {e}" for e in errors))
    return "\n".join(out)


@mcp.tool()
def list_parts(project: str) -> str:
    """Lista los componentes con su valor, MPN, fabricante y código LCSC,
    y señala los que no tienen parte asignada.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
    """
    _, _, root = _circuit(_find_lpp(project))
    parts = _component_parts(root)
    lines = ["Componente | valor | MPN | fabricante | LCSC"]
    missing = []
    for name in sorted(parts, key=lambda s: (re.sub(r"\d+", "", s), int(re.sub(r"\D", "", s) or 0))):
        p = parts[name]
        if not p["has_device"]:
            continue  # símbolo solo de esquemático (GND, VCC...)
        lines.append(f"{name} | {p['value']} | {p['mpn']} | {p['manufacturer']} | {p['attrs'].get('LCSC', '')}"
                     + (" | NO MONTAR" if p.get("dnp") else ""))
        if p["has_device"] and not p.get("dnp") and not p["mpn"] and not p["attrs"].get("LCSC"):
            missing.append(name)
    lines.append("")
    lines.append(f"Sin parte asignada ({len(missing)}): {', '.join(missing)}" if missing
                 else "Todos los componentes tienen parte asignada.")
    return "\n".join(lines)


# === Piezas: no montar (DNP) y piezas básicas de JLCPCB ===

@mcp.tool()
def set_dnp(project: str, designators: list[str], dnp: bool = True) -> str:
    """Marca (o desmarca) piezas como "no montar" (DNP): pines que se sueldan a mano,
    puentes de soldadura, pads de prueba. production_check no las cuenta como
    faltantes y export_production las saca de la BOM y del CPL de JLCPCB.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        designators: Piezas, p. ej. ["J4", "J5", "JP1", "TP1"].
        dnp: true para marcar, false para quitar la marca.
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    cpath, text, root = _circuit(lpp)
    comps = {c.child("name").arg(): c for c in root.children("component")}
    edits, done, errors = [], [], []
    for des in designators:
        c = comps.get(des)
        if c is None:
            errors.append(f"{des}: no existe")
            continue
        old = [a for a in c.children("attribute") if a.arg() == "DNP"]
        for a in old:
            ls = text.rfind("\n", 0, a.start)
            edits.append((ls, a.end, ""))
        if dnp:
            anchor = c.child("lock_assembly") or c.child("name")
            line_end = text.find("\n", anchor.end)
            edits.append((line_end, line_end,
                          '\n  (attribute "DNP" (type string) (unit none) (value "true"))'))
        done.append(des)
    if not done:
        return "Sin cambios.\n" + "\n".join(errors)
    backup = _backup_and_write(lpp, cpath, _apply_edits(text, edits))
    out = [f"{'Marcadas' if dnp else 'Desmarcadas'} como no montar: {', '.join(done)}",
           f"Copia de seguridad: {backup}"]
    if errors:
        out.append("\n".join(f"- {e}" for e in errors))
    return "\n".join(out)


@mcp.tool()
def assign_basic_parts(project: str, dry_run: bool = False, overwrite: bool = False) -> str:
    """Asigna automáticamente piezas BÁSICAS de JLCPCB (sin recargo de montaje) a
    resistencias y condensadores según su valor y tamaño (0603/0805...).
    Usa una tabla verificada; puedes ampliarla en tools/jlc_basic.json con filas
    {"type": "R"|"C", "size": "0603", "value": 4700, "mpn": ..., "manufacturer": ..., "lcsc": ...}
    (valor en ohm o farad).

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        dry_run: Solo muestra qué asignaría, sin escribir.
        overwrite: Reemplaza también las piezas que ya tienen MPN.
    """
    lpp = _find_lpp(project)
    if not dry_run and (msg := _locked_msg(lpp)):
        return msg
    prj = R.Project(lpp, _board_file(lpp, None))
    table = R.load_basic_table(SERVER_DIR)
    plan, skipped = {}, []
    for c in prj.components.values():
        if not c["device"] or prj.is_dnp(c):
            continue
        if c["part"] and not overwrite:
            continue
        part, why = R.suggest_basic_part(prj, c, table)
        if part:
            plan.setdefault(part, []).append(c["name"])
        elif "RESISTANCE" in c["attrs"] or "CAPACITANCE" in c["attrs"]:
            skipped.append(f"{c['name']}: {why}")
    if not plan:
        return "No hay piezas para asignar." + ("\n" + "\n".join(skipped) if skipped else "")
    out = []
    for (mpn, manu, lcsc), names in sorted(plan.items(), key=lambda kv: kv[1][0]):
        names = sorted(names, key=lambda n: (re.sub(r"\d+", "", n), int(re.sub(r"\D", "", n) or 0)))
        if dry_run:
            out.append(f"{', '.join(names)} → {mpn} ({manu}) {lcsc}")
        else:
            r = set_component_part(project, names, mpn, manu, lcsc)
            out.append(r.splitlines()[0])
    if skipped:
        out.append("Sin equivalente básico (asígnalas con set_component_part):")
        out += [f"- {s}" for s in skipped]
    return ("Vista previa (no se escribió nada):\n" if dry_run else "") + "\n".join(out)


# === Estado del proyecto y revisión previa ===

@mcp.tool()
def project_lock_status(project: str, remove_if_stale: bool = False) -> str:
    """Dice si LibrePCB tiene el proyecto abierto (archivo .lock) y si ese bloqueo es
    real o quedó de una sesión cerrada. Con remove_if_stale=true borra un bloqueo viejo.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        remove_if_stale: Borra el .lock solo si el proceso de LibrePCB ya no existe.
    """
    lpp = _find_lpp(project)
    li = R.lock_info(lpp.parent)
    if not li["locked"]:
        return "El proyecto NO está abierto en LibrePCB: se puede modificar."
    who = f"{li.get('user', '')} en {li.get('host', '')}, proceso {li.get('pid')}, hace {li.get('age_min')} min"
    if li.get("alive") is True:
        return f"El proyecto está ABIERTO en LibrePCB ({who}). Ciérralo (⌘Q) antes de modificarlo."
    if li.get("alive") is False:
        if remove_if_stale:
            (lpp.parent / ".lock").unlink()
            return f"Había un bloqueo viejo ({who}); el proceso ya no existe. Lo borré."
        return (f"Bloqueo viejo: el proceso de LibrePCB ya no existe ({who}). "
                "Llama de nuevo con remove_if_stale=true para quitarlo.")
    return f"Hay un .lock ({who}) pero no puedo comprobar el proceso desde este equipo."


@mcp.tool()
def preflight(project: str, board: str | None = None) -> str:
    """Revisión ANTES de rutear: bloqueo del proyecto, contorno, ocupación de la placa,
    redes de potencia que no caben en pads finos, puentes de cobre en footprints,
    piezas sin MPN/LCSC y clases de red. Sirve para decidir antes de gastar ruteos.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    prj = R.Project(lpp, _board_file(lpp, board))
    items = R.preflight(prj)
    lines = [f"Revisión previa: {lpp.stem}", ""]
    lines += [f"[{lvl:5}] {msg}" for lvl, msg in items] or ["Sin observaciones."]
    falta = any(l == "FALTA" for l, _ in items)
    lines += ["", "Hay que resolver lo marcado FALTA antes de rutear." if falta
              else "Se puede rutear (autoroute)."]
    return "\n".join(lines)


# === Ruteo automático (Freerouting) sin pasar por la GUI ===

SERVER_DIR = Path(__file__).resolve().parent


def _route_dir(lpp: Path) -> Path:
    return lpp.parent / "output" / "route"


def _resolve_in_project(lpp: Path, file: str) -> Path:
    p = Path(file).expanduser()
    p = p if p.is_absolute() else lpp.parent / p
    p = p.resolve()
    if lpp.parent.resolve() not in p.parents:
        raise ValueError("El archivo debe estar dentro del proyecto.")
    return p


@mcp.tool()
def export_dsn(project: str, clearance: float = 0.15, signal_width: float = 0.2,
               board: str | None = None) -> str:
    """Genera el archivo para Freerouting (Specctra DSN) directamente desde los archivos
    del proyecto, sin abrir LibrePCB, y ya corregido: clases de red con su ancho,
    GND ruteado con pistas, formas reales de los pads (ranuras incluidas), margen en
    fiduciales y agujeros, franjas sin vías/cobre en el borde y zonas sin cobre.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        clearance: Separación entre cobres en mm (nunca menor que el DRC ni que las clases).
        signal_width: Ancho de las pistas de señal en mm.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    prj = R.Project(lpp, _board_file(lpp, board))
    out = _route_dir(lpp)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "board.dsn"
    path.write_text(R.export_dsn(prj, {"clearance": clearance, "signal_width": signal_width}), encoding="utf-8")
    nets = len([n for n in prj.pins_by_net().values() if len(n) >= 2])
    return (f"DSN generado: {path.relative_to(lpp.parent)}\n"
            f"{len(prj.devices)} piezas, {len(prj.pads)} pads, {nets} redes a rutear.")


@mcp.tool()
def fix_dsn(project: str, dsn_file: str, clearance: float = 0.15) -> str:
    """Corrige un DSN exportado desde la GUI de LibrePCB (si no usas export_dsn):
    agrega las clases de red con su ancho, quita los planos (GND con pistas), arregla
    ranuras de largo cero, agranda el margen de fiduciales y agrega franjas sin vías
    en el borde. Escribe <archivo>-fixed.dsn.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        dsn_file: Ruta al .dsn dentro del proyecto.
        clearance: Separación global en mm.
    """
    lpp = _find_lpp(project)
    src = _resolve_in_project(lpp, dsn_file)
    prj = R.Project(lpp, _board_file(lpp, None))
    s = src.read_text(encoding="utf-8")
    notes = []
    n0 = s.count("(plane ")
    s = re.sub(r'  \(plane "[^"]+"\n   \(polygon [^\n]*\n(?:    [^\n]*\n)*?   \)\n  \)\n', "", s)
    if n0:
        notes.append(f"planos quitados: {n0 - s.count('(plane ')}")
    def fix_path(m):
        a, b = m.group(3).split(), None
        if len(a) == 4 and a[0] == a[2] and a[1] == a[3] and (float(a[0]) or float(a[1])):
            a = [str(-float(a[0])), str(-float(a[1])), a[2], a[3]]
            notes.append("ranura corregida")
        return f"(path {m.group(1)} {m.group(2)} {' '.join(a)})"
    s = re.sub(r"\(path (top_cu|bot_cu) ([\d.]+) ([-\d. ]+)\)", fix_path, s)
    drc = max(prj.rules.get("min_copper_copper_clearance", 0.127), clearance,
              *[c["clearance"] for c in prj.netclasses.values()])
    s = re.sub(r"\(rule\n\s+\(width [\d.]+\)\n\s+\(clearance [\d.]+\)",
               f"(rule\n   (width 0.2)\n   (clearance {_fmt(drc)})", s, count=1)
    via = re.search(r'\(via\s+"([^"]+)"\s*\)', s)
    via_name = via.group(1) if via else "via"
    classes = {}
    for net_uuid in prj.pins_by_net():
        c = prj.class_of(net_uuid)
        if c and c["width"] > 0:
            classes.setdefault(c["name"], (c, []))[1].append(prj.net_name(net_uuid))
    blocks = "".join(
        f' (class "{n}" ' + " ".join(f'"{x}"' for x in sorted(nets))
        + f'\n  (circuit (use_via "{via_name}"))\n  (rule (width {_fmt(c["width"])}) (clearance {_fmt(drc)}))\n )\n'
        for n, (c, nets) in classes.items())
    i = s.rindex(" (wiring")
    j = s.rindex(" )", 0, i)
    s = s[:j] + blocks + s[j:]
    if prj.obox:
        x0, y0, x1, y1 = prj.obox
        band = 0.8
        ko = "".join(f'  (via_keepout "edge{l}{k}" (rect {l} {_fmt(a)} {_fmt(b)} {_fmt(c)} {_fmt(d)}))\n'
                     for l in ("top_cu", "bot_cu")
                     for k, (a, b, c, d) in enumerate([(x0, y0, x1, y0 + band), (x0, y1 - band, x1, y1),
                                                       (x0, y0, x0 + band, y1), (x1 - band, y0, x1, y1)]))
    else:
        ko = ""
    for f in prj.fiducials:
        d = max(f["size"]) + 2 * max(f["clearance"], 0.5)
        ko += f'  (keepout "fid" (circle top_cu {_fmt(d)} {_fmt(f["pos"][0])} {_fmt(f["pos"][1])}))\n'
    m = re.search(r"\n  \(via\b", s)
    if m and ko:
        s = s[:m.start() + 1] + ko + s[m.start() + 1:]
    dst = src.with_name(src.stem + "-fixed.dsn")
    dst.write_text(s, encoding="utf-8")
    notes.append(f"clases: {', '.join(classes) or 'ninguna'}; separación {_fmt(drc)} mm")
    return f"DSN corregido: {dst.relative_to(lpp.parent)}\n" + "; ".join(dict.fromkeys(notes))


@mcp.tool()
def autoroute(project: str, apply: bool = True, clearance: float = 0.15, signal_width: float = 0.2,
              allow_rotate: list[str] | None = None, timeout_minutes: int = 20,
              board: str | None = None) -> str:
    """Rutea la placa con Freerouting de punta a punta, sin pasar por la GUI:
    genera el DSN, rutea en etapas (1: anchos completos; 2: completa lo que falte con
    pistas finas dejando fija la etapa 1; 3: rehace el ruteo solo alrededor de los pines
    que faltan), prueba variantes de separación y de ancho de señal (0.2 / 0.15 mm),
    verifica conectividad y reglas, corrige vías juntas o pistas pegadas al borde
    y, si apply=true y LibrePCB está cerrado, escribe las pistas en la placa.

    Corre en segundo plano (puede tardar varios minutos): consulta el avance con
    autoroute_status.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        apply: Escribe el mejor resultado en board.lp al terminar (con copia de seguridad).
        clearance: Separación entre cobres de la etapa 1, en mm.
        signal_width: Ancho de las pistas de señal, en mm.
        allow_rotate: Piezas que se pueden girar si quedan conexiones sin rutear
            (p. ej. ["U6"]); ["auto"] permite girar las que tengan pines sin conectar.
            Por defecto no se gira nada (cambiar la ubicación es decisión tuya).
        timeout_minutes: Tiempo máximo por corrida de Freerouting.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    bfile = _board_file(lpp, board)
    workdir = _route_dir(lpp) / R.stamp()
    status = _latest_route_status(lpp)
    if status and status.get("state") == "running":
        try:
            os.kill(int(status.get("pid", 0)), 0)
            return "Ya hay un ruteo en curso. Consulta autoroute_status."
        except (ProcessLookupError, ValueError, PermissionError):
            pass
    java, jar, msg = R.find_router(SERVER_DIR)
    if not java:
        return msg
    workdir.mkdir(parents=True, exist_ok=True)
    job = {"lpp": str(lpp), "board": str(bfile), "server_dir": str(SERVER_DIR), "workdir": str(workdir),
           "apply": apply,
           "opts": {"clearance": clearance, "signal_width": signal_width,
                    "allow_rotate": allow_rotate or [], "timeout_minutes": timeout_minutes}}
    jf = workdir / "job.json"
    jf.write_text(json.dumps(job, indent=1), encoding="utf-8")
    log = open(workdir / "worker.log", "w", encoding="utf-8")
    subprocess.Popen([sys.executable, str(SERVER_DIR / "route_worker.py"), str(jf)],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
                     cwd=str(workdir))
    return (f"Ruteo iniciado en segundo plano ({msg}).\n"
            f"Carpeta: {workdir.relative_to(lpp.parent)}\n"
            "Consulta el avance con autoroute_status (cada corrida tarda 1-5 minutos)."
            + ("" if apply else "\nNo se escribirá la placa: usa apply_ses con el resultado."))


def _latest_route_status(lpp: Path) -> dict | None:
    d = _route_dir(lpp)
    files = sorted(d.glob("*/status.json")) if d.exists() else []
    if not files:
        return None
    try:
        st = json.loads(files[-1].read_text(encoding="utf-8"))
        st["_dir"] = str(files[-1].parent)
        return st
    except (json.JSONDecodeError, OSError):
        return None


@mcp.tool()
def autoroute_status(project: str) -> str:
    """Muestra el avance o el resultado del último autoroute: conexiones sin rutear,
    problemas de reglas, variantes probadas y si las pistas ya se escribieron en la placa.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
    """
    lpp = _find_lpp(project)
    st = _latest_route_status(lpp)
    if st is None:
        return "No hay ruteos registrados. Usa autoroute."
    rel = Path(st["_dir"]).relative_to(lpp.parent)
    lines = [f"Ruteo {rel}: {st['state']}"]
    if st["state"] == "running":
        mins = (R.time.time() - st["started"]) / 60
        lines.append(f"En curso hace {mins:.0f} min. Últimos pasos:")
        lines += [f"  {l}" for l in st["log"][-8:]]
        return "\n".join(lines)
    if st["state"] == "error":
        return "\n".join(lines + ["Error:", st.get("error", "")[-1500:]])
    res = st.get("result", {})
    if not res.get("ok"):
        return "\n".join(lines + [res.get("error", "Sin resultado."), *st["log"][-8:]])
    lines.append(f"Mejor variante: {res['best']} (separación {res['clearance']} mm, "
                 f"señales {res.get('signal_width', '?')} mm"
                 + (f", giros {res['rotations']}" if res["rotations"] else "") + ")")
    lines.append(f"Conexiones sin rutear: {res['unrouted']}")
    for u in res.get("unconnected", [])[:20]:
        lines.append(f"  - {u['net']}: pin de {u['pin']} en ({u['x']}, {u['y']})")
    lines.append(f"Problemas de reglas: {len(res['issues'])}")
    lines += [f"  - {i}" for i in res["issues"][:20]]
    if res.get("fixes"):
        lines.append(f"Ajustes finos aplicados: {len(res['fixes'])}")
    lines.append(f"Tramos {res['wires']}, vías {res['vias']}.")
    lines.append("Variantes: " + "; ".join(
        f"{a['tag']}={a['unrouted']} sin rutear/{a['issues']} reglas" for a in res["attempts"]))
    if st.get("applied"):
        s = st["apply_stats"]
        lines.append(f"ESCRITO en la placa: {s['traces']} tramos, {s['vias']} vías"
                     + (f"; piezas giradas: {', '.join(s['moved'])}" if s.get("moved") else "")
                     + f". Copia: {st['backup']}")
        lines.append("Siguiente: run_drc / production_check.")
    elif st.get("apply_msg"):
        lines.append(st["apply_msg"])
        lines.append(f"Resultado guardado en: {Path(res['best_json']).relative_to(lpp.parent)}")
    else:
        lines.append(f"Resultado: {Path(res['best_json']).relative_to(lpp.parent)} (aplícalo con apply_ses)")
    if res["unrouted"] and not res["rotations"]:
        refs = sorted({u["pin"] for u in res.get("unconnected", [])})
        lines.append(f"Para cerrar lo que falta puedes permitir girar: autoroute(allow_rotate={refs}).")
    return "\n".join(lines)


def _load_ses_any(lpp: Path, file: str | None) -> tuple[dict, Path]:
    if file:
        p = _resolve_in_project(lpp, file)
    else:
        st = _latest_route_status(lpp)
        if not st or not st.get("result", {}).get("ok"):
            raise FileNotFoundError("No hay resultado de autoroute; indica ses_file.")
        p = Path(st["result"]["best_json"])
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8")), p
    return R.read_ses(p.read_text(encoding="utf-8")), p


@mcp.tool()
def verify_ses(project: str, ses_file: str | None = None, board: str | None = None) -> str:
    """Revisa un resultado de ruteo ANTES de usarlo: conexiones completas (pin por pin),
    anchos mínimos por clase, pistas y vías junto al borde y taladros de vías juntos.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        ses_file: .ses (o best.ses de autoroute) dentro del proyecto. Por defecto, el último autoroute.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    ses, p = _load_ses_any(lpp, ses_file)
    prj = R.Project(lpp, _board_file(lpp, board))
    rep = R.verify_routing(prj, ses)
    ok = rep["unrouted"] == 0 and not rep["issues"]
    return (f"Archivo: {p.relative_to(lpp.parent)}\n" + R.format_verify(rep) + "\n\n"
            + ("LISTO para aplicar (apply_ses)." if ok else "NO está listo: hay que corregir lo de arriba."))


@mcp.tool()
def apply_ses(project: str, ses_file: str | None = None, force: bool = False,
              board: str | None = None) -> str:
    """Escribe un ruteo (SES de Freerouting o resultado de autoroute) directamente en la
    placa, sin la GUI: borra las pistas anteriores (deja los fiduciales), aplica los
    giros de piezas del ruteo y crea pistas, vías y uniones. Antes verifica el ruteo.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        ses_file: .ses o best.ses dentro del proyecto. Por defecto, el último autoroute.
        force: Aplica aunque la verificación encuentre conexiones sin rutear.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    bfile = _board_file(lpp, board)
    ses, p = _load_ses_any(lpp, ses_file)
    rep = R.verify_routing(R.Project(lpp, bfile), ses)
    if rep["unrouted"] and not force:
        return ("No apliqué: el ruteo deja conexiones sin rutear.\n" + R.format_verify(rep)
                + "\nUsa force=true si igual quieres escribirlo.")
    text, stats = R.apply_ses_to_board(lpp, bfile, ses)
    backup = _backup_and_write(lpp, bfile, text)
    return (f"Ruteo escrito en {bfile.relative_to(lpp.parent)}: {stats['traces']} tramos, "
            f"{stats['vias']} vías, {stats['junctions']} uniones "
            f"(reemplazó {stats['removed_segments']} segmentos anteriores).\n"
            + (f"Piezas giradas/movidas según el ruteo: {', '.join(stats['moved'])}\n" if stats["moved"] else "")
            + (f"Redes del SES que no existen en el proyecto: {', '.join(stats['nets_unknown'])}\n"
               if stats["nets_unknown"] else "")
            + f"Copia de seguridad: {backup}\nSiguiente: run_drc.")


@mcp.tool()
def clear_routing(project: str, board: str | None = None) -> str:
    """Borra todas las pistas, vías y uniones de la placa (deja fiduciales, planos,
    piezas y contorno). Útil antes de volver a rutear.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    if msg := _locked_msg(lpp):
        return msg
    bfile = _board_file(lpp, board)
    text, n = R.clear_routing_text(bfile.read_text(encoding="utf-8"))
    if not n:
        return "La placa no tiene pistas."
    backup = _backup_and_write(lpp, bfile, text)
    return f"Borrados {n} segmentos de ruteo (fiduciales intactos).\nCopia de seguridad: {backup}"


# === Salidas de producción ===

def _production_jobs(include_step: bool) -> tuple[str, dict]:
    ids = {k: _new_uuid() for k in ("gerber", "pnp", "bom", "ibom", "step", "netlist", "zip")}
    step = (f""" (job {ids['step']} (name "STEP")
  (type 3d_model)
  (board default)
  (variant default)
  (output "3d/{{{{PROJECT}}}}.step")
 )
""" if include_step else "")
    jobs = f"""(librepcb_jobs
 (job {ids['gerber']} (name "Gerber")
  (type gerber_excellon)
  (outlines (suffix "_OUTLINES.gbr"))
  (copper_top (suffix "_COPPER-TOP.gbr"))
  (copper_inner (suffix "_COPPER-IN{{{{CU_LAYER}}}}.gbr"))
  (copper_bot (suffix "_COPPER-BOTTOM.gbr"))
  (soldermask_top (suffix "_SOLDERMASK-TOP.gbr"))
  (soldermask_bot (suffix "_SOLDERMASK-BOTTOM.gbr"))
  (silkscreen_top (suffix "_SILKSCREEN-TOP.gbr"))
  (silkscreen_bot (suffix "_SILKSCREEN-BOTTOM.gbr"))
  (solderpaste_top (create true) (suffix "_SOLDERPASTE-TOP.gbr"))
  (solderpaste_bot (create true) (suffix "_SOLDERPASTE-BOTTOM.gbr"))
  (drills (merge false)
   (suffix_pth "_DRILLS-PTH.drl")
   (suffix_npth "_DRILLS-NPTH.drl")
   (suffix_merged "_DRILLS.drl")
   (suffix_buried "_DRILLS-PLATED-{{{{START_LAYER}}}}-{{{{END_LAYER}}}}.drl")
   (g85_slots false)
  )
  (board default)
  (output "gerber/{{{{PROJECT}}}}")
 )
 (job {ids['pnp']} (name "PnP")
  (type pnp) (comment false)
  (tht true) (smt true) (mixed true) (fiducial false) (other false)
  (board default)
  (variant default)
  (top (create false) (output "asm/{{{{PROJECT}}}}_PnP_TOP.csv"))
  (bottom (create false) (output "asm/{{{{PROJECT}}}}_PnP_BOT.csv"))
  (both (create true) (output "asm/{{{{PROJECT}}}}_PnP.csv"))
 )
 (job {ids['bom']} (name "BOM")
  (type bom)
  (board default)
  (variant default)
  (output "asm/{{{{PROJECT}}}}_BOM.csv")
 )
 (job {ids['ibom']} (name "Interactive BOM")
  (type interactive_bom)
  (view_mode left_right) (highlight_pin1 none) (dark_mode false)
  (rotation 0.0) (offset_back_rotation false)
  (show_silkscreen true) (show_fabrication true)
  (show_pads true) (show_tracks true) (show_zones true)
  (checkboxes "Sourced,Placed")
  (component_order "C,R,L,D,U,Y,X,F")
  (board default)
  (variant default)
  (output "asm/{{{{PROJECT}}}}_iBOM.html")
 )
{step} (job {ids['netlist']} (name "Netlist")
  (type netlist)
  (board default)
  (output "{{{{PROJECT}}}}_Netlist.d356")
 )
 (job {ids['zip']} (name "Gerber ZIP")
  (type archive)
  (input {ids['gerber']} (destination ""))
  (output "{{{{PROJECT}}}}_gerber.zip")
 )
)
"""
    return jobs, ids


def _read_csv(path: Path) -> list[dict]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


@mcp.tool()
def export_production(project: str, fab: str = "jlcpcb", include_step: bool = True) -> str:
    """Genera todo lo necesario para fabricar y montar la placa en output/produccion/:
    Gerber + taladros (y un ZIP listo para subir), BOM, pick&place, BOM interactiva (HTML),
    netlist IPC-D-356 y modelo 3D STEP. Con fab="jlcpcb" también crea la BOM y el CPL
    en el formato de montaje de JLCPCB.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        fab: "jlcpcb" o "ninguno".
        include_step: Exportar el modelo 3D STEP (puede tardar).
    """
    lpp = _find_lpp(project)
    outdir = lpp.parent / "output" / "produccion"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    jobs_text, _ = _production_jobs(include_step)
    jobs_file = lpp.parent / "output" / "mcp-jobs.lp"
    jobs_file.write_text(jobs_text, encoding="utf-8")
    result = _run_cli(["open-project", "--jobs", str(jobs_file), "--run-jobs",
                       "--outdir", str(outdir), str(lpp)])
    files = sorted(p for p in outdir.rglob("*") if p.is_file())
    out = [result, ""]
    if not files:
        out.append("No se generaron archivos. Revisa el mensaje de librepcb-cli.")
        return "\n".join(out)

    if fab.lower() == "jlcpcb":
        pnp_files = sorted(outdir.glob("asm/*_PnP.csv"))
        if pnp_files:
            rows = _read_csv(pnp_files[0])
            _, _, croot = _circuit(lpp)
            parts = _component_parts(croot)
            dnp = sorted({r["Designator"] for r in rows if parts.get(r["Designator"], {}).get("dnp")})
            rows = [r for r in rows if r["Designator"] not in dnp]
            jdir = outdir / "jlcpcb"
            jdir.mkdir(exist_ok=True)
            with open(jdir / "CPL.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
                for r in rows:
                    w.writerow([r["Designator"], f"{r['Position X']}mm", f"{r['Position Y']}mm",
                                r["Side"], r["Rotation"]])
            groups: dict[tuple, list[str]] = {}
            for r in rows:
                p = parts.get(r["Designator"], {})
                comment = r["Value"] if r["Value"] and "{{" not in r["Value"] else p.get("mpn", "")
                key = (comment, r["Package"], p.get("attrs", {}).get("LCSC", ""), p.get("mpn", ""))
                groups.setdefault(key, []).append(r["Designator"])
            with open(jdir / "BOM.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "MPN"])
                for (val, pkg, lcsc, mpn), des in sorted(groups.items()):
                    w.writerow([val, ",".join(sorted(des)), pkg, lcsc, mpn])
            no_lcsc = sorted({d for (_, _, lcsc, _), ds in groups.items() if not lcsc for d in ds})
            files = sorted(p for p in outdir.rglob("*") if p.is_file())
            if dnp:
                out.append(f"No montar (fuera de BOM/CPL de JLCPCB): {', '.join(dnp)}.")
            if no_lcsc:
                out.append(f"Sin código LCSC ({len(no_lcsc)}): {', '.join(no_lcsc)}. "
                           "Asígnalos con set_component_part o elige las piezas en la web de JLCPCB.")
            out.append("JLCPCB: revisa la rotación de cada pieza en la vista previa de montaje; "
                       "algunas librerías usan un 0° distinto al de JLCPCB.")
    out.append(f"\nArchivos en {outdir}:")
    out += [f"- {p.relative_to(outdir)}" for p in files]
    return "\n".join(out)


@mcp.tool()
def production_check(project: str, board: str | None = None) -> str:
    """Revisión completa antes de fabricar: ERC, DRC, contorno, superposiciones,
    planos de GND, agujeros de montaje, fiduciales, piezas sin MPN/LCSC y
    reglas de fabricante. Devuelve una lista de verificación con lo que falta.

    Args:
        project: Nombre, carpeta o ruta al .lpp del proyecto.
        board: Nombre de la placa (opcional).
    """
    lpp = _find_lpp(project)
    _, bfile, text, devices, obox = _load_board(project, board)
    root = _parse_sexp(text)
    checks: list[tuple[bool | None, str]] = []

    erc = _run_cli(["open-project", "--erc", str(lpp)])
    checks.append(("[exit 0]" in erc, "ERC sin errores" if "[exit 0]" in erc else "ERC con errores (corre run_erc)"))
    drc_args = ["open-project", "--drc"]
    if board:
        drc_args.append(f"--board={board}")
    drc = _run_cli([*drc_args, str(lpp)])
    n_err = drc.count("[ERROR]")
    n_warn = drc.count("[WARNING]")
    ran = "Run DRC" in drc or "[exit 0]" in drc
    drc_ok = ran and n_err == 0 and ("[exit 0]" in drc or n_warn > 0)
    unrouted = len(re.findall(r"(?i)missing connection|unrouted|airwire", drc))
    checks.append((drc_ok, "DRC sin errores" if drc_ok else
                   (f"DRC con {n_err} errores{f' ({unrouted} conexiones sin rutear)' if unrouted else ''} (corre run_drc)"
                    if ran else "El DRC no pudo correr (corre run_drc para ver el motivo)")))
    if n_warn:
        checks.append((None, f"DRC: {n_warn} avisos (no bloquean; revisa con run_drc)"))

    checks.append((obox is not None, "Contorno de placa definido" if obox else "Falta el contorno (set_board_outline)"))
    if obox:
        w, h = obox[2] - obox[0], obox[3] - obox[1]
        checks.append((None, f"Tamaño de la placa: {_fmt(w)} x {_fmt(h)} mm"))
    probs = _placement_problems(devices, obox)
    checks.append((not probs, "Sin superposiciones ni piezas fuera" if not probs
                   else f"{len(probs)} problemas de ubicación (get_board_layout)"))

    _, _, croot = _circuit(lpp)
    nets = {n.arg(): n.child("name").arg() for n in croot.children("net")}
    planes = [(p.child("layer").arg(), nets.get(p.child("net").arg(), "?")) for p in root.children("plane")]
    gnd = [l for l, n in planes if "GND" in n.upper()]
    checks.append((bool(gnd), f"Plano de GND en {', '.join(gnd)}" if gnd else "Sin plano de GND (add_ground_planes)"))
    traces = sum(len(ns.children("trace")) for ns in root.children("netsegment"))
    checks.append((traces > 0, f"{traces} pistas dibujadas" if traces else "No hay pistas: falta rutear (autoroute)"))
    holes = len(root.children("hole"))
    checks.append((holes >= 3 or None, f"{holes} agujeros de montaje" if holes else "Sin agujeros de montaje (add_mounting_holes, opcional)"))
    fids = sum(1 for ns in root.children("netsegment") for p in ns.children("pad")
               if p.child("function") and "fiducial" in p.child("function").arg())
    fids += sum(1 for d in devices if "FID" in d["name"].upper())
    checks.append((fids >= 2 or None, f"{fids} fiduciales" if fids else "Sin fiduciales (add_fiducials, recomendado para montaje SMT)"))
    zones = len(root.children("zone"))
    checks.append((None, f"{zones} zonas prohibidas" if zones else "Sin zonas prohibidas (si hay antena, usa add_keepout_zone)"))

    parts = _component_parts(croot)
    dnp = sorted(n for n, p in parts.items() if p.get("dnp"))
    missing = sorted(n for n, p in parts.items()
                     if p["has_device"] and not p.get("dnp") and not p["mpn"] and not p["attrs"].get("LCSC"))
    checks.append((not missing, "Todas las piezas a montar tienen MPN/LCSC" if not missing
                   else f"{len(missing)} piezas sin MPN/LCSC: {', '.join(missing[:12])} "
                        "(assign_basic_parts, set_component_part o set_dnp si no se montan)"))
    if dnp:
        checks.append((None, f"No se montan (DNP): {', '.join(dnp)}"))
    li = R.lock_info(lpp.parent)
    if li["locked"]:
        checks.append((None, "LibrePCB tiene el proyecto abierto" + (" (bloqueo viejo)" if li.get("alive") is False else "")
                       + ": no guardes desde la GUI encima de los cambios del servidor."))
    drcs = root.child("design_rule_check")
    if drcs is not None and drcs.child("min_copper_copper_clearance"):
        cl = drcs.child("min_copper_copper_clearance").arg()
        checks.append((None, f"Separación mínima del DRC: {cl} mm (apply_fab_profile para ajustarla al fabricante)"))

    sym = {True: "OK  ", False: "FALTA", None: "INFO"}
    ready = all(ok is not False for ok, _ in checks)
    lines = [f"Revisión de producción: {lpp.stem} / {bfile.parent.name}", ""]
    lines += [f"[{sym[ok]}] {msg}" for ok, msg in checks]
    lines.append("")
    lines.append("LISTA PARA FABRICAR: export_production" if ready else "Todavía NO está lista para fabricar.")
    if not drc_ok:
        lines.append("\nResumen DRC:\n" + "\n".join(drc.splitlines()[:40]))
    return "\n".join(lines)


@mcp.tool()
def cli_command(args: list[str]) -> str:
    """Ejecuta librepcb-cli con argumentos arbitrarios (p. ej. ["open-project", "--help"]).

    Útil para opciones no cubiertas por las otras herramientas, como
    --run-job o open-library --check.

    Args:
        args: Lista de argumentos tal como irían en la terminal.
    """
    return _run_cli(args)


if __name__ == "__main__":
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    mcp.run()  # transporte stdio

"""
Motor de placa para el servidor MCP de LibrePCB.

Todo lo que necesita leer o escribir la placa sin abrir LibrePCB:
  - Modelo del proyecto (redes, clases, componentes, pads en coordenadas de placa).
  - Exportar DSN (Specctra) completo y corregido para Freerouting.
  - Leer SES, verificar conectividad/anchos y escribir las pistas en board.lp.
  - Borrar el ruteo (dejando fiduciales), ubicar la serigrafía, revisión previa.

No depende del SDK de MCP: lo usan librepcb_mcp.py y route_worker.py.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
import uuid as _uuidlib
import zipfile
from pathlib import Path

# ============================================================================
# S-expressions con posiciones (para editar sin reformatear el archivo)
# ============================================================================


class Node:
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

    def args(self):
        return [it.text for it in self.items[1:] if not it.is_list]


def parse(text: str) -> Node:
    root, stack, i, n = Node(0), [], 0, len(text)
    stack.append(root)
    while i < n:
        c = text[i]
        if c == "(":
            node = Node(i)
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
            node = Node(i, "".join(buf), True)
            node.end = j + 1
            stack[-1].items.append(node)
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            node = Node(i, text[i:j])
            node.end = j
            stack[-1].items.append(node)
            i = j
    return root.items[0] if root.items else root


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + new + text[end:]
    return text


def fmt(v: float) -> str:
    s = f"{round(v, 6):.6f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def new_uuid() -> str:
    return str(_uuidlib.uuid4())


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def xy(node: Node) -> tuple[float, float]:
    return float(node.arg(1)), float(node.arg(2))


def rot(x, y, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def to_world(px, py, pos, angle, flip):
    if flip:
        px = -px
    x, y = rot(px, py, angle)
    return x + pos[0], y + pos[1]


# ============================================================================
# Modelo del proyecto
# ============================================================================


def _arc_points(p0, p1, angle_deg, seg=8):
    """Puntos intermedios de un arco LibrePCB (vértice con ángulo) de p0 a p1."""
    if abs(angle_deg) < 1e-9:
        return []
    a = math.radians(angle_deg)
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return []
    r = chord / (2 * math.sin(abs(a) / 2))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    h = math.sqrt(max(r * r - (chord / 2) ** 2, 0.0))
    nx, ny = -dy / chord, dx / chord
    sgn = 1 if a > 0 else -1
    if abs(a) > math.pi:
        sgn = -sgn
    cx, cy = mx + sgn * nx * h, my + sgn * ny * h
    t0 = math.atan2(y0 - cy, x0 - cx)
    pts = []
    for k in range(1, seg):
        t = t0 + a * k / seg
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return pts


def polygon_points(poly: Node) -> list[tuple[float, float]]:
    verts = poly.children("vertex")
    out = []
    for i, v in enumerate(verts):
        p = xy(v.child("position"))
        out.append(p)
        ang = float(v.child("angle").arg()) if v.child("angle") else 0.0
        if ang and i + 1 < len(verts):
            out += _arc_points(p, xy(verts[i + 1].child("position")), ang)
    return out


class Pad:
    """Pad de un footprint ya ubicado en la placa (coordenadas de placa)."""
    __slots__ = ("device", "ref", "uuid", "pin_id", "net", "x", "y", "angle", "w", "h",
                 "radius", "layers", "tht", "local", "hole")

    def corners(self, grow=0.0):
        hw, hh = self.w / 2 + grow, self.h / 2 + grow
        return [(self.x + cx, self.y + cy) for cx, cy in
                (rot(sx * hw, sy * hh, self.angle) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)))]

    def bbox(self, grow=0.0):
        c = self.corners(grow)
        return (min(p[0] for p in c), min(p[1] for p in c), max(p[0] for p in c), max(p[1] for p in c))

    def contains(self, x, y, tol=0.0):
        lx, ly = rot(x - self.x, y - self.y, -self.angle)
        return abs(lx) <= self.w / 2 + tol and abs(ly) <= self.h / 2 + tol


class Project:
    """Lee circuit.lp, la librería local y board.lp."""

    def __init__(self, lpp: Path, board_file: Path):
        self.lpp = lpp
        self.dir = lpp.parent
        self.board_file = board_file
        self.board_text = board_file.read_text(encoding="utf-8")
        self.board = parse(self.board_text)
        self.circuit_path = self.dir / "circuit" / "circuit.lp"
        self.circuit_text = self.circuit_path.read_text(encoding="utf-8")
        self.circuit = parse(self.circuit_text)
        self._load_circuit()
        self._load_library()
        self._load_board()

    # ---- circuito -------------------------------------------------------
    def _load_circuit(self):
        c = self.circuit
        self.netclasses = {}
        for nc in c.children("netclass"):
            def num(key, default=0.0):
                n = nc.child(key)
                if n is None:
                    return default
                try:
                    return float(n.arg())
                except ValueError:
                    return default
            self.netclasses[nc.arg()] = {
                "name": nc.child("name").arg(),
                "width": num("default_trace_width", 0.0),
                "clearance": num("min_copper_copper_clearance", 0.0),
                "min_width": num("min_copper_width", 0.0),
            }
        self.nets, self.net_class = {}, {}
        for n in c.children("net"):
            self.nets[n.arg()] = n.child("name").arg()
            if n.child("netclass") is not None:
                self.net_class[n.arg()] = n.child("netclass").arg()
        self.components = {}
        for comp in c.children("component"):
            dev = comp.child("device")
            attrs = {}
            for a in comp.children("attribute"):
                attrs[a.arg()] = {"value": a.child("value").arg(),
                                  "unit": a.child("unit").arg() if a.child("unit") else "none",
                                  "type": a.child("type").arg() if a.child("type") else "string"}
            part = None
            if dev is not None and dev.children("part"):
                p = dev.children("part")[0]
                part = {"mpn": p.arg(),
                        "manufacturer": p.child("manufacturer").arg() if p.child("manufacturer") else "",
                        "attrs": {a.arg(): a.child("value").arg() for a in p.children("attribute")}}
            self.components[comp.arg()] = {
                "uuid": comp.arg(),
                "name": comp.child("name").arg(),
                "value": comp.child("value").arg() if comp.child("value") else "",
                "attrs": attrs,
                "device": dev.arg() if dev is not None else None,
                "part": part,
                "signals": {s.arg(): (s.child("net").arg() if s.child("net") else "none")
                            for s in comp.children("signal")},
                "node": comp,
            }

    def is_dnp(self, comp: dict) -> bool:
        v = comp["attrs"].get("DNP", {}).get("value", "")
        return v.strip().lower() in ("true", "1", "yes", "si", "sí")

    # ---- librería local -------------------------------------------------
    def _load_library(self):
        self.lib_devices = {}
        for f in (self.dir / "library" / "dev").glob("*/device.lp"):
            r = parse(f.read_text(encoding="utf-8"))
            self.lib_devices[r.arg()] = {
                "name": r.child("name").arg() if r.child("name") else "",
                "package": r.child("package").arg() if r.child("package") else None,
                "pads": {p.arg(): (p.child("signal").arg() if p.child("signal") else "none")
                         for p in r.children("pad")},
            }
        self.footprints, self.package_names, self.package_files = {}, {}, {}
        for f in (self.dir / "library" / "pkg").glob("*/package.lp"):
            r = parse(f.read_text(encoding="utf-8"))
            self.package_names[r.arg()] = r.child("name").arg() if r.child("name") else ""
            self.package_files[r.arg()] = f
            for fp in r.children("footprint"):
                pads = []
                for p in fp.children("pad"):
                    holes = []
                    for h in p.children("hole"):
                        holes.append((float(h.child("diameter").arg()),
                                      [xy(v.child("position")) for v in h.children("vertex")]))
                    size = xy(p.child("size"))
                    pads.append({
                        "uuid": p.arg(),
                        "package_pad": p.child("package_pad").arg() if p.child("package_pad") else p.arg(),
                        "side": p.child("side").arg() if p.child("side") else "top",
                        "shape": p.child("shape").arg() if p.child("shape") else "roundrect",
                        "pos": xy(p.child("position")),
                        "rot": float(p.child("rotation").arg()) if p.child("rotation") else 0.0,
                        "size": size,
                        "radius": float(p.child("radius").arg()) if p.child("radius") else 0.0,
                        "holes": holes,
                        "function": p.child("function").arg() if p.child("function") else "standard",
                    })
                court, other, copper = [], [], []
                for poly in fp.children("polygon"):
                    layer = poly.child("layer").arg()
                    pts = polygon_points(poly)
                    if "courtyard" in layer:
                        court += pts
                    else:
                        other += pts
                    if layer in ("top_cu", "bot_cu"):
                        w = float(poly.child("width").arg()) if poly.child("width") else 0.0
                        copper.append({"layer": layer, "pts": pts, "width": w})
                for circ in fp.children("circle"):
                    cx, cy = xy(circ.child("position"))
                    rr = float(circ.child("diameter").arg()) / 2
                    layer = circ.child("layer").arg()
                    pts = [(cx - rr, cy - rr), (cx + rr, cy + rr)]
                    (court if "courtyard" in layer else other).extend(pts)
                for p in pads:
                    hw, hh = p["size"][0] / 2, p["size"][1] / 2
                    for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                        rx, ry = rot(dx, dy, p["rot"])
                        other.append((p["pos"][0] + rx, p["pos"][1] + ry))
                npth = []
                for h in fp.children("hole"):
                    npth.append((float(h.child("diameter").arg()),
                                 [xy(v.child("position")) for v in h.children("vertex")]))
                pts = court or other
                bbox = None
                if pts:
                    bbox = (min(p[0] for p in pts), min(p[1] for p in pts),
                            max(p[0] for p in pts), max(p[1] for p in pts))
                self.footprints[fp.arg()] = {"pads": pads, "bbox": bbox, "copper": copper,
                                             "npth": npth, "package": r.arg()}

    # ---- placa ----------------------------------------------------------
    def _load_board(self):
        b = self.board
        layers = b.child("layers")
        self.inner_layers = int(layers.child("inner").arg()) if layers is not None and layers.child("inner") else 0
        self.rules = {}
        drc = b.child("design_rule_check")
        for it in (drc.items[1:] if drc is not None else []):
            if it.is_list and len(it.items) == 2 and not it.items[1].is_list:
                try:
                    self.rules[it.name] = float(it.arg())
                except ValueError:
                    pass
        dr = b.child("design_rules")
        self.default_trace_width = 0.25
        self.via_drill = 0.3
        self.via_ring = 0.15
        if dr is not None:
            if dr.child("default_trace_width"):
                self.default_trace_width = float(dr.child("default_trace_width").arg())
            if dr.child("default_via_drill_diameter"):
                self.via_drill = float(dr.child("default_via_drill_diameter").arg())
            var = dr.child("via_annular_ring")
            if var is not None:
                ratio = float(var.child("ratio").arg()) if var.child("ratio") else 0.25
                mn = float(var.child("min").arg()) if var.child("min") else 0.15
                mx = float(var.child("max").arg()) if var.child("max") else 2.0
                self.via_ring = min(max(ratio * self.via_drill, mn), mx)
        self.via_size = self.via_drill + 2 * self.via_ring

        self.outline = []
        for poly in b.children("polygon"):
            if poly.child("layer").arg() == "brd_outlines":
                self.outline = polygon_points(poly)
        xs = [p[0] for p in self.outline] or [0.0]
        ys = [p[1] for p in self.outline] or [0.0]
        self.obox = (min(xs), min(ys), max(xs), max(ys)) if self.outline else None

        self.devices, self.pads = [], []
        for d in b.children("device"):
            comp = self.components.get(d.arg(), {"name": d.arg()[:8], "signals": {}, "attrs": {},
                                                 "value": "", "part": None, "uuid": d.arg()})
            pos = xy(d.child("position"))
            angle = float(d.child("rotation").arg())
            flip = d.child("flip").arg() == "true" if d.child("flip") else False
            fp = self.footprints.get(d.child("lib_footprint").arg())
            libdev = self.lib_devices.get(d.child("lib_device").arg(), {"pads": {}})
            dev = {"uuid": d.arg(), "ref": comp["name"], "pos": pos, "rot": angle, "flip": flip,
                   "node": d, "comp": comp, "footprint": fp, "pads": []}
            if fp and fp["bbox"]:
                lb = fp["bbox"]
                cs = [to_world(x, y, pos, angle, flip) for x in (lb[0], lb[2]) for y in (lb[1], lb[3])]
                dev["bbox"] = (min(c[0] for c in cs), min(c[1] for c in cs),
                               max(c[0] for c in cs), max(c[1] for c in cs))
            else:
                dev["bbox"] = (pos[0] - 0.5, pos[1] - 0.5, pos[0] + 0.5, pos[1] + 0.5)
            for fpad in (fp["pads"] if fp else []):
                pad = Pad()
                pad.device, pad.ref, pad.uuid = d.arg(), comp["name"], fpad["uuid"]
                pad.pin_id = fpad["uuid"].replace("-", "")
                sig = libdev["pads"].get(fpad["package_pad"], "none")
                pad.net = comp["signals"].get(sig, "none") if sig != "none" else "none"
                pad.x, pad.y = to_world(*fpad["pos"], pos, angle, flip)
                pad.angle = (angle - fpad["rot"]) if flip else (angle + fpad["rot"])
                pad.w, pad.h = fpad["size"]
                pad.radius = fpad["radius"]
                pad.tht = bool(fpad["holes"])
                side = fpad["side"]
                if pad.tht:
                    pad.layers = ("top_cu", "bot_cu")
                else:
                    top = (side == "top") != flip
                    pad.layers = ("top_cu",) if top else ("bot_cu",)
                pad.local = fpad
                pad.hole = fpad["holes"]
                dev["pads"].append(pad)
                self.pads.append(pad)
            self.devices.append(dev)
        self.by_ref = {d["ref"]: d for d in self.devices}

        # Agujeros de placa, zonas, planos, segmentos de red
        self.holes = []
        for h in b.children("hole"):
            verts = [xy(v.child("position")) for v in h.children("vertex")]
            self.holes.append((float(h.child("diameter").arg()), verts))
        self.zones = []
        for z in b.children("zone"):
            flags = {k: (z.child(k).arg() == "true") if z.child(k) else False
                     for k in ("no_copper", "no_planes", "no_exposure", "no_devices")}
            self.zones.append({"layers": [l.arg() for l in z.children("layer")],
                               "pts": polygon_points(z), **flags, "node": z})
        self.planes = [{"layer": p.child("layer").arg(), "net": p.child("net").arg()}
                       for p in b.children("plane")]
        self.fiducials, self.vias, self.traces = [], [], []
        for ns in b.children("netsegment"):
            net = ns.child("net").arg() if ns.child("net") else "none"
            for p in ns.children("pad"):
                fn = p.child("function").arg() if p.child("function") else ""
                pos = xy(p.child("position"))
                size = xy(p.child("size"))
                cl = float(p.child("clearance").arg()) if p.child("clearance") else 0.0
                self.fiducials.append({"pos": pos, "size": size, "clearance": cl,
                                       "fiducial": "fiducial" in fn, "net": net})
            for v in ns.children("via"):
                self.vias.append({"pos": xy(v.child("position")), "net": net})
            self.traces += [t for t in ns.children("trace")]

    # ---- utilidades -----------------------------------------------------
    def net_name(self, net_uuid):
        return self.nets.get(net_uuid, "")

    def class_of(self, net_uuid):
        c = self.net_class.get(net_uuid)
        return self.netclasses.get(c) if c else None

    def plane_nets(self):
        return {p["net"] for p in self.planes}

    def pins_by_net(self):
        out = {}
        for p in self.pads:
            if p.net != "none":
                out.setdefault(p.net, []).append(p)
        return out

    def locked(self) -> bool:
        return (self.dir / ".lock").exists()


# ============================================================================
# Bloqueo del proyecto
# ============================================================================


def lock_info(project_dir: Path) -> dict:
    """Lee .lock de LibrePCB y comprueba si el proceso sigue vivo (mismo equipo)."""
    lock = project_dir / ".lock"
    if not lock.exists():
        return {"locked": False}
    lines = lock.read_text(encoding="utf-8", errors="replace").splitlines()
    info = {"locked": True, "user": lines[0] if lines else "", "host": lines[2] if len(lines) > 2 else "",
            "pid": None, "alive": None, "age_min": round((time.time() - lock.stat().st_mtime) / 60, 1)}
    if len(lines) > 3:
        try:
            info["pid"] = int(lines[3])
        except ValueError:
            pass
    if info["pid"]:
        import os
        import socket
        same_host = info["host"].split(".")[0].lower() == socket.gethostname().split(".")[0].lower()
        if same_host:
            try:
                os.kill(info["pid"], 0)
                info["alive"] = True
            except ProcessLookupError:
                info["alive"] = False
            except PermissionError:
                info["alive"] = True
    return info


# ============================================================================
# DSN (Specctra) para Freerouting
# ============================================================================

DSN_DEFAULTS = {
    "signal_width": 0.2,      # pista de señal
    "clearance": 0.15,        # separación global cobre-cobre
    "edge_via_band": 0.8,     # franja sin vías junto al borde
    "edge_copper_band": None,  # franja sin cobre (None = min_copper_board_clearance de la placa)
    "fiducial_clearance": 0.5,
    "route_plane_nets": True,  # rutear también las redes con plano (GND) con pistas
}


def _dq(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def _padstack_shapes(pad: Pad):
    """Formas Specctra en coordenadas locales del pad (sin rotar)."""
    w, h, r = pad.w, pad.h, pad.radius
    shapes = []
    for layer in pad.layers:
        if r >= 0.99 and abs(w - h) < 1e-6:
            shapes.append(f"(circle {layer} {fmt(w)})")
        elif r >= 0.99:
            if w > h:
                d = (w - h) / 2
                shapes.append(f"(path {layer} {fmt(h)} {fmt(-d)} 0 {fmt(d)} 0)")
            else:
                d = (h - w) / 2
                shapes.append(f"(path {layer} {fmt(w)} 0 {fmt(-d)} 0 {fmt(d)})")
        else:
            shapes.append(f"(rect {layer} {fmt(-w / 2)} {fmt(-h / 2)} {fmt(w / 2)} {fmt(h / 2)})")
    return shapes


def dsn_via_name(prj: Project) -> str:
    return f"via-{fmt(prj.via_drill)}:{fmt(prj.via_size)}"


def export_dsn(prj: Project, opts: dict | None = None, wiring: list[str] | None = None,
               rotations: dict[str, float] | None = None) -> str:
    """Genera el DSN completo desde los archivos del proyecto.

    opts: ver DSN_DEFAULTS. Además acepta "class_width": {nombre_clase: ancho} para
    forzar anchos (2ª etapa) y "signal_clearance" para la separación global.
    wiring: líneas (wire ...)/(via ...) ya formateadas para la sección (wiring).
    rotations: {ref: rotación} para probar girar piezas sin tocar la placa.
    """
    o = dict(DSN_DEFAULTS)
    o.update(opts or {})
    rotations = rotations or {}
    rules = prj.rules
    drc_min_cl = rules.get("min_copper_copper_clearance", 0.127)
    class_cls = [c["clearance"] for c in prj.netclasses.values() if c["clearance"] > 0]
    clearance = max([o["clearance"], drc_min_cl] + class_cls)
    copper_band = o["edge_copper_band"]
    if copper_band is None:
        copper_band = max(rules.get("min_copper_board_clearance", 0.3) - clearance + 0.02, 0.0)
    plane_nets = prj.plane_nets() if not o["route_plane_nets"] else set()

    L = []
    L.append(f"(pcb {_dq(prj.lpp.stem)}")
    L.append(" (parser\n  (string_quote \")\n  (space_in_quoted_tokens on)\n"
             "  (host_cad \"LibrePCB-MCP\")\n  (host_version \"2\")\n )")
    L.append(" (resolution mm 1000000)\n (unit mm)")
    L.append(" (structure")
    L.append("  (layer top_cu (type signal))")
    L.append("  (layer bot_cu (type signal))")
    if prj.outline:
        coords = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in prj.outline)
        L.append(f"  (boundary (path pcb 0 {coords}))")
    # planos solo si no se rutean con pistas
    for p in prj.planes:
        if p["net"] in plane_nets and prj.outline:
            coords = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in prj.outline)
            L.append(f"  (plane {_dq(prj.net_name(p['net']))} (polygon {p['layer']} 0 {coords}))")
    # agujeros de la placa
    npth_cl = rules.get("min_copper_npth_clearance", 0.25)
    for i, (dia, verts) in enumerate(prj.holes):
        for vx, vy in verts[:1]:
            L.append(f"  (keepout \"hole{i}\" (circle signal {fmt(dia + 2 * npth_cl)} {fmt(vx)} {fmt(vy)}))")
    # zonas sin cobre
    for i, z in enumerate(prj.zones):
        if not z["no_copper"] or not z["pts"]:
            continue
        coords = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in z["pts"] + z["pts"][:1])
        for layer in z["layers"]:
            if layer in ("top_cu", "bot_cu"):
                L.append(f"  (keepout \"zone{i}{layer}\" (polygon {layer} 0 {coords}))")
    # fiduciales y pads sueltos
    for i, f in enumerate(prj.fiducials):
        d = max(f["size"]) + 2 * max(f["clearance"], o["fiducial_clearance"])
        L.append(f"  (keepout \"fid{i}\" (circle top_cu {fmt(d)} {fmt(f['pos'][0])} {fmt(f['pos'][1])}))")
    # franjas en el borde
    if prj.obox:
        x0, y0, x1, y1 = prj.obox
        for kind, band in (("via_keepout", o["edge_via_band"]), ("keepout", copper_band)):
            if band <= 0:
                continue
            rects = [(x0, y0, x1, y0 + band), (x0, y1 - band, x1, y1),
                     (x0, y0, x0 + band, y1), (x1 - band, y0, x1, y1)]
            for layer in ("top_cu", "bot_cu"):
                for j, (a, b, c, d) in enumerate(rects):
                    L.append(f"  ({kind} \"edge{kind[0]}{layer}{j}\" (rect {layer} {fmt(a)} {fmt(b)} {fmt(c)} {fmt(d)}))")
    via_name = dsn_via_name(prj)
    L.append(f"  (via {_dq(via_name)})")
    drill_cl = rules.get("min_drill_drill_clearance", 0.5)
    via_via = max(clearance, drill_cl + prj.via_drill - prj.via_size + 0.01)
    L.append("  (rule")
    L.append(f"   (width {fmt(o['signal_width'])})")
    L.append(f"   (clearance {fmt(clearance)})")
    L.append(f"   (clearance {fmt(via_via)} (type via_via))")
    L.append("   (clearance 0.0 (type smd_via_same_net))")
    L.append("   (clearance 0.0 (type via_via_same_net))")
    L.append("  )")
    L.append(" )")

    # colocación e imágenes (una imagen por pieza)
    L.append(" (placement")
    for d in prj.devices:
        if not d["pads"] and not (d["footprint"] and d["footprint"]["npth"]):
            continue
        angle = rotations.get(d["ref"], d["rot"])
        side = "back" if d["flip"] else "front"
        L.append(f"  (component {_dq('IMG_' + d['ref'])} (place {_dq(d['ref'])} "
                 f"{fmt(d['pos'][0])} {fmt(d['pos'][1])} {side} {fmt(angle % 360)}))")
    L.append(" )")
    L.append(" (library")
    padstacks = {}
    for d in prj.devices:
        fp = d["footprint"]
        if not d["pads"] and not (fp and fp["npth"]):
            continue
        L.append(f"  (image {_dq('IMG_' + d['ref'])}")
        for pad in d["pads"]:
            shapes = _padstack_shapes(pad)
            key = tuple(shapes)
            if key not in padstacks:
                padstacks[key] = f"ps{len(padstacks)}"
            lp = pad.local
            L.append(f"   (pin {padstacks[key]} (rotate {fmt(lp['rot'])}) {pad.pin_id} "
                     f"{fmt(lp['pos'][0])} {fmt(lp['pos'][1])})")
        for dia, verts in (fp["npth"] if fp else []):
            if verts:
                L.append(f"   (keepout (circle signal {fmt(dia + 2 * npth_cl)} {fmt(verts[0][0])} {fmt(verts[0][1])}))")
        for cp in (fp["copper"] if fp else []):
            pts = cp["pts"]
            if len(pts) >= 2:
                coords = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)
                if len(pts) >= 3 and cp.get("width", 0) == 0:
                    L.append(f"   (keepout (polygon {cp['layer']} 0 {coords}))")
                else:
                    L.append(f"   (keepout (path {cp['layer']} {fmt(max(cp['width'], 0.1))} {coords}))")
        L.append("  )")
    for shapes, name in padstacks.items():
        L.append(f"  (padstack {name}")
        for s in shapes:
            L.append(f"   (shape {s})")
        L.append("   (attach off)\n  )")
    L.append(f"  (padstack {_dq(via_name)}")
    L.append(f"   (shape (circle top_cu {fmt(prj.via_size)}))")
    L.append(f"   (shape (circle bot_cu {fmt(prj.via_size)}))")
    L.append("   (attach off)\n  )")
    L.append(" )")

    # red y clases
    L.append(" (network")
    by_net = prj.pins_by_net()
    for net_uuid, pins in sorted(by_net.items(), key=lambda kv: prj.net_name(kv[0])):
        if len(pins) < 2:
            continue
        L.append(f"  (net {_dq(prj.net_name(net_uuid))}")
        L.append("   (pins " + " ".join(f"{_dq(p.ref)}-{p.pin_id}" for p in pins) + ")")
        L.append("  )")
    class_width = o.get("class_width") or {}
    classes = {}
    for net_uuid in by_net:
        c = prj.class_of(net_uuid)
        if c and c["width"] > 0:
            classes.setdefault(c["name"], (c, []))[1].append(prj.net_name(net_uuid))
    for name, (c, nets) in classes.items():
        w = class_width.get(name, c["width"])
        w = max(w, c["min_width"], rules.get("min_copper_width", 0.0))
        L.append(f"  (class {_dq(name)} " + " ".join(_dq(n) for n in sorted(nets)))
        L.append(f"   (circuit (use_via {_dq(via_name)}))")
        L.append(f"   (rule (width {fmt(w)}) (clearance {fmt(clearance)}))")
        L.append("  )")
    L.append(" )")
    L.append(" (wiring")
    L += ["  " + w for w in (wiring or [])]
    L.append(" )")
    L.append(")")
    return "\n".join(L) + "\n"


# ============================================================================
# SES (resultado de Freerouting)
# ============================================================================


def _tok(s):
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "()":
            yield c
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j = s.index('"', i + 1)
            yield s[i:j + 1]
            i = j + 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in "()":
                j += 1
            yield s[i:j]
            i = j


def _plain(s):
    st = [[]]
    for t in _tok(s):
        if t == "(":
            st.append([])
        elif t == ")":
            x = st.pop()
            st[-1].append(x)
        else:
            st[-1].append(t)
    return st[0][0] if st[0] else []


def _find(node, name):
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            yield c


def read_ses(path_or_text) -> dict:
    """Devuelve {"nets": {nombre: {"wires": [(capa, ancho, [(x,y)...])], "vias": [(x,y)]}},
    "placement": {ref: (x, y, lado, rot)}}. Coordenadas en mm."""
    text = path_or_text if "(" in str(path_or_text)[:20] else Path(path_or_text).read_text(encoding="utf-8")
    ses = _plain(text)
    res = 1e6
    out = {"nets": {}, "placement": {}}
    for routes in _find(ses, "routes"):
        for r in _find(routes, "resolution"):
            res = float(r[2])
        for no in _find(routes, "network_out"):
            for n in _find(no, "net"):
                name = n[1].strip('"')
                entry = out["nets"].setdefault(name, {"wires": [], "vias": []})
                for w in _find(n, "wire"):
                    for p in _find(w, "path"):
                        nums = [float(v) / res for v in p[3:] if not isinstance(v, list)]
                        pts = list(zip(nums[::2], nums[1::2]))
                        entry["wires"].append((p[1], float(p[2]) / res, pts))
                for v in _find(n, "via"):
                    entry["vias"].append((float(v[2]) / res, float(v[3]) / res))
    for pl in _find(ses, "placement"):
        r = 1e6
        for rr in _find(pl, "resolution"):
            r = float(rr[2])
        for comp in _find(pl, "component"):
            for p in _find(comp, "place"):
                out["placement"][p[1].strip('"')] = (float(p[2]) / r, float(p[3]) / r, p[4],
                                                    float(p[5]) if len(p) > 5 else 0.0)
    return out


def dedupe_ses_padstacks(text: str) -> str:
    """Freerouting a veces repite la misma definición de vía; LibrePCB la rechaza."""
    try:
        root = parse(text)
    except (IndexError, ValueError):
        return text
    edits = []
    for routes in root.children("routes"):
        for lib in routes.children("library_out"):
            seen = set()
            for ps in lib.children("padstack"):
                name = ps.arg()
                if name in seen:
                    ls = text.rfind("\n", 0, ps.start)
                    edits.append((ls if ls >= 0 else ps.start, ps.end, ""))
                seen.add(name)
    return apply_edits(text, edits)


def ses_to_wiring(ses: dict, via_name: str, protect=True, free_near=None, radius=0.0) -> list[str]:
    """Convierte el ruteo de un SES en líneas (wiring) de DSN.
    protect: fija el ruteo (2ª etapa). free_near: puntos [(x, y)] alrededor de los cuales
    el ruteo queda libre para que Freerouting lo rehaga (radio en mm)."""
    out = []
    free_near = free_near or []

    def near(pts):
        return any(math.hypot(x - a, y - b) < radius for x, y in pts for a, b in free_near)

    for net, e in ses["nets"].items():
        for layer, w, pts in e["wires"]:
            coords = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)
            t = " (type protect)" if protect and not near(pts) else ""
            out.append(f"(wire (path {layer} {fmt(w)} {coords}) (net {_dq(net)}){t})")
        for x, y in e["vias"]:
            t = " (type protect)" if protect and not near([(x, y)]) else ""
            out.append(f"(via {_dq(via_name)} {fmt(x)} {fmt(y)} (net {_dq(net)}){t})")
    return out


# ============================================================================
# Verificación de un ruteo (antes de entregarlo)
# ============================================================================


def _dseg(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


class _UF:
    def __init__(self):
        self.p = {}

    def f(self, k):
        self.p.setdefault(k, k)
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def u(self, a, b):
        self.p[self.f(a)] = self.f(b)


def _wire_touches_pad(wire, pad: Pad):
    layer, w, pts = wire
    if layer not in pad.layers:
        return False
    if any(pad.contains(x, y, 0.01) for x, y in pts):
        return True
    r = min(pad.w, pad.h) / 2
    return any(_dseg((pad.x, pad.y), pts[i], pts[i + 1]) < r for i in range(len(pts) - 1))


def _wire_touches_point(wire, pt, tol):
    pts = wire[2]
    if len(pts) == 1:
        return math.hypot(pts[0][0] - pt[0], pts[0][1] - pt[1]) < tol
    return any(_dseg(pt, pts[i], pts[i + 1]) < tol for i in range(len(pts) - 1))


def _seg_rect_dist(a, b, hw, hh):
    """Distancia entre el segmento a-b y el rectángulo [-hw,hw]x[-hh,hh] (0 si se cortan)."""
    def pt_rect(p):
        return math.hypot(max(abs(p[0]) - hw, 0.0), max(abs(p[1]) - hh, 0.0))
    # ¿se cortan? (Liang-Barsky)
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    inside = True
    for p_, q_ in ((-dx, a[0] + hw), (dx, hw - a[0]), (-dy, a[1] + hh), (dy, hh - a[1])):
        if p_ == 0:
            if q_ < 0:
                inside = False
                break
        else:
            r = q_ / p_
            if p_ < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                inside = False
                break
    if inside:
        return 0.0
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return min([pt_rect(a), pt_rect(b)] + [_dseg(c, a, b) for c in corners])


def verify_routing(prj: Project, ses: dict, rotations_applied=True) -> dict:
    """Comprueba conectividad, anchos y distancias al borde. Devuelve un informe."""
    name_to_uuid = {v: k for k, v in prj.nets.items()}
    pins = prj.pins_by_net()
    # Si el SES trae otra rotación para alguna pieza, se recalculan sus pads.
    moved = {}
    for ref, (x, y, side, r) in ses.get("placement", {}).items():
        d = prj.by_ref.get(ref)
        if d and (abs((r - d["rot"]) % 360) > 1e-6 and abs((r - d["rot"]) % 360 - 360) > 1e-6
                  or abs(x - d["pos"][0]) > 1e-4 or abs(y - d["pos"][1]) > 1e-4):
            moved[ref] = (x, y, r)
    def pad_world(p: Pad):
        if p.ref not in moved:
            return p
        x, y, r = moved[p.ref]
        d = prj.by_ref[p.ref]
        q2 = Pad()
        for k in Pad.__slots__:
            setattr(q2, k, getattr(p, k))
        q2.x, q2.y = to_world(*p.local["pos"], (x, y), r, d["flip"])
        q2.angle = (r - p.local["rot"]) if d["flip"] else (r + p.local["rot"])
        return q2

    unconnected, total = [], 0
    for net_uuid, plist in pins.items():
        if len(plist) < 2:
            continue
        plist = [pad_world(p) for p in plist]
        name = prj.net_name(net_uuid)
        e = ses["nets"].get(name, {"wires": [], "vias": []})
        uf = _UF()
        wires, vias = e["wires"], e["vias"]
        for i, wv in enumerate(wires):
            uf.f(("w", i))
            for j, p in enumerate(plist):
                if _wire_touches_pad(wv, p):
                    uf.u(("w", i), ("p", j))
            for k, v in enumerate(vias):
                if _wire_touches_point(wv, v, prj.via_size / 2):
                    uf.u(("w", i), ("v", k))
            for k in range(i):
                o = wires[k]
                if o[0] != wv[0]:
                    continue
                if (_wire_touches_point(o, wv[2][0], 0.01) or _wire_touches_point(o, wv[2][-1], 0.01)
                        or _wire_touches_point(wv, o[2][0], 0.01) or _wire_touches_point(wv, o[2][-1], 0.01)):
                    uf.u(("w", i), ("w", k))
        for j, p in enumerate(plist):
            for k, v in enumerate(vias):
                if p.tht and p.contains(v[0], v[1], 0.0):
                    uf.u(("p", j), ("v", k))
        groups = {}
        for j in range(len(plist)):
            groups.setdefault(uf.f(("p", j)), []).append(j)
        if len(groups) > 1:
            biggest = max(groups.values(), key=len)
            for g in groups.values():
                if g is biggest:
                    continue
                for j in g:
                    unconnected.append({"net": name, "pin": f"{plist[j].ref}", "x": round(plist[j].x, 2),
                                        "y": round(plist[j].y, 2)})
            total += len(groups) - 1

    # Anchos mínimos por clase y distancias al borde
    issues = rotation_conflicts(prj, {ref: v[2] for ref, v in moved.items()}) if moved else []
    min_w = prj.rules.get("min_copper_width", 0.0)
    edge_cl = prj.rules.get("min_copper_board_clearance", 0.0)
    drill_edge = prj.rules.get("min_drill_board_clearance", 0.0)
    drill_cl = prj.rules.get("min_drill_drill_clearance", 0.0)
    allvias = []
    for name, e in ses["nets"].items():
        c = prj.class_of(name_to_uuid.get(name, ""))
        need = max(min_w, c["min_width"] if c else 0.0)
        for layer, w, pts in e["wires"]:
            if w + 1e-6 < need:
                issues.append(f"{name}: pista de {fmt(w)} mm < mínimo {fmt(need)} mm")
            if prj.obox:
                x0, y0, x1, y1 = prj.obox
                for x, y in pts:
                    d = min(x - x0, y - y0, x1 - x, y1 - y) - w / 2
                    if d + 1e-6 < edge_cl:
                        issues.append(f"{name}: pista a {fmt(d)} mm del borde (mín {fmt(edge_cl)}) en ({fmt(x)}, {fmt(y)})")
                        break
        for v in e["vias"]:
            allvias.append((name, v))
            if prj.obox:
                x0, y0, x1, y1 = prj.obox
                d = min(v[0] - x0, v[1] - y0, x1 - v[0], y1 - v[1]) - prj.via_drill / 2
                if d + 1e-6 < drill_edge:
                    issues.append(f"{name}: vía a {fmt(d)} mm del borde (mín {fmt(drill_edge)}) en ({fmt(v[0])}, {fmt(v[1])})")
    # Separación pista ↔ pads de otras redes (lo que LibrePCB revisa en el DRC)
    min_cl = prj.rules.get("min_copper_copper_clearance", 0.0)
    pad_list = [pad_world(p) for p in prj.pads]
    pad_boxes = [(p, p.bbox()) for p in pad_list]
    for name, e in ses["nets"].items():
        nu = name_to_uuid.get(name)
        c = prj.class_of(nu or "")
        need = max(min_cl, c["clearance"] if c else 0.0)
        reported = 0
        for layer, w, pts in e["wires"]:
            for k in range(len(pts) - 1):
                a, b = pts[k], pts[k + 1]
                sx0, sx1 = min(a[0], b[0]) - w / 2 - need, max(a[0], b[0]) + w / 2 + need
                sy0, sy1 = min(a[1], b[1]) - w / 2 - need, max(a[1], b[1]) + w / 2 + need
                for p, bb in pad_boxes:
                    if p.net == nu or layer not in p.layers:
                        continue
                    if bb[2] < sx0 or bb[0] > sx1 or bb[3] < sy0 or bb[1] > sy1:
                        continue
                    la = rot(a[0] - p.x, a[1] - p.y, -p.angle)
                    lb = rot(b[0] - p.x, b[1] - p.y, -p.angle)
                    rr = min(max(p.radius, 0.0), 1.0) * min(p.w, p.h) / 2  # esquinas redondeadas
                    dist = _seg_rect_dist(la, lb, p.w / 2 - rr, p.h / 2 - rr) - rr - w / 2
                    if dist + 1e-4 < need and reported < 5:
                        issues.append(f"{name}: pista a {fmt(max(dist, 0))} mm del pad de {p.ref} "
                                      f"({prj.net_name(p.net) or 'sin red'}), mín {fmt(need)}")
                        reported += 1
    for i in range(len(allvias)):
        for j in range(i):
            (n1, a), (n2, b) = allvias[i], allvias[j]
            d = math.hypot(a[0] - b[0], a[1] - b[1]) - prj.via_drill
            if d + 1e-6 < drill_cl:
                issues.append(f"Vías {n1}/{n2} con taladros a {fmt(d)} mm (mín {fmt(drill_cl)}) en ({fmt(a[0])}, {fmt(a[1])})")
    n_wires = sum(len(e["wires"]) for e in ses["nets"].values())
    n_vias = sum(len(e["vias"]) for e in ses["nets"].values())
    return {"unrouted": total, "unconnected": unconnected, "issues": issues,
            "wires": n_wires, "vias": n_vias, "moved": moved}


def format_verify(rep: dict) -> str:
    lines = [f"Tramos: {rep['wires']}, vías: {rep['vias']}",
             f"Conexiones sin rutear: {rep['unrouted']}"]
    for u in rep["unconnected"][:30]:
        lines.append(f"  - {u['net']}: pin de {u['pin']} en ({u['x']}, {u['y']}) sin conectar")
    if rep["moved"]:
        lines.append("Piezas que el ruteo movió o giró: " + ", ".join(
            f"{k} → rot {fmt(v[2])}" for k, v in rep["moved"].items()))
    lines.append(f"Problemas de reglas: {len(rep['issues'])}")
    lines += [f"  - {i}" for i in rep["issues"][:30]]
    return "\n".join(lines)


# ============================================================================
# Escribir el ruteo en board.lp (sin pasar por la GUI de LibrePCB)
# ============================================================================


def _netsegment_blocks(text_root: Node, keep_fiducials=True):
    """(start, end) de cada netsegment a borrar."""
    out = []
    for ns in text_root.children("netsegment"):
        if keep_fiducials and ns.children("pad"):
            continue
        out.append((ns.start, ns.end))
    return out


def clear_routing_text(text: str, keep_fiducials=True) -> tuple[str, int]:
    root = parse(text)
    blocks = _netsegment_blocks(root, keep_fiducials)
    edits = []
    for s, e in blocks:
        # borra también la sangría y el salto de línea
        ls = text.rfind("\n", 0, s)
        edits.append((ls if ls >= 0 else s, e, ""))
    return apply_edits(text, edits), len(blocks)


def _move_device_edits(dev_node: Node, new_pos, new_rot, flip):
    edits = []
    old_pos = xy(dev_node.child("position"))
    old_rot = float(dev_node.child("rotation").arg())
    p = dev_node.child("position")
    edits.append((p.items[1].start, p.items[1].end, fmt(new_pos[0])))
    edits.append((p.items[2].start, p.items[2].end, fmt(new_pos[1])))
    r = dev_node.child("rotation")
    edits.append((r.items[1].start, r.items[1].end, fmt(new_rot % 360)))
    for st in dev_node.children("stroke_text"):
        sp = st.child("position")
        lx, ly = rot(xy(sp)[0] - old_pos[0], xy(sp)[1] - old_pos[1], -old_rot)
        if flip:
            lx = -lx
        wx, wy = to_world(lx, ly, new_pos, new_rot, flip)
        edits.append((sp.items[1].start, sp.items[1].end, fmt(wx)))
        edits.append((sp.items[2].start, sp.items[2].end, fmt(wy)))
        sr = st.child("rotation")
        a = float(sr.arg())
        rel = (old_rot - a) if flip else (a - old_rot)
        na = (new_rot - rel) if flip else (new_rot + rel)
        edits.append((sr.items[1].start, sr.items[1].end, fmt(na % 360)))
    return edits


def apply_ses_to_board(lpp: Path, board_file: Path, ses: dict) -> tuple[str, dict]:
    """Devuelve (nuevo texto de board.lp, resumen). No escribe en disco."""
    # 1) Aplicar cambios de ubicación del SES (p. ej. piezas giradas)
    prj = Project(lpp, board_file)
    text = prj.board_text
    edits, moved = [], []
    for ref, (x, y, side, r) in ses.get("placement", {}).items():
        d = prj.by_ref.get(ref)
        if not d:
            continue
        dr = (r - d["rot"]) % 360
        if (min(dr, 360 - dr) > 1e-6 or abs(x - d["pos"][0]) > 1e-4 or abs(y - d["pos"][1]) > 1e-4):
            edits += _move_device_edits(d["node"], (x, y), r, d["flip"])
            moved.append(ref)
    if edits:
        text = apply_edits(text, edits)
    # 2) Borrar el ruteo anterior
    text, removed = clear_routing_text(text)
    board_file_text = text
    # 3) Recalcular pads con la ubicación final
    tmp = Project.__new__(Project)
    tmp.__dict__.update(prj.__dict__)
    tmp.board_text = board_file_text
    tmp.board = parse(board_file_text)
    tmp._load_board()
    prj = tmp

    name_to_uuid = {v: k for k, v in prj.nets.items()}
    pins = prj.pins_by_net()
    blocks, stats = [], {"traces": 0, "vias": 0, "junctions": 0, "segments": 0, "nets_unknown": []}
    TOL = 1e-3
    for net_name, e in ses["nets"].items():
        net_uuid = name_to_uuid.get(net_name)
        if net_uuid is None:
            stats["nets_unknown"].append(net_name)
            continue
        wires = [(l, w, [p for k, p in enumerate(pts) if k == 0 or
                         math.hypot(p[0] - pts[k - 1][0], p[1] - pts[k - 1][1]) > 1e-6])
                 for l, w, pts in e["wires"]]
        wires = [w for w in wires if len(w[2]) >= 2]
        vias = [(x, y) for x, y in e["vias"]]
        # Vías inútiles (sin pistas en ambas capas): LibrePCB las marca en el DRC
        def via_layers(v):
            return {l for l, w, pts in wires
                    if any(math.hypot(p[0] - v[0], p[1] - v[1]) < 1e-3 for p in pts)}
        useless = [v for v in vias if len(via_layers(v)) < 2]
        vias = [v for v in vias if v not in useless]
        stats.setdefault("useless_vias", 0)
        stats["useless_vias"] += len(useless)
        # 3a) Uniones en T: un extremo que cae en medio de otro tramo de la misma capa
        changed = True
        while changed:
            changed = False
            for i, (l, w, pts) in enumerate(wires):
                for end in (pts[0], pts[-1]):
                    for k, (l2, w2, pts2) in enumerate(wires):
                        if k == i or l2 != l:
                            continue
                        for s in range(len(pts2) - 1):
                            a, b = pts2[s], pts2[s + 1]
                            if (_dseg(end, a, b) < TOL and math.hypot(end[0] - a[0], end[1] - a[1]) > TOL
                                    and math.hypot(end[0] - b[0], end[1] - b[1]) > TOL):
                                pts2.insert(s + 1, end)
                                changed = True
                                break
                        if changed:
                            break
                    if changed:
                        break
                if changed:
                    break
        # 3b) Anclas
        via_ids = [new_uuid() for _ in vias]
        junctions = {}  # (capa, x, y) -> uuid
        uf = _UF()
        net_pads = pins.get(net_uuid, [])

        stubs = []

        def anchor(layer, pt, endpoint):
            for k, v in enumerate(vias):
                if math.hypot(v[0] - pt[0], v[1] - pt[1]) < TOL * 10:
                    return ("via", via_ids[k]), f"(via {via_ids[k]})"
            if endpoint:
                cands = [p for p in net_pads if layer in p.layers and p.contains(pt[0], pt[1], 0.01)]
                if cands:
                    p = min(cands, key=lambda p: math.hypot(p.x - pt[0], p.y - pt[1]))
                    if math.hypot(p.x - pt[0], p.y - pt[1]) > TOL:
                        # LibrePCB lleva el extremo al centro del pad: se agrega una unión en el
                        # punto real y un tramo corto dentro del pad, para no mover la pista.
                        key = (layer, round(pt[0], 4), round(pt[1], 4))
                        if key not in junctions:
                            junctions[key] = new_uuid()
                        stubs.append((layer, ("j", junctions[key]), f"(junction {junctions[key]})",
                                      ("pad", p.device, p.uuid), f"(device {p.device}) (pad {p.uuid})"))
                        return ("j", junctions[key]), f"(junction {junctions[key]})"
                    return ("pad", p.device, p.uuid), f"(device {p.device}) (pad {p.uuid})"
            key = (layer, round(pt[0], 4), round(pt[1], 4))
            if key not in junctions:
                junctions[key] = new_uuid()
            return ("j", junctions[key]), f"(junction {junctions[key]})"

        traces = []
        stub_done = set()
        for l, w, pts in wires:
            for s in range(len(pts) - 1):
                stubs = []
                a_key, a_txt = anchor(l, pts[s], s == 0)
                b_key, b_txt = anchor(l, pts[s + 1], s + 1 == len(pts) - 1)
                for sl, j_key, j_txt, p_key, p_txt in stubs:
                    if (j_key, p_key) not in stub_done:
                        stub_done.add((j_key, p_key))
                        traces.append((sl, w, j_key, j_txt, p_key, p_txt))
                        uf.u(j_key, p_key)
                if a_key == b_key:
                    continue
                traces.append((l, w, a_key, a_txt, b_key, b_txt))
                uf.u(a_key, b_key)
        # 3c) Un netsegment por grupo conectado
        groups = {}
        for t in traces:
            groups.setdefault(uf.f(t[2]), {"traces": [], "vias": set(), "junctions": set()})["traces"].append(t)
        for k, vid in enumerate(via_ids):
            g = groups.setdefault(uf.f(("via", vid)), {"traces": [], "vias": set(), "junctions": set()})
            g["vias"].add(k)
        for key, jid in junctions.items():
            groups[uf.f(("j", jid))]["junctions"].add(key)
        for g in groups.values():
            out = [f" (netsegment {new_uuid()}", f"  (net {net_uuid})"]
            for k in sorted(g["vias"]):
                x, y = vias[k]
                out.append(f"  (via {via_ids[k]} (from top_cu) (to bot_cu)")
                out.append(f"   (position {fmt(x)} {fmt(y)}) (drill auto) (size auto) (exposure off)")
                out.append("  )")
            for key in sorted(g["junctions"]):
                out.append(f"  (junction {junctions[key]} (position {fmt(key[1])} {fmt(key[2])}))")
            for l, w, _, a_txt, _, b_txt in g["traces"]:
                out.append(f"  (trace {new_uuid()} (layer {l}) (width {fmt(w)})")
                out.append(f"   (from {a_txt})")
                out.append(f"   (to {b_txt})")
                out.append("  )")
            out.append(" )")
            blocks.append("\n".join(out))
            stats["segments"] += 1
            stats["traces"] += len(g["traces"])
            stats["vias"] += len(g["vias"])
            stats["junctions"] += len(g["junctions"])
    root = parse(board_file_text)
    end = root.end - 1
    new_text = board_file_text[:end].rstrip() + "\n" + "\n".join(blocks) + "\n)\n"
    stats["removed_segments"] = removed
    stats["moved"] = moved
    return new_text, stats


# ============================================================================
# Serigrafía: nombres sin choques
# ============================================================================


def _ov(a, b, m=0.0):
    return not (a[2] + m <= b[0] or b[2] + m <= a[0] or a[3] + m <= b[1] or b[3] + m <= a[1])


def place_silkscreen(prj: Project, remove_values=True, hide_unplaced=True,
                     heights=(1.0, 0.8), pad_clearance=0.2) -> tuple[str, dict]:
    """Reubica los nombres (R1, U3...) en lugares libres. Devuelve (texto nuevo, resumen)."""
    min_h = prj.rules.get("min_silkscreen_text_height", 0.0)
    stroke = max(prj.rules.get("min_silkscreen_width", 0.15), 0.15) + 0.007
    heights = [h for h in heights if h + 1e-9 >= min_h] or [max(min_h, 0.8)]
    obst = [p.bbox() for p in prj.pads if "top_cu" in p.layers]
    # Las vías van tapadas con máscara: la serigrafía puede pasar encima.
    for dia, verts in prj.holes:
        for vx, vy in verts[:1]:
            obst.append((vx - dia / 2, vy - dia / 2, vx + dia / 2, vy + dia / 2))
    for f in prj.fiducials:
        r = max(f["size"]) / 2 + max(f["clearance"], 0.5)
        obst.append((f["pos"][0] - r, f["pos"][1] - r, f["pos"][0] + r, f["pos"][1] + r))
    zones = []
    for z in prj.zones:
        if z["no_copper"] and z["pts"]:
            xs, ys = [p[0] for p in z["pts"]], [p[1] for p in z["pts"]]
            zones.append((min(xs), min(ys), max(xs), max(ys)))
    bodies = {d["ref"]: d["bbox"] for d in prj.devices if not d["flip"]}
    W = prj.obox or (0, 0, 1e9, 1e9)
    placed, result = [], {}
    order = sorted(prj.devices, key=lambda d: (-(d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]), d["ref"]))
    for strict, h in [(1, x) for x in heights] + [(0, x) for x in heights]:
        for d in order:
            if d["flip"] or d["uuid"] in result:
                continue
            ref = d["ref"]
            w, hh = len(ref) * 0.85 * h + 0.2, h + 0.25
            body = d["bbox"]
            cx, cy = (body[0] + body[2]) / 2, (body[1] + body[3]) / 2
            g = 0.25
            cands = [(cx, body[3] + g + hh / 2), (cx, body[1] - g - hh / 2),
                     (body[0] - g - w / 2, cy), (body[2] + g + w / 2, cy)]
            for dd in (0.6, 1.2, 1.8):
                cands += [(cx + dd, body[3] + g + hh / 2), (cx - dd, body[3] + g + hh / 2),
                          (cx + dd, body[1] - g - hh / 2), (cx - dd, body[1] - g - hh / 2)]
            inside = [(cx, cy), (cx, cy + 1.5), (cx, cy - 1.5), (cx + 1.5, cy), (cx - 1.5, cy)]
            if not strict:
                cands += inside
            for px, py in cands:
                bb = (px - w / 2, py - hh / 2, px + w / 2, py + hh / 2)
                if bb[0] < W[0] + 0.6 or bb[1] < W[1] + 0.6 or bb[2] > W[2] - 0.6 or bb[3] > W[3] - 0.6:
                    continue
                if any(_ov(bb, o, pad_clearance) for o in obst):
                    continue
                if any(_ov(bb, z) for z in zones):
                    continue
                if strict and any(_ov(bb, b, 0.05) for b in bodies.values()):
                    continue
                if not strict and _ov(bb, body) and (px, py) not in inside:
                    continue
                if any(_ov(bb, p, 0.15) for p in placed):
                    continue
                placed.append(bb)
                result[d["uuid"]] = (px, py, h)
                break

    text = prj.board_text
    edits, stats = [], {"moved": 0, "hidden": [], "values_removed": 0}
    for d in prj.devices:
        node = d["node"]
        for st in node.children("stroke_text"):
            val = st.child("value").arg() if st.child("value") else ""
            layer = st.child("layer").arg() if st.child("layer") else ""
            ls = text.rfind("\n", 0, st.start)
            if remove_values and (val == "{{VALUE}}" or layer.endswith("_values")):
                edits.append((ls, st.end, ""))
                stats["values_removed"] += 1
                continue
            if val != "{{NAME}}" or d["flip"]:
                continue
            if d["uuid"] in result:
                px, py, h = result[d["uuid"]]
                hn, pn, rn, an = st.child("height"), st.child("position"), st.child("rotation"), st.child("align")
                sw = st.child("stroke_width")
                edits.append((hn.items[1].start, hn.items[1].end, fmt(h)))
                if sw is not None:
                    edits.append((sw.items[1].start, sw.items[1].end, fmt(stroke)))
                edits.append((pn.items[1].start, pn.items[1].end, fmt(px)))
                edits.append((pn.items[2].start, pn.items[2].end, fmt(py)))
                edits.append((rn.items[1].start, rn.items[1].end, "0.0"))
                if an is not None and len(an.items) >= 3:
                    edits.append((an.items[1].start, an.items[2].end, "center center"))
                stats["moved"] += 1
            elif hide_unplaced:
                edits.append((ls, st.end, ""))
                stats["hidden"].append(d["ref"])
    return apply_edits(text, edits), stats


def fix_legend_widths(prj: Project, write=None) -> list[str]:
    """Engrosa líneas de serigrafía de los footprints locales por debajo del mínimo."""
    min_w = prj.rules.get("min_silkscreen_width", 0.0)
    if min_w <= 0:
        return []
    target = fmt(math.ceil(min_w * 100 - 1e-9) / 100)
    changed = []
    for pkg_uuid, f in prj.package_files.items():
        s = f.read_text(encoding="utf-8")
        def bump(m):
            w = float(m.group(2))
            return m.group(1) + (target if 0 < w < min_w else m.group(2)) + ")"
        s2 = re.sub(r'(\((?:polygon|circle) [0-9a-f-]+ \(layer (?:top|bot)_legend\)\s+(?:\(diameter [\d.]+\)\s+)?\(width )([\d.]+)\)', bump, s)
        if s2 != s:
            if write:
                write(f, s2)
            else:
                f.write_text(s2, encoding="utf-8")
            changed.append(prj.package_names.get(pkg_uuid, pkg_uuid))
    return changed


# ============================================================================
# Revisión previa al ruteo
# ============================================================================


def preflight(prj: Project) -> list[tuple[str, str]]:
    """Lista de (nivel, mensaje). Niveles: FALTA, AVISO, INFO."""
    out = []
    li = lock_info(prj.dir)
    if li["locked"]:
        out.append(("AVISO", "LibrePCB tiene el proyecto abierto"
                    + (" (bloqueo viejo: el proceso ya no existe)" if li.get("alive") is False else "")
                    + ". Ciérralo antes de modificar archivos."))
    if not prj.outline:
        out.append(("FALTA", "La placa no tiene contorno (set_board_outline)."))
    # Área
    if prj.obox:
        area = (prj.obox[2] - prj.obox[0]) * (prj.obox[3] - prj.obox[1])
        holes = sum(math.pi * (d / 2 + 1) ** 2 for d, _ in prj.holes)
        used = sum((d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]) for d in prj.devices)
        zone = sum((max(p[0] for p in z["pts"]) - min(p[0] for p in z["pts"])) *
                   (max(p[1] for p in z["pts"]) - min(p[1] for p in z["pts"])) for z in prj.zones if z["pts"])
        ratio = used / max(area - holes - zone, 1e-6)
        lvl = "AVISO" if ratio > 0.5 else "INFO"
        out.append((lvl, f"Ocupación de piezas: {ratio:.0%} del área útil"
                    + (" (alta: el ruteo será difícil; considera agrandar la placa)" if ratio > 0.5 else "")))
    # Alimentación vs pads finos
    clearance = max(prj.rules.get("min_copper_copper_clearance", 0.127), 0.13)
    by_net = prj.pins_by_net()
    for net_uuid, pads in by_net.items():
        c = prj.class_of(net_uuid)
        if not c or c["width"] <= 0:
            continue
        narrow = []
        for p in pads:
            others = [o for o in prj.pads if o.device == p.device and o is not p and o.net != p.net
                      and set(o.layers) & set(p.layers)]
            gap = min((max(abs(o.x - p.x) - (o.w + p.w) / 2, abs(o.y - p.y) - (o.h + p.h) / 2)
                       for o in others), default=9)
            if min(p.w, p.h) + 1e-6 < c["width"] and gap < c["width"] + 2 * clearance:
                narrow.append(p.ref)
        if narrow:
            out.append(("AVISO", f"{prj.net_name(net_uuid)} ({fmt(c['width'])} mm) no cabe en los pads de "
                        f"{', '.join(sorted(set(narrow)))}: el ruteo usará un tramo más delgado al llegar."))
    # Cobre sin red en footprints (p. ej. puentes de soldadura cerrados)
    for d in prj.devices:
        fp = d["footprint"]
        if fp and fp["copper"]:
            out.append(("AVISO", f"{d['ref']} tiene cobre propio entre pads (puente cerrado): el DRC lo marcará. "
                        "Decide si dejarlo abierto o unir las redes."))
    # Partes
    missing = [c["name"] for c in prj.components.values()
               if c["device"] and not c["part"] and not prj.is_dnp(c)]
    if missing:
        out.append(("AVISO", f"{len(missing)} piezas sin MPN/LCSC: {', '.join(sorted(missing))} "
                    "(assign_basic_parts / set_component_part, o set_dnp si no se montan)."))
    # Clases vs separación
    for c in prj.netclasses.values():
        if c["clearance"] > prj.rules.get("min_copper_copper_clearance", 0):
            out.append(("INFO", f"La clase '{c['name']}' exige {fmt(c['clearance'])} mm de separación; "
                        "el ruteo usará al menos eso para todas las redes."))
    return out


# ============================================================================
# Piezas básicas de JLCPCB (verificadas en jlcpcb.com, nov 2026)
# ============================================================================

JLC_BASIC = {
    # (tipo, tamaño, valor en unidades base) -> (MPN, fabricante, LCSC)
    ("R", "0603", 22.0): ("0603WAF220JT5E", "UNI-ROYAL", "C23345"),
    ("R", "0603", 100.0): ("0603WAF1000T5E", "UNI-ROYAL", "C22775"),
    ("R", "0603", 330.0): ("0603WAF3300T5E", "UNI-ROYAL", "C23138"),
    ("R", "0603", 1e3): ("0603WAF1001T5E", "UNI-ROYAL", "C21190"),
    ("R", "0603", 3.3e3): ("0603WAF3301T5E", "UNI-ROYAL", "C22978"),
    ("R", "0603", 4.7e3): ("0603WAF4701T5E", "UNI-ROYAL", "C23162"),
    ("R", "0603", 5.1e3): ("0603WAF5101T5E", "UNI-ROYAL", "C23186"),
    ("R", "0603", 10e3): ("0603WAF1002T5E", "UNI-ROYAL", "C25804"),
    ("R", "0603", 22e3): ("0603WAF2202T5E", "UNI-ROYAL", "C31850"),
    ("R", "0603", 30e3): ("0603WAF3002T5E", "UNI-ROYAL", "C22984"),
    ("R", "0603", 33e3): ("0603WAF3302T5E", "UNI-ROYAL", "C4216"),
    ("R", "0603", 100e3): ("0603WAF1003T5E", "UNI-ROYAL", "C25803"),
    ("C", "0603", 100e-9): ("CC0603KRX7R9BB104", "YAGEO", "C14663"),
    ("C", "0603", 1e-6): ("CL10A105KB8NNNC", "Samsung Electro-Mechanics", "C15849"),
    ("C", "0603", 4.7e-6): ("CL10A475KO8NNNC", "Samsung Electro-Mechanics", "C19666"),
    ("C", "0603", 10e-6): ("CL10A106KP8NNNC", "Samsung Electro-Mechanics", "C19702"),
    ("C", "0805", 10e-6): ("CL21A106KAYNNNE", "Samsung Electro-Mechanics", "C15850"),
    ("C", "0805", 22e-6): ("CL21A226MAQNNNE", "Samsung Electro-Mechanics", "C45783"),
}

_UNIT = {"ohm": 1, "milliohm": 1e-3, "kiloohm": 1e3, "megaohm": 1e6,
         "farad": 1, "millifarad": 1e-3, "microfarad": 1e-6, "nanofarad": 1e-9, "picofarad": 1e-12}


def load_basic_table(server_dir: Path) -> dict:
    table = dict(JLC_BASIC)
    extra = server_dir / "tools" / "jlc_basic.json"
    if extra.exists():
        for row in json.loads(extra.read_text(encoding="utf-8")):
            table[(row["type"], row["size"], float(row["value"]))] = (row["mpn"], row["manufacturer"], row["lcsc"])
    return table


def suggest_basic_part(prj: Project, comp: dict, table: dict):
    """(tipo, tamaño, valor) -> parte, o None. Solo resistencias y condensadores."""
    attrs = comp["attrs"]
    kind, a = None, None
    if "RESISTANCE" in attrs:
        kind, a = "R", attrs["RESISTANCE"]
    elif "CAPACITANCE" in attrs:
        kind, a = "C", attrs["CAPACITANCE"]
    if not kind:
        return None, "sin atributo RESISTANCE/CAPACITANCE"
    try:
        val = float(a["value"]) * _UNIT.get(a["unit"], 1)
    except ValueError:
        return None, f"valor '{a['value']}' no numérico"
    d = next((x for x in prj.devices if x["uuid"] == comp["uuid"]), None)
    pkg = prj.package_names.get(d["footprint"]["package"], "") if d and d["footprint"] else ""
    m = re.search(r"\b(0402|0603|0805|1206)\b", pkg)
    if not m:
        return None, f"tamaño no reconocido ({pkg})"
    for (k, size, v), part in table.items():
        if k == kind and size == m.group(1) and abs(v - val) <= abs(val) * 1e-6:
            return part, ""
    return None, f"{kind} {m.group(1)} {val:g} no está en la tabla de piezas básicas"


# ============================================================================
# Java y Freerouting
# ============================================================================


def _class_major(jar: Path) -> int:
    try:
        with zipfile.ZipFile(jar) as z:
            for name in ("app/freerouting/Freerouting.class", "app/freerouting/gui/MainApplication.class"):
                if name in z.namelist():
                    b = z.read(name)[:8]
                    return int.from_bytes(b[6:8], "big")
    except (zipfile.BadZipFile, OSError):
        pass
    return 0


def _java_version(java: str) -> int:
    try:
        r = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = re.search(r'version "(\d+)(?:\.(\d+))?', r.stderr + r.stdout)
    if not m:
        return 0
    v = int(m.group(1))
    return int(m.group(2) or 0) if v == 1 else v


def _bundled_java(server_dir: Path) -> str | None:
    import platform
    tools = server_dir / "tools"
    for java in sorted(tools.glob("jre*/**/bin/java")):
        if java.is_file():
            return str(java)
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    osn = "mac" if platform.system() == "Darwin" else "linux"
    for tgz in sorted(tools.glob(f"jre*-{osn}-{arch}.tar.gz")):
        import tarfile
        dest = tools / tgz.name.replace(".tar.gz", "")
        with tarfile.open(tgz) as t:
            t.extractall(dest)
        for java in dest.glob("**/bin/java"):
            java.chmod(0o755)
            for f in java.parent.iterdir():
                f.chmod(0o755)
            for lib in dest.glob("**/lib/**/*"):
                if lib.is_file() and (lib.suffix in (".dylib", ".so") or lib.name == "jspawnhelper"):
                    lib.chmod(0o755)
            return str(java)
    return None


def find_router(server_dir: Path):
    """Devuelve (java, jar, mensaje). Elige una combinación compatible."""
    import os
    jars = []
    if os.environ.get("FREEROUTING_JAR"):
        jars.append(Path(os.environ["FREEROUTING_JAR"]))
    jars += sorted((server_dir / "tools").glob("freerouting-2.0*.jar"))
    jars += sorted((server_dir / "tools").glob("freerouting*.jar"), reverse=True)
    jars = [j for j in dict.fromkeys(jars) if j.exists()]
    if not jars:
        return None, None, "No encontré Freerouting en tools/ (./start.sh --install-freerouting)."
    javas = []
    for c in (os.environ.get("JAVA"), _bundled_java(server_dir), shutil.which("java"),
              "/opt/homebrew/opt/openjdk/bin/java", "/usr/local/opt/openjdk/bin/java"):
        if c and Path(c).exists() and c not in javas:
            javas.append(c)
    need_by_major = {61: 17, 65: 21, 69: 25}
    tried = []
    for jar in jars:
        need = need_by_major.get(_class_major(jar), 17)
        for java in javas:
            v = _java_version(java)
            tried.append(f"{Path(java).parent.parent.name} (Java {v})")
            if v >= need:
                return java, jar, f"Java {v} + {jar.name}"
    return None, None, ("No hay un Java compatible con Freerouting. Probé: " + ", ".join(dict.fromkeys(tried))
                        + ". Instala con ./start.sh --install-java.")


def run_freerouting(java: str, jar: Path, dsn: Path, ses: Path, timeout_s: int = 1200,
                    retries: int = 3, log=None) -> tuple[bool, str]:
    """Corre Freerouting. Dos fallas conocidas de la versión 2.0.x se manejan aquí:
    a veces no escribe el .ses (se reintenta) y a veces sigue colgado después de
    escribirlo (se cierra apenas el archivo queda estable)."""
    last = ""
    for attempt in range(1, retries + 1):
        if ses.exists():
            ses.unlink()
        dsn, ses = Path(dsn).resolve(), Path(ses).resolve()
        logf = dsn.with_suffix(".log")
        cmd = [java, "-Djava.awt.headless=true", "-jar", str(Path(jar).resolve()), "--gui.enabled=false",
               "-de", str(dsn), "-do", str(ses)]
        with open(logf, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, cwd=str(dsn.parent))
            t0, size, stable = time.time(), -1, 0
            while True:
                time.sleep(2)
                done = proc.poll() is not None
                cur = ses.stat().st_size if ses.exists() else 0
                if cur > 0 and cur == size:
                    stable += 1
                else:
                    stable = 0
                size = cur
                if done or stable >= 2 or time.time() - t0 > timeout_s:
                    break
                if time.time() - t0 > 20 and not cur:
                    try:
                        head = logf.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        head = ""
                    if "Couldn't load the input file" in head or "Couldn't read" in head:
                        break
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        try:
            last = "\n".join(logf.read_text(encoding="utf-8", errors="replace").splitlines()[-10:])
        except OSError:
            last = ""
        if ses.exists() and ses.stat().st_size > 0:
            txt = ses.read_text(encoding="utf-8")
            if "(network_out" in txt:
                ses.write_text(dedupe_ses_padstacks(txt), encoding="utf-8")
                return True, f"intento {attempt}"
        if log:
            log(f"Freerouting no entregó resultado (intento {attempt}); reintentando")
    return False, last


# ============================================================================
# Ajustes finos automáticos sobre el SES (vías juntas / pistas al borde)
# ============================================================================


def nudge_fix(prj: Project, ses: dict, max_move=0.12) -> list[str]:
    """Corrige violaciones pequeñas moviendo vías o vértices hasta max_move mm."""
    done = []
    drill_cl = prj.rules.get("min_drill_drill_clearance", 0.0)
    edge_cl = prj.rules.get("min_copper_board_clearance", 0.0)
    need = drill_cl + prj.via_drill + 0.005

    def move_point(net, old, new):
        e = ses["nets"][net]
        e["vias"] = [new if math.hypot(v[0] - old[0], v[1] - old[1]) < 1e-6 else v for v in e["vias"]]
        for k, (l, w, pts) in enumerate(e["wires"]):
            e["wires"][k] = (l, w, [new if math.hypot(p[0] - old[0], p[1] - old[1]) < 1e-6 else p for p in pts])

    allv = [(n, v) for n, e in ses["nets"].items() for v in e["vias"]]
    for i in range(len(allv)):
        for j in range(i):
            (n1, a), (n2, b) = allv[i], allv[j]
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if 0 < d < need and (need - d) / 2 <= max_move:
                ux, uy = (a[0] - b[0]) / d, (a[1] - b[1]) / d
                s = (need - d) / 2
                na, nb = (a[0] + ux * s, a[1] + uy * s), (b[0] - ux * s, b[1] - uy * s)
                move_point(n1, a, na)
                move_point(n2, b, nb)
                allv[i], allv[j] = (n1, na), (n2, nb)
                done.append(f"vías {n1}/{n2} separadas {fmt(2 * s)} mm")
    if prj.obox:
        x0, y0, x1, y1 = prj.obox
        for net, e in ses["nets"].items():
            for k, (l, w, pts) in enumerate(e["wires"]):
                for p in list(pts):
                    lim = edge_cl + w / 2 + 0.005
                    nx = min(max(p[0], x0 + lim), x1 - lim)
                    ny = min(max(p[1], y0 + lim), y1 - lim)
                    if (nx, ny) != p and math.hypot(nx - p[0], ny - p[1]) <= max_move:
                        move_point(net, p, (nx, ny))
                        done.append(f"{net}: punto de pista alejado del borde {fmt(math.hypot(nx - p[0], ny - p[1]))} mm")
    return done


def rotation_conflicts(prj: Project, rotations: dict) -> list[str]:
    """Problemas de ubicación que causaría girar piezas (pads o cuerpos que chocan)."""
    out = []
    cl = prj.rules.get("min_copper_copper_clearance", 0.127) + 0.05
    for ref, angle in rotations.items():
        d = prj.by_ref.get(ref)
        if not d or not d["footprint"]:
            continue
        lb = d["footprint"]["bbox"]
        cs = [to_world(x, y, d["pos"], angle, d["flip"]) for x in (lb[0], lb[2]) for y in (lb[1], lb[3])]
        bb = (min(c[0] for c in cs), min(c[1] for c in cs), max(c[0] for c in cs), max(c[1] for c in cs))
        for o in prj.devices:
            if o is d or o["flip"] != d["flip"]:
                continue
            ob = o["bbox"]
            if not (bb[2] <= ob[0] or ob[2] <= bb[0] or bb[3] <= ob[1] or ob[3] <= bb[1]):
                # solo cuenta si antes no se tocaban
                ab = d["bbox"]
                if (ab[2] <= ob[0] or ob[2] <= ab[0] or ab[3] <= ob[1] or ob[3] <= ab[1]):
                    out.append(f"{ref} girada choca con {o['ref']}")
        for p in d["pads"]:
            px, py = to_world(*p.local["pos"], d["pos"], angle, d["flip"])
            pa = (angle - p.local["rot"]) if d["flip"] else (angle + p.local["rot"])
            hw, hh = p.w / 2, p.h / 2
            corners = [(px + rx, py + ry) for rx, ry in (rot(sx * hw, sy * hh, pa) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)))]
            pb = (min(c[0] for c in corners), min(c[1] for c in corners), max(c[0] for c in corners), max(c[1] for c in corners))
            for q2 in prj.pads:
                if q2.device == d["uuid"] or not set(q2.layers) & set(p.layers):
                    continue
                qb = q2.bbox()
                if not (pb[2] + cl <= qb[0] or qb[2] + cl <= pb[0] or pb[3] + cl <= qb[1] or qb[3] + cl <= pb[1]):
                    out.append(f"pad de {ref} girada queda junto a {q2.ref}")
    return sorted(set(out))


# ============================================================================
# Ruteo completo en etapas (lo usa route_worker.py)
# ============================================================================


def route_project(lpp: Path, board_file: Path, server_dir: Path, workdir: Path,
                  opts: dict | None = None, log=print) -> dict:
    o = {"clearance": 0.15, "signal_width": 0.2, "stage2_power_width": 0.2,
         "stage2_signal_width": None, "stage2_clearance": None,
         "variants": [(0.0, None), (0.0, 0.15), (-0.01, None), (0.01, 0.15)], "allow_rotate": [],
         "timeout_minutes": 20}
    o.update(opts or {})
    workdir.mkdir(parents=True, exist_ok=True)
    java, jar, msg = find_router(server_dir)
    if not java:
        return {"ok": False, "error": msg}
    log(f"Ruteador: {msg}")
    prj = Project(lpp, board_file)
    via_name = dsn_via_name(prj)
    drc_cl = prj.rules.get("min_copper_copper_clearance", 0.127)
    drc_w = prj.rules.get("min_copper_width", 0.127)
    class_cl = max([c["clearance"] for c in prj.netclasses.values()] + [0.0])
    floor_cl = max(drc_cl, class_cl, 0.13)
    s2_cl = o["stage2_clearance"] or floor_cl
    s2_sig = o["stage2_signal_width"] or max(drc_w, 0.13)
    timeout_s = int(o["timeout_minutes"] * 60)

    best = None
    attempts = []

    def try_variant(tag, clearance, rotations, signal_width=None):
        nonlocal best
        signal_width = signal_width or o["signal_width"]
        if rotations:
            conflicts = rotation_conflicts(prj, rotations)
            if conflicts:
                log(f"[{tag}] descartado: {'; '.join(conflicts[:3])}")
                return None
        base = {"clearance": clearance, "signal_width": signal_width}
        d1 = workdir / f"{tag}-1.dsn"
        s1 = workdir / f"{tag}-1.ses"
        d1.write_text(export_dsn(prj, base, rotations=rotations), encoding="utf-8")
        log(f"[{tag}] etapa 1: separación {fmt(clearance)} mm, señales {fmt(signal_width)} mm"
            + (f", giros {rotations}" if rotations else ""))
        ok, info = run_freerouting(java, jar, d1, s1, timeout_s, log=log)
        if not ok:
            log(f"[{tag}] Freerouting falló: {info}")
            return None
        ses = read_ses(s1)
        rep = verify_routing(prj, ses)
        log(f"[{tag}] etapa 1: {rep['unrouted']} sin rutear")
        final_ses_path = s1
        if rep["unrouted"]:
            widths = {c["name"]: max(o["stage2_power_width"], c["min_width"]) for c in prj.netclasses.values()}
            s2opts = {"clearance": s2_cl, "signal_width": s2_sig, "class_width": widths}
            d2 = workdir / f"{tag}-2.dsn"
            s2 = workdir / f"{tag}-2.ses"
            d2.write_text(export_dsn(prj, s2opts, wiring=ses_to_wiring(ses, via_name), rotations=rotations),
                          encoding="utf-8")
            log(f"[{tag}] etapa 2: completar con pistas finas (sep. {fmt(s2_cl)} mm)")
            ok, info = run_freerouting(java, jar, d2, s2, timeout_s, log=log)
            if ok:
                ses2 = read_ses(s2)
                rep2 = verify_routing(prj, ses2)
                log(f"[{tag}] etapa 2: {rep2['unrouted']} sin rutear")
                if rep2["unrouted"] <= rep["unrouted"]:
                    ses, rep, final_ses_path = ses2, rep2, s2
        for radius in (3.0, 6.0):
            if not rep["unrouted"]:
                break
            pts = [(u["x"], u["y"]) for u in rep["unconnected"]]
            widths = {c["name"]: max(o["stage2_power_width"], c["min_width"]) for c in prj.netclasses.values()}
            d3 = workdir / f"{tag}-3r{int(radius)}.dsn"
            s3 = workdir / f"{tag}-3r{int(radius)}.ses"
            d3.write_text(export_dsn(prj, {"clearance": s2_cl, "signal_width": s2_sig, "class_width": widths},
                                     wiring=ses_to_wiring(ses, via_name, free_near=pts, radius=radius),
                                     rotations=rotations), encoding="utf-8")
            log(f"[{tag}] etapa 3: rehacer el ruteo a {fmt(radius)} mm de los pines que faltan")
            ok, info = run_freerouting(java, jar, d3, s3, timeout_s, log=log)
            if ok:
                ses3 = read_ses(s3)
                rep3 = verify_routing(prj, ses3)
                log(f"[{tag}] etapa 3: {rep3['unrouted']} sin rutear")
                if (rep3["unrouted"], len(rep3["issues"])) < (rep["unrouted"], len(rep["issues"])):
                    ses, rep, final_ses_path = ses3, rep3, s3
        fixes = nudge_fix(prj, ses) if rep["issues"] else []
        if fixes:
            rep = verify_routing(prj, ses)
            log(f"[{tag}] ajustes finos: {len(fixes)}")
        score = (rep["unrouted"], len(rep["issues"]))
        attempts.append({"tag": tag, "clearance": clearance, "signal_width": signal_width, "rotations": rotations,
                         "unrouted": rep["unrouted"], "issues": len(rep["issues"])})
        if best is None or score < best["score"]:
            best = {"score": score, "ses": ses, "rep": rep, "tag": tag, "fixes": fixes,
                    "rotations": rotations, "clearance": clearance, "signal_width": signal_width,
                    "path": str(final_ses_path)}
        return rep

    for k, (dv, sw) in enumerate(o["variants"]):
        c = round(o["clearance"] + dv, 3)
        sw = max(sw or o["signal_width"], drc_w)
        if c < floor_cl - 1e-9:
            continue
        rep = try_variant(f"v{k}", c, {}, sw)
        if best and best["score"] == (0, 0):
            break
    if best and best["score"][0] > 0 and o["allow_rotate"]:
        bad_nets = {u["net"] for u in best["rep"]["unconnected"]}
        name_to_uuid = {v: k for k, v in prj.nets.items()}
        refs = {p.ref for n in bad_nets for p in prj.pins_by_net().get(name_to_uuid.get(n), [])}
        allow = refs if o["allow_rotate"] == ["auto"] else refs & set(o["allow_rotate"])
        # primero las piezas chicas (girar una resistencia no cambia nada eléctrico)
        for ref in sorted(allow, key=lambda r: (len(prj.by_ref[r]["pads"]) if r in prj.by_ref else 99, r)):
            d = prj.by_ref.get(ref)
            if not d:
                continue
            for extra in (180, 90, 270):
                rep = try_variant(f"rot-{ref}-{extra}", best["clearance"], {ref: (d["rot"] + extra) % 360},
                                  best["signal_width"])
                if best["score"] == (0, 0):
                    break
            if best["score"] == (0, 0):
                break
    if best is None:
        return {"ok": False, "error": "Freerouting no produjo ningún resultado.", "attempts": attempts}
    out_ses = workdir / "best.ses"
    out_ses.write_text(json.dumps({"nets": best["ses"]["nets"], "placement": best["ses"]["placement"]}),
                       encoding="utf-8")
    return {"ok": True, "best": best["tag"], "signal_width": best["signal_width"], "unrouted": best["rep"]["unrouted"],
            "issues": best["rep"]["issues"], "unconnected": best["rep"]["unconnected"],
            "fixes": best["fixes"], "rotations": best["rotations"], "clearance": best["clearance"],
            "wires": best["rep"]["wires"], "vias": best["rep"]["vias"], "attempts": attempts,
            "best_json": str(out_ses), "best_ses": best["path"]}


# ============================================================================
# Copias de seguridad (mismo formato que librepcb_mcp.py, para undo_last_change)
# ============================================================================


def stamp() -> str:
    t = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f"-{int(t * 1e6) % 1_000_000:06d}"


def backup_and_write(lpp: Path, path: Path, new_text: str) -> str:
    rel = path.relative_to(lpp.parent)
    bdir = lpp.parent / "output" / "mcp-backups" / rel.parent
    bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / f"{path.name}.bak-{stamp()}"
    shutil.copy2(path, backup)
    path.write_text(new_text, encoding="utf-8")
    return str(backup.relative_to(lpp.parent))

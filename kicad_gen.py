#!/usr/bin/env python3
"""kicad_gen.py - mini framework para GENERAR esquematicos KiCad 10 por codigo.

STATUS:
  Used to generate real multi-sheet KiCad 10 schematics (up to a root + 5 child
  sheets and ~150 parts; hierarchical sheets reused by multi-instance, 15
  instantiated sheets with local rails), all passing
  `kicad-cli sch erc --severity-all` with 0 errors / 0 warnings.
  - `power_local()` + ordering fix in `power_variant()`: a new name that starts
    with the base name (+3V3 -> +3V3_MOD) used to produce +3V3_MOD_MOD and KiCad
    would not load the lib (85 phantom hier_label_mismatch in the ERC).
  - ejemplo/generar_ejemplo.py (multi-instance + power_local + stable UUIDs +
    datasheet_local): ERC 0/0.
  USO    : ver README.md y ejemplo/generar_ejemplo.py. Minimo:
      import kicad_gen as K
      from kicad_gen import Part, wire, gnd, rail, pwr_flag, load, use
      K.configurar("mi_proyecto")
      R_ = load("Device", "R"); P5V = load("power", "+5V")
      raiz = K.Sheet("RAIZ", "mi_proyecto.kicad_sch", "A4", "Titulo"); use(raiz)
      r1 = Part("R1", "10k", R_, 60, 50)         # x, y en u = 1.27 mm
      rail("+5V", *r1.pin(1), P5V); gnd(*r1.pin(2))
      K.chequear_geometria(raiz); K.write_sheet(raiz, ".")

      Hoja reutilizable N veces (multi-instancia):
      hija = K.Sheet("RELE", "rele_10a_driver_v1.kicad_sch", "A4", "...", page=5, madre=raiz)
      K.instancias(hija, raiz, ("3", "4"))          # 2 instancias: refs K301.., K401..
      use(hija); Part("K1", ...)                    # dentro de la hoja los refs son cortos (K1, R1)
      use(raiz); sheet_symbol(hija, x, y, w, h, pins, inst=0, nombre="RELE_1")
                 sheet_symbol(hija, x, y2, w, h, pins, inst=1, nombre="RELE_2")

      Riel LOCAL por instancia (no se funde entre instancias de la misma hoja):
      P3V3M_ = K.power_local("power", "+3V3", "+3V3_MOD")
      rail("+3V3_MOD", *algo.pin(1), P3V3M_)
  DEPENDE: symlib.py (misma carpeta). KiCad 10 para verificar (kicad-cli).
  GOTCHAS: - Coordenadas SIEMPRE en u = 1.27 mm; los pines de las libs estandar caen
             en multiplos de 2.54 -> todo en grilla. wire() aborta si sale diagonal
             (= un pin no esta donde se asumio: usar Part.pin(n), no calcular a mano).
           - cada simbolo necesita sus bloques (pin "n" (uuid)) e (instances ...)
             con el path de la hoja (o de CADA instancia), si no KiCad no conecta.
           - (mirror y) espeja en X (izq<->der); (mirror x) espeja en Y.
           - el angulo de un campo de texto se normaliza a 0/90 y se le SUMA la
             rotacion del simbolo (Part.to_kicad ya lo hace, y da vuelta el
             justify cuando rot 90/180 o mirror y lo espejan).
           - power_out + PWR_FLAG en la misma red = error ERC. El flag va solo
             en redes alimentadas por pines pasivos (conector, diodo, resistencia).
           - sheet pin: borde derecho = angulo 0 + justify right; borde
             izquierdo = angulo 180 + justify left. Shape identica a la del
             hierarchical_label de la hoja hija.
           - Un extremo de cable que cae en el INTERIOR de otro cable necesita
             junction() o KiCad no lo conecta: chequear_geometria() lo detecta.
           - Referencias duplicadas entre hojas (o entre instancias): KiCad las
             fusiona en el netlist SIN avisar -> chequear_referencias() antes de escribir.
           - Simbolos derivados con `extends` (BC547) dan 'lib_symbol_mismatch':
             cargar el base (Q_NPN_CBE) y poner el part number en Value.
           - Multi-instancia: la propiedad Reference escrita en el archivo es la de
             la PRIMERA instancia; la real la da cada (instances ... (path ...)
             (reference ...)). El `page` de cada instancia es page + inst.
             `chequear_referencias` ya compara los refs expandidos por instancia.
           - `power_variant()`: el replace de "<base>_..." (sub-unidades) tiene que
             ir ANTES que el replace del nombre exacto "<base>"; si no, un nombre
             nuevo que empieza con el base (+3V3 -> +3V3_MOD) da +3V3_MOD_MOD.
           - `power_local()` hace que la red quede acotada al PATH de cada instancia
             de la hoja (una hoja instanciada 2 veces -> 2 redes distintas, p.ej.
             /PT100_1/+3V3_MOD y /PT100_2/+3V3_MOD): usarlo para rieles internos que
             NO deben fundirse entre instancias (nunca para GND ni para una red que
             SI debe ser compartida entre instancias, ahi va power_variant o el
             simbolo original).
           - UUIDs deterministas (uuid5 por hoja:ref / hoja:ref:pin / archivo /
             instancia): dos corridas del mismo generador dan archivos byte a byte
             iguales; regenerar no rompe el vinculo con el .kicad_pcb.
           - `datasheet_local()`: solo mira el filesystem al momento de generar; si
             el PDF se agrega despues hay que volver a correr el generador.
"""
import datetime
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from symlib import get_symbol, get_pins  # noqa: E402

U = 1.27
NL = chr(10)

# ---------------------------------------------------------------- config
PROJECT = "proyecto"        # nombre del proyecto KiCad (campo instances/project)
CUSTOM_LIB = "propios"      # nombre de la lib de simbolos propios (.kicad_sym)
GENERATOR = "kicad_gen"     # campo (generator ...) de los archivos


def configurar(project, custom_lib=None, generator=None):
    """Llamar UNA vez antes de crear hojas."""
    global PROJECT, CUSTOM_LIB, GENERATOR
    PROJECT = project
    CUSTOM_LIB = custom_lib or project
    GENERATOR = generator or GENERATOR


NS = uuid.UUID("6f1c3a52-7b1e-4a1d-9c0e-7e2a5b1d0c11")   # namespace fijo de kicad_gen
_nuid = [0]


def uid(clave=None):
    """UUID DETERMINISTA: uuid5 sobre una clave estable. Con `clave` (hoja:ref, hoja:ref:pin,
    archivo de hoja, hoja:archivo:centena) el mismo elemento recibe siempre el mismo UUID aunque
    se agreguen o quiten otros; sin clave, secuencia por proyecto (cables, uniones, textos: no
    importan para el vinculo esquematico-PCB)."""
    if clave is None:
        _nuid[0] += 1
        clave = f"{PROJECT}:seq:{_nuid[0]}"
    return str(uuid.uuid5(NS, clave))


def q(v):
    v = round(v, 3)
    return int(v) if v == int(v) else v


def mm(u):
    """u (1.27 mm) -> mm."""
    return q(u * U)


# ------------------------------------------------------------------ libs
LIBS = {}      # lib_id -> bloque s-expression
PINMAPS = {}   # lib_id -> {num: (x, y, ang, name, etype)}  (mm, y hacia arriba)


def load(lib, name):
    lid = f"{lib}:{name}"
    if lid not in LIBS:
        block, lid = get_symbol(lib, name)
        LIBS[lid] = block
        PINMAPS[lid] = get_pins(block)
    return lid


def power_variant(base_lib, base_name, new_name):
    """Simbolo de power con otro nombre de red (p.ej. +12V -> VSYS)."""
    block, _ = get_symbol(base_lib, base_name)
    # primero las sub-unidades ("+3V3_0_1"), despues el nombre exacto: si new_name empieza con base_name
    # (+3V3 -> +3V3_MOD) el orden inverso producia "+3V3_MOD_MOD" y KiCad no cargaba la lib (rev D, 20-sep-2026)
    block = block.replace(f'"{base_name}_', f'"{new_name}_')
    block = block.replace(f'"{base_lib}:{base_name}"', f'"{new_name}"', 1)
    block = block.replace(f'"{base_name}"', f'"{new_name}"')
    lid = f"{CUSTOM_LIB}:{new_name}"
    block = block.replace(f'(symbol "{new_name}"', f'(symbol "{lid}"', 1)
    LIBS[lid] = block
    PINMAPS[lid] = get_pins(block)
    return lid


def power_local(base_lib, base_name, new_name):
    """Simbolo de power LOCAL (KiCad 9/10: `(power local)`): la red queda limitada al PATH de
    la hoja, y en una hoja multi-instancia cada instancia tiene la suya
    (/PT100_1/+3V3_MOD, /PT100_2/+3V3_MOD) en vez de fundirse en una sola red global."""
    lid = power_variant(base_lib, base_name, new_name)
    assert "(power global)" in LIBS[lid], lid
    LIBS[lid] = LIBS[lid].replace("(power global)", "(power local)", 1)
    return lid


def modulo_symbol(name, izq, der, w=25.4, ref="U", val="", desc="", datasheet=""):
    """Simbolo rectangular con pines a izquierda y derecha (modulos, breakouts, ICs sin simbolo).

    izq/der: listas de (numero, nombre, tipo_electrico) o None (fila vacia).
    Los pines quedan a +-(w/2 + 5.08) del centro, filas de 2.54 mm.
    """
    n = max(len(izq), len(der))
    h = (n + 1) * 2.54
    y0, y1 = h / 2, -h / 2
    x0, x1 = -w / 2, w / 2
    pins, pmap = [], {}

    def add(items, x, ang):
        for i, it in enumerate(items):
            if it is None:
                continue
            num, pname, et = it
            py = q(y0 - 2.54 * (i + 1))
            pins.append(f'      (pin {et} line (at {q(x)} {py} {ang}) (length 5.08)\n'
                        f'        (name "{pname}" (effects (font (size 1.27 1.27))))\n'
                        f'        (number "{num}" (effects (font (size 1.27 1.27)))))')
            pmap[str(num)] = (q(x), py, float(ang), pname, et)
    add(izq, x0 - 5.08, 0)
    add(der, x1 + 5.08, 180)
    lid = f"{CUSTOM_LIB}:{name}"
    block = f'''    (symbol "{lid}"
      (pin_names (offset 1.016))
      (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "{ref}" (at 0 {q(y0 + 2.54)} 0)
        (effects (font (size 1.27 1.27))))
      (property "Value" "{val or name}" (at 0 {q(y1 - 2.54)} 0)
        (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Datasheet" "{datasheet}" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (property "Description" "{desc}" (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "{name}_0_1"
        (rectangle (start {q(x0)} {q(y0)}) (end {q(x1)} {q(y1)})
          (stroke (width 0.254) (type default))
          (fill (type background))))
      (symbol "{name}_1_1"
{NL.join(pins)}
      )
    )'''
    LIBS[lid] = block
    PINMAPS[lid] = pmap
    return lid


def custom_lib_text():
    """Texto del .kicad_sym con los simbolos propios (lib CUSTOM_LIB)."""
    out = [f'(kicad_symbol_lib (version 20231120) (generator "{GENERATOR}")']
    for lid, block in sorted(LIBS.items()):
        if lid.startswith(CUSTOM_LIB + ":"):
            name = lid.split(":", 1)[1]
            out.append(block.replace(f'(symbol "{lid}"', f'(symbol "{name}"', 1))
    out.append(")")
    return NL.join(out) + NL


def write_custom_lib(outdir, descr="Simbolos propios"):
    """Escribe CUSTOM_LIB.kicad_sym + sym-lib-table (solo si hay simbolos propios)."""
    if not any(k.startswith(CUSTOM_LIB + ":") for k in LIBS):
        return None
    path = os.path.join(outdir, CUSTOM_LIB + ".kicad_sym")
    with open(path, "w", encoding="utf-8") as f:
        f.write(custom_lib_text())
    with open(os.path.join(outdir, "sym-lib-table"), "w", encoding="utf-8") as f:
        f.write(f'(sym_lib_table\n  (version 7)\n  (lib (name "{CUSTOM_LIB}")(type "KiCad")'
                f'(uri "${{KIPRJMOD}}/{CUSTOM_LIB}.kicad_sym")(options "")(descr "{descr}"))\n)\n')
    return path


def write_project(outdir, name=None):
    """Escribe un .kicad_pro minimo si no existe (KiCad lo completa al abrir)."""
    name = name or PROJECT
    pro = os.path.join(outdir, name + ".kicad_pro")
    if not os.path.exists(pro):
        with open(pro, "w", encoding="utf-8") as f:
            f.write('{\n  "board": {"design_settings": {}},\n  "libraries": {"pinned_footprint_libs": [], '
                    f'"pinned_symbol_libs": []}},\n  "meta": {{"filename": "{name}.kicad_pro", "version": 1}},\n'
                    '  "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},\n  "sheets": [],\n'
                    '  "text_variables": {}\n}\n')
    return pro


def datasheet_local(mpn, url, carpeta="hoja_de_datos", outdir=None):
    """Campo Datasheet: `${KIPRJMOD}/<carpeta>/<mpn>.pdf` (tecla D en KiCad lo abre) si ese
    archivo YA existe en `<outdir o carpeta del script que llama>/<carpeta>/`; si no, la `url`
    tal cual. No hay tabla de piezas: se llama con el MPN ya resuelto por el proyecto."""
    base = outdir or os.path.dirname(os.path.abspath(sys.argv[0]))
    if mpn and os.path.exists(os.path.join(base, carpeta, f"{mpn}.pdf")):
        return "${KIPRJMOD}/" + carpeta + "/" + mpn + ".pdf"
    return url


# ---------------------------------------------------------------- hojas
class Sheet:
    """Una hoja. Sin `madre` es la raiz; con `madre` es hija (jerarquica).

    page: numero de pagina (1 = raiz). El archivo de la raiz debe llamarse
    PROJECT.kicad_sch para que KiCad lo abra desde el .kicad_pro.
    """

    def __init__(self, name, filename, paper, title, page=1, comments=(), madre=None):
        self.name, self.filename, self.paper = name, filename, paper
        self.title, self.page, self.comments = title, page, comments
        self.file_uuid = uid(f"{PROJECT}:file:{filename}")
        self.instancias = []                # [(child_uuid, path, centena)] si es multi-instancia
        if madre is None:
            self.child_uuid = None
            self.path = "/" + self.file_uuid
        else:
            self.child_uuid = uid(f"{PROJECT}:sheet:{filename}")   # uuid del bloque (sheet ...) en la madre
            self.path = madre.path + "/" + self.child_uuid
        self.elems = []
        self.libs = set()
        self.wires = []
        self.junctions = []


CUR = [None]


def instancias(sheet, madre, centenas):
    """Declara que `sheet` se instancia len(centenas) veces desde `madre`.

    Cada instancia tiene su uuid de bloque (sheet ...) en la madre y su path; los refs
    de los Parts de la hoja pasan a <letras><centena><nn> (K1 -> K301 / K401).
    """
    sheet.instancias = []
    for c in centenas:
        cu = uid(f"{PROJECT}:sheet:{sheet.filename}:{c}")
        sheet.instancias.append((cu, madre.path + "/" + cu, str(c)))
    sheet.child_uuid = sheet.instancias[0][0]
    sheet.path = sheet.instancias[0][1]
    return sheet


def instancias_multi(sheet, pares):
    """Como instancias(), pero cada instancia cuelga de una madre distinta: pares = [(madre, centena), ...].
    Sirve para una hoja de biblioteca usada dentro de varias hojas hijas (p.ej. un buck reusado
    en dos bloques distintos del mismo proyecto)."""
    sheet.instancias = []
    for madre, c in pares:
        cu = uid(f"{PROJECT}:sheet:{sheet.filename}:{c}")
        sheet.instancias.append((cu, madre.path + "/" + cu, str(c)))
    sheet.child_uuid = sheet.instancias[0][0]
    sheet.path = sheet.instancias[0][1]
    return sheet


def ref_inst(ref, centena):
    m = re.match(r"([A-Za-z#]+)(\d+)$", ref)
    if not m:
        return ref
    return f"{m.group(1)}{centena}{int(m.group(2)):02d}"


def use(sheet):
    """Las funciones de dibujo (Part, wire, ...) van a la hoja activa."""
    CUR[0] = sheet


def rot_offset(px, py, rot, mirror=None):
    vx, vy = px, -py
    if mirror == "y":
        vx = -vx
    elif mirror == "x":
        vy = -vy
    if rot == 90:
        vx, vy = vy, -vx
    elif rot == 180:
        vx, vy = -vx, -vy
    elif rot == 270:
        vx, vy = -vy, vx
    return vx, vy


class Part:
    """Simbolo colocado. x, y en u. ref_off/val_off en u relativos al centro.

    campos: dict de propiedades extra (p.ej. {"Funcion": "...", "Poblar": "..."}),
    quedan ocultas en la hoja pero salen en el BOM con --fields.
    datasheet / lcsc: propiedades Datasheet y LCSC (campo lcsc se guarda como property "LCSC").
    """

    def __init__(self, ref, value, lib_id, x, y, rot=0, footprint="",
                 ref_off=(-3, -5), val_off=(-3, 5), hide_value=False,
                 mirror=None, campos=None, just="left", datasheet="", lcsc="", dnp=False):
        self.ref, self.value, self.lib_id = ref, value, lib_id
        self.x, self.y, self.rot, self.mirror = x * U, y * U, rot, mirror
        self.footprint = footprint
        self.uuid = uid(f"{PROJECT}:{CUR[0].filename}:{ref}")
        self.ref_off, self.val_off = ref_off, val_off
        self.hide_value = hide_value
        self.campos = dict(campos or {})
        self.datasheet = datasheet
        if lcsc:
            self.campos["LCSC"] = lcsc
        self.dnp = dnp
        self.just = just
        self._path = CUR[0].path
        self._sheet = CUR[0]
        CUR[0].elems.append(self)
        CUR[0].libs.add(lib_id)

    def pin(self, num):
        """Coordenadas (u) del punto de conexion del pin `num` ya rotado/espejado."""
        px, py, _, _, _ = PINMAPS[self.lib_id][str(num)]
        dx, dy = rot_offset(px, py, self.rot, self.mirror)
        return (q((self.x + dx) / U), q((self.y + dy) / U))

    def refs(self):
        """Referencias reales (una por instancia de la hoja)."""
        if self._sheet.instancias:
            return [ref_inst(self.ref, c) for (_, _, c) in self._sheet.instancias]
        return [self.ref]

    def to_kicad(self):
        sx, sy = q(self.x), q(self.y)
        oculto = "#PWR" in self.ref or "#FLG" in self.ref
        ta = 90 if self.rot in (90, 270) else 0
        # KiCad normaliza el angulo del campo y le suma la rotacion del simbolo: con
        # rot 90 o 180, o con (mirror y), la justificacion escrita se DIBUJA espejada
        # (verificado en render y modelado igual en chequear_solapes_sch.py). Para que
        # "justify left en (dx,dy)" signifique siempre "el texto empieza en dx y crece
        # a la derecha", en esos casos se escribe 'right' en el archivo.
        flip = (self.rot in (90, 180)) != (self.mirror == "y")
        just = self.just
        if flip:
            just = {"left": "right", "right": "left"}.get(just, just)
        rx, ry = q(sx + self.ref_off[0] * U), q(sy + self.ref_off[1] * U)
        vx, vy = q(sx + self.val_off[0] * U), q(sy + self.val_off[1] * U)
        mir = f"(mirror {self.mirror})" if self.mirror else ""
        extra = ""
        for k, v in self.campos.items():
            extra += (f'    (property "{k}" "{v}" (at {sx} {sy} 0)\n'
                      f'      (effects (font (size 1.27 1.27)) (hide yes)))\n')
        pins = "".join(f'    (pin "{n}" (uuid "{uid(f"{PROJECT}:{self._sheet.filename}:{self.ref}:pin:{n}")}"))' + NL
                       for n in sorted(PINMAPS[self.lib_id]))
        if self._sheet.instancias:
            inst = "".join(f'      (path "{p}" (reference "{ref_inst(self.ref, c)}") (unit 1))' + NL
                           for (_, p, c) in self._sheet.instancias)
            ref_txt = ref_inst(self.ref, self._sheet.instancias[0][2])
        else:
            inst = f'      (path "{self._path}" (reference "{self.ref}") (unit 1))' + NL
            ref_txt = self.ref
        return f'''  (symbol
    (lib_id "{self.lib_id}")
    (at {sx} {sy} {self.rot})
    {mir}
    (unit 1) (exclude_from_sim no) (in_bom {"no" if oculto else "yes"})
    (on_board yes) (dnp {"yes" if self.dnp else "no"})
    (uuid "{self.uuid}")
    (property "Reference" "{ref_txt}"
      (at {rx} {ry} {ta})
      (effects (font (size 1.27 1.27)) (justify {just}){" (hide yes)" if oculto else ""}))
    (property "Value" "{self.value}"
      (at {vx} {vy} {ta})
      (effects (font (size 1.27 1.27)) (justify {just}){" (hide yes)" if self.hide_value else ""}))
    (property "Footprint" "{self.footprint}"
      (at {sx} {sy} 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
    (property "Datasheet" "{self.datasheet}"
      (at {sx} {sy} 0)
      (effects (font (size 1.27 1.27)) (hide yes)))
{extra}{pins}    (instances (project "{PROJECT}"
{inst}    ))
  )
'''


class Raw:
    def __init__(self, text):
        self.text = text
        CUR[0].elems.append(self)

    def to_kicad(self):
        return self.text


def wire(*pts):
    """Polilinea de cables por puntos (u). Aborta si un tramo sale diagonal."""
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if (x1, y1) == (x2, y2):
            continue
        if x1 != x2 and y1 != y2:
            raise SystemExit(f"CABLE DIAGONAL en {CUR[0].name}: ({x1},{y1})->({x2},{y2}) "
                             "(un pin fuera de la grilla que se asumio)")
        CUR[0].wires.append(((x1, y1), (x2, y2)))
        Raw('  (wire (pts (xy %s %s) (xy %s %s))%s'
            '    (stroke (width 0) (type default)) (uuid "%s"))%s'
            % (mm(x1), mm(y1), mm(x2), mm(y2), NL, uid(), NL))


def junction(x, y):
    CUR[0].junctions.append((x, y))
    Raw('  (junction (at %s %s) (diameter 0) (color 0 0 0 0)%s'
        '    (uuid "%s"))%s' % (mm(x), mm(y), NL, uid(), NL))


def no_connect(x, y):
    Raw('  (no_connect (at %s %s) (uuid "%s"))%s' % (mm(x), mm(y), uid(), NL))


def glabel(text, x, y, angle=0, shape="bidirectional"):
    just = "left" if angle == 0 else "right"
    Raw('  (global_label "%s"%s    (shape %s) (at %s %s %d)%s'
        '    (effects (font (size 1.524 1.524)) (justify %s))%s'
        '    (uuid "%s"))%s'
        % (text, NL, shape, mm(x), mm(y), angle, NL, just, NL, uid(), NL))


def hlabel(text, x, y, angle=0, shape="input"):
    """Etiqueta JERARQUICA (conecta con el sheet pin de la hoja madre)."""
    just = "left" if angle == 0 else "right"
    Raw('  (hierarchical_label "%s"%s    (shape %s) (at %s %s %d)%s'
        '    (effects (font (size 1.524 1.524)) (justify %s))%s'
        '    (uuid "%s"))%s'
        % (text, NL, shape, mm(x), mm(y), angle, NL, just, NL, uid(), NL))


def label(text, x, y, angle=0):
    """Etiqueta LOCAL sobre un cable (nombra la red, no reemplaza el cable)."""
    Raw('  (label "%s"%s    (at %s %s %d)%s'
        '    (effects (font (size 1.27 1.27)) (justify left bottom))%s'
        '    (uuid "%s"))%s'
        % (text, NL, mm(x), mm(y), angle, NL, NL, uid(), NL))


def note(text, x, y, size=1.5, bold=False):
    Raw('  (text "%s"%s    (at %s %s 0)%s'
        '    (effects (font (size %s %s)%s) (justify left))%s'
        '    (uuid "%s"))%s'
        % (text.replace('"', "'"), NL, mm(x), mm(y), NL, size, size,
           " (bold yes)" if bold else "", NL, uid(), NL))


def notas(lineas, x, y, paso=2.6, size=1.45, size_bold=1.65):
    """Bloque de lineas de texto. Cada linea: str o (str, bold)."""
    for i, ln in enumerate(lineas):
        if isinstance(ln, tuple):
            t, b = ln
        else:
            t, b = ln, False
        if t:
            note(t, x, y + i * paso, size_bold if b else size, b)


def marco(x0, y0, x1, y1, dash=True):
    Raw('  (rectangle (start %s %s) (end %s %s)%s'
        '    (stroke (width 0.2) (type %s)) (fill (type none))%s'
        '    (uuid "%s"))%s' % (mm(x0), mm(y0), mm(x1), mm(y1), NL,
                                 "dash" if dash else "default", NL, uid(), NL))


def linea(x0, y0, x1, y1, dash=True, w=0.3):
    Raw('  (polyline (pts (xy %s %s) (xy %s %s))%s'
        '    (stroke (width %s) (type %s)) (uuid "%s"))%s'
        % (mm(x0), mm(y0), mm(x1), mm(y1), NL, w, "dash" if dash else "default",
           uid(), NL))


def sheet_symbol(hija, x0, y0, w, h, pins, inst=0, nombre=None):
    """Bloque (sheet ...) de la hoja `hija` dentro de la hoja activa (la madre).

    pins: lista de (nombre, shape, lado, y_u); lado 'L' (borde izquierdo) o 'R'
    (borde derecho). Cada pin debe tener su hlabel() con la misma shape en la hija.
    inst: indice de instancia (hojas multi-instancia); nombre: Sheetname a mostrar.
    """
    ptxt = ""
    for pname, shape, lado, py in pins:
        if lado == "R":
            px, ang, just = x0 + w, 0, "right"
        else:
            px, ang, just = x0, 180, "left"
        ptxt += (f'    (pin "{pname}" {shape} (at {mm(px)} {mm(py)} {ang}) (uuid "{uid()}")\n'
                 f'      (effects (font (size 1.27 1.27)) (justify {just})))\n')
    cu = hija.instancias[inst][0] if hija.instancias else hija.child_uuid
    Raw(f'''  (sheet (at {mm(x0)} {mm(y0)}) (size {mm(w)} {mm(h)})
    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (stroke (width 0.3) (type solid)) (fill (color 0 0 0 0.0000))
    (uuid "{cu}")
    (property "Sheetname" "{nombre or hija.name}" (at {mm(x0)} {mm(y0 - 0.6)} 0)
      (effects (font (size 2 2) (bold yes)) (justify left bottom)))
    (property "Sheetfile" "{hija.filename}" (at {mm(x0)} {mm(y0 + h + 0.6)} 0)
      (effects (font (size 1.27 1.27)) (justify left top)))
{ptxt}    (instances (project "{PROJECT}" (path "{CUR[0].path}" (page "{hija.page + inst}"))))
  )
''')
    return cu


# --------------------------------------------------------- power helpers
_npwr = [0]
_nflg = [0]


def _pwr(ref_prefix, counter, value, lid, x, y, rot, ref_off, val_off, hide_value=False):
    counter[0] += 1
    return Part(f"{ref_prefix}{counter[0]:03d}", value, lid, x, y, rot,
                ref_off=ref_off, val_off=val_off, hide_value=hide_value)


def gnd(x, y, rot=0):
    """GND: pin en (x,y), simbolo hacia abajo (rot 0) o hacia arriba (rot 180)."""
    return _pwr("#PWR", _npwr, "GND", load("power", "GND"), x, y, rot,
                (2, 1), (-2, 4.2) if rot == 0 else (-2, -4.2))


def rail(name, x, y, lid, rot=0):
    """Simbolo de riel (+5V, +3V3, ...) con el pin en (x,y), dibujado hacia arriba."""
    w = len(name)
    return _pwr("#PWR", _npwr, name, lid, x, y, rot,
                (2, 1), (-w * 0.55, -3.4) if rot == 0 else (-w * 0.55, 4.5))


def pwr_flag(x, y):
    return _pwr("#FLG", _nflg, "PWR_FLAG", load("power", "PWR_FLAG"), x, y, 0,
                (2, -4), (-6, -6), hide_value=True)


# ----------------------------------------------------------------- doc
def write_sheet(sheet, outdir, rev="A", company="", date=None):
    """Escribe sheet.filename en outdir. La raiz (sin madre) lleva sheet_instances."""
    body = "".join(e.to_kicad() for e in sheet.elems)
    libsyms = "\n".join(LIBS[k] for k in sorted(sheet.libs))
    date = date or datetime.date.today().isoformat()
    comments = "".join(f'    (comment {i + 1} "{c}")\n' for i, c in enumerate(sheet.comments))
    tail = '  (sheet_instances\n    (path "/" (page "1"))\n  )\n' if sheet.child_uuid is None else ""
    doc = f'''(kicad_sch
  (version 20231120)
  (generator "{GENERATOR}")
  (generator_version "1.0")
  (uuid "{sheet.file_uuid}")
  (paper "{sheet.paper}")
  (title_block
    (title "{sheet.title}")
    (date "{date}")
    (rev "{rev}")
    (company "{company}")
{comments}  )
  (lib_symbols
{libsyms}
  )
{body}{tail})
'''
    path = os.path.join(outdir, sheet.filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path


def chequear_geometria(sheet):
    """Cada junction debe estar sobre >= 1 cable (interior o extremo) y cada extremo de
    cable que cae en el INTERIOR de otro cable debe tener junction (si no, KiCad no conecta)."""
    def sobre(p, w):
        (x1, y1), (x2, y2) = w
        x, y = p
        if x1 == x2:
            return x == x1 and min(y1, y2) <= y <= max(y1, y2)
        return y == y1 and min(x1, x2) <= x <= max(x1, x2)

    def interior(p, w):
        return sobre(p, w) and p != w[0] and p != w[1]
    errores = []
    js = set(sheet.junctions)
    for j in sheet.junctions:
        if not any(sobre(j, w) for w in sheet.wires):
            errores.append(f"junction {j} no esta sobre ningun cable")
    for w in sheet.wires:
        for p in w:
            if p in js:
                continue
            if any(interior(p, w2) for w2 in sheet.wires if w2 is not w):
                errores.append(f"extremo de cable {p} toca el interior de otro cable SIN junction")
    if errores:
        raise SystemExit("GEOMETRIA " + sheet.name + ":" + NL + "  " + (NL + "  ").join(errores))
    print(f"geometria {sheet.name}: {len(sheet.wires)} cables, {len(sheet.junctions)} junctions OK")


def chequear_referencias(sheets):
    """Referencias unicas en TODAS las hojas y TODAS las instancias (KiCad fusiona duplicados)."""
    vistos = {}
    for sh in sheets:
        for e in sh.elems:
            if isinstance(e, Part) and not e.ref.startswith("#"):
                for r in e.refs():
                    if r in vistos:
                        raise SystemExit(f"REFERENCIA DUPLICADA: {r} en {sh.name} y en {vistos[r]}")
                    vistos[r] = sh.name
    print(f"referencias: {len(vistos)} componentes (instancias expandidas), sin duplicados")
    return vistos

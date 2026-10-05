# -*- coding: utf-8 -*-
"""chequear_solapes_sch.py - QA de texto para esquematicos KiCad (falla si dos
textos visibles se pisan).

STATUS:
  Ran on every sheet of several real projects (0 overlaps after fixing them
  guided by this output) and on ejemplo/. Its justify/rotation model for
  fields matches KiCad's actual render (checked by eye on the PNG export).
  USO    : python chequear_solapes_sch.py archivo.kicad_sch [--tol 0.2]
           -> codigo 0 sin solapes, 1 con solapes; ademas informa textos rotados,
              bbox y ocupacion de la hoja. verificar.py lo llama por cada hoja.
  DEPENDE: nada (stdlib). Parser s-expression propio.
  GOTCHAS: - Cota CONSERVADORA: ancho = n_car * size * 1.0 (la fuente stroke avanza
             ~0.93*size). Un "solape" de decimas puede no verse en el PDF; --tol
             lo relaja, pero el objetivo es cero solapes.
           - No mira el cuerpo de los simbolos ni los cables: un texto sobre un
             cable no se detecta aca (mirar el PNG).
           - Areas utiles solo para A4, A3 y A2 (tabla UTIL; otro papel cae en A4).

Detalle de la formula:

    ancho = n_caracteres * size * 1.0     (cota conservadora; el avance real
                                           de la fuente stroke es ~0.93*size)
    alto  = n_lineas     * size * 1.6

y falla si dos rectangulos se solapan. Es el paso obligatorio del pipeline
ANTES de exportar el PDF.

Ademas informa (no falla): textos rotados 90/270, bbox del dibujo y
ocupacion de la hoja.

Uso:
    python chequear_solapes_sch.py archivo.kicad_sch [--tol 0.2]
Salida: 0 si no hay solapes, 1 si hay.
"""
import sys
import os

# --------------------------------------------------------------- parser
def parse(text):
    """S-expression -> listas anidadas. Los strings quedan como ('s', valor)."""
    i, n = 0, len(text)
    stack = [[]]
    while i < n:
        c = text[i]
        if c == '(':
            nuevo = []
            stack[-1].append(nuevo)
            stack.append(nuevo)
            i += 1
        elif c == ')':
            stack.pop()
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n:
                if text[j] == '\\':
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            stack[-1].append(('s', ''.join(buf)))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    return stack[0][0]


def head(node):
    return node[0] if node and isinstance(node[0], str) else None


def find(node, name):
    return [c for c in node if isinstance(c, list) and head(c) == name]


def val(node):
    """Valor string de ('s', x) o del token crudo."""
    return node[1] if isinstance(node, tuple) else node


# ------------------------------------------------------ extraccion de texto
PAPER = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0)}
# area util por papel (margenes del marco de KiCad)
UTIL = {"A4": (12.0, 12.0, 285.0, 165.0), "A3": (15.0, 15.0, 405.0, 250.0), "A2": (15.0, 15.0, 579.0, 370.0)}


def efectos(node):
    """(size, justify_h, oculto, justify_v) del nodo (effects ...)."""
    size, just, hide, jv = 1.27, 'center', False, 'center'
    ef = find(node, 'effects')
    if ef:
        ef = ef[0]
        fo = find(ef, 'font')
        if fo:
            sz = find(fo[0], 'size')
            if sz:
                size = float(val(sz[0][1]))
        ju = find(ef, 'justify')
        if ju:
            toks = [val(t) for t in ju[0][1:]]
            for t in toks:
                if t in ('left', 'right'):
                    just = t
                elif t in ('top', 'bottom'):
                    jv = t
        for h in find(ef, 'hide'):
            if val(h[1]) == 'yes':
                hide = True
        if 'hide' in [t for t in ef if isinstance(t, str)]:
            hide = True
    return size, just, hide, jv


def at(node):
    a = find(node, 'at')
    if not a:
        return None
    p = a[0]
    x, y = float(val(p[1])), float(val(p[2]))
    ang = float(val(p[3])) if len(p) > 3 else 0.0
    return x, y, ang


def caja(texto, x, y, ang, size, just, extra_car=0, jv='center'):
    lineas = texto.split('\\n')
    ncar = max(len(l) for l in lineas) + extra_car
    w = ncar * size * 1.0
    h = len(lineas) * size * 1.6
    if abs(ang) in (90.0, 270.0):
        w, h = h, w
        # texto vertical: crece hacia arriba (left) o hacia abajo (right)
        x0, x1 = x - w / 2, x + w / 2
        if just == 'right':
            y0, y1 = y, y + h
        else:
            y0, y1 = y - h, y
    else:
        if just == 'left':
            x0, x1 = x, x + w
        elif just == 'right':
            x0, x1 = x - w, x
        else:
            x0, x1 = x - w / 2, x + w / 2
        if jv == 'bottom':      # KiCad dibuja el texto ARRIBA del punto
            y0, y1 = y - h, y
        elif jv == 'top':
            y0, y1 = y, y + h
        else:
            y0, y1 = y - h / 2, y + h / 2
    return x0, y0, x1, y1


def recolectar(root):
    """[(etiqueta, texto, x0,y0,x1,y1, angulo)] de todo el texto visible."""
    cajas = []
    for nodo in root:
        if not isinstance(nodo, list):
            continue
        t = head(nodo)
        if t == 'lib_symbols':
            continue                      # definiciones, no se dibujan
        if t == 'text':
            s = val(nodo[1])
            x, y, a = at(nodo)
            size, just, hide, jv = efectos(nodo)
            if not hide:
                cajas.append(('nota', s,
                              *caja(s, x, y, a, size, just, 0, jv), a))
        elif t in ('global_label', 'label', 'hierarchical_label'):
            s = val(nodo[1])
            x, y, a = at(nodo)
            size, just, hide, jv = efectos(nodo)
            # el global_label dibuja una flecha alrededor del texto
            extra = 3 if t == 'global_label' else 1
            if not hide:
                cajas.append((t, s,
                              *caja(s, x, y, a, size, just, extra, jv), a))
        elif t == 'symbol':
            ref = ''
            for p in find(nodo, 'property'):
                if val(p[1]) == 'Reference':
                    ref = val(p[2])
            rot_sym = at(nodo)[2] if at(nodo) else 0.0
            # (mirror y) = espejado horizontal -> el justify se da vuelta
            esp = any(val(m[1]) == 'y' for m in find(nodo, 'mirror'))
            for p in find(nodo, 'property'):
                nombre, s = val(p[1]), val(p[2])
                if nombre in ('Footprint', 'Datasheet', 'Description') or not s:
                    continue
                size, just, hide, jv = efectos(p)
                if hide:
                    continue
                x, y, a = at(p)
                # KiCad ajusta el angulo del campo a 0/90 y le suma la
                # rotacion del simbolo; 180/270 = mismo eje pero espejado.
                eff = (rot_sym + (90 if a % 180 else 0)) % 360
                if esp:
                    just = {'left': 'right', 'right': 'left'}.get(just, just)
                if eff >= 180:
                    eff -= 180
                    just = {'left': 'right', 'right': 'left'}.get(just, just)
                cajas.append((f'{ref}.{nombre}', s,
                              *caja(s, x, y, eff, size, just, 0, jv), eff))
    return cajas


def solapan(a, b, tol):
    return not (a[4] - tol <= b[2] or b[4] - tol <= a[2] or
                a[5] - tol <= b[3] or b[5] - tol <= a[3])


def ocupacion(root, cajas, papel, paso=2.54):
    """% del area util realmente cubierta (texto + cables + simbolos).

    El bbox solo mide extension; esto mide DENSIDAD, que es lo que pide
    Objetivo 55-75 %.
    """
    ux0, uy0, ux1, uy1 = UTIL.get(papel, UTIL['A4'])
    nx = int((ux1 - ux0) / paso) + 1
    ny = int((uy1 - uy0) / paso) + 1
    celdas = set()

    def marcar(x0, y0, x1, y1):
        i0 = max(0, int((min(x0, x1) - ux0) / paso))
        i1 = min(nx - 1, int((max(x0, x1) - ux0) / paso))
        j0 = max(0, int((min(y0, y1) - uy0) / paso))
        j1 = min(ny - 1, int((max(y0, y1) - uy0) / paso))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                celdas.add((i, j))

    for c in cajas:
        marcar(c[2], c[3], c[4], c[5])
    for w in find(root, 'wire'):
        p = find(w, 'pts')
        if p:
            xy = find(p[0], 'xy')
            for a, b in zip(xy, xy[1:]):
                marcar(float(val(a[1])), float(val(a[2])),
                       float(val(b[1])), float(val(b[2])))
    for s in find(root, 'symbol'):
        a = at(s)
        if a:
            marcar(a[0] - 5, a[1] - 5, a[0] + 5, a[1] + 5)
    return len(celdas) / float(nx * ny) * 100


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ruta = argv[1]
    tol = 0.2
    if '--tol' in argv:
        tol = float(argv[argv.index('--tol') + 1])
    root = parse(open(ruta, encoding='utf-8').read())
    papel = 'A4'
    for p in find(root, 'paper'):
        papel = val(p[1])
    cajas = recolectar(root)

    choques = []
    for i in range(len(cajas)):
        for j in range(i + 1, len(cajas)):
            if solapan(cajas[i], cajas[j], tol):
                choques.append((cajas[i], cajas[j]))

    print('=== %s  (%s, %d textos visibles) ===' %
          (os.path.basename(ruta), papel, len(cajas)))

    rotados = [c for c in cajas if abs(c[6]) in (90.0, 270.0)]
    print('  textos rotados 90/270 : %d %s' %
          (len(rotados), '' if not rotados else
           '<- regla: cero texto vertical'))
    for c in rotados[:12]:
        print('      %-16s "%s"' % (c[0], c[1][:30]))

    if cajas:
        x0 = min(c[2] for c in cajas); y0 = min(c[3] for c in cajas)
        x1 = max(c[4] for c in cajas); y1 = max(c[5] for c in cajas)
        ux0, uy0, ux1, uy1 = UTIL.get(papel, UTIL['A4'])
        ext = ((x1 - x0) * (y1 - y0)) / ((ux1 - ux0) * (uy1 - uy0)) * 100
        print('  bbox de texto         : x %.1f..%.1f  y %.1f..%.1f' %
              (x0, x1, y0, y1))
        print('  extension del bbox    : %.0f %% del area util' % ext)
        print('  ocupacion (densidad)  : %.0f %%  (objetivo 55-75 %%)' %
              ocupacion(root, cajas, papel))
        if x1 > ux1 + 1 or y1 > uy1 + 1 or x0 < ux0 - 1 or y0 < uy0 - 1:
            print('  AVISO: hay texto fuera del area util %s' %
                  str((ux0, uy0, ux1, uy1)))

    print('\n=== SOLAPES DE TEXTO: %d ===' % len(choques))
    for a, b in choques:
        print('  %-16s "%s"' % (a[0], a[1][:38]))
        print('  %-16s "%s"' % (b[0], b[1][:38]))
        print('      A x %.1f..%.1f y %.1f..%.1f | B x %.1f..%.1f y %.1f..%.1f'
              % (a[2], a[4], a[3], a[5], b[2], b[4], b[3], b[5]))
    return 1 if choques else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

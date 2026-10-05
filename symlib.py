#!/usr/bin/env python3
"""symlib.py - extractor de simbolos de las librerias estandar de KiCad 10.

STATUS:
  Resolves `extends` chains and keeps the child's properties. Every schematic
  generated with kicad_gen extracts its symbols through this module and passed
  `kicad-cli sch erc --severity-all` with 0/0 (including ejemplo/).
  USO    :
      from symlib import get_symbol, get_pins
      block, lib_id = get_symbol("Device", "R")     # s-expression para lib_symbols
      pins = get_pins(block)                          # {"1": (x, y, ang, nombre, tipo)}
      python symlib.py                                # lista pines de simbolos comunes
  DEPENDE: KiCad 10 instalado (lee los .kicad_sym de share/kicad/symbols). Si no
           esta en la ruta por defecto: variable de entorno KICAD_SYMS.
  GOTCHAS: - Simbolos derivados (`extends`, p.ej. BC547 -> Q_NPN_CBE) se aplanan;
             KiCad marca 'lib_symbol_mismatch' si compara contra la lib oficial.
             Para evitarlo, cargar el simbolo BASE y poner el part number en Value.
           - Las coordenadas de pin son las del sistema del simbolo (y hacia
             ARRIBA); kicad_gen.rot_offset() las pasa al sistema de la hoja.
           - Solo la primera aparicion de `(symbol "nombre"` en la lib; nombres
             con prefijo comun (p.ej. "R" vs "R_Small") no se confunden porque
             la busqueda incluye la comilla de cierre.
"""
import os
import re

KICAD_SYMS = os.environ.get(
    "KICAD_SYMS", r"C:\Program Files\KiCad\10.0\share\kicad\symbols")


def _find_symbol_block(text, name):
    """Devuelve el bloque '(symbol "name" ...)' balanceado, o None."""
    needle = f'(symbol "{name}"'
    i = text.find(needle)
    if i < 0:
        return None
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    raise ValueError(f"parentesis desbalanceados buscando {name}")


def get_symbol(lib, name):
    """Bloque del simbolo con `extends` resuelto (renombra el padre al hijo).

    Devuelve (texto_bloque, lib_id) donde lib_id = 'lib:name'.
    """
    path = os.path.join(KICAD_SYMS, lib + ".kicad_sym")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    block = _find_symbol_block(text, name)
    if block is None:
        raise KeyError(f"{name} no esta en {lib}")

    def _props(txt):
        """Lista de bloques (property ...) balanceados, en orden."""
        out = []
        for mm in re.finditer(r'\(property\s+"', txt):
            i = mm.start()
            depth, j = 0, i
            while j < len(txt):
                if txt[j] == '(':
                    depth += 1
                elif txt[j] == ')':
                    depth -= 1
                    if depth == 0:
                        out.append((i, j + 1, txt[i:j + 1]))
                        break
                j += 1
        return out

    m = re.search(r'\(extends\s+"([^"]+)"\)', block)
    if m:
        parent = m.group(1)
        pblock = _find_symbol_block(text, parent)
        while re.search(r'\(extends\s+"([^"]+)"\)', pblock):   # cadenas de extends
            abuelo = re.search(r'\(extends\s+"([^"]+)"\)', pblock).group(1)
            pblock = _find_symbol_block(text, abuelo).replace(
                f'"{abuelo}"', f'"{parent}"').replace(f'"{abuelo}_', f'"{parent}_')
        # el hijo manda en TODAS sus propiedades (texto completo, con posiciones)
        child_props = _props(block)
        base = pblock.replace(f'"{parent}"', f'"{name}"').replace(
            f'"{parent}_', f'"{name}_')
        pprops = _props(base)
        if pprops and child_props:
            ini, fin = pprops[0][0], pprops[-1][1]
            nuevo = ("\n\t\t".join(p[2] for p in child_props))
            base = base[:ini] + nuevo + base[fin:]
        block = base
    # prefijar el lib_id dentro del bloque: (symbol "Device:R" ...)
    block = block.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)
    return block, f"{lib}:{name}"


def get_pins(block):
    """Dict numero_pin -> (x, y, angulo, nombre, tipo_electrico).

    Coordenadas del sistema del simbolo (y hacia arriba), punto de conexion.
    """
    pins = {}
    for m in re.finditer(
            r'\(pin\s+(\w+)\s+\w+\s*\(at\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)'
            r'.*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"',
            block, re.S):
        etype, x, y, ang, name, num = m.groups()
        pins[num] = (float(x), float(y), float(ang), name, etype)
    return pins


if __name__ == "__main__":
    wanted = [
        ("Device", "R"), ("Device", "C"), ("Device", "C_Polarized"),
        ("Device", "D"), ("Device", "D_Schottky"), ("Device", "D_Zener"),
        ("Device", "LED"), ("Device", "Fuse"),
        ("Isolator", "PC817"),
        ("Relay", "SANYOU_SRD_Form_C"),
        ("Regulator_Linear", "L7805"),
        ("Transistor_FET", "IRLZ44N"),
        ("Connector", "Screw_Terminal_01x02"),
        ("Connector", "Screw_Terminal_01x03"),
        ("power", "GND"), ("power", "+5V"), ("power", "PWR_FLAG"),
    ]
    for lib, name in wanted:
        block, lid = get_symbol(lib, name)
        print(f"== {lid}")
        for num, (x, y, ang, pname, et) in sorted(get_pins(block).items()):
            print(f"   pin {num:>3} '{pname}' {et:<10} at ({x:7.2f},{y:7.2f}) ang {ang:.0f}")

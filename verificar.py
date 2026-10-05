#!/usr/bin/env python3
"""verificar.py - pipeline de verificacion de un esquematico KiCad (evidencia, no opinion).

STATUS:
  Child sheets are discovered by reading the (sheet ...) blocks of the root.
  The full pipeline ran on a 6-sheet project (ERC 0/0, no text overlaps, 6-page
  PDF, netlist, BOM) and on ejemplo/ (`python verificar.py ejemplo/ejemplo.kicad_sch`
  = RESULTADO OK).
  USO    : python verificar.py raiz.kicad_sch
               [--bom-fields "Reference,Value,Footprint,${QUANTITY}"]
               [--bom-labels "Referencia,Valor,Huella,Cant"]
               [--group-by "Value,Footprint"] [--sin-png]
           Escribe al lado de la raiz: erc.txt, <raiz>.pdf, <raiz>.net,
           <raiz>_bom.csv y salida/pN.png (una por pagina, si hay pymupdf).
           Sale con 1 si el ERC tiene errores O warnings, o si hay solapes de texto.
  DEPENDE: kicad-cli (KiCad 10; ruta en KICAD o variable de entorno KICAD_CLI),
           chequear_solapes_sch.py (misma carpeta), pymupdf opcional para los PNG.
  GOTCHAS: - `--severity-all`: un WARNING tambien hace fallar. Es a proposito
             (politica: cero warnings; si uno es legitimo, excluirlo en KiCad).
           - Los campos extra del BOM (p.ej. "Funcion") solo salen si el Part los
             tiene en `campos`; un campo inexistente sale vacio, no falla.
           - kicad-cli imprime en UTF-8 con caracteres raros: se lee con
             errors="replace" para que no reviente en consola Windows.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KICAD = os.environ.get("KICAD_CLI", r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe")


def run(*args, cwd=None):
    print("$", " ".join(args))
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def hojas_de(raiz, vistas=None):
    """[raiz, hijas, nietas, ...] leyendo (property "Sheetfile" "x") recursivamente."""
    vistas = vistas if vistas is not None else []
    if raiz in vistas:
        return vistas
    vistas.append(raiz)
    txt = open(raiz, encoding="utf-8").read()
    for hija in re.findall(r'\(property\s+"Sheetfile"\s+"([^"]+)"', txt):
        hojas_de(os.path.join(os.path.dirname(raiz), hija), vistas)
    return vistas


def arg(argv, flag, default):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    raiz = os.path.abspath(argv[1])
    d = os.path.dirname(raiz)
    base = os.path.splitext(os.path.basename(raiz))[0]
    fields = arg(argv, "--bom-fields", "Reference,Value,Footprint,${QUANTITY}")
    labels = arg(argv, "--bom-labels", "Referencia,Valor,Huella,Cant")
    group = arg(argv, "--group-by", "Value,Footprint")
    ok = True

    r = run(KICAD, "sch", "erc", "--severity-all", "-o", "erc.txt", raiz, cwd=d)
    txt = open(os.path.join(d, "erc.txt"), encoding="utf-8").read() if r.returncode == 0 else ""
    resumen = [ln for ln in txt.splitlines() if "ERC messages" in ln]
    print("ERC:", resumen[0].strip() if resumen else (r.stdout + r.stderr)[-300:])
    if not resumen or "Errors 0  Warnings 0" not in resumen[0]:
        ok = False
        if txt:
            print(txt)

    for h in hojas_de(raiz):
        r = run(sys.executable, os.path.join(HERE, "chequear_solapes_sch.py"), h)
        lineas = r.stdout.splitlines()
        res = [ln for ln in lineas if "SOLAPES DE TEXTO" in ln or "ocupacion" in ln
               or "fuera del area" in ln or "bbox" in ln]
        print(f"  {os.path.basename(h)}: " + " | ".join(x.strip() for x in res))
        if r.returncode != 0:
            ok = False
            print("\n".join(lineas[-40:]))

    pdf = os.path.join(d, base + ".pdf")
    r = run(KICAD, "sch", "export", "pdf", "-o", pdf, raiz, cwd=d)
    print("PDF:", r.returncode, r.stdout.strip()[-80:])
    if r.returncode != 0:
        ok = False
    r = run(KICAD, "sch", "export", "netlist", "-o", os.path.join(d, base + ".net"), raiz, cwd=d)
    print("NET:", r.returncode)
    r = run(KICAD, "sch", "export", "bom", "-o", os.path.join(d, base + "_bom.csv"),
            "--fields", fields, "--labels", labels, "--group-by", group,
            "--sort-field", "Reference", raiz, cwd=d)
    print("BOM:", r.returncode, r.stderr.strip()[-200:] if r.returncode else "")
    if r.returncode != 0:
        ok = False
    if "--sin-png" not in argv:
        try:
            import fitz
            out = os.path.join(d, "salida")
            os.makedirs(out, exist_ok=True)
            doc = fitz.open(pdf)
            for i, pg in enumerate(doc):
                pg.get_pixmap(dpi=110).save(os.path.join(out, f"p{i + 1}.png"))
            print(f"PNG: {len(doc)} paginas en {out}")
        except ImportError:
            print("PNG: pymupdf no disponible (pip install pymupdf)")
    print("RESULTADO:", "OK" if ok else "FALLA")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

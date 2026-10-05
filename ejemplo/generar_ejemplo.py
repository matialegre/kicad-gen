#!/usr/bin/env python3
"""Ejemplo de kicad_gen: bornera -> riel +5V -> DOS instancias de un modulo LED (hoja hija
multi-instancia), con riel LOCAL por instancia (`power_local`) y UUIDs deterministas.

    python generar_ejemplo.py                    -> ejemplo.kicad_sch + modulo_led.kicad_sch
    python ..\\verificar.py ejemplo.kicad_sch     -> ERC 0/0, PDF, netlist, BOM

Es la prueba de humo de kicad_gen (incluida la multi-instancia y power_local):
si esto no pasa el ERC, algo se rompio.

Determinismo (uid() con clave estable, 2026-09-20): correr este script dos veces
seguidas y comparar sha256 de ejemplo.kicad_sch y modulo_led.kicad_sch da el MISMO
hash las dos veces -- probado a mano, no hay un script aparte para esto.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # ..\ = carpeta kicad_gen
import kicad_gen as K  # noqa: E402
from kicad_gen import (Part, wire, junction, gnd, rail, pwr_flag, load, use,  # noqa: E402
                       note, marco, sheet_symbol)

K.configurar("ejemplo")

R_ = load("Device", "R")
LED_ = load("Device", "LED")
TB2_ = load("Connector", "Screw_Terminal_01x02")
P5V_ = load("power", "+5V")
# riel LOCAL (KiCad `(power local)`): cada instancia del modulo tiene SU PROPIA red
# +VIN_MOD, no una unica red global compartida entre las dos instancias.
VINMOD_ = K.power_local("power", "+5V", "+VIN_MOD")

FP_R = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
FP_LED = "LED_THT:LED_D5.0mm"

# ------------------------------------------------------------- hojas
raiz = K.Sheet("RAIZ", "ejemplo.kicad_sch", "A4", "Ejemplo kicad_gen: multi-instancia + power_local",
               comments=("2 instancias de un modulo LED + riel local por instancia",
                         "github.com/matialegre/kicad-gen"))
# hoja hija SIN madre fija en el constructor: K.instancias() la liga a `raiz` y fija
# su path/child_uuid reales (se instancia 2 veces: los refs internos R1/D1 se
# expanden a R101/D101 y R201/D201).
modulo = K.Sheet("MODULO_LED", "modulo_led.kicad_sch", "A4",
                  "Modulo LED (hoja reutilizada 2 veces)", page=2)
K.instancias(modulo, raiz, ("1", "2"))

use(modulo)
# El modulo se alimenta ENTERAMENTE del riel LOCAL +VIN_MOD (sin pin jerarquico): cada
# instancia (LED_1, LED_2) tiene su PROPIA red +VIN_MOD, scopeada al path de esa
# instancia -- no se funden aunque las dos hojas usen el MISMO archivo modulo_led.kicad_sch.
r1 = Part("R1", "330", R_, 40, 32, footprint=FP_R, ref_off=(2, -1.2), val_off=(2, 1.2),
          datasheet=K.datasheet_local("GENERICO_330R", "https://www.vishay.com/doc?20035", outdir=HERE),
          campos={"JLC": "https://jlcpcb.com/partdetail/C17513"})
d1 = Part("D1", "LED_VERDE", LED_, 40, 42, rot=90, footprint=FP_LED, ref_off=(2, -1.2), val_off=(2, 1.2),
          datasheet=K.datasheet_local("LED_VERDE_GENERICO", "https://www.vishay.com/doc?83006", outdir=HERE),
          campos={"JLC": "https://jlcpcb.com/partdetail/C2286"})

xr, yr1 = r1.pin(1)                    # pin de arriba de R1
rail("+VIN_MOD", xr, yr1, VINMOD_)     # pin del riel coincide EXACTO con el pin de R1: conectados sin cable
# Un simbolo de power (rail/gnd) es "power input": necesita un PWR_FLAG en la red o el ERC
# tira power_pin_not_driven -- y por ser riel LOCAL, cada instancia necesita el SUYO (no alcanza
# con el de la hoja madre): por eso va aca, dentro de la hoja reutilizable.
wire((xr, yr1), (xr + 5, yr1))
pwr_flag(xr + 5, yr1)
junction(xr, yr1)

wire(r1.pin(2), d1.pin(2))
xd, yk = d1.pin(1)                     # catodo
wire((xd, yk), (xd, yk + 5))
gnd(xd, yk + 5)
junction(xd, yk + 5)

marco(15, 22, 55, 60)
note("MODULO LED: I = (5 V - 2 V) / 330 = 9 mA. GND global (comun a las 2 instancias);", 16, 62, 1.3)
note("+VIN_MOD es riel LOCAL: la instancia 1 y la 2 NO comparten esta red.", 16, 64.5, 1.3)

K.chequear_geometria(modulo)
K.write_sheet(modulo, HERE, rev="A", company="kicad-gen", date="2026-09-20")

# ------------------------------------------------------------------- hoja madre
use(raiz)
j1 = Part("J1", "5V_IN", TB2_, 40, 20, mirror="y",
          footprint="TerminalBlock:TerminalBlock_MaiXu_MX126-5.0-02P_1x02_P5.00mm",
          ref_off=(-10, -3), val_off=(-10, 5))

# +5V/GND de la hoja madre: NO alimentan el modulo (que se basta con su riel local);
# quedan para mostrar el patron original rail()+pwr_flag() con dos redes en la misma hoja.
x_j1, y_j11 = j1.pin(1)
wire((x_j1, y_j11), (50, y_j11), (50, 20))
rail("+5V", 50, 20, P5V_)
junction(50, 20)
wire((50, 20), (55, 20))
pwr_flag(55, 20)

x_j2, y_j12 = j1.pin(2)
wire((x_j2, y_j12), (46, y_j12), (46, 12))
gnd(46, 12)
junction(46, 12)
wire((46, 12), (50, 12))
pwr_flag(50, 12)

# DOS instancias de la hoja MODULO_LED (sin pines: se alimenta sola via riel local).
sheet_symbol(hija=modulo, x0=78, y0=15, w=40, h=20, pins=[], inst=0, nombre="LED_1")
sheet_symbol(hija=modulo, x0=78, y0=40, w=40, h=20, pins=[], inst=1, nombre="LED_2")

marco(28, 6, 130, 68)
note("EJEMPLO: hoja MODULO_LED instanciada 2 veces (K.instancias); riel +VIN_MOD LOCAL", 30, 70, 1.4, True)
note("por instancia; UUIDs deterministas (uid() con clave); Datasheet local/URL (datasheet_local()).", 30, 72.3, 1.4)

K.chequear_referencias([raiz, modulo])
K.chequear_geometria(raiz)
K.write_sheet(raiz, HERE, rev="A", company="kicad-gen", date="2026-09-20")
K.write_custom_lib(HERE)
K.write_project(HERE)
print("OK -> ejemplo.kicad_sch + modulo_led.kicad_sch")

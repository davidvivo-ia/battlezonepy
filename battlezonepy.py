#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pygame-ce>=2.5"]
# ///
"""battlezonepy — Battlezone (Atari, 1980) recreado desde su código fuente original.

Un solo fichero. Las constantes, la IA y las tablas de modelos salen de leer
BZONE.MAC y BZMTNS.MAC (github.com/historicalsource/battlezone, rev 1). Cada
función cita la rutina original que reimplementa (``← ROBOT``, ``← COLCHK``…)
para poder leer los dos códigos uno junto al otro.

    uv run battlezonepy.py              # jugar
    python battlezonepy.py --selftest   # pruebas sin ventana

Homenaje no oficial. «Battlezone» es una marca de Atari.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
# §1 CONSTANTES — cada valor cita su origen en el fuente de Atari
# ═══════════════════════════════════════════════════════════════════════════

TICK_HZ = 1000 / 64          # ← NMI: SYNC cada 16 NMI de 4 ms, «END OF FRAME (64 MS)»
SUBSTEPS = 4                 # ← MAIN 18$: COLCHK + SHUPDT cuatro veces por tick
WORLD = 0x10000              # ← TPOSX/TPOSY de 16 bits: el mundo es un toro
HALF = WORLD // 2

# La Math Box rota el modelo con un producto que divide x e y entre 2; z (DDEND) no pasa
# por ese producto, y ROTPNT duplica el centro del objeto para compensar.  ← PNTPUT, ROTPNT
MODEL_SCALE = 2.0            # x, y de la tabla (.NWORD/4) → mundo
MODEL_ZSCALE = 4.0           # z de la tabla → mundo (z conserva la escala .NWORD)
Z_SCALE = 1.0                # EXPOSZ, LOGOZ, BOUNCE (unidades .NWORD) → mundo  ← KLUDGE

SCREEN_W, SCREEN_H = 1024, 768   # espacio del AVG a escala 1  ← HATCH (1023 × 767)
FOCAL = 4096 / math.tau      # 8 tramos de montaña × 512 = 360°  ← MOUNTS  [HIPÓTESIS para el 3D]
NEAR_CULL = 0x400 // 2       # ← ROTPNT «IF TOO CLOSE, THROW OUT»
FAR_CULL = 0x7B00 // 2       # ← ROTPNT «IF TOO FAR, THROW OUT»
NEAR_CLIP = 24.0             # plano cercano para recortar aristas  [DISEÑO]
VIEW_TOP = 236               # techo de la ventana 3D, bajo la franja roja  [DISEÑO]

SIN_SCALE = 127              # SIN/COS devuelven ±127 en el byte alto
MOVE_STEP = 0.75 * SIN_SCALE # ← M.SET: sen/2 + sen/4
SHELL_STEP = 2 * SIN_SCALE   # ← MOTION (disparo): COS desplazado un bit
SHELL_LIFE = 0x7F            # ← FIRECT: subpasos de vida del proyectil
MISSILE_STEP = 4 * SIN_SCALE # ← BUZBOM: SIN desplazado dos bits
SPAWN_DIST = 0.75 * 0x7FFF   # ← ROB1: «X=X-1/4*X» sobre un COS de 16 bits

PTS_TANK, PTS_SUPER, PTS_MISSILE, PTS_SAUCER = 1, 3, 2, 5   # miles  ← QWIKCK, SAUCHK
BONUS_BCD = (0, 0x14, 0x24, 0x49)    # ← BONTBL: vida al SUPERAR el valor
BONUS_K = (0, 15, 25, 50)            # lo mismo, en miles, para la CLI
MISLVL = (5, 10, 20, 30)             # ← MISLVL: misil desde estas puntuaciones (miles)
SUPER_BONUS_K = 100                  # ← NEWLIF rama SUPER

STARTZ = 0x1800              # ← R2D3CK: altura de entrada del misil
TOP = 0x200                  # ← BUZBOM: por debajo de TOP el misil es vulnerable
GRAVITY = 4                  # ← GRAVTY
EXPTBX = (-30, -30, 5, 50, 0, -40)   # ← EXPTBX (.NWORD, ×4)
EXPTBY = (30, 0, -5, 50, -40, -40)   # ← EXPTBY
IZVEL = (55, 40, 70, 88, 40, 66)     # ← IZVEL: velocidad vertical inicial de las piezas

PROXTB = {0x00: 0x340, 0x01: 0x340, 0x0C: 0x400, 0x0F: 0x3C0}  # tanque enemigo ↔ obstáculo
PRXTBL = {0x00: 56, 0x01: 88, 0x0C: 86, 0x0F: 0}               # proyectil ↔ obstáculo (dist/4)
PLAYER_OBJ_RADIUS = 0x480    # ← OBJOBJ, jugador: «COLLIDE SO WE CAN SEE OBJECT»
TANK_HI, MISSILE_HI = 5, 3   # ← OBJOBJ: choque tanque-tanque si byte alto ≤ 5 (misil ≤ 3)
SAUCER_HIT = 0x90            # ← SAUCHK
RADAR_RANGE_HI = 0x80        # ← DRADAR: TDIST < 0x80
VOLCANO = (3067, 94)         # ← MOUNTS 22$: x = 16·TANGLE − 0x605, y = 0x5E
NOROCK = 5                   # ← VOLCNO

FIELD_MODEL = {0x00: "PYR", 0x01: "CUB", 0x0C: "ROCK1", 0x0F: "ROCK2"}
TANK_DEBRIS = ("EX1", "EX2", "EX0", "RDR", "EX2", "EX1")       # ← OBJTBL 0x10–0x15
SUPER_DEBRIS = ("EX1", "EX2", "EX0", "EX2", "EX2", "EX1")      # ← EXPLDE: TR7 no tiene radar
MISSILE_DEBRIS = ("EX2", "EX4", "EX1", "EX3", "EX1", "EX4")    # ← OBJTBL 0x18–0x1D

TANK, SUPER, MISSILE = "tank", "super", "missile"
ATTRACT, PLAY, INITIALS = "attract", "play", "initials"

GREEN = (64, 255, 80)
RED = (255, 52, 40)
HISCORE_FILE = Path.home() / ".battlezonepy.json"

# ═══════════════════════════════════════════════════════════════════════════
# DATOS — generados a partir de BZMTNS.MAC y BZONE.MAC (rev 1); no editar a mano
# ═══════════════════════════════════════════════════════════════════════════

# fmt: off
VEC2D = {'CRACKS': [[('C',), (-100, 50, 0), (-75, 0, 6), (35, -100, 0), (40, 100, 6), (-100, 100, 6), (250, 25, 0),
             (-150, -125, 6), (100, -50, 6)],
            [(80, 0, 6), (-220, -50, 0), (65, -50, 6), (-100, 150, 0), (0, -45, 6), (-25, 145, 0),
             (-150, 45, 6), (225, 30, 0), (-75, -75, 6), (250, 25, 0), (10, 95, 6), (-10, -95, 0),
             (100, 60, 6)],
            [(100, 60, 6), (-100, 45, 0), (0, -105, 6), (-275, -10, 0), (-105, 95, 6), (-120, -125, 0),
             (-50, 10, 6), (50, -10, 0), (-130, -120, 6), (305, -70, 0), (-90, -5, 6), (190, -100, 0),
             (45, -140, 6), (110, 240, 0), (0, -40, 6)],
            [(105, 0, 6), (-215, -200, 0), (-80, -150, 6), (-155, 390, 0), (0, -50, 6), (0, 50, 0),
             (-85, 25, 6), (120, 295, 0), (-120, 115, 6), (175, 15, 0), (-55, -130, 6), (480, -25, 0),
             (70, 0, 6), (-195, 105, 0), (25, -60, 6), (75, 85, 6)],
            [(-15, 65, 6), (50, -40, 0), (-35, -25, 6), (-40, -465, 0), (105, 100, 6), (-80, -160, 0),
             (-25, 60, 6), (-10, -60, 6), (-250, -350, 0), (-35, 60, 6), (-40, -60, 6), (-115, 400, 0),
             (-35, -30, 6)],
            [(50, -70, 6), (-100, 30, 0), (50, 40, 6), (475, -20, 0), (-50, -50, 6), (135, 600, 0),
             (15, -50, 6), (15, -340, 0), (95, -110, 6)],
            [(115, 75, 6), (-90, -115, 0), (-25, 40, 6), (-735, -70, 0), (-65, -15, 6)],
            [(-85, 45, 6), (50, -95, 0), (35, 50, 6), (855, -15, 0), (-30, 60, 6), (80, 30, 6)]],
 'EXPIC': [(-88, 0, 0), (0, 0, 7), (-88, -88, 0), (0, 0, 7), (88, -88, 0), (0, 0, 7), (144, 32, 0), (0, 0, 7),
           (88, -32, 0), (0, 0, 7), (0, 88, 0), (0, 0, 7), (32, 144, 0), (0, 0, 7), (-32, 144, 0), (0, 0, 7),
           (-200, -32, 0), (0, 0, 7), (-144, 32, 0), (0, 0, 7)],
 'MTNS': [(0, 64, 0), (32, -32, 3), (-16, -8, 0), (80, 40, 3), (32, 0, 3), (32, -32, 3), (-64, 32, 3),
          (0, -64, 0), (128, 64, 3), (64, -64, 3), (32, 0, 0), (-64, 32, 3), (-32, 32, 0), (64, -32, 3),
          (64, -16, 3), (96, -16, 3), (48, 160, 0), (5, -12, 7), (0, -12, 7), (-8, -12, 7), (-12, -12, 7),
          (-12, -3, 7), (-12, 3, 7), (12, -9, 7), (12, -3, 7), (12, 3, 7), (12, 9, 7), (6, 12, 7), (0, 12, 7),
          (-4, 12, 7), (-11, 12, 7), (-13, 3, 2), (-15, -4, 2), (-10, -8, 2), (-5, -11, 2), (-2, -13, 2),
          (6, -15, 2), (27, 3, 0), (0, -4, 5), (6, -1, 5), (6, 11, 5), (-4, -1, 5), (-1, 2, 5), (18, 20, 0),
          (-3, -3, 5), (1, 3, 5), (-3, 1, 5), (0, 6, 5), (1, 1, 2), (7, -150, 0), (32, 0, 0), (64, 48, 3),
          (32, -48, 3), (-32, 48, 0), (32, -16, 3), (64, 32, 3), (160, -64, 3), (-128, 32, 3), (-32, 32, 3),
          (-64, -32, 0), (96, -32, 3), (128, 0, 0), (160, 32, 3), (0, -32, 0), (0, 32, 0), (64, 0, 3),
          (0, -32, 0), (-64, 32, 3), (64, 0, 0), (64, 32, 3), (-64, -32, 0), (96, -32, 3), (32, 32, 3),
          (-64, 32, 3), (32, 0, 3), (32, -32, 3), (32, 16, 3), (32, -16, 3), (-96, -32, 3), (96, 32, 0),
          (64, 32, 3), (64, -32, 3), (-96, 16, 0), (32, -16, 3), (32, 16, 3), (0, -48, 0), (96, 8, 3),
          (-64, 24, 3), (32, 16, 3), (64, -16, 3), (-32, -24, 3), (-32, 40, 3), (64, -16, 0), (32, 0, 3),
          (0, -32, 0), (0, 32, 0), (64, -32, 3), (-64, 32, 0), (128, -32, 3), (160, 0, 0), (96, 32, 3),
          (32, -32, 3), (-32, 32, 0), (32, 32, 3), (32, -64, 3), (32, 0, 0), (-64, 64, 3), (32, -32, 0),
          (32, 32, 3), (32, -32, 3), (0, -32, 0), (0, 32, 0), (64, 32, 3), (32, -64, 3), (32, 0, 0),
          (-64, 64, 3), (32, -32, 0), (32, 16, 3), (96, -48, 3), (128, 0, 0), (160, 32, 3), (0, -32, 0),
          (0, 32, 0), (64, 0, 3), (224, -32, 3), (-32, 48, 3), (-64, -32, 3), (96, -16, 0), (96, 64, 3),
          (64, -40, 3), (-16, -24, 0), (64, 96, 3), (3, -7, 3), (5, 5, 3), (3, -6, 3), (5, 8, 3), (0, -96, 0),
          (0, 96, 0), (64, -96, 3), (96, 64, 3), (64, -64, 3), (64, 0, 0), (-128, 64, 3), (64, -32, 0),
          (64, 32, 3), (128, -64, 3), (-64, 32, 0), (64, 16, 3), (32, -48, 3), (-32, 48, 0), (96, -48, 3),
          (192, 0, 0), (224, 32, 3), (64, -32, 3), (-32, 16, 0), (64, 48, 3), (0, -64, 0)],
 'MTNSEG': [[(0, 64, 0), (32, -32, 3), (-16, -8, 0), (80, 40, 3), (32, 0, 3), (32, -32, 3), (-64, 32, 3),
             (0, -64, 0), (128, 64, 3), (64, -64, 3), (32, 0, 0), (-64, 32, 3), (-32, 32, 0), (64, -32, 3),
             (64, -16, 3), (96, -16, 3), (48, 160, 0), (5, -12, 7), (0, -12, 7), (-8, -12, 7), (-12, -12, 7),
             (-12, -3, 7), (-12, 3, 7), (12, -9, 7), (12, -3, 7), (12, 3, 7), (12, 9, 7), (6, 12, 7),
             (0, 12, 7), (-4, 12, 7), (-11, 12, 7), (-13, 3, 2), (-15, -4, 2), (-10, -8, 2), (-5, -11, 2),
             (-2, -13, 2), (6, -15, 2), (27, 3, 0), (0, -4, 5), (6, -1, 5), (6, 11, 5), (-4, -1, 5),
             (-1, 2, 5), (18, 20, 0), (-3, -3, 5), (1, 3, 5), (-3, 1, 5), (0, 6, 5), (1, 1, 2),
             (7, -150, 0)],
            [(32, 0, 0), (64, 48, 3), (32, -48, 3), (-32, 48, 0), (32, -16, 3), (64, 32, 3), (160, -64, 3),
             (-128, 32, 3), (-32, 32, 3), (-64, -32, 0), (96, -32, 3), (128, 0, 0), (160, 32, 3),
             (0, -32, 0)],
            [(0, 32, 0), (64, 0, 3), (0, -32, 0), (-64, 32, 3), (64, 0, 0), (64, 32, 3), (-64, -32, 0),
             (96, -32, 3), (32, 32, 3), (-64, 32, 3), (32, 0, 3), (32, -32, 3), (32, 16, 3), (32, -16, 3),
             (-96, -32, 3), (96, 32, 0), (64, 32, 3), (64, -32, 3), (-96, 16, 0), (32, -16, 3), (32, 16, 3),
             (0, -48, 0), (96, 8, 3), (-64, 24, 3), (32, 16, 3), (64, -16, 3), (-32, -24, 3), (-32, 40, 3),
             (64, -16, 0), (32, 0, 3), (0, -32, 0)],
            [(0, 32, 0), (64, -32, 3), (-64, 32, 0), (128, -32, 3), (160, 0, 0), (96, 32, 3), (32, -32, 3),
             (-32, 32, 0), (32, 32, 3), (32, -64, 3), (32, 0, 0), (-64, 64, 3), (32, -32, 0), (32, 32, 3),
             (32, -32, 3), (0, -32, 0)],
            [(0, 32, 0), (64, 32, 3), (32, -64, 3), (32, 0, 0), (-64, 64, 3), (32, -32, 0), (32, 16, 3),
             (96, -48, 3), (128, 0, 0), (160, 32, 3), (0, -32, 0)],
            [(0, 32, 0), (64, 0, 3), (224, -32, 3), (-32, 48, 3), (-64, -32, 3), (96, -16, 0), (96, 64, 3),
             (64, -40, 3), (-16, -24, 0), (64, 96, 3), (3, -7, 3), (5, 5, 3), (3, -6, 3), (5, 8, 3),
             (0, -96, 0)],
            [(0, 96, 0), (64, -96, 3), (96, 64, 3), (64, -64, 3), (64, 0, 0), (-128, 64, 3), (64, -32, 0),
             (64, 32, 3), (128, -64, 3), (-64, 32, 0), (64, 16, 3), (32, -48, 3), (-32, 48, 0),
             (96, -48, 3)],
            [(192, 0, 0), (224, 32, 3), (64, -32, 3), (-32, 16, 0), (64, 48, 3), (0, -64, 0)]],
 'RDRING': [('C',), (68, 316, 0), (-8, 0, 7), (-60, -60, 0), (0, -8, 7), (-60, 68, 0), (-8, 0, 7), (68, 0, 0),
            (-36, 52, 5), (36, 8, 0), (0, 8, 7), (36, -16, 0), (-36, -52, 5)],
 'TANKS': [(-6, 6, 6), (9, 3, 6), (3, 6, 6), (36, -9, 6), (-6, -6, 6), (-36, 0, 6), (18, 12, 0), (21, 0, 6),
           (0, -3, 6), (-9, 0, 6), (27, -9, 0)],
 'XCROS1': [('C',), (0, -175, 0), (0, 100, 7), (0, 40, 3), (-35, 0, 0), (-40, -40, 7), (150, 0, 7),
            (-40, 40, 7), (0, 70, 0), (40, 40, 7), (-150, 0, 7), (40, -40, 7), (35, 0, 0), (0, 40, 3),
            (0, 100, 7)],
 'XCROSS': [('C',), (0, -175, 0), (0, 100, 3), (-75, 25, 0), (0, -25, 3), (150, 0, 3), (0, 25, 3),
            (0, 100, 0), (0, 25, 3), (-150, 0, 3), (0, -25, 3), (75, 25, 0), (0, 100, 3)]}
MODELS = {'CUB': ([(-128, -128, -80), (-128, 128, -80), (128, 128, -80), (128, -128, -80), (-128, -128, 80),
          (-128, 128, 80), (128, 128, 80), (128, -128, 80)],
         [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 5), (5, 6), (6, 7), (7, 4), (5, 1), (2, 6), (7, 3)]),
 'EX0': ([(-147, -86, -37), (-147, 86, -37), (147, 86, -122), (147, -86, -122), (-68, -42, -12),
          (-68, 42, -12), (0, -10, -47), (0, 10, -47), (45, 10, -72), (45, -10, -72), (270, 10, -125),
          (260, 10, -134), (270, -10, -125), (260, -10, -134)],
         [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 5), (5, 1), (5, 2), (3, 4), (6, 12), (12, 10), (10, 7),
          (7, 6), (6, 9), (9, 8), (8, 11), (11, 13), (13, 9), (7, 8), (11, 10), (12, 13)]),
 'EX1': ([(55, 0, -68), (-80, -20, -47), (85, 20, -24), (-46, 0, -89), (-31, -20, -64), (-29, 20, -52)],
         [(0, 3), (3, 5), (5, 2), (2, 0), (0, 1), (1, 2), (2, 5), (5, 4), (4, 1), (4, 3)]),
 'EX2': ([(-60, -30, -80), (-94, 16, -70), (180, 40, -96), (160, -30, -80), (-10, -16, -20), (0, 8, -15),
          (14, -40, -50), (30, 50, -60)],
         [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 6), (6, 7), (7, 5), (5, 4), (5, 1), (7, 2), (3, 6)]),
 'EX3': ([(-20, -3, -72), (118, 28, -108), (200, -11, 3), (22, -4, -67)],
         [(0, 2), (2, 1), (1, 3), (3, 0), (0, 1), (2, 3)]),
 'EX4': ([(-75, -18, -46), (-58, -42, -46), (-58, -68, -59), (-75, -68, -71), (-24, 42, -51), (10, 3, -48),
          (10, -65, -81), (-24, -58, -101)],
         [(1, 2), (2, 3), (3, 7), (7, 6), (6, 5), (5, 4), (4, 0), (0, 1), (1, 5), (6, 2)]),
 'PYR': ([(-128, -128, -80), (-128, 128, -80), (128, 128, -80), (128, -128, -80), (0, 0, 80)],
         [(0, 4), (4, 1), (1, 0), (0, 3), (3, 4), (4, 2), (2, 3), (2, 1)]),
 'R2D': ([(-96, 36, 0), (-96, 18, 12), (-96, -18, 12), (-96, -36, 0), (-96, -18, -12), (-96, 18, -12),
          (-24, 72, 0), (-24, 48, 24), (-24, -48, 24), (-24, -72, 0), (-24, -48, -24), (-24, 48, -24),
          (288, 0, 0), (348, 0, 0), (-36, -36, -42), (-36, 36, -42), (36, 36, -42), (36, -36, -42),
          (-12, -12, -23), (-12, 12, -23), (12, 12, -21), (12, -12, -21), (-24, 0, 24), (132, 18, 12),
          (132, -18, 12), (12, 0, 36)],
         [(13, 12), (12, 6), (6, 0), (0, 1), (1, 7), (7, 8), (8, 9), (9, 10), (10, 11), (11, 6), (6, 7),
          (7, 12), (12, 8), (8, 2), (2, 3), (3, 9), (9, 12), (12, 10), (10, 4), (4, 5), (5, 11), (11, 12),
          (24, 23), (23, 22), (22, 24), (24, 25), (25, 23), (25, 22), (1, 2), (3, 4), (5, 0), (18, 19),
          (19, 20), (20, 21), (21, 18), (18, 14), (14, 15), (15, 16), (16, 17), (17, 14), (15, 19), (20, 16),
          (17, 21)]),
 'RDR': ([(0, -20, 20), (20, -40, 25), (20, -40, 30), (0, -20, 35), (0, 20, 20), (20, 40, 25), (20, 40, 30),
          (0, 20, 35)],
         [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 5), (5, 6), (6, 7), (7, 4), (7, 3)]),
 'ROCK1': ([(-200, -200, -80), (-200, 200, -80), (200, 200, -80), (200, -200, -80), (0, 0, 100)],
           [(0, 4), (4, 1), (1, 0), (0, 3), (3, 4), (4, 2), (2, 3), (2, 1)]),
 'ROCK2': ([(-160, -160, -80), (-160, 160, -80), (160, 160, -80), (160, -160, -80), (-160, -160, -10),
            (-160, 160, -10), (160, 160, -10), (160, -160, -10)],
           [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (4, 5), (5, 6), (6, 7), (7, 4), (5, 1), (2, 6), (7, 3)]),
 'SAU': ([(-60, 0, -10), (-40, 40, -10), (0, 60, -10), (40, 40, -10), (60, 0, -10), (40, -40, -10),
          (0, -60, -10), (-40, -40, -10), (-240, 0, 20), (-170, 170, 20), (0, 240, 20), (170, 170, 20),
          (240, 0, 20), (170, -170, 20), (0, -240, 20), (-170, -170, 20), (0, 0, 70)],
         [(16, 8), (8, 9), (9, 16), (16, 10), (10, 11), (11, 16), (16, 12), (12, 13), (13, 16), (16, 14),
          (14, 15), (15, 16), (0, 7), (7, 15), (15, 8), (8, 0), (0, 1), (1, 9), (9, 10), (10, 2), (2, 3),
          (3, 11), (11, 12), (12, 4), (4, 5), (5, 13), (13, 14), (14, 6), (6, 7), (6, 5), (4, 3), (2, 1)]),
 'SHL': ([(-10, -10, -12), (-10, -10, -2), (-10, 10, -2), (-10, 10, -12), (20, 0, -7)],
         [(0, 4), (4, 1), (1, 0), (0, 3), (3, 4), (4, 2), (2, 3), (2, 1)]),
 'TNK': ([(-184, -128, -80), (-184, 128, -80), (242, 128, -80), (242, -128, -80), (-256, -142, -52),
          (-256, 142, -52), (312, 142, -52), (312, -142, -52), (-170, -86, -30), (-170, 86, -30),
          (170, 86, -30), (170, -86, -30), (-128, -42, 12), (-128, 42, 12), (-32, -10, -2), (-32, 10, -2),
          (32, 10, -12), (32, -10, -12), (280, 10, -2), (280, 10, -12), (280, -10, -2), (280, -10, -12),
          (-128, 0, 12), (-128, 0, 20)],
         [(23, 22), (12, 13), (14, 20), (20, 18), (18, 15), (15, 14), (14, 17), (17, 16), (16, 19), (19, 21),
          (21, 17), (15, 16), (19, 18), (20, 21), (3, 0), (0, 4), (4, 7), (7, 6), (6, 2), (2, 3), (3, 7),
          (7, 11), (11, 10), (10, 6), (6, 5), (5, 9), (9, 10), (10, 13), (13, 9), (9, 8), (8, 11), (11, 12),
          (12, 8), (8, 4), (4, 5), (5, 1), (1, 2), (1, 0)]),
 'TR7': ([(364, 92, -80), (-114, 138, -80), (-114, -138, -80), (364, -92, -80), (-114, 114, -23),
          (-114, -114, -23), (274, 0, -69), (-68, 68, -29), (-114, 68, -23), (-114, -68, -23),
          (-68, -68, -29), (-68, 46, 11), (-114, 46, 11), (-114, -46, 11), (-68, -46, 11), (320, 22, -11),
          (22, 22, -11), (22, -22, -11), (320, -22, -11), (320, 22, 0), (-22, 22, 0), (-22, -22, 0),
          (320, -22, 0), (-114, 0, 11), (-114, 0, 69)],
         [(0, 1), (1, 4), (4, 0), (0, 3), (3, 2), (2, 5), (5, 3), (2, 1), (4, 5), (9, 10), (10, 6), (6, 14),
          (14, 13), (13, 9), (9, 8), (8, 7), (7, 6), (6, 11), (11, 12), (12, 8), (12, 13), (14, 11), (19, 22),
          (22, 21), (21, 20), (20, 16), (16, 15), (15, 18), (18, 17), (17, 16), (15, 19), (22, 18), (17, 21),
          (23, 24)])}
TREADS = {'TREAD4': [(-256, -142, -52), (-256, 142, -52), (-230, -137, -62), (-230, 137, -62), (-204, -133, -72),
            (-204, 133, -72)],
 'TREAD5': [(-250, -141, -54), (-250, 141, -54), (-224, -136, -64), (-224, 136, -64), (-198, -132, -74),
            (-198, 132, -74)],
 'TREAD6': [(-243, -139, -57), (-243, 139, -57), (-217, -135, -67), (-217, 135, -67), (-191, -130, -77),
            (-191, 130, -77)],
 'TREAD7': [(-237, -138, -59), (-237, 138, -59), (-211, -134, -69), (-211, 134, -69), (-184, -129, -79),
            (-184, 129, -79)],
 'TREAD8': [(312, -142, -52), (312, 142, -52), (288, -137, -62), (288, 137, -62), (264, -133, -72),
            (264, 133, -72)],
 'TREAD9': [(306, -141, -54), (306, 141, -54), (282, -136, -64), (282, 136, -64), (258, -132, -74),
            (258, 132, -74)],
 'TREADA': [(300, -139, -57), (300, 139, -57), (276, -135, -67), (276, 135, -67), (252, -130, -77),
            (252, 130, -77)],
 'TREADB': [(294, -138, -59), (294, 138, -59), (270, -134, -69), (270, 134, -69), (246, -129, -79),
            (246, 129, -79)]}
TREAD_EDGES = [(0, 1), (2, 3), (4, 5)]
EXHAUST = [[(0, 13, -45), (9, 9, -45), (13, 0, -45), (9, -9, -45), (0, -13, -45), (-9, -9, -45), (-13, 0, -45),
  (-9, 9, -45)],
 [(0, 25, -50), (18, 18, -50), (25, 0, -50), (18, -18, -50), (0, -25, -50), (-18, -18, -50), (-25, 0, -50),
  (-18, 18, -50)],
 [(0, 38, -55), (27, 27, -55), (38, 0, -55), (27, -27, -55), (0, -38, -55), (-27, -27, -55), (-38, 0, -55),
  (-27, 27, -55)],
 [(0, 50, -60), (36, 36, -60), (50, 0, -60), (36, -36, -60), (0, -50, -60), (-36, -36, -60), (-50, 0, -60),
  (-36, 36, -60)],
 [(0, 63, -65), (44, 44, -65), (63, 0, -65), (44, -44, -65), (0, -63, -65), (-44, -44, -65), (-63, 0, -65),
  (-44, 44, -65)],
 [(0, 75, -70), (53, 53, -70), (75, 0, -70), (53, -53, -70), (0, -75, -70), (-53, -53, -70), (-75, 0, -70),
  (-53, 53, -70)],
 [(0, 88, -75), (66, 66, -75), (88, 0, -75), (66, -66, -75), (0, -88, -75), (-66, -66, -75), (-88, 0, -75),
  (-66, 66, -75)],
 [(0, 100, -80), (71, 71, -80), (100, 0, -80), (71, -71, -80), (0, -100, -80), (-71, -71, -80),
  (-100, 0, -80), (-71, 71, -80)]]
PLAYFIELD = [(8192, 8192, 12, 0), (0, 16384, 15, 16), (0, 32768, 12, 32), (16384, 32768, 15, 64), (32768, 32768, 12, 24),
 (32768, 16384, 0, 40), (32768, 0, 1, 48), (16384, 0, 0, 56), (12288, 20480, 1, 64), (49152, 6144, 15, 72),
 (63232, 17408, 12, 80), (51200, 16384, 0, 88), (55296, 35840, 1, 96), (37888, 3072, 15, 104),
 (38912, 59392, 12, 112), (59392, 58368, 0, 120), (28672, 39936, 1, 128), (30720, 52224, 15, 136),
 (16384, 46080, 12, 144), (9216, 48128, 0, 152), (11264, 62464, 1, 160)]
LOGO = {'BATBL': ([(56, 1280, 8), (56, 960, 8), (168, 800, 22), (280, 880, 36), (400, 800, 50), (512, 960, 64),
            (512, 1280, 64), (168, 1120, 22), (168, 1040, 22), (280, 1120, 36), (400, 1040, 50),
            (400, 1120, 50), (56, 800, 8), (8, 560, 22), (56, 320, 8), (512, 560, 64), (224, 640, 28),
            (256, 560, 32), (224, 480, 28), (336, 560, 42)],
           [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 0), (7, 8), (8, 9), (9, 10), (10, 11),
            (11, 7), (12, 13), (13, 14), (14, 15), (15, 12), (16, 17), (17, 18), (18, 19), (19, 16)]),
 'TLETBL': ([(56, 160, 8), (400, 80, 50), (400, -160, 50), (56, -240, 8), (400, -320, 50), (400, -560, 50),
             (56, -560, 8), (56, -960, 8), (112, -1360, 14), (168, -1120, 22), (224, -1120, 28),
             (280, -1280, 36), (336, -1120, 42), (400, -1120, 50), (456, -1360, 56), (512, -960, 64),
             (168, -720, 22), (512, -720, 64), (512, 480, 64), (400, 480, 50), (400, 240, 50)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9), (9, 10), (10, 11),
             (11, 12), (12, 13), (13, 14), (14, 15), (15, 7), (7, 16), (16, 17), (17, 18), (18, 19), (19, 20),
             (20, 0)]),
 'ZONTBL': ([(-512, 1200, -64), (-512, 560, -64), (-400, 880, -50), (-56, 560, -8), (-56, 1200, -8),
             (-168, 880, -22), (-512, 80, -64), (-56, 80, -8), (-400, 400, -50), (-400, 240, -50),
             (-168, 240, -22), (-168, 400, -22), (-512, 0, -64), (-280, -160, -36), (-512, -640, -64),
             (-456, -1040, -56), (-400, -800, -50), (-336, -800, -42), (-280, -960, -36), (-224, -800, -28),
             (-168, -800, -22), (-112, -1040, -14), (-56, -640, -8), (-280, -480, -36), (-56, 0, -8)],
            [(1, 0), (0, 5), (5, 4), (4, 3), (3, 2), (2, 1), (1, 3), (3, 7), (7, 6), (6, 1), (9, 8), (8, 11),
             (11, 10), (10, 9), (14, 22), (22, 23), (23, 24), (24, 12), (12, 13), (13, 14), (14, 15),
             (15, 16), (16, 17), (17, 18), (18, 19), (19, 20), (20, 21), (21, 22)])}
# fmt: on

# ═══════════════════════════════════════════════════════════════════════════
# §2 MATEMÁTICA FIJA — ángulos de 8 bits, mundo toroidal, distancia de la Math Box
# ═══════════════════════════════════════════════════════════════════════════

def rad(a: float) -> float:
    """Ángulo de la máquina (256 unidades = 360°) a radianes."""
    return a * math.tau / 256


def wd(d: float) -> float:
    """Diferencia toroidal mínima en un eje (aritmética de 16 bits con signo)."""
    return (d + HALF) % WORLD - HALF


def sang(a: float) -> float:
    """Ángulo con signo en (−128, 128]: la resta de bytes del 6502."""
    return (a + 128) % 256 - 128


def mb_dist(dx: float, dy: float) -> int:
    """Distancia de la Math Box: max + 3/8·min  ← mathbox.cpp, comando 0x1E."""
    a, b = int(abs(wd(dx))), int(abs(wd(dy)))
    hi, lo = (a, b) if a >= b else (b, a)
    lo >>= 2
    return hi + lo + (lo >> 1)


def track(fx: float, fy: float, tx: float, ty: float) -> int:
    """Rumbo de (fx, fy) hacia (tx, ty) en unidades de 8 bits  ← TRACK (arcotangente)."""
    return round(math.atan2(wd(ty - fy), wd(tx - fx)) * 128 / math.pi) & 0xFF


def bcd(n: int) -> int:
    """Byte BCD de los miles (0–99): el 6502 compara y resta estos bytes en binario."""
    return int(str(n % 100), 16)


def cos8(a: float) -> float:
    return SIN_SCALE * math.cos(rad(int(a) & 0xFF))


def sin8(a: float) -> float:
    return SIN_SCALE * math.sin(rad(int(a) & 0xFF))


# ═══════════════════════════════════════════════════════════════════════════
# §3 MODELOS — vértices y aristas sacados de BZMTNS.MAC y de las listas *OBJ
# ═══════════════════════════════════════════════════════════════════════════

def _scale(verts):
    return [(x * MODEL_SCALE, y * MODEL_SCALE, z * MODEL_ZSCALE) for x, y, z in verts]


MODEL = {name: (_scale(v), e) for name, (v, e) in MODELS.items()}
TREAD = {name: _scale(v) for name, v in TREADS.items()}
REAR_TREADS = ("TREAD7", "TREAD6", "TREAD5", "TREAD4")   # ← OBJPNT 4–7, «REVERSED AFTER 1 TRIAL & 1 ERROR»
FRONT_TREADS = ("TREAD8", "TREAD9", "TREADA", "TREADB")  # ← OBJPNT 8–11
EXHAUST_PTS = [_scale(v) for v in EXHAUST]
LOGO_PARTS = [(_scale(v), e) for v, e in LOGO.values()]


def _panorama():
    """Polilínea absoluta de las 8 cordilleras (512 unidades cada una)  ← MTN0–MTN7."""
    segs = []
    for k, routine in enumerate(VEC2D["MTNSEG"]):
        x, y = 512 * k, 0
        for dx, dy, b in routine:
            if b:
                segs.append((x, y, x + dx, y + dy, b))
            x, y = x + dx, y + dy
    return segs


PANORAMA = _panorama()

# ═══════════════════════════════════════════════════════════════════════════
# §4 CAMPO — los 21 obstáculos de PTBLX1/PTBLY1/PTBLO1
# ═══════════════════════════════════════════════════════════════════════════

FIELD = PLAYFIELD  # (x, y, tipo, orientación)

# ═══════════════════════════════════════════════════════════════════════════
# §5 ENTIDADES
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class Body:
    x: float = 0.0
    y: float = 0.0
    a: float = 0.0          # TANGLE + LANGLE: medio paso de resolución
    px: float = 0.0
    py: float = 0.0
    pa: float = 0.0
    tread: int = 0          # ← TRDCTR

    def snap(self) -> None:
        self.px, self.py, self.pa = self.x, self.y, self.a

    def teleport(self, x: float, y: float) -> None:
        self.x, self.y = x % WORLD, y % WORLD
        self.px, self.py = self.x, self.y


@dataclass(slots=True)
class Shell:
    ct: int = 0             # ← FIRECT: >0 en vuelo, <0 explotando, 0 libre
    x: float = 0.0
    y: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    px: float = 0.0
    py: float = 0.0

    def snap(self) -> None:
        self.px, self.py = self.x, self.y


@dataclass(slots=True)
class Enemy(Body):
    kind: str = TANK
    dead: int = 0           # ← COLFLG+2: ≠0 mientras explota
    z: float = 0.0          # ← EXPOSZ+0C: altura del misil (unidades .NWORD)
    pz: float = 0.0
    objcol: int = 0         # ← OBJCOL+2
    radar: int = 0          # ← RANGLE: antena giratoria


@dataclass(slots=True)
class Piece:
    x: float
    y: float
    z: float                # unidades .NWORD
    vx: float
    vy: float
    vz: float
    a: float
    spin: float
    model: str
    px: float = 0.0
    py: float = 0.0
    pz: float = 0.0


@dataclass(slots=True)
class Saucer:
    on: bool = False        # ← SAUCER
    x: float = 0.0
    y: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    a: float = 0.0          # ← SCANGL
    timer: int = 0          # ← STIMER
    boom: int = 0           # ← SCOLFG
    px: float = 0.0
    py: float = 0.0

    def snap(self) -> None:
        self.px, self.py = self.x, self.y


@dataclass(slots=True)
class Ctrl:
    """Estado de las dos palancas y botones durante un tick."""
    left: int = 0           # −1 atrás, 0 neutro, +1 adelante
    right: int = 0
    fire: bool = False
    start: bool = False
    up: bool = False        # flancos, para las iniciales
    down: bool = False
    select: bool = False


@dataclass(slots=True)
class Options:
    lives: int = 3          # 2–5  ← OPTION & 3, + 2
    bonus: int = 1          # índice de BONTBL
    missile: int = 1        # índice de MISLVL


def default_table() -> list[list]:
    # La firma «EDR» de Ed Rotberg aparece tal cual en BZONE.MAC (CHKS2: .ASCII /EDR/)
    names = ("EDR", "MPH", "BZN", "ATR", "AVG", "MBX", "TNK", "R2D", "UFO", "VOL")
    return [[s, n] for s, n in zip((30, 25, 22, 20, 18, 16, 14, 12, 10, 8), names)]


# ═══════════════════════════════════════════════════════════════════════════
# §6 JUEGO — IA, física y estados (sin pygame: se prueba en --selftest)
# ═══════════════════════════════════════════════════════════════════════════


class Game:
    def __init__(self, opts: Options | None = None, seed: int | None = None,
                 table: list | None = None) -> None:
        self.opts = opts or Options()
        self.rng = random.Random(seed)
        self.table = table or default_table()
        self.events: list[str] = []   # sonidos de un disparo, los consume la interfaz
        self.fx: list[tuple] = []     # efectos visuales con posición, los consume el render
        self.player = Body(x=128, y=128)
        self.pshell = Shell()
        self.enemy = Enemy()
        self.eshell = Shell()
        self.saucer = Saucer()
        self.pieces: list[Piece] = []
        self.rocks = [[0, 0, 0, 0, 0] for _ in range(NOROCK)]  # tiempo, vx, vy, x, y
        self.mode = ATTRACT
        self.gover = True
        self.score = 0          # ← HITS (miles)
        self.ekills = 0         # ← HITS+2
        self.lives = 0
        self.dead = 0           # ← COLFLG del jugador
        self.crack = 0          # ← CRACK
        self.bounce = 0         # ← BOUNCE
        self.objcol = 0         # ← OBJCOL
        self.frame = 0
        self.ticks = 0
        self.timout = 0
        self.ftimer = 0
        self.action = 0
        self.state = 0
        self.rgoal = 0
        self.skill = 0
        self.nor2d3 = -1        # ← NOR2D3: misiles de esta partida − 1
        self.eirnge = 0
        self.tdist = 0x7F
        self.pturn = 0
        self.pside = 0
        self.sangle = 0         # barrido del radar
        self.blip = 0
        self.moving = False
        self.show_table = False
        self.logo = False
        self.logox = 0
        self.logoz = 0
        self.initials = ""
        self.letter = 0
        self.ltimer = 0
        self.new_rank = -1
        self.start_logo()
        self.tankck()

    # ── utilidades ────────────────────────────────────────────────────────
    def prand(self) -> int:
        """← PRAND: el generador del POKEY, aquí determinista con --seed."""
        return self.rng.randrange(256)

    def fxe(self, kind: str, x: float, y: float, z: float = 0.0, a: float = 0.0) -> None:
        """Evento visual (explosión, chispa, fogonazo). No afecta a la lógica."""
        if len(self.fx) < 256:
            self.fx.append((kind, x, y, z, a))

    def emit(self, name: str) -> None:
        if self.mode != ATTRACT or name in ("start", "gameover"):   # la atracción es muda
            self.events.append(name)

    @property
    def hi_score(self) -> int:
        return self.table[0][0]

    def tr7(self) -> bool:
        """← TR7CHK: tras el sexto misil, todos los tanques son supertanques."""
        return self.nor2d3 >= 5

    # ── arranque de partida ───────────────────────────────────────────────
    def start_game(self) -> None:
        """← PWRON / pulsar START."""
        self.mode, self.gover = PLAY, False
        self.score = self.ekills = 0
        self.lives = self.opts.lives
        self.nor2d3 = -1
        self.timout = self.ftimer = 0
        self.crack = self.bounce = self.objcol = self.dead = 0
        self.saucer = Saucer()
        self.pshell, self.eshell = Shell(), Shell()
        self.pieces.clear()
        self.player = Body(x=128, y=128)
        self.logo = self.show_table = False
        self.enemy = Enemy()
        self.robchk()
        self.emit("start")

    def start_logo(self) -> None:
        self.logo = True
        self.logox, self.logoz = 0x400, -0x400   # ← BATINT

    # ── el tick: el bucle MAIN entero ─────────────────────────────────────
    def tick(self, c: Ctrl) -> None:
        self.ticks += 1
        for b in (self.player, self.enemy):
            b.snap()
        self.enemy.pz = self.enemy.z
        for s in (self.pshell, self.eshell, self.saucer):
            s.snap()
        for p in self.pieces:
            p.px, p.py, p.pz = p.x, p.y, p.z

        if self.mode == INITIALS:
            self.enter_initials(c)
            return
        if self.mode == ATTRACT and c.start:
            self.start_game()
            return

        self.volcno()
        self.frame = (self.frame + 1) & 0xFF
        if self.frame == 0:
            self.timout += 1
            if self.timout >= 4 and not self.crack:
                if self.mode == ATTRACT:
                    self.attract_cycle()
                elif self.enemy.kind != MISSILE:
                    self.r2d3ck()   # ← MAIN 3$: si nadie muere en ~65 s, llega un misil
        if self.ftimer < 0xFF:
            self.ftimer += 1
        self.action = (self.action - 1) & 0xFF
        if self.logo:
            self.battle_logo()

        self.rotate_explosions()
        self.dradar()
        self.compute_pturn()

        for _ in range(SUBSTEPS):
            self.colchk()
            self.shupdt()
        if self.enemy.kind == MISSILE or self.tr7():
            self.qwikck()
        self.robot()
        self.saucmv()
        self.bounce >>= 1   # ← BOUND
        self.motion(c)
        if self.crack:
            self.wnshld()

    # ── atracción ─────────────────────────────────────────────────────────
    def attract_cycle(self) -> None:
        """← MAIN 1$: tabla de récords ↔ logo + demostración."""
        self.tankck()
        self.timout = 3
        self.show_table = not self.show_table
        self.frame = 0x80 if self.show_table else 0
        if not self.show_table:
            self.start_logo()

    def battle_logo(self) -> None:
        """← BATTLE: el logotipo sube y se aleja; al acabar, BATINT."""
        self.logoz += 8
        self.logox += 0x40
        if self.logoz >= 0x200:
            self.logo = False
            self.timout, self.frame = 3, 0

    # ── volcán ────────────────────────────────────────────────────────────
    def volcno(self) -> None:
        """← VOLCNO: cinco piedras con gravedad («ESTA RUTINA PUEDE CULPARSE A RUWEN OBIN»)."""
        for r in self.rocks:
            if r[0] == 0:
                if self.prand() & 7 == 0:
                    vx = (self.prand() & 3) + 1
                    if self.prand() & 0x40:
                        vx = -vx
                    r[:] = [0x1F, vx, (self.prand() & 7) + 5, 0, 0]
                continue
            r[0] -= 1
            r[2] -= 1
            r[4] += r[2]
            r[3] += r[1]
            if r[0] == 0 or r[4] < -94:
                r[:] = [0, 0, 0, 0, 0]

    # ── radar ─────────────────────────────────────────────────────────────
    def dradar(self) -> None:
        """← DRADAR: barrido, eco, distancia (TDIST) y aviso de enemigo a tiro."""
        self.sangle = (self.sangle + 0x0B) & 0xFF
        e, p = self.enemy, self.player
        if e.dead:
            return
        bearing = track(p.x, p.y, e.x, e.y)
        g = int(sang(int(p.a) - bearing))
        if 0 <= sang(self.sangle - g) < 0x0C:
            self.blip = 0xF0
        self.tdist = mb_dist(e.x - p.x, e.y - p.y) >> 8
        if self.tdist >= RADAR_RANGE_HI:
            self.eirnge = 0
            if e.kind == MISSILE:
                if self.timout >= 4:
                    self.tankck()
                else:
                    self.r2d3ck()
        else:
            if self.blip >= 0xF0 and self.mode != INITIALS:
                self.emit("beep")
            if not self.eirnge:
                self.eirnge = 1
                if not self.gover:
                    self.emit("warn")
        if self.blip:
            self.blip = max(0, self.blip - 8)

    def compute_pturn(self) -> None:
        """← MAIN 35$: rumbo relativo del enemigo (PTURN) y su lado (SAVE2)."""
        e, p = self.enemy, self.player
        rel = int(sang(track(p.x, p.y, e.x, e.y) - int(p.a)))
        self.pside = rel
        self.pturn = abs(rel)

    # ── colisiones ────────────────────────────────────────────────────────
    def objobj(self, b: Body, is_player: bool) -> str | None:
        """← OBJOBJ: tanque contra obstáculos y tanque contra tanque."""
        missile = self.enemy.kind == MISSILE
        for ox, oy, t, _ in FIELD:
            d = mb_dist(ox - b.x, oy - b.y)
            if is_player:
                if d <= PLAYER_OBJ_RADIUS:
                    return "obj"
            else:
                if missile:
                    d = d + d // 2   # «3/4 TANK RADIUS»
                if d <= PROXTB[t]:
                    return "obj"
        if self.dead or self.enemy.dead:
            return None
        other = self.enemy if is_player else self.player
        hi = mb_dist(other.x - b.x, other.y - b.y) >> 8
        if hi <= (MISSILE_HI if missile else TANK_HI):
            return "tank"
        return None

    def hit_radius(self) -> int:
        """← SHRTCK: el blanco es más ancho visto de lado que de frente."""
        d = int(sang(2 * (int(self.player.a) - int(self.enemy.a))))
        t = abs(d) >> 2
        t = t + 0x18 if self.enemy.kind == MISSILE else t >> 1
        return t + (t >> 1) + 0x38

    def shrtck(self, shell: Shell, by_player: bool) -> bool:
        """← SHRTCK: proyectil contra el tanque contrario."""
        e = self.enemy
        target_dead = e.dead if by_player else self.dead
        if target_dead:
            return False
        tx, ty = (e.x, e.y) if by_player else (self.player.x, self.player.y)
        d = mb_dist(shell.x - tx, shell.y - ty)
        if d >= 0x400:
            return False
        if e.kind == MISSILE and e.z >= TOP:
            return False
        if self.hit_radius() < d >> 2:
            return False
        self.timout = 0
        old = self.score
        if by_player:
            e.dead = 0x20
            self.score += {MISSILE: PTS_MISSILE, SUPER: PTS_SUPER}.get(e.kind, PTS_TANK)
            self.exinit()
            self.newlif(old)
            self.emit("boom")
            self.fxe("boom", e.x, e.y, e.z * Z_SCALE if e.kind == MISSILE else 0.0)
        else:
            self.kill_player()
            self.ekills += 1
        shell.ct = -0x80
        return True

    def kill_player(self) -> None:
        self.fxe("hit", self.player.x, self.player.y)
        self.dead = 0x20
        self.crack = 2
        self.bounce = 0xFF
        self.lives -= 1
        self.emit("crash")
        if self.lives <= 0:
            self.lives = 0
            self.gover = True

    def qwikck(self) -> None:
        """← QWIKCK: comprobación rápida extra contra enemigos veloces."""
        if self.pshell.ct > 0:
            self.shrtck(self.pshell, True)

    def colchk(self) -> None:
        """← COLCHK: proyectiles contra tanque, platillo y obstáculos."""
        if self.gover and not self.crack:
            return
        for shell, by_player in ((self.eshell, False), (self.pshell, True)):
            if shell.ct <= 0:
                continue
            if self.shrtck(shell, by_player):
                continue
            if self.sauchk(shell, by_player):
                continue
            for ox, oy, t, _ in FIELD:
                d = mb_dist(shell.x - ox, shell.y - oy)
                if d < 0x400 and PRXTBL[t] >= d >> 2:
                    shell.ct = -0x60
                    self.emit("thud")
                    self.fxe("spark", shell.x, shell.y)
                    break

    def sauchk(self, shell: Shell, by_player: bool) -> bool:
        """← SAUCHK."""
        s = self.saucer
        if not s.on or s.boom:
            return False
        d = mb_dist(s.x - shell.x, s.y - shell.y)
        if d >= 0x400 or SAUCER_HIT < d >> 2:
            return False
        s.boom = 0x40
        shell.ct = -0x60
        self.emit("saucer_boom")
        self.fxe("saucer", s.x, s.y)
        if by_player:
            old = self.score
            self.score += PTS_SAUCER
            self.newlif(old)
        return True

    def shupdt(self) -> None:
        """← SHUPDT: un subpaso de los dos proyectiles."""
        for s in (self.pshell, self.eshell):
            if s.ct > 0:
                s.ct -= 1
                s.x = (s.x + s.dx) % WORLD
                s.y = (s.y + s.dy) % WORLD

    def newlif(self, old: int) -> None:
        """← NEWLIF: vida extra al superar el umbral; superbonus a los 100.000."""
        v = BONUS_BCD[self.opts.bonus]
        if not v:
            return
        if self.score >= SUPER_BONUS_K:
            if old < SUPER_BONUS_K:
                self.lives += 1
                self.gover = False
                self.emit("bonus")
            return
        if bcd(old) <= v < bcd(self.score):
            self.lives += 1
            self.gover = False
            self.emit("bonus")

    # ── explosiones ───────────────────────────────────────────────────────
    def exinit(self) -> None:
        """← EXINIT: seis piezas salen volando del enemigo."""
        e = self.enemy
        models = {MISSILE: MISSILE_DEBRIS, SUPER: SUPER_DEBRIS}.get(e.kind, TANK_DEBRIS)
        self.pieces = [
            Piece(e.x, e.y, 0.0, EXPTBX[i] * 4, EXPTBY[i] * 4, IZVEL[i], self.prand(),
                  (8 * i + 3) * (-1 if i & 1 else 1), models[i], e.x, e.y, 0.0)
            for i in range(6)
        ]

    def rotate_explosions(self) -> None:
        """← ROTATE/EXPLDE: piezas en vuelo y estallidos de proyectil."""
        if self.enemy.dead:
            for p in self.pieces:
                if p.z < 0:
                    continue
                p.x = (p.x + p.vx) % WORLD
                p.y = (p.y + p.vy) % WORLD
                p.z += p.vz * 4
                if p.vz > -0x7C:
                    p.vz -= GRAVITY
                p.a = (p.a + p.spin) % 256
            if all(p.z < 0 for p in self.pieces):
                self.pieces.clear()
                self.enemy.dead = 0
                self.robchk()
        for s in (self.pshell, self.eshell):
            if s.ct < 0:
                s.ct += 0x0F
                if s.ct >= 0:
                    s.ct = 0

    # ── aparición del enemigo ─────────────────────────────────────────────
    def robchk(self) -> None:
        """← ROBCHK: tanque o (al 50 %) misil, según la puntuación."""
        if self.gover:
            self.tankck()
            return
        if self.score < 100 and bcd(self.score) < bcd(MISLVL[self.opts.missile]):
            self.tankck()
            return
        if self.prand() & 1 == 0:
            self.tankck()
            return
        self.timout = 2
        self.r2d3ck()

    def tankck(self) -> None:
        """← TANKCK: un tanque nuevo delante del jugador."""
        e = self.enemy
        self.rgoal = self.prand()
        e.kind = SUPER if self.tr7() else TANK
        e.dead = 0
        e.z = 0
        self.action = 1
        self.timout = 1
        mask = 0x0F
        if not self.gover:
            if self.score >= 100:
                n = 7 >> 1
            else:
                d = bcd(self.score) - bcd(self.ekills)
                n = min(d, 7) >> 1 if d > 0 else 0
            for _ in range(n):
                mask = (mask << 1 | 1) & 0xFF
        self.rob1(mask, half_chance=True)

    def r2d3ck(self) -> None:
        """← R2D3CK: el misil entra desde lo alto, delante del jugador."""
        e = self.enemy
        self.nor2d3 += 1
        e.kind = MISSILE
        e.dead = 0
        e.z = STARTZ
        e.objcol = 0
        self.state = 0x80
        self.rob1(0x0F, half_chance=False)
        e.a = e.pa = self.rgoal = track(e.x, e.y, self.player.x, self.player.y)
        self.emit("missile")

    def rob1(self, mask: int, half_chance: bool) -> None:
        """← ROB1: posición de entrada en una ventana angular delante del jugador."""
        off = self.prand() & mask
        if self.prand() & 0x40 == 0:
            off = ~off & 0xFF
        ang = (int(self.player.a) + off) & 0xFF
        dist = SPAWN_DIST
        if half_chance and self.prand() & 1:
            dist /= 2
        e = self.enemy
        e.teleport(self.player.x + dist * math.cos(rad(ang)),
                   self.player.y + dist * math.sin(rad(ang)))
        e.pz = e.z
        self.eirnge = 0
        self.ftimer = 0

    # ── IA del tanque enemigo ─────────────────────────────────────────────
    def robot(self) -> None:
        """← ROBOT: máquina de estados de movimiento y disparo."""
        e = self.enemy
        if e.dead:
            return
        if e.kind == MISSILE:
            self.buzbom()
            return
        ox, oy = e.x, e.y
        e.radar = (e.radar + 0x0B) & 0xFF
        if self.state & 1:   # retirada tras chocar
            self.step(e, -1)
            e.a = (e.a + (0.5 if self.state & 2 else -0.5)) % 256
            e.tread -= 1
            if self.action == 0:
                self.state &= 0xFC
                self.rgoal = int(e.a)
                self.action = 0x34
            return
        if self.action == 0:
            self.decide()
            return
        diff = int(sang(int(e.a) - self.rgoal))
        turns = 4 if e.kind == SUPER else 2
        if abs(diff) >= self.skill:
            for _ in range(turns):
                e.a = (e.a + (0.5 if diff < 0 else -0.5)) % 256
                self.fireit()
            return
        if diff:
            e.a = (e.a + (0.5 if diff < 0 else -0.5)) % 256
        self.fireit()
        hi = mb_dist(self.player.x - e.x, self.player.y - e.y) >> 8
        if hi < (8 if e.kind == SUPER else 5):
            return
        mult = 2 if e.kind == SUPER else 1
        for again in (False, True):
            if again and int(e.a) != self.rgoal:
                break
            self.step(e, 1, mult)
            hit = self.objobj(e, False)
            if hit:
                if hit == "obj" and not self.gover:
                    self.state |= (self.prand() & 2) | 1
                    self.action = 0x30
                e.x, e.y = ox, oy
                return
            if not again:
                e.tread += 1

    def decide(self) -> None:
        """← R.40$: atacar, huir o vagar."""
        if self.gover:
            self.r_rand()
            return
        if self.ftimer == 0xFF or self.prand() & 1 == 0 or self.score >= 100:
            self.r_atck()
            return
        d = bcd(self.score) - bcd(self.ekills)
        if d == 0:
            self.r_evad()
        elif d < 0:
            self.r_rand()
        else:
            self.r_atck()

    def r_evad(self) -> None:
        e, p = self.enemy, self.player
        if self.prand() & 7 == 0:
            self.state |= 1
        else:
            self.state = 0
            self.rgoal = track(e.x, e.y, p.x, p.y) ^ 0x40
        self.action = 0x40

    def r_rand(self) -> None:
        v = self.prand() & 0x1F
        self.rgoal = (self.rgoal + v if self.prand() & 0x80 else self.rgoal - v) & 0xFF
        self.state = 0
        self.react(0)

    def r_atck(self) -> None:
        e, p = self.enemy, self.player
        self.rgoal = track(e.x, e.y, p.x, p.y)
        self.state = 0x80
        self.react(0x80)

    def react(self, a: int) -> None:
        """← REACT: tiempo de reacción (ACTION) y umbral de giro rápido (SKILL).

        Se reproduce tal cual el rev 1: en partida, SKILL oscila entre 2 y 16.
        """
        if self.gover:
            self.action = 0x30
            self.skill = 0x0A * 2
            return
        self.action = (4 - a) << 4 if a < 4 else 4
        if self.skill & 0x80:
            v = 0x0A
        else:
            v = 10 - self.skill
            if v < 0:
                v = 1
        self.skill = v * 2

    def fireit(self) -> None:
        """← FIREIT: el enemigo dispara si está alineado y tiene proyectil."""
        e, p = self.enemy, self.player
        if self.ftimer < 0x20:
            return
        if self.ftimer != 0xFF and self.score < 100 and bcd(self.score) >> 1 == 0:
            if self.pturn >= 0x20 or self.tdist >= 0x24:
                return   # con un novato, solo dispara si le ven y está cerca
        if abs(sang(track(e.x, e.y, p.x, p.y) - int(e.a))) >= 2:
            return
        if self.eshell.ct != 0 or self.dead:
            return
        self.launch(self.eshell, e)
        self.emit("efire")
        self.fxe("efire", e.x, e.y, 0.0, e.a)

    def launch(self, s: Shell, b: Body) -> None:
        s.ct = SHELL_LIFE
        s.x, s.y = b.x, b.y
        s.px, s.py = b.x, b.y
        s.dx = 2 * cos8(b.a)
        s.dy = 2 * sin8(b.a)

    def step(self, b: Body, direction: int, mult: int = 1) -> None:
        """← M.FOR / M.REV."""
        k = MOVE_STEP * mult * direction
        b.x = (b.x + k * math.cos(rad(int(b.a)))) % WORLD
        b.y = (b.y + k * math.sin(rad(int(b.a)))) % WORLD

    # ── el misil ──────────────────────────────────────────────────────────
    def buzbom(self) -> None:
        """← BUZBOM: el misil pica en zigzag hacia el jugador y salta obstáculos."""
        e, p = self.enemy, self.player
        e.tread += 1
        low = e.z < TOP
        if low and e.objcol:
            e.z += 0x100   # levita sobre el obstáculo
            return
        d = int(sang(int(p.a) - self.rgoal))
        if abs(d) < 0x40:   # «INSURE NO BACK ATTACKS»
            self.rgoal = (self.rgoal + (-2 if d >= 0 else 2)) & 0xFF
        else:
            dd = int(sang(self.rgoal - track(e.x, e.y, p.x, p.y)))
            if dd < 0:
                self.rgoal = (self.rgoal + (2 if dd >= -3 else 1)) & 0xFF
            elif dd > 0:
                self.rgoal = (self.rgoal - (1 if dd < 3 else 2)) & 0xFF
        ang = self.rgoal
        if self.nor2d3 != 0:
            if self.score >= 100:
                thr = 8
            else:
                thr = bcd(MISLVL[self.opts.missile] + 25) - bcd(self.score)
                if thr < 8:
                    thr = 8
            if thr < self.tdist:
                f = self.frame & 0x1F
                ang = ang - f if self.frame & 0x08 else ang + f
        e.a = ang & 0xFF
        ox, oy = e.x, e.y
        e.x = (e.x + MISSILE_STEP * math.cos(rad(e.a))) % WORLD
        e.y = (e.y + MISSILE_STEP * math.sin(rad(e.a))) % WORLD
        if self.pshell.ct > 0 and self.shrtck(self.pshell, True):
            return
        hit = self.objobj(e, False)
        if hit is None:
            e.objcol = 0
            if e.z > 0:
                e.z = max(0, e.z - 0x100)
            return
        if not low:
            return
        if hit == "tank":
            self.missile_strike()
            return
        e.x, e.y = ox, oy
        e.objcol += 1

    def missile_strike(self) -> None:
        """← BUZBOM 60$: el misil alcanza al jugador y estallan los dos."""
        self.enemy.dead = 0x20
        self.ekills += 1
        self.exinit()
        self.kill_player()
        self.emit("boom")
        self.fxe("boom", self.enemy.x, self.enemy.y, self.enemy.z * Z_SCALE)

    # ── el platillo ───────────────────────────────────────────────────────
    def saucmv(self) -> None:
        """← SAUCMV: deriva al azar; aparece a partir de 2.000 puntos."""
        s = self.saucer
        if s.boom:
            s.boom -= 2
            if s.boom <= 0:
                s.boom = 0
                s.timer = self.prand()
                s.on = False
            return
        if s.on:
            s.a = (s.a + 8) % 256
            if s.timer:
                s.x = (s.x + s.dx) % WORLD
                s.y = (s.y + s.dy) % WORLD
                s.timer -= 1
            else:
                self.saucer_course()
            return
        if bcd(self.score) >> 1 == 0:
            return
        if s.timer:
            s.timer -= 1
            return
        s.x = self.prand() * 256 + (s.x % 256)
        s.y = self.prand() * 256 + (s.y % 256)
        s.px, s.py = s.x, s.y
        s.on = True
        self.saucer_course()

    def saucer_course(self) -> None:
        s = self.saucer
        s.dx = (self.prand() ^ 0x80) - 0x80   # byte con signo: ±128 por tick
        s.dy = (self.prand() ^ 0x80) - 0x80
        s.timer = self.prand() >> 1

    # ── el jugador ────────────────────────────────────────────────────────
    def motion(self, c: Ctrl) -> None:
        """← MOTION: dos palancas, tabla MTAB, choque y disparo."""
        p = self.player
        self.moving = False
        if self.gover:
            if self.mode == ATTRACT and not self.logo and not self.crack:
                self.step(p, -1 if self.frame & 0x40 else 1)
                hi = int(self.enemy.x) >> 8
                if not hi & 0x80:
                    p.a = (p.a - 0.5) % 256
                elif hi & 0x40:
                    p.a = (p.a + 0.5) % 256
            return
        if self.dead:
            return
        ox, oy = p.x, p.y
        lever = (c.left, c.right)
        turn, move = {
            (1, 1): (0, 2), (-1, -1): (0, -2),
            (1, 0): (-0.5, 1), (0, 1): (0.5, 1),
            (-1, 0): (0.5, -1), (0, -1): (-0.5, -1),
            (1, -1): (-1, 0), (-1, 1): (1, 0),
        }.get(lever, (0, 0))
        p.a = (p.a + turn) % 256
        if move:
            for _ in range(abs(move)):
                self.step(p, 1 if move > 0 else -1)
            p.tread += 1 if move > 0 else -1
            if self.objobj(p, True):
                p.x, p.y = ox, oy
                if not self.objcol:
                    self.emit("boing")
                    self.bounce = 0x3F
                    self.objcol = 1
            else:
                self.objcol = 0
        elif turn:
            self.objcol = 0
        self.moving = bool(move or turn)
        if c.fire and self.pshell.ct == 0:
            self.launch(self.pshell, p)
            self.emit("fire")
            self.fxe("fire", p.x, p.y, 0.0, p.a)

    def wnshld(self) -> None:
        """← WNSHLD: el parabrisas se resquebraja; luego, vida siguiente o fin."""
        if self.crack < 32:
            self.crack += 2
            return
        if self.pshell.ct != 0:
            return
        self.crack = 0
        self.state = 0
        self.dead = 0
        self.ftimer = 0
        if self.gover:
            self.game_over()
            return
        self.action = 0x30
        self.rgoal = self.prand()
        p = self.player
        for _ in range(256):
            p.teleport(self.prand() + 256 * (self.prand() & 0x3F), self.prand() * 256 + self.prand())
            if not self.objobj(p, True):
                break
        self.objcol = self.eirnge = 0
        p.a = p.pa = float(self.prand())
        if self.enemy.kind == MISSILE:
            self.tankck()
        elif not self.enemy.dead:
            self.robchk()

    # ── fin de partida y récords ──────────────────────────────────────────
    def game_over(self) -> None:
        """← CKSCOR."""
        self.mode = ATTRACT
        self.timout, self.frame = 3, 0x80
        self.show_table = True
        self.logo = False
        rank = next((i for i, (s, _) in enumerate(self.table) if self.score > s), -1)
        if rank >= 0 and self.score > 0:
            self.mode = INITIALS
            self.new_rank = rank
            self.initials, self.letter, self.ltimer = "", 0, 0
        self.emit("gameover")

    def enter_initials(self, c: Ctrl) -> None:
        """← HISCRE: tres iniciales, letra a letra."""
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ "
        self.ltimer += 1
        self.frame = (self.frame + 1) & 0xFF
        if c.up:
            self.letter = (self.letter + 1) % len(alphabet)
        if c.down:
            self.letter = (self.letter - 1) % len(alphabet)
        if c.select:
            self.initials += alphabet[self.letter]
            self.letter = 0
            self.ltimer = 0
            self.emit("beep")
        if len(self.initials) >= 3 or self.ltimer > int(TICK_HZ * 30):
            name = (self.initials + "   ")[:3]
            self.table.insert(self.new_rank, [self.score, name])
            del self.table[10:]
            self.mode = ATTRACT
            self.show_table = True
            self.timout, self.frame = 3, 0x80
            save_table(self.table)

    # ── vista para la interfaz ────────────────────────────────────────────
    def hud_messages(self) -> list[tuple[str, int, int, float, tuple]]:
        """Textos de la pantalla en coordenadas del AVG (posiciones de ENGMSG ×4)."""
        out = []
        f = self.frame
        if self.mode == INITIALS:
            out += [("GREAT SCORE", -176, 160, 3.2, GREEN),
                    ("ENTER YOUR INITIALS", -228, 100, 2.6, GREEN),
                    ("CHANGE LETTER WITH RIGHT HAND CONTROLLER", -308, 40, 2.0, GREEN),
                    ("SELECT LETTER WITH FIRE BUTTON", -230, 0, 2.0, GREEN)]
            cur = "ABCDEFGHIJKLMNOPQRSTUVWXYZ "[self.letter] if f & 4 else "_"
            out.append(((self.initials + cur).ljust(3, "_")[:3], -36, -80, 5.0, GREEN))
            return out
        if self.show_table and self.mode == ATTRACT:
            out.append(("HIGH SCORES", -112, 160, 3.2, GREEN))
            for i, (s, n) in enumerate(self.table):
                out.append((f"{s:>4}000  {n}", -112, 110 - i * 30, 2.6, GREEN))
        e = self.enemy
        if not self.show_table and not self.logo and not e.dead and not self.crack:
            if self.tdist < RADAR_RANGE_HI and f & 2:
                out.append(("ENEMY IN RANGE", -440, 360, 2.5, RED))
            if self.pturn >= 22 and f & 2:
                side = "REAR" if self.pturn > 0x6B else ("LEFT" if self.pside >= 0 else "RIGHT")
                out.append(("ENEMY TO " + side, -440, 296, 2.5, RED))
        if self.objcol and not f & 4:
            out.append(("MOTION BLOCKED BY OBJECT", -440, 328, 2.5, RED))
        out.append((f"SCORE {self.score or '':>5}000", 128, 320, 2.5, RED))
        out.append((f"HIGH SCORE {self.hi_score or '':>4}000", 128, 280, 2.5, RED))
        if self.gover and not self.crack and not self.logo:
            if not self.show_table:
                out.append(("GAME OVER", -112, 96, 4.0, GREEN))
            if f & 8:
                out.append(("PRESS START", -136, -40 if self.show_table else 0, 4.0, GREEN))
            out.append(("@ 1980 ATARI", -96, -330, 2.5, GREEN))
        return out


def load_table() -> list:
    try:
        data = json.loads(HISCORE_FILE.read_text())
        table = [[int(s), str(n)[:3]] for s, n in data][:10]
        if len(table) == 10:
            return table
    except (OSError, ValueError, TypeError):
        pass
    return default_table()


def save_table(table: list) -> None:
    try:
        HISCORE_FILE.write_text(json.dumps(table))
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# §7 RENDER — proyección, recorte y dibujo vectorial (pygame-ce)
# ═══════════════════════════════════════════════════════════════════════════

# Tipografía vectorial propia (rejilla 4×6). La original vive en VGAN, que no se publicó.
FONT = {
    "A": "00 04 26 44 40|03 43", "B": "00 06 36 45 44 33 03|33 42 41 30 00",
    "C": "40 00 06 46", "D": "00 06 26 44 42 20 00", "E": "40 00 06 46|03 33",
    "F": "00 06 46|03 33", "G": "46 06 00 40 43 23", "H": "00 06|40 46|03 43",
    "I": "00 40|20 26|06 46", "J": "03 00 40 46", "K": "00 06|46 03 40",
    "L": "06 00 40", "M": "00 06 23 46 40", "N": "00 06 40 46", "O": "00 06 46 40 00",
    "P": "00 06 46 43 03", "Q": "00 06 46 42 20 00|22 40", "R": "00 06 46 43 03 40",
    "S": "00 40 43 03 06 46", "T": "06 46|20 26", "U": "06 00 40 46", "V": "06 20 46",
    "W": "06 00 23 40 46", "X": "00 46|06 40", "Y": "06 23 46|23 20", "Z": "06 46 00 40",
    "0": "00 06 46 40 00", "1": "20 26", "2": "06 46 43 03 00 40", "3": "00 40 46 06|03 43",
    "4": "06 03 43|46 40", "5": "00 40 43 03 06 46", "6": "06 00 40 43 03",
    "7": "06 46 40", "8": "00 40 46 06 00|03 43", "9": "40 46 06 03 43",
    "-": "03 43", ".": "20 21", "_": "00 40", " ": "",
    "@": "10 30 41 45 36 16 05 01 10|32 22 24 34",
}
GLYPHS = {
    ch: [[(int(p[0]), int(p[1])) for p in poly.split()] for poly in spec.split("|") if poly]
    for ch, spec in FONT.items()
}


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_wrap(a: float, b: float, t: float, period: float) -> float:
    d = (b - a + period / 2) % period - period / 2
    return (a + d * t) % period


class Renderer:
    """Render vectorial en coordenadas del AVG: x a la derecha, y hacia arriba, origen al centro.

    Es la base de los tres aspectos: «1980» (sin brillo), «vector» (brillo de fósforo)
    y «2026» (subclase Renderer2026). Escala sola a cualquier resolución.
    """

    solid = False   # rellenar los objetos para que tapen lo que hay detrás

    def __init__(self, pg, screen, glow: bool) -> None:
        self.pg = pg
        self.glow = glow
        self.pal = {k: GREEN for k in ("field", "enemy", "shell", "eshell", "missile", "saucer",
                                        "debris", "logo", "mountain", "moon", "sight", "crack",
                                        "text", "ember")}
        self.pal["hud"] = RED
        self.resize(screen)

    def resize(self, screen) -> None:
        self.screen = self.target = screen
        w, h = screen.get_size()
        self.w, self.h = w, h
        self.s = h / SCREEN_H
        self.ox, self.oy = w / 2, h / 2
        self.half_w = w / (2 * self.s)
        self.lw = 1
        self.clip3d = self.pg.Rect(0, int(self.oy - VIEW_TOP * self.s), w, h)

    # ── primitivas ────────────────────────────────────────────────────────
    @staticmethod
    def shade(color, k: float):
        k = max(0.0, min(1.0, k))
        return (int(color[0] * k), int(color[1] * k), int(color[2] * k))

    def px(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + x * self.s, self.oy - y * self.s

    def line(self, x0, y0, x1, y1, color, k=1.0) -> None:
        a, b = self.px(x0, y0), self.px(x1, y1)
        if self.glow:
            self.pg.draw.line(self.target, self.shade(color, k * 0.22), a, b, 3)
        self.pg.draw.aaline(self.target, self.shade(color, k), a, b, self.lw)

    def dot(self, x, y, color, k=1.0, r=2) -> None:
        cx, cy = self.px(x, y)
        self.pg.draw.circle(self.target, self.shade(color, k), (int(cx), int(cy)), max(1, int(r * self.s)))

    def vectors(self, routine, x, y, color, scale=1.0, k=1.0):
        """Intérprete de listas VCTR del AVG: (dx, dy, brillo 0–7) y ('C',) = centro."""
        for item in routine:
            if item[0] == "C":
                x, y = 0.0, 0.0
                continue
            dx, dy, b = item
            nx, ny = x + dx * scale, y + dy * scale
            if b:
                if dx == 0 and dy == 0:
                    self.dot(x, y, color, k * b / 7)
                else:
                    self.line(x, y, nx, ny, color, k * min(1.0, b / 6))
            x, y = nx, ny
        return x, y

    def text(self, s: str, x: float, y: float, size: float, color) -> None:
        for ch in s.upper():
            for poly in GLYPHS.get(ch, ()):
                for (ax, ay), (bx, by) in zip(poly, poly[1:]):
                    self.line(x + ax * size, y + ay * size, x + bx * size, y + by * size, color)
                if len(poly) == 2 and poly[0] == poly[1]:
                    self.dot(x + poly[0][0] * size, y + poly[0][1] * size, color)
            x += 6 * size

    # ── cámara y proyección ───────────────────────────────────────────────
    def set_camera(self, x, y, a, bounce) -> None:
        self.cx, self.cy, self.ca = x, y, a
        self.cc, self.cs = math.cos(rad(a)), math.sin(rad(a))
        self.zoff = -bounce * Z_SCALE   # ← KLUDGE: «SBC BOUNCE»

    def to_camera(self, x, y):
        dx, dy = wd(x - self.cx), wd(y - self.cy)
        return dx * self.cc + dy * self.cs, -dx * self.cs + dy * self.cc

    def visible(self, fx, fy) -> bool:
        """← ROTPNT: delante, ni muy cerca ni muy lejos, y dentro de 45°."""
        return NEAR_CULL <= fx < FAR_CULL and abs(fy) < fx

    def project(self, x, y, z) -> tuple[float, float]:
        return -y * FOCAL / x, z * FOCAL / x

    def seg3(self, p, q, color, k) -> None:
        (x0, y0, z0), (x1, y1, z1) = p, q
        if x0 < NEAR_CLIP and x1 < NEAR_CLIP:
            return
        if x0 < NEAR_CLIP or x1 < NEAR_CLIP:
            t = (NEAR_CLIP - x0) / (x1 - x0)
            xm, ym, zm = NEAR_CLIP, y0 + (y1 - y0) * t, z0 + (z1 - z0) * t
            if x0 < NEAR_CLIP:
                x0, y0, z0 = xm, ym, zm
            else:
                x1, y1, z1 = xm, ym, zm
        self.line(-y0 * FOCAL / x0, z0 * FOCAL / x0, -y1 * FOCAL / x1, z1 * FOCAL / x1, color, k)

    def model(self, verts, edges, fx, fy, fz, rel, color, k) -> list:
        c, s = math.cos(rad(rel)), math.sin(rad(rel))
        z0 = fz + self.zoff
        pts = [(fx + vx * c - vy * s, fy + vx * s + vy * c, vz + z0) for vx, vy, vz in verts]
        if self.solid:
            self.fill_hull(pts)
        for a, b in edges:
            self.seg3(pts[a], pts[b], color, k)
        return pts

    def fill_hull(self, pts) -> None:
        """Silueta convexa rellena: el objeto tapa lo que tiene detrás (pintor, de lejos a cerca)."""
        if any(x < NEAR_CLIP * 4 for x, _, _ in pts):
            return
        scr = sorted({(int(self.ox - y * FOCAL / x * self.s), int(self.oy - z * FOCAL / x * self.s))
                      for x, y, z in pts})
        if len(scr) < 3:
            return
        hull = convex_hull(scr)
        if len(hull) >= 3:
            self.pg.draw.polygon(self.target, self.fill_color, hull)

    def depth_k(self, fx: float, base: int = 0xA0) -> float:
        """← DRAW D.SBRT: el brillo cae con la distancia (DQUE), con suelo en 0x30."""
        b = base - ((int(2 * fx) >> 8) & 0xF0)
        return max(0x30, b) / 0xA0

    def queue(self, items, name, wx, wy, wa, color, z=0.0, base=0xA0, extra=None) -> None:
        """Encola un modelo visible con su profundidad, para pintarlo de lejos a cerca."""
        fx, fy = self.to_camera(wx, wy)
        if not self.visible(fx, fy):
            return
        verts, edges = MODEL[name]

        def paint():
            self.model(verts, edges, fx, fy, z, wa - self.ca, color, self.depth_k(fx, base))
            if extra:
                extra()
        items.append((fx, paint))

    # ── la escena ─────────────────────────────────────────────────────────
    def draw(self, g: Game, t: float) -> None:
        p = g.player
        cam_a = lerp_wrap(p.pa, p.a, t, 256)
        self.set_camera(lerp_wrap(p.px, p.x, t, WORLD), lerp_wrap(p.py, p.y, t, WORLD), cam_a, g.bounce)
        self.begin(g, t)
        world = not (g.show_table and g.mode == ATTRACT) and g.mode != INITIALS
        if world:
            self.target.set_clip(self.clip3d)
            self.sky(g, cam_a)
            self.mountains(g, cam_a)
            self.ground(g)
            self.scene(g, t)
            self.target.set_clip(None)
            if not g.logo:
                self.crosshair(g)
            if g.crack:
                self.cracks(g.crack)
        self.radar(g)
        self.lives(g)
        for s, x, y, size, color in g.hud_messages():
            self.text(s, x, y, size, self.pal["hud"] if color == RED else self.pal["text"])
        self.end(g)

    def begin(self, g: Game, t: float) -> None:
        self.target.fill((0, 0, 0))

    def end(self, g: Game) -> None:
        pass

    def sky(self, g: Game, a: float) -> None:
        pass

    def ground(self, g: Game) -> None:
        pass

    def mountain_x(self, px: float, a: float) -> float:
        centre = (512 - 16 * a) % 4096
        return (px - centre + 2048) % 4096 - 2048

    def mountains(self, g: Game, a: float) -> None:
        """← MOUNTS: panorama de 4096 unidades, 16 por unidad de ángulo."""
        drop = g.bounce >> 4
        hw = self.half_w + 40
        self.line(-hw, -drop, hw, -drop, self.pal["mountain"], 3 / 6)
        for x0, y0, x1, y1, b in PANORAMA:
            sx = self.mountain_x(x0, a)
            if -hw < sx < hw:
                color = self.pal["mountain"] if b == 3 else self.pal["moon"]
                self.line(sx, y0 - drop, sx + x1 - x0, y1 - drop, color, min(1.0, b / 5))
        vx = self.mountain_x(VOLCANO[0], a)
        if abs(vx) < hw:   # ← MOUNTS 21$: ¿se ve el volcán?
            vy = VOLCANO[1] - drop
            for timer, _, _, rx, ry in g.rocks:
                if timer:
                    self.dot(vx + rx, vy + ry, self.pal["ember"], ((timer << 3) & 0xE0) / 0xE0 + 0.3, 2)

    def scene(self, g: Game, t: float) -> None:
        pal = self.pal
        items: list = []
        for ox, oy, kind, ang in FIELD:
            self.queue(items, FIELD_MODEL[kind], ox, oy, ang, pal["field"])
        e = g.enemy
        if not e.dead:
            ex, ey = lerp_wrap(e.px, e.x, t, WORLD), lerp_wrap(e.py, e.y, t, WORLD)
            ea = lerp_wrap(e.pa, e.a, t, 256)
            if e.kind == MISSILE:
                z = lerp(e.pz, e.z, t) * Z_SCALE
                self.queue(items, "R2D", ex, ey, ea, pal["missile"], z=z,
                           extra=lambda: self.exhaust_ring(e, ex, ey, z))
            elif e.kind == SUPER:
                self.queue(items, "TR7", ex, ey, ea, pal["enemy"])
            else:
                self.queue(items, "TNK", ex, ey, ea, pal["enemy"],
                           extra=lambda: (self.treads(e, ex, ey, ea), self.radar_dish(e, ex, ey, ea)))
        for piece in g.pieces:
            if piece.z >= 0:
                x, y = lerp_wrap(piece.px, piece.x, t, WORLD), lerp_wrap(piece.py, piece.y, t, WORLD)
                self.queue(items, piece.model, x, y, piece.a, pal["debris"],
                           z=lerp(piece.pz, piece.z, t) * Z_SCALE)
        for shell, owner, key in ((g.pshell, g.player, "shell"), (g.eshell, e, "eshell")):
            sx, sy = lerp_wrap(shell.px, shell.x, t, WORLD), lerp_wrap(shell.py, shell.y, t, WORLD)
            if shell.ct > 0:
                self.queue(items, "SHL", sx, sy, owner.a, pal[key], base=0xE0)
                self.shell_trail(sx, sy, key)
            elif shell.ct < 0:
                fx, _ = self.to_camera(sx, sy)
                items.append((fx, lambda sx=sx, sy=sy, ct=shell.ct: self.starburst(sx, sy, ct)))
        s = g.saucer
        if s.on:
            sx, sy = lerp_wrap(s.px, s.x, t, WORLD), lerp_wrap(s.py, s.y, t, WORLD)
            if not s.boom or s.boom & 4:
                self.queue(items, "SAU", sx, sy, s.a, pal["saucer"])
        items.sort(key=lambda it: -it[0])
        for _, paint in items:
            paint()
        if g.logo:
            self.battle_logo(g, t)
        self.particles()

    def shell_trail(self, sx, sy, key) -> None:
        pass

    def particles(self) -> None:
        pass

    def exhaust_ring(self, e: Enemy, ex, ey, z) -> None:
        """← EXHOBJ: anillo de puntos en el suelo, bajo el misil."""
        fx, fy = self.to_camera(ex, ey)
        for vx, vy, vz in EXHAUST_PTS[e.tread & 7]:
            x, y = fx + vx, fy + vy
            if x > NEAR_CLIP:
                sx, sy = self.project(x, y, vz + self.zoff)
                self.dot(sx, sy, self.pal["missile"], 0.9, 1)

    def treads(self, e: Enemy, ex, ey, ea) -> None:
        """← ROTATE: se ven las orugas delanteras o las traseras, animadas con TRDCTR."""
        rel = (ea - self.ca + 0x40) % 256
        names = FRONT_TREADS if rel >= 128 else REAR_TREADS
        fx, fy = self.to_camera(ex, ey)
        solid, self.solid = self.solid, False
        self.model(TREAD[names[e.tread & 3]], TREAD_EDGES, fx, fy, 0, ea - self.ca,
                   self.pal["enemy"], self.depth_k(fx, 0x80))
        self.solid = solid

    def radar_dish(self, e: Enemy, ex, ey, ea) -> None:
        """La antena gira sobre el mástil (vértices 22–23 de TNKTBL)."""
        mx = ex - 256 * math.cos(rad(ea))
        my = ey - 256 * math.sin(rad(ea))
        fx, fy = self.to_camera(mx, my)
        if fx > NEAR_CLIP:
            verts, edges = MODEL["RDR"]
            solid, self.solid = self.solid, False
            self.model(verts, edges, fx, fy, 0, e.radar - self.ca, self.pal["enemy"], self.depth_k(fx))
            self.solid = solid

    def starburst(self, wx, wy, ct) -> None:
        """← EXPOBJ / EXPIC: estallido de proyectil, dibujado como puntos."""
        fx, fy = self.to_camera(wx, wy)
        if fx < NEAR_CLIP * 4:
            return
        k = FOCAL / fx * (1.6 + ct / 160)
        sx, sy = -fy * FOCAL / fx, -20 * FOCAL / fx + self.zoff * FOCAL / fx
        self.vectors(VEC2D["EXPIC"], sx, sy, self.pal["shell"], max(0.15, k))

    def battle_logo(self, g: Game, t: float) -> None:
        """← BATTLE: «BATTLE» y, cuando sube lo bastante, «ZONE»."""
        lx = lerp(g.logox - 0x40, g.logox, t) / 2
        lz = lerp(g.logoz - 8, g.logoz, t) * Z_SCALE
        parts = LOGO_PARTS if g.logoz >= -0x250 else LOGO_PARTS[:2]
        solid, self.solid = self.solid, False
        for verts, edges in parts:
            self.model(verts, edges, lx, 0, lz, 0, self.pal["logo"], 1.0)
        self.solid = solid

    # ── HUD ───────────────────────────────────────────────────────────────
    def crosshair(self, g: Game) -> None:
        """← MAIN 41$: mira normal o alineada; parpadea con el proyectil en vuelo."""
        if g.pshell.ct > 0 and g.ticks & 2:
            return
        name = "XCROS1" if g.pturn < 2 and not g.enemy.dead else "XCROSS"
        self.vectors(VEC2D[name], 0, 0, self.pal["sight"])

    def cracks(self, crack: int) -> None:
        """← WNSHLD: hasta 8 grietas, una más por tick."""
        x = y = 0.0
        for routine in VEC2D["CRACKS"][: min(8, crack // 2)]:
            x, y = self.vectors(routine, x, y, self.pal["crack"])

    def radar(self, g: Game) -> None:
        """← DRADAR / RDRING: barrido, marcas, cuña de visión y eco."""
        cx, cy = 0, 316
        hud = self.pal["hud"]
        self.radar_back(g, cx, cy)
        self.vectors(VEC2D["RDRING"], 0, 0, hud)
        s = rad(g.sangle)
        self.line(cx, cy, cx + 63 * math.sin(s), cy + 63 * math.cos(s), hud, 0.9)
        e, p = g.enemy, g.player
        if g.blip and not e.dead and g.tdist < RADAR_RANGE_HI:
            gang = rad(int(p.a) - track(p.x, p.y, e.x, e.y))
            r = g.tdist / 2
            self.dot(cx + r * math.sin(gang), cy + r * math.cos(gang), hud, g.blip / 0xF0, 3)

    def radar_back(self, g: Game, cx, cy) -> None:
        pass

    def lives(self, g: Game) -> None:
        """← INFO: un tanquecito por vida."""
        x, y = 128.0, 360.0
        for _ in range(g.lives):
            x, y = self.vectors(VEC2D["TANKS"], x, y, self.pal["hud"])


def convex_hull(points: list) -> list:
    """Envolvente convexa (cadena monótona de Andrew) de puntos ya ordenados."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower: list = []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


@dataclass(slots=True)
class Spark:
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    life: float
    ttl: float
    color: tuple
    size: float
    grav: float = 2600.0


class Renderer2026(Renderer):
    """Gráficos de 2026 sobre el mismo mundo de 1980.

    Resolución nativa y panorámica, objetos sólidos que se tapan entre sí, cielo con
    estrellas, montañas macizas, rejilla de suelo con niebla, partículas 3D, persistencia
    de fósforo, bloom real (desenfoque gaussiano y suma), viñeta, sacudidas de cámara y
    aberración cromática al recibir un impacto. Todo sigue siendo vectorial y en un fichero.
    """

    solid = True
    GROUND_Z = -320.0          # suelo de los modelos (z = −80 en tabla × 4)

    def __init__(self, pg, screen, low: bool = False, crt: bool = False) -> None:
        self.low, self.crt = low, crt
        self.rng = random.Random(2026)
        self.sparks: list[Spark] = []
        self.sprites: dict = {}
        self.hurt = self.shake = self.flash = self.recoil = 0.0
        self.clock = 0.0
        self.last_ms = None
        star_rng = random.Random(1980)
        self.stars = [(star_rng.uniform(0, 4096), star_rng.uniform(40, 520), star_rng.uniform(0.25, 1.0),
                       star_rng.uniform(0, math.tau)) for _ in range(260)]
        super().__init__(pg, screen, glow=False)
        self.fill_color = (2, 9, 10)
        self.pal = {
            "field": (70, 235, 205), "enemy": (150, 255, 90), "shell": (210, 255, 200),
            "eshell": (255, 150, 60), "missile": (255, 90, 60), "saucer": (255, 90, 230),
            "debris": (190, 255, 120), "logo": (110, 255, 210), "mountain": (60, 200, 190),
            "moon": (225, 240, 255), "sight": (120, 255, 150), "crack": (230, 255, 240),
            "text": (130, 255, 170), "hud": (255, 70, 55), "ember": (255, 140, 40),
        }

    def resize(self, screen) -> None:
        super().resize(screen)
        pg, w, h = self.pg, self.w, self.h
        self.lw = 2 if self.s >= 1.15 else 1
        self.clip3d = pg.Rect(0, 0, w, h)
        self.S = pg.Surface((w, h)).convert()
        self.P = pg.Surface((w, h)).convert()
        self.P.fill((0, 0, 0))
        self.target = self.S
        # pirámide de bloom: 1/4, 1/8 y 1/16 de la resolución
        self.small = (max(4, w // 4), max(4, h // 4))
        self.mid = (max(2, w // 8), max(2, h // 8))
        self.tiny = (max(1, w // 16), max(1, h // 16))
        self.margin = int(40 * self.s)
        self.bg = self.make_background()
        self.scan = self.make_scanlines() if self.crt else None

    # ── superficies precalculadas ────────────────────────────────────────
    def make_background(self):
        pg, w, h, m = self.pg, self.w, self.h, self.margin
        bg = pg.Surface((w, h + 2 * m)).convert()
        hy = int(self.oy) + m
        top, mid, hor = (1, 2, 10), (6, 14, 34), (18, 48, 62)
        g0, g1 = (5, 22, 24), (0, 2, 3)
        for y in range(h + 2 * m):
            if y < hy:
                t = y / max(1, hy)
                c = [lerp(a, b, t) for a, b in zip(top, mid)] if t < 0.8 else \
                    [lerp(a, b, (t - 0.8) / 0.2) for a, b in zip(mid, hor)]
            else:
                t = min(1.0, (y - hy) / max(1, h + 2 * m - hy) * 1.6)
                c = [lerp(a, b, t ** 0.6) for a, b in zip(g0, g1)]
            pg.draw.line(bg, [int(v) for v in c], (0, y), (w, y))
        vig = self.make_vignette((w, h + 2 * m))   # viñeta horneada en el fondo: no cuesta nada por fotograma
        bg.blit(vig, (0, 0), special_flags=pg.BLEND_MULT)
        return bg

    def make_vignette(self, size):
        pg = self.pg
        sw, sh = 64, 36
        small = pg.Surface((sw, sh)).convert()
        for y in range(sh):
            for x in range(sw):
                dx, dy = (x - sw / 2 + 0.5) / (sw / 2), (y - sh / 2 + 0.5) / (sh / 2)
                v = int(255 * max(0.0, min(1.0, 1.15 - 0.45 * (dx * dx + dy * dy))))
                small.set_at((x, y), (v, v, v))
        return pg.transform.smoothscale(small, size)

    def make_scanlines(self):
        pg = self.pg
        sl = pg.Surface((self.w, self.h)).convert()
        sl.fill((255, 255, 255))
        for y in range(0, self.h, 3):
            pg.draw.line(sl, (175, 175, 175), (0, y), (self.w, y))
        return sl

    def sprite(self, color, r: int, level: int):
        """Halo radial precalculado para sumar luz (BLEND_ADD)."""
        key = (color, r, level)
        spr = self.sprites.get(key)
        if spr is None:
            pg = self.pg
            spr = pg.Surface((2 * r + 1, 2 * r + 1)).convert()
            spr.fill((0, 0, 0))
            k0 = level / 4
            for i in range(r, 0, -1):
                f = (1 - i / (r + 1)) ** 2.2 * k0
                pg.draw.circle(spr, self.shade(color, f), (r, r), i)
            if len(self.sprites) > 2000:
                self.sprites.clear()
            self.sprites[key] = spr
        return spr

    def glow_at(self, sx, sy, color, radius_px, k=1.0) -> None:
        r = max(1, min(160, int(radius_px)))
        level = max(1, min(4, int(k * 4 + 0.5)))
        spr = self.sprite(color, r, level)
        self.target.blit(spr, (int(sx) - r, int(sy) - r), special_flags=self.pg.BLEND_ADD)

    # ── primitivas con luz ────────────────────────────────────────────────
    def dot(self, x, y, color, k=1.0, r=2) -> None:
        cx, cy = self.px(x, y)
        self.glow_at(cx, cy, color, r * 3 * self.s, k)
        self.pg.draw.circle(self.target, self.shade(color, k), (int(cx), int(cy)), max(1, int(r * 0.6 * self.s)))

    def visible(self, fx, fy) -> bool:
        return NEAR_CULL <= fx < 34000 and abs(fy) < 1.7 * fx   # panorámico y con niebla, sin saltos

    def depth_k(self, fx: float, base: int = 0xA0) -> float:
        return min(1.0, 1.25 * math.exp(-fx / 15000)) * (base / 0xA0) ** 0.5

    # ── fotograma ─────────────────────────────────────────────────────────
    def begin(self, g: Game, t: float) -> None:
        now = self.pg.time.get_ticks() / 1000
        dt = 1 / 60 if self.last_ms is None else max(0.0, min(0.1, now - self.last_ms))
        self.last_ms = now
        self.dt = dt
        self.clock += dt
        self.consume_fx(g)
        for name in ("hurt", "shake", "flash", "recoil"):
            setattr(self, name, max(0.0, getattr(self, name) - dt * {"hurt": 1.2, "shake": 2.5,
                                                                    "flash": 6.0, "recoil": 7.0}[name]))
        if g.moving and not g.dead:
            self.zoff += 4 * math.sin(self.clock * 9)       # balanceo al rodar
        self.zoff += 26 * self.recoil                        # retroceso del cañón
        drop = (g.bounce >> 4) * self.s
        self.target = self.S
        self.S.blit(self.bg, (0, int(drop) - self.margin))
        self.update_sparks(dt)

    def sky(self, g: Game, a: float) -> None:
        drop = g.bounce >> 4
        for px_, py_, b, ph in self.stars:
            sx = self.mountain_x(px_, a)
            if abs(sx) < self.half_w:
                tw = 0.65 + 0.35 * math.sin(self.clock * 2.3 + ph)
                cx, cy = self.px(sx, py_ - drop)
                c = self.shade((200, 220, 255), b * tw)
                self.S.fill(c, (int(cx), int(cy), max(1, int(self.s)), max(1, int(self.s))))
        mx = self.mountain_x(482, a)   # halo de la luna
        if abs(mx) < self.half_w + 80:
            cx, cy = self.px(mx, 132 - drop)
            self.glow_at(cx, cy, (60, 80, 110), 110 * self.s, 1.0)

    def mountains(self, g: Game, a: float) -> None:
        drop = g.bounce >> 4
        hw = self.half_w + 40
        fill = (5, 22, 26)
        for x0, y0, x1, y1, b in PANORAMA:
            if b != 3:
                continue
            sx = self.mountain_x(x0, a)
            if -hw < sx < hw:
                pts = [self.px(sx, y0 - drop), self.px(sx + x1 - x0, y1 - drop),
                       self.px(sx + x1 - x0, -drop), self.px(sx, -drop)]
                self.pg.draw.polygon(self.S, fill, pts)
        super().mountains(g, a)
        hx, hy = self.px(0, -drop)
        self.pg.draw.line(self.S, (30, 110, 110), (0, hy), (self.w, hy), max(1, int(self.s)))
        vx = self.mountain_x(VOLCANO[0], a)
        if abs(vx) < hw:   # resplandor del cráter
            cx, cy = self.px(vx, VOLCANO[1] - drop)
            self.glow_at(cx, cy, (120, 50, 10), 46 * self.s, 0.5 + 0.25 * math.sin(self.clock * 3))

    def ground(self, g: Game) -> None:
        """Rejilla del suelo anclada al mundo: da sensación de avance. Se funde con la niebla."""
        spacing = 4096 if self.low else 2048
        reach = 16384
        n = reach // spacing
        z = self.GROUND_Z + self.zoff
        color = (40, 170, 160)
        ox, oy = self.cx % spacing, self.cy % spacing
        cc, cs = self.cc, self.cs
        for axis in (0, 1):
            for i in range(-n, n + 1):
                fixed = i * spacing - (ox if axis == 0 else oy)
                prev = None
                for j in range(-n // 2, n // 2 + 1):
                    run = j * spacing * 2
                    rx, ry = (fixed, run) if axis == 0 else (run, fixed)
                    fx, fy = rx * cc + ry * cs, -rx * cs + ry * cc
                    cur = (fx, fy, z)
                    if prev is not None and (fx > NEAR_CLIP or prev[0] > NEAR_CLIP):
                        d = max(prev[0], fx)
                        k = 0.55 * math.exp(-d / 6500) * min(1.0, math.hypot(rx, ry) / 900)
                        if k > 0.03:
                            self.seg3(prev, cur, color, k)
                    prev = cur

    def shell_trail(self, sx, sy, key) -> None:
        color = self.pal[key]
        for _ in range(2):
            self.sparks.append(Spark(sx + self.rng.uniform(-30, 30), sy + self.rng.uniform(-30, 30),
                                     -14 + self.rng.uniform(-8, 8), 0, 0, 0, 0.22, 0.22, color, 34, 0))

    def exhaust_ring(self, e: Enemy, ex, ey, z) -> None:
        super().exhaust_ring(e, ex, ey, z)
        a = rad(e.a)
        for _ in range(3):
            self.sparks.append(Spark(ex - 700 * math.cos(a), ey - 700 * math.sin(a), z + self.rng.uniform(-40, 40),
                                     self.rng.uniform(-300, 300), self.rng.uniform(-300, 300),
                                     self.rng.uniform(0, 300), 0.35, 0.35, (255, 120, 40), 60, 200))

    # ── partículas ────────────────────────────────────────────────────────
    def burst(self, x, y, z, n, colors, speed, size, ttl, up=0.6) -> None:
        rnd = self.rng
        for _ in range(n):
            th, ph = rnd.uniform(0, math.tau), rnd.uniform(-0.2, 1.0) * up * math.pi / 2
            v = rnd.uniform(0.25, 1.0) * speed
            life = rnd.uniform(0.5, 1.0) * ttl
            self.sparks.append(Spark(x, y, z, v * math.cos(th) * math.cos(ph), v * math.sin(th) * math.cos(ph),
                                     v * math.sin(ph) + speed * 0.3, life, life, rnd.choice(colors),
                                     size * rnd.uniform(0.6, 1.4)))

    def consume_fx(self, g: Game) -> None:
        for kind, x, y, z, a in g.fx:
            if kind == "boom":
                self.burst(x, y, z - 120, 40 if self.low else 90,
                           [(255, 230, 160), (255, 150, 50), (255, 110, 30), self.pal["enemy"]],
                           2600, 90, 1.3, up=0.9)
                self.burst(x, y, z - 100, 10, [(255, 120, 40), (120, 60, 30)], 700, 320, 0.9, up=1.0)  # bola de fuego
                self.sparks.append(Spark(x, y, z - 60, 0, 0, 0, 0.45, 0.45, (255, 200, 120), 1600, 0))
                self.shake = max(self.shake, 0.35)
            elif kind == "spark":
                self.burst(x, y, -20, 18 if self.low else 40, [(255, 220, 140), self.pal["field"]], 2200, 40, 0.6)
                self.sparks.append(Spark(x, y, -20, 0, 0, 0, 0.2, 0.2, (255, 220, 160), 500, 0))
            elif kind == "saucer":
                self.burst(x, y, 60, 120, [self.pal["saucer"], (90, 220, 255), (255, 255, 255)], 3000, 60, 1.4, up=1.0)
            elif kind == "fire":
                self.flash = 0.35
                self.recoil = 1.0
                fx, fy = math.cos(rad(a)), math.sin(rad(a))
                self.burst(x + 700 * fx, y + 700 * fy, -30, 8, [(255, 255, 220), self.pal["shell"]], 500, 22, 0.25)
            elif kind == "efire":
                fx, fy = math.cos(rad(a)), math.sin(rad(a))
                self.sparks.append(Spark(x + 620 * fx, y + 620 * fy, 0, 0, 0, 0, 0.25, 0.25, (255, 170, 80), 700, 0))
            elif kind == "hit":
                self.hurt, self.shake = 1.0, 1.0
        g.fx.clear()
        if len(self.sparks) > 1500:
            del self.sparks[: len(self.sparks) - 1500]

    def update_sparks(self, dt: float) -> None:
        alive = []
        gz = self.GROUND_Z
        for p in self.sparks:
            p.life -= dt
            if p.life <= 0:
                continue
            p.vz -= p.grav * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.z += p.vz * dt
            if p.z < gz:   # rebote en el suelo
                p.z, p.vz = gz, -p.vz * 0.35
                p.vx *= 0.6
                p.vy *= 0.6
            alive.append(p)
        self.sparks = alive

    def particles(self) -> None:
        for p in self.sparks:
            fx, fy = self.to_camera(p.x, p.y)
            if fx < NEAR_CLIP * 6 or abs(fy) > 2 * fx:
                continue
            sx, sy = self.project(fx, fy, p.z + self.zoff)
            cx, cy = self.px(sx, sy)
            k = (p.life / p.ttl) ** 0.7 * min(1.0, 1.4 * math.exp(-fx / 20000))
            self.glow_at(cx, cy, p.color, p.size * FOCAL / fx * self.s, k)

    # ── HUD ───────────────────────────────────────────────────────────────
    def radar_back(self, g: Game, cx, cy) -> None:
        pg = self.pg
        c = self.px(cx, cy)
        pg.draw.circle(self.S, (26, 3, 3), (int(c[0]), int(c[1])), int(70 * self.s))
        pg.draw.circle(self.S, (90, 14, 10), (int(c[0]), int(c[1])), int(70 * self.s), max(1, int(self.s)))
        for i in range(1, 10):   # estela del barrido
            s = rad(g.sangle - i * 2.2)
            self.line(cx, cy, cx + 63 * math.sin(s), cy + 63 * math.cos(s), self.pal["hud"], 0.45 * (1 - i / 10))

    # ── posproceso ────────────────────────────────────────────────────────
    def end(self, g: Game) -> None:
        pg = self.pg
        dt = self.dt
        if self.flash:
            v = int(40 * self.flash)
            self.S.fill((v, v, int(v * 0.8)), special_flags=pg.BLEND_ADD)
        keep = int(255 * math.exp(-dt / 0.045))   # persistencia del fósforo (~45 ms)
        self.P.fill((keep, keep, keep), special_flags=pg.BLEND_MULT)
        self.P.blit(self.S, (0, 0), special_flags=pg.BLEND_MAX)
        sx = sy = 0
        if self.shake:
            amp = 14 * self.s * self.shake
            sx, sy = int(self.rng.uniform(-amp, amp)), int(self.rng.uniform(-amp, amp))
        out = self.screen
        if sx or sy:
            out.fill((0, 0, 0))
        out.blit(self.P, (sx, sy))
        if not self.low:
            # Bloom por pirámide: reducir es desenfocar. Tres niveles sumados a 1/4 y un solo
            # reescalado a pantalla completa; mucho más barato que un gaussiano.
            ss = pg.transform.smoothscale
            small = ss(self.P, self.small)
            small.fill((22, 22, 22), special_flags=pg.BLEND_SUB)        # solo brilla lo brillante
            mid = ss(small, self.mid)
            glow = ss(mid, self.small)                                   # halo cercano
            glow.blit(ss(ss(mid, self.tiny), self.small), (0, 0), special_flags=pg.BLEND_ADD)  # halo ancho
            out.blit(ss(glow, (self.w, self.h)), (sx, sy), special_flags=pg.BLEND_ADD)
        if self.hurt:   # aberración cromática y destello rojo
            ghost = self.P.copy()
            ghost.fill((255, 40, 40), special_flags=pg.BLEND_MULT)
            off = int(10 * self.s * self.hurt)
            out.blit(ghost, (sx + off, sy), special_flags=pg.BLEND_ADD)
            out.fill((int(70 * self.hurt), 0, 0), special_flags=pg.BLEND_ADD)
        if self.scan is not None:
            out.blit(self.scan, (0, 0), special_flags=pg.BLEND_MULT)


# ═══════════════════════════════════════════════════════════════════════════
# §8 SONIDO — síntesis al estilo POKEY, sin ficheros
# ═══════════════════════════════════════════════════════════════════════════

class Sound:
    RATE = 22050

    def __init__(self, pg, enabled: bool) -> None:
        self.ok = False
        self.pg = pg
        if not enabled:
            return
        try:
            if not pg.mixer.get_init():
                pg.mixer.init(self.RATE, -16, 1, 512)
            self.rate, _, self.channels = pg.mixer.get_init()   # la frecuencia real manda
            pg.mixer.set_num_channels(12)
            self.snd = {k: self._make(v) for k, v in self._synth().items()}
            self.loop_ch = {k: pg.mixer.Channel(i) for i, k in enumerate(("idle", "move", "saucer", "missile"))}
            pg.mixer.set_reserved(4)
            self.ok = True
        except Exception as exc:   # sin tarjeta de sonido: se juega en silencio
            print(f"sonido desactivado: {exc}", file=sys.stderr)

    def _make(self, samples):
        buf = array.array("h", (max(-32767, min(32767, int(v * 32767))) for v in samples))
        if self.channels == 2:
            st = array.array("h", bytes(len(buf) * 4))
            st[0::2] = buf
            st[1::2] = buf
            buf = st
        return self.pg.mixer.Sound(buffer=buf.tobytes())

    def _synth(self) -> dict:
        r = self.rate
        rng = random.Random(1980)

        def noise(dur, decay, lp, vol, attack=0.0):
            out, y = [], 0.0
            n = int(dur * r)
            for i in range(n):
                y += lp * (rng.uniform(-1, 1) - y)
                t = i / r
                env = math.exp(-t / decay) * (min(1.0, t / attack) if attack else 1.0)
                out.append(y * env * vol)
            return out

        def tone(f0, f1, dur, vol, wave="sq", decay=None):
            out, ph = [], 0.0
            n = int(dur * r)
            for i in range(n):
                f = f0 + (f1 - f0) * i / n
                ph += f / r
                v = (1.0 if ph % 1 < 0.5 else -1.0) if wave == "sq" else math.sin(ph * math.tau)
                env = math.exp(-i / r / decay) if decay else 1.0
                out.append(v * vol * env)
            return out

        def mix(*parts):
            n = max(len(p) for p in parts)
            return [sum(p[i] for p in parts if i < len(p)) for i in range(n)]

        def engine(rate_hz, vol):
            n = int(r * 12 / rate_hz)   # bucle con un número entero de pulsos
            out, y = [], 0.0
            for i in range(n):
                y += 0.08 * (rng.uniform(-1, 1) - y)
                ph = (i * rate_hz / r) % 1
                out.append((y * 2.2 + (0.5 if ph < 0.25 else -0.15)) * vol * (1 - ph * 0.7))
            return out

        warble = [math.sin(math.tau * (520 + 140 * math.sin(math.tau * 7 * i / r)) * i / r) * 0.12
                  for i in range(int(r * 6 / 7))]
        return {
            "fire": mix(noise(0.45, 0.12, 0.35, 0.9), tone(180, 60, 0.15, 0.3)),
            "efire": mix(noise(0.35, 0.10, 0.25, 0.6), tone(140, 50, 0.12, 0.2)),
            "boom": noise(1.8, 0.55, 0.12, 1.0, attack=0.01),
            "crash": mix(noise(1.4, 0.45, 0.5, 1.0), tone(300, 40, 0.6, 0.25)),
            "thud": noise(0.3, 0.07, 0.3, 0.6),
            "boing": tone(620, 140, 0.28, 0.35, "sin", decay=0.2),
            "beep": tone(1250, 1250, 0.05, 0.18),
            "warn": mix(tone(880, 880, 0.08, 0.2), [0.0] * int(r * 0.11) + tone(660, 660, 0.08, 0.2),
                        [0.0] * int(r * 0.22) + tone(880, 880, 0.08, 0.2)),
            "bonus": mix(*[[0.0] * int(r * 0.18 * k) + tone(1760, 1760, 0.5, 0.25, "sin", 0.15)
                           for k in range(4)]),
            "saucer_boom": [v * 0.8 for v in tone(900, 90, 1.2, 0.5, "sin")],
            "missile": tone(1400, 400, 0.6, 0.18, "sin"),
            "start": tone(440, 880, 0.25, 0.2),
            "gameover": tone(500, 120, 0.9, 0.2, "sin", decay=0.6),
            "idle": engine(18, 0.22),
            "move": engine(34, 0.3),
            "saucer": warble,
            "drone": [math.sin(math.tau * 95 * i / r) * 0.15 + rng.uniform(-0.03, 0.03)
                      for i in range(int(r * 20 / 95))],
        }

    def update(self, g: Game) -> None:
        if not self.ok:
            g.events.clear()
            return
        for name in g.events:
            if name in self.snd:
                self.snd[name].play()
        g.events.clear()
        playing = g.mode == PLAY and not g.dead
        self._loop("idle", "idle", playing and not g.moving)
        self._loop("move", "move", playing and g.moving)
        s = g.saucer
        self._loop("saucer", "saucer", playing and s.on and not s.boom)
        e = g.enemy
        near = playing and e.kind == MISSILE and not e.dead and g.tdist < RADAR_RANGE_HI
        self._loop("missile", "drone", near)
        if near:
            self.loop_ch["missile"].set_volume(max(0.15, 1 - g.tdist / 128))

    def _loop(self, ch: str, snd: str, on: bool) -> None:
        c = self.loop_ch[ch]
        if on and not c.get_busy():
            c.play(self.snd[snd], loops=-1)
        elif not on and c.get_busy():
            c.stop()


# ═══════════════════════════════════════════════════════════════════════════
# §9 ENTRADA — teclado y mando
# ═══════════════════════════════════════════════════════════════════════════

HELP = """battlezonepy — controles
  Modo arcade asistido: flechas para mover y girar, ESPACIO o CTRL para disparar.
  Modo tanque (purista): Q/A palanca izquierda, O/L palanca derecha.
  Mando: sticks = palancas, cruceta = modo asistido, cualquier botón = disparo, START = empezar.
  ENTER o 1: empezar · F11: pantalla completa · ESC: salir
"""


def read_levers(pg, keys, joy) -> tuple[int, int, bool]:
    K = pg
    fire = keys[K.K_SPACE] or keys[K.K_LCTRL] or keys[K.K_RCTRL]
    left = (keys[K.K_q] - keys[K.K_a])
    right = (keys[K.K_o] - keys[K.K_l])
    up, down = keys[K.K_UP], keys[K.K_DOWN]
    lt, rt = keys[K.K_LEFT], keys[K.K_RIGHT]
    if joy is not None:
        try:
            ly, ry = joy.get_axis(1), joy.get_axis(3 if joy.get_numaxes() > 3 else 2)
            if abs(ly) > 0.5:
                left = -1 if ly > 0 else 1
            if abs(ry) > 0.5:
                right = -1 if ry > 0 else 1
            if joy.get_numhats():
                hx, hy = joy.get_hat(0)
                up, down, lt, rt = up or hy > 0, down or hy < 0, lt or hx < 0, rt or hx > 0
            fire = fire or any(joy.get_button(i) for i in range(min(4, joy.get_numbuttons())))
        except Exception:
            pass
    if not (left or right):   # modo asistido → combinaciones de MTAB
        if up and lt:
            left, right = 0, 1
        elif up and rt:
            left, right = 1, 0
        elif down and lt:
            left, right = -1, 0
        elif down and rt:
            left, right = 0, -1
        elif up:
            left, right = 1, 1
        elif down:
            left, right = -1, -1
        elif lt:
            left, right = -1, 1
        elif rt:
            left, right = 1, -1
    return left, right, bool(fire)


# ═══════════════════════════════════════════════════════════════════════════
# §10 BUCLE — paso fijo de 64 ms y render interpolado
# ═══════════════════════════════════════════════════════════════════════════

def run(args, opts: Options) -> None:
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame as pg

    pg.mixer.pre_init(Sound.RATE, -16, 1, 512)
    pg.init()
    modern = args.gfx == "2026"
    # Resolución interna fija y escalado por GPU (SDL «SCALED»): el coste por fotograma no
    # depende de si el monitor es 1080p o 4K, y la ventana se puede redimensionar libremente.
    size = args.res if modern else (SCREEN_W, SCREEN_H)
    flags = pg.SCALED | pg.RESIZABLE | (pg.FULLSCREEN if args.fullscreen else 0)

    def open_window():
        try:
            return pg.display.set_mode(size, flags, vsync=1)
        except pg.error:
            return pg.display.set_mode(size, flags)

    screen = open_window()
    pg.display.set_caption("battlezonepy — homenaje no oficial a Battlezone (Atari, 1980)")
    pg.joystick.init()
    joy = pg.joystick.Joystick(0) if pg.joystick.get_count() else None

    game = Game(opts, args.seed, load_table())
    if modern:
        rend = Renderer2026(pg, screen, low=args.low, crt=args.crt)
    else:
        rend = Renderer(pg, screen, glow=args.gfx == "vector")
    interpolate = args.gfx != "1980"
    sound = Sound(pg, not args.mute)
    print(HELP)
    clock = pg.time.Clock()
    step = 1.0 / TICK_HZ
    acc = 0.0
    edges = Ctrl()
    frames = 0
    running = True
    while running:
        for ev in pg.event.get():
            if ev.type == pg.QUIT:
                running = False
            elif ev.type == pg.KEYDOWN:
                if ev.key == pg.K_ESCAPE:
                    running = False
                elif ev.key == pg.K_F11:
                    pg.display.toggle_fullscreen()
                elif ev.key in (pg.K_RETURN, pg.K_KP_ENTER, pg.K_1):
                    edges.start = True
                    edges.select = True
                elif ev.key in (pg.K_UP, pg.K_RIGHT, pg.K_o):
                    edges.up = True
                elif ev.key in (pg.K_DOWN, pg.K_LEFT, pg.K_l):
                    edges.down = True
                elif ev.key in (pg.K_SPACE, pg.K_LCTRL, pg.K_RCTRL):
                    edges.select = True
            elif ev.type == pg.JOYBUTTONDOWN:
                edges.select = True
                if ev.button >= 6:
                    edges.start = True
            elif ev.type == pg.JOYHATMOTION:
                edges.up |= ev.value[1] > 0 or ev.value[0] > 0
                edges.down |= ev.value[1] < 0 or ev.value[0] < 0
            elif ev.type == pg.JOYDEVICEADDED and joy is None:
                joy = pg.joystick.Joystick(ev.device_index)
        acc += min(0.25, clock.tick(120) / 1000)
        while acc >= step:
            left, right, fire = read_levers(pg, pg.key.get_pressed(), joy)
            game.tick(Ctrl(left, right, fire, edges.start, edges.up, edges.down, edges.select))
            edges = Ctrl()
            acc -= step
        rend.draw(game, acc / step if interpolate else 1.0)
        sound.update(game)
        pg.display.flip()
        frames += 1
        if args.frames and frames >= args.frames:
            if args.screenshot:
                pg.image.save(screen, args.screenshot)
            running = False
    pg.quit()


# ═══════════════════════════════════════════════════════════════════════════
# §11 SELFTEST — el contrato del README, sin ventana
# ═══════════════════════════════════════════════════════════════════════════

def selftest() -> int:
    ok = 0
    fails = []

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        if cond:
            ok += 1
            print(f"  ✓ {name}")
        else:
            fails.append(name)
            print(f"  ✗ {name}")

    print("battlezonepy --selftest")
    check("wrap(65535 + 1) == 0", (65535 + 1) % WORLD == 0)
    check("distancia toroidal simétrica", mb_dist(100 - 65500, 0) == mb_dist(65500 - 100, 0) == 136)
    check("Math Box: max + 3/8·min", mb_dist(800, 400) == 800 + 100 + 50)
    check("21 obstáculos en el campo", len(FIELD) == 21)
    check("tick de 64 ms", abs(TICK_HZ - 15.625) < 1e-9)

    def scored(kind, ehits=0):
        g = Game(seed=1)
        g.start_game()
        g.enemy.kind, g.enemy.dead, g.enemy.z = kind, 0, 0
        g.player.x, g.player.y, g.player.a = 100.0, 100.0, 0.0
        g.enemy.x, g.enemy.y, g.enemy.a = 300.0, 100.0, 0.0
        g.pshell.ct, g.pshell.x, g.pshell.y = 10, 300.0, 100.0
        assert g.shrtck(g.pshell, True)
        return g.score

    check("tanque 1000 / supertanque 3000 / misil 2000",
          (scored(TANK), scored(SUPER), scored(MISSILE)) == (1, 3, 2))
    g = Game(seed=2)
    g.start_game()
    g.saucer.on, g.saucer.x, g.saucer.y = True, 5000.0, 5000.0
    g.pshell.ct, g.pshell.x, g.pshell.y = 10, 5000.0, 5100.0
    check("platillo 5000", g.sauchk(g.pshell, True) and g.score == 5)

    g = Game(Options(lives=3, bonus=1), seed=3)
    g.start_game()
    g.score = 14
    g.newlif(13)
    l0 = g.lives
    g.score = 15
    g.newlif(14)
    check("vida extra al superar 15000, no al alcanzarlo", l0 == 3 and g.lives == 4)
    g.score = 100
    g.newlif(99)
    check("superbonus en 100000", g.lives == 5)
    lives = []
    for n in (2, 3, 4, 5):
        g = Game(Options(lives=n), seed=0)
        g.start_game()
        lives.append(g.lives)
    check("vidas = 2..5 según el DIP", lives == [2, 3, 4, 5])

    g = Game(seed=4)
    g.start_game()
    g.player.x, g.player.y, g.player.a = 40000.0, 40000.0, 0.0
    g.launch(g.pshell, g.player)
    launched = g.pshell.ct
    g.motion(Ctrl(fire=True))
    events = [e for e in g.events if e == "fire"]
    n = 0
    while g.pshell.ct > 0:
        g.shupdt()
        n += 1
    check("un proyectil por tanque; vive 127 subpasos",
          launched == SHELL_LIFE and not events and n == 127)

    g = Game(seed=5)
    g.start_game()
    ox, oy, _, _ = FIELD[5]
    g.player.x, g.player.y, g.player.a = ox - 1300.0, float(oy), 0.0
    for _ in range(40):
        g.motion(Ctrl(1, 1))
    check("BAKOUT: el jugador nunca queda dentro de un obstáculo",
          mb_dist(g.player.x - ox, g.player.y - oy) > PLAYER_OBJ_RADIUS and g.objcol == 1)

    g = Game(seed=6)
    g.start_game()
    g.player.x, g.player.y, g.player.a = 30000.0, 30000.0, 0.0
    g.enemy.x, g.enemy.y, g.enemy.a = 34000.0, 30000.0, 128.0
    g.ftimer, g.score = 0x10, 10
    g.fireit()
    early = g.eshell.ct
    g.ftimer = 0x40
    g.fireit()
    check("el enemigo no dispara durante la gracia de FTIMER < 0x20", early == 0 and g.eshell.ct > 0)

    g = Game(seed=7)
    g.start_game()
    g.score = 1
    for _ in range(600):
        g.saucmv()
    check("el platillo no aparece con menos de 2000 puntos", not g.saucer.on)

    class Probe(Renderer):
        def __init__(self):
            self.glow, self.zs = False, []

        def line(self, x0, y0, x1, y1, color, k=1.0):
            self.zs.append((x0, x1))

    r = Probe()
    r.zoff = 0
    r.seg3((-500, 10, 0), (1000, 10, 0), GREEN, 1)
    check("recorte: ningún vértice detrás del plano cercano",
          len(r.zs) == 1 and all(abs(x) < 1e6 for x in r.zs[0]))

    g = Game(seed=8)
    g.start_game()
    for _ in range(3000):
        g.tick(Ctrl(1, 1, True))
    check("3000 ticks de partida sin excepciones", True)
    g = Game(seed=9)
    for _ in range(2000):
        g.tick(Ctrl())
    check("modo atracción estable", g.mode == ATTRACT)

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    try:
        import pygame as pg
    except ImportError:
        print("  · pygame-ce no está instalado: se omiten las pruebas de render")
    else:
        pg.display.init()
        surf = pg.display.set_mode((640, 360))
        g = Game(seed=10)
        g.start_game()
        for _ in range(40):
            g.tick(Ctrl(1, 1, True))
        g.saucer.on, g.saucer.x, g.saucer.y = True, g.player.x + 3000, g.player.y
        g.fxe("boom", g.player.x + 3000, g.player.y)
        g.fxe("hit", g.player.x, g.player.y)
        renderers = (Renderer2026(pg, surf), Renderer2026(pg, surf, low=True, crt=True),
                     Renderer(pg, surf, glow=True))
        for rend in renderers:
            for _ in range(3):
                rend.draw(g, 0.5)
        check("render 2026, 2026 --low --crt y vector sin excepciones", not g.fx and renderers[0].sparks)
        pg.quit()

    print(f"{ok} correctas, {len(fails)} fallidas")
    return 1 if fails else 0


def parse_res(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("usa ANCHOxALTO, por ejemplo 1920x1080") from None
    if not (320 <= w <= 7680 and 240 <= h <= 4320):
        raise argparse.ArgumentTypeError("resolución fuera de rango")
    return w, h


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="battlezonepy", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fullscreen", action="store_true", help="pantalla completa")
    ap.add_argument("--lives", type=int, choices=(2, 3, 4, 5), default=3, help="vidas iniciales (DIP)")
    ap.add_argument("--bonus", type=int, choices=BONUS_K[0:1] + tuple(k * 1000 for k in BONUS_K[1:]),
                    default=15000, help="vida extra (0 = ninguna)")
    ap.add_argument("--missile", type=int, choices=tuple(k * 1000 for k in MISLVL), default=10000,
                    help="puntuación desde la que aparecen misiles")
    ap.add_argument("--seed", type=int, default=None, help="semilla: partida reproducible")
    ap.add_argument("--gfx", choices=("2026", "vector", "1980"), default="2026",
                    help="aspecto: 2026 (bloom, sólidos, partículas, panorámico), "
                         "vector (alambre con brillo, 4:3) o 1980 (15,6 imágenes/s, como el original)")
    ap.add_argument("--classic", action="store_true", help="atajo de --gfx 1980")
    ap.add_argument("--res", type=parse_res, default=(1600, 900), metavar="ANCHOxALTO",
                    help="resolución interna del aspecto 2026 (por defecto 1600x900; "
                         "la GPU la escala a la ventana o a la pantalla completa)")
    ap.add_argument("--low", action="store_true", help="2026 sin bloom y con menos partículas (equipos lentos)")
    ap.add_argument("--crt", action="store_true", help="2026 con líneas de barrido de tubo")
    ap.add_argument("--mute", action="store_true", help="sin sonido")
    ap.add_argument("--selftest", action="store_true", help="pruebas sin ventana")
    ap.add_argument("--frames", type=int, default=0, help=argparse.SUPPRESS)
    ap.add_argument("--screenshot", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.classic:
        args.gfx = "1980"
    if args.selftest:
        return selftest()
    opts = Options(lives=args.lives,
                   bonus=0 if args.bonus == 0 else BONUS_K.index(args.bonus // 1000),
                   missile=MISLVL.index(args.missile // 1000))
    try:
        run(args, opts)
    except ImportError:
        print("Falta pygame-ce:  python -m pip install 'pygame-ce>=2.5'   (o: uv run battlezonepy.py)",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

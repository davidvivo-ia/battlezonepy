# battlezonepy

Recreación fiel de **Battlezone (Atari, 1980)** en Python, en **un único fichero: `battlezonepy.py`**.

No es un juego «inspirado en» Battlezone. El objetivo es reconstruir el comportamiento de la recreativa de 1980 a partir de su **código fuente original**, que publicó el colectivo historicalsource. Todas las constantes, reglas de IA y puntuaciones de este documento salen de leer `BZONE.MAC` y sus ficheros hermanos. Cuando algo es inferencia y no lectura, se dice.

> Marcas de confianza que se usan en el documento:
> **[FUENTE]** leído directamente en el ensamblador original de Atari ·
> **[DERIVADO]** calculado a partir de valores del fuente ·
> **[HIPÓTESIS]** razonable, pero hay que calibrarlo contra MAME ·
> **[DISEÑO]** decisión de este proyecto, no del original.

---

## 1. La regla innegociable: un solo fichero

```
battlezonepy/
├── battlezonepy.py   ← todo el juego: motor, modelos, IA, sonido, UI
├── README.md
└── LICENSE           ← MIT
```

Nada de `src/`, ni paquetes, ni carpetas de assets. Los modelos, los textos y los sonidos se **generan en código**, igual que en 1980 vivían como tablas dentro de la ROM. Si el fichero pasa de 3.000 líneas, la solución es escribir mejor código, no partirlo.

El motivo: este juego cabía en 8 KB de ROM. Un único fichero, legible y ejecutable, es el homenaje honesto, y además es el mejor formato para estudiarlo.

---

## 2. Stack (Python 2026)

| Pieza | Elección | Motivo |
|---|---|---|
| Intérprete | **Python ≥ 3.13** (probado en 3.14) | `match`, `dataclass(slots=True)`, tipado moderno |
| Gráficos, entrada y audio | **pygame-ce ≥ 2.5** | Líneas con antialiasing (`draw.aaline`), mando, `sndarray` para sintetizar sonido. La *community edition* es el fork vivo; el `pygame` clásico va por detrás |
| Matemáticas | `math` de la stdlib | El 3D de unos 30 objetos por fotograma no necesita NumPy. `numpy` es opcional y solo sirve para generar los buffers de sonido |
| Dependencias | **PEP 723** dentro del script | El propio fichero declara sus dependencias y `uv run` las resuelve sin `requirements.txt` |

Cabecera que debe llevar `battlezonepy.py`:

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pygame-ce>=2.5"]
# ///
"""battlezonepy — Battlezone (Atari 1980) recreado desde el fuente original."""
```

### Cómo ejecutarlo

```bash
# Recomendado: uv resuelve las dependencias leyendo la cabecera del script
uv run battlezonepy.py

# Por la vía clásica
python -m pip install "pygame-ce>=2.5"
python battlezonepy.py

# Opciones
python battlezonepy.py --fullscreen --scale 2
python battlezonepy.py --lives 3 --bonus 15000 --missile 10000   # DIP switches de la recreativa
python battlezonepy.py --seed 42                                  # partida reproducible
python battlezonepy.py --selftest                                 # tests sin abrir ventana
```

---

## 3. Lo que dice el código original

### 3.1 Ficheros y autores [FUENTE]

- `BZONE.MAC` (unas 4.900 líneas): el núcleo del juego. La cabecera dice `DATE INITIATED: 25-APR-79`, `1ST PERSON VIEW OF TRADITIONAL ATARI TANK GAME` y `CONTROLS: ULTRA TANK TYPE CONTROLS`.
- `BZONE.DOC`: hoja de release fechada el **31 de agosto de 1981**. Programador, **Edward Rotberg**; jefe de proyecto, **Morgan Hoff**. Ensamblador `MAC65` y enlazador `LINKM`.
- Orden de enlazado: `BZMTNS, BZONE, BZSOUN, VGUT, BZSTST`.
- `BZMTNS.MAC`: horizonte, montañas y volcán, las **tablas de los modelos 3D** (`PYRTBL`, `CUBTBL`, `TNKTBL`, `SHLTBL`…) y las tablas de DIP switches `BONTBL` y `MISLVL`.
- `BZSOUN.MAC`: el sonido, con el chip POKEY más circuitería discreta.
- `MBUDOC.DOC` y `MBUCOD.V05`: el microcódigo de la **Math Box** (AMD 2901), el coprocesador que hacía rotaciones, productos y divisiones.
- `VGUT.MAC` y `VGMC.MAC`: utilidades del generador vectorial (AVG).

Rotberg explica su método en la cabecera: los **«atomic comments»**. Cada bloque de 1 a 20 instrucciones lleva una etiqueta: `;D` (decisión), `;C` (cálculo), `;B` (salto), `;L` (enlace) o `;S` (sincronización). Copiamos la idea: cada función de `battlezonepy.py` nombra la rutina original que reimplementa (`# ← ROBOT`, `# ← COLCHK`…), para poder leer los dos códigos uno junto al otro.

### 3.2 Bucle principal [FUENTE: `MAIN`, línea ~420]

Un tick del original, en este orden:

1. Sincronizar con la NMI y animar el volcán (`VOLCNO`).
2. Ventana inferior, ventana superior, montañas y horizonte (`MOUNTS`).
3. `ROTATE`: elegir los objetos visibles, trasladarlos y rotarlos al espacio de cámara.
4. `PNTPUT` y `DRAW`: proyectar cada objeto y emitir sus vectores.
5. Parabrisas roto si el jugador ha muerto (`WNSHLD`).
6. Radar (`DRADAR`), mira (`XCROSS` o `XCROS1` según haya alineación) y mensajes.
7. **Física en 4 subpasos**: `COLCHK` y `SHUPDT` se ejecutan cuatro veces por fotograma. Así los proyectiles avanzan a pasos cortos y nunca atraviesan un objeto sin tocarlo. **Hay que conservarlo.**
8. `QWIKCK` (choque rápido proyectil → enemigo), `ROBOT`, `SAUCMV`, `BOUND` y `MOTION` (jugador).

### 3.3 Mundo, ángulos y unidades [FUENTE + DERIVADO]

- Las posiciones son de **16 bits por eje** (`TPOSX`, `TPOSY`). El mundo es un **toro de 65.536 × 65.536**: si sales por un lado, entras por el contrario. No hay muros.
- Los ángulos son de **8 bits** (`TANGLE`, es decir, 256 unidades = 360°) más un bit de fracción (`LANGLE`, en pasos de `0x80`). Cada llamada a `ITANGL` o `DTANGL` gira media unidad.
- El campo tiene **21 obstáculos fijos** (`PTBLX1` / `PTBLY1`, valores ×4). Se copian tal cual: ese mapa *es* Battlezone.
- Modelos: **pirámide**, **cubo** (bloque ancho y estrecho), tanque, proyectil, orugas, radar, fragmentos de explosión, misil (`R2DOBJ`), platillo (`SAUOBJ`) y supertanque (`TR7OBJ`). Las tablas de vértices están en `BZMTNS.MAC` (`PYRTBL`, `CUBTBL`…).

### 3.4 Controles del jugador [FUENTE: `MOTION`, `MTAB`]

Dos palancas, cada una con tres posiciones: adelante, neutro y atrás. La tabla `MTAB` decide qué hace cada combinación:

| Palanca izq. | Palanca der. | Acción | Giro por tick |
|---|---|---|---|
| ↑ | ↑ | avance a tope | — |
| ↓ | ↓ | retroceso a tope | — |
| ↑ | — | avanza y gira a la derecha | ½ unidad |
| — | ↑ | avanza y gira a la izquierda | ½ unidad |
| ↓ | — | retrocede y gira | ½ unidad |
| ↑ | ↓ | **pivota sobre sí mismo** a la derecha | 1 unidad |
| ↓ | ↑ | **pivota sobre sí mismo** a la izquierda | 1 unidad |

El paso de avance lo calcula `M.SET` como `0,75 · sen/cos(ángulo)`, y el avance a tope lo aplica dos veces [FUENTE]. Al chocar con un obstáculo, `BAKOUT` devuelve el tanque a su posición anterior, suena `BOING`, la cámara rebota (`BOUNCE`) y aparece **«MOTION BLOCKED BY OBJECT»**.

Mapeo en battlezonepy [DISEÑO]:

- **Modo tanque (purista)**: `Q`/`A` hacen de palanca izquierda y `P`/`L` de palanca derecha (en mando, los dos sticks analógicos).
- **Modo arcade asistido (por defecto)**: flechas y barra espaciadora para disparar. Se traduce a las combinaciones de `MTAB` de arriba, así que la física es idéntica.

### 3.5 Proyectiles [FUENTE: disparo en `MOTION`, `SHUPDT`, `COLCHK`]

- **Un proyectil por tanque a la vez** (mientras `FIRECT ≠ 0` no se puede disparar). Vida: `FIRECT = 0x7F`, es decir, 127 subpasos, unos **32 fotogramas** a 4 subpasos por fotograma.
- La velocidad es `2·cos/sen(ángulo)` por subpaso [DERIVADO], lo que da un alcance de unas 127 × 254 ≈ **32.000 unidades, medio mundo**.
- Si el proyectil choca con un obstáculo, explota (`FIRECT = 0xA0`). Si choca con un tanque, se llama a `EXINIT`.

### 3.6 Enemigos y puntuación [FUENTE: `QWIKCK`, `SAUCHK`, `BUZBOM`]

`HITS` guarda la puntuación **en BCD y en miles**. Por eso en pantalla se imprime literalmente `SCORE ... 000`.

| Enemigo | Puntos | Comportamiento |
|---|---|---|
| **Tanque** | 1.000 | IA estándar (§3.7) |
| **Supertanque** (`TR7`) | 3.000 | Avanza al **doble de velocidad** y guarda más distancia (`MOUTHI ≥ 8` frente a `≥ 5`). Aparece cuando `NOR2D3 ≥ 5` (`TR7CHK`) |
| **Misil** (`R2D3`, «buzz bomber») | 2.000 | Se lanza contra el jugador en zigzag (`FRAME & 0x1F` va modulando el ángulo). Arranca en altura (`STARTZ = 0x1800`). Si choca con un obstáculo, **levita** por encima y vuelve a picar. Solo se le puede dar por debajo de `TOP = 0x200`. Nunca ataca por la espalda |
| **Platillo** | 5.000 | Inofensivo. Solo aparece con `HITS ≥ 2` (2.000 puntos). Deriva al azar con un temporizador aleatorio y gira `+8` por tick |

Nunca hay más de un enemigo de tierra a la vez (tanque, supertanque o misil), y como mucho un platillo además.

### 3.7 IA del tanque enemigo [FUENTE: `ROBOT`, `REACT`, `FIREIT`, `ROBCHK`]

Es una máquina de estados que gobiernan `ACTION` (temporizador de reacción) y `STATE`:

- **Decisión** (cuando `ACTION == 0`):
  - Si lleva demasiado tiempo «portándose bien» (`FTIMER == 0xFF`), si lo decide una moneda al aire o si el jugador tiene 100.000 puntos o más, pasa a **ATACAR**: `RGOAL = TRACK()`, la arcotangente hacia el jugador.
  - Si no, compara las bajas del jugador (`HITS`) con las del enemigo (`HITS+2`):
    - jugador **por delante**: **ATACAR**
    - **empate**: **HUIR**, rumbo a `TRACK() XOR 0x40`, una ruta de escape perpendicular
    - jugador **por detrás**: **VAGAR**, con `RGOAL ± (rand & 0x1F)`
  - En modo attract, el enemigo siempre vaga al azar.
- **Dificultad adaptativa** (`REACT`): el tiempo de reacción y la puntería (`SKILL`) dependen de `HITS − HITS_enemigo`. Cuanto mejor juegas, más rápido y certero se vuelve. Es ajuste dinámico de dificultad en 1979.
- **Giro**: si el error angular supera `SKILL`, pivota 2 pasos por tick; si no, corrige 1 paso y avanza.
- **Distancia**: no avanza si ya está cerca (`MOUTHI < 5`). Busca el duelo, no la embestida.
- **Disparo** (`FIREIT`): tienen que cumplirse todas estas condiciones: `FTIMER ≥ 0x20` (periodo de gracia tras aparecer), alineación de menos de 2 unidades, proyectil propio disponible y jugador vivo. Contra un novato (`HITS < 2`) solo dispara si el jugador lo tiene a la vista.
- **Choque con un obstáculo**: activa el bit 0 de `STATE`, retrocede girando hacia un lado al azar y luego fija un objetivo nuevo.
- **Aparición** (`ROBCHK` / `TANKCK`): el enemigo aparece en una **ventana delante del jugador**, más o menos ancha según la diferencia de bajas. Si `HITS ≥ MISLVL[dip]`, hay un 50 % de probabilidad de que salga un **misil** en vez de un tanque.

### 3.8 Vidas, bonus y DIP switches [FUENTE: `NEWLIF`, `BONTBL`, `MISLVL`]

| Ajuste | Valores | Origen |
|---|---|---|
| Vidas iniciales | `(OPTION & 3) + 2` → **2, 3, 4 o 5** | inicialización en `PWRON` |
| Vida extra | **ninguna / 15.000 / 25.000 / 50.000** | `BONTBL: .BYTE 0,14,24,49` (en BCD; la vida se da al *superar* el valor) |
| Superbonus | **100.000** | `NEWLIF`, rama `SUPER` |
| El misil aparece desde | **5.000 / 10.000 / 20.000 / 30.000** | `MISLVL: .BYTE 5,10,20,30` |

Valores por defecto recomendados [DISEÑO]: 3 vidas, bonus a 15.000 y misil desde 10.000.

### 3.9 HUD y mensajes [FUENTE: tabla `ENGMSG`]

Textos que hay que reproducir literalmente: `ENEMY IN RANGE`, `ENEMY TO LEFT` / `RIGHT` / `REAR` (parpadean cuando el enemigo está fuera de campo, con `PTURN ≥ 22`), `MOTION BLOCKED BY OBJECT`, `GAME OVER`, `PRESS START`, `SCORE … 000`, `HIGH SCORE … 000`, `HIGH SCORES`, `GREAT SCORE`, `ENTER YOUR INITIALS` y `© 1980 ATARI` (este último solo en la pantalla de attract, por respeto al original).

- **Radar**: un círculo arriba en el centro, con un barrido de `+0x0B` por tick y un punto para el enemigo cuando está a tiro.
- **Mira**: dos variantes (`XCROSS` / `XCROS1`), según esté o no alineado el enemigo.
- **Muerte**: el **parabrisas se resquebraja** poco a poco (`CRACKS`, 16 segmentos dibujados por pasos) con la imagen congelada.
- **Récords**: 10 puestos con 3 iniciales, elegidas letra a letra.
- **Modo attract**: un tanque manejado por la IA conduce solo, luego aparece el logo `BATTLE ZONE` y después la tabla de récords.

### 3.10 Color y pantalla [FUENTE + DISEÑO]

El monitor original era un **vectorial monocromo verde**, con una tira de celofán rojo encima de la franja superior, donde están el radar y los mensajes. Para reproducirlo:

- Fondo `#000000` y vectores `#33FF33`, con una intensidad que baja con la profundidad (`DQUE`: lo lejano se ve más tenue).
- Franja superior (HUD) en `#FF3030`.
- **Brillo de fósforo** [DISEÑO, opcional]: cada línea se dibuja dos veces; la segunda, más gruesa y al 25 % de alfa, sobre una superficie en modo aditivo.
- Resolución lógica base de 1024 × 768 (la relación de aspecto del AVG), escalada a la ventana.

---

## 4. Arquitectura de `battlezonepy.py` [DISEÑO]

Secciones del fichero, en este orden y cada una con su comentario de cabecera:

```
§0  Cabecera PEP 723, docstring, imports, CLI (argparse)
§1  CONSTANTES       ← todos los valores originales, con un comentario que cita la rutina o línea
§2  MATEMÁTICA FIJA  angle8 ↔ radianes, tablas sen/cos de 256 entradas, wrap toroidal
§3  MODELOS          vértices y aristas de PYR, CUB, TNK, SHL, R2D, SAU, TR7, TREADS, EXP*
§4  CAMPO            los 21 obstáculos de PTBLX1/PTBLY1, montañas y volcán
§5  ENTIDADES        @dataclass(slots=True): Tank, Shell, Missile, Saucer, Debris
§6  RENDER           cámara, recorte contra el plano cercano, proyección en perspectiva, glow
§7  IA               robot(), react(), fireit(), buzbom(), saucmv(), robchk()
§8  FÍSICA           motion(), colchk() en 4 subpasos, qwikck(), objobj(), bakout()
§9  SONIDO           síntesis al estilo POKEY: motor, disparo, explosión, boing, campana de bonus
§10 ESTADOS          ATTRACT → PLAY → DEATH (cristal roto) → GAMEOVER → INITIALS → HISCORES
§11 BUCLE            paso de tiempo fijo + render con interpolación
§12 SELFTEST         aserciones de puntuación, wrap, colisiones e IA, sin abrir ventana
```

### Decisiones técnicas

- **Paso de tiempo fijo.** La lógica corre a frecuencia constante y el render a la de la pantalla. El original se sincronizaba con la NMI, y su frecuencia efectiva es una **[HIPÓTESIS]** que hay que medir en MAME (`-debug`, con un breakpoint en `MAIN`). Mientras tanto, la lógica va a **60 Hz** con una única constante `TICK_HZ` para poder calibrarla.
- **Enteros fieles donde importan.** Los ángulos son `int` entre 0 y 255, más media unidad de fracción. Las posiciones son `int` módulo 65536. La proyección y el dibujo van en `float`. Así se conserva el «tacto» del original (giro en saltos fijos) sin pelearse con aritmética de 8 bits.
- **La Math Box no se emula.** La sustituyen `math` y una tabla sen/cos de 256 entradas. Importa el resultado (rotación, distancia, la arcotangente de `TRACK`), no el microcódigo del AMD 2901.
- **Distancia toroidal.** Antes de calcular cualquier distancia o ángulo, usa siempre la diferencia mínima módulo 65536 en cada eje. Si no, la IA se vuelve loca en el borde del mundo.
- **Recorte contra el plano cercano** antes de dividir por `z`. Sin él, un obstáculo justo detrás de la cámara genera líneas que cruzan toda la pantalla.
- **Sonido sintetizado.** Nada de WAV. Ondas cuadradas y ruido LFSR generados con `pygame.sndarray` (o con `array`, sin NumPy). El motor tiene dos velocidades (`LIDLE` / `HIDLE`), como en el original.
- **RNG determinista.** `random.Random(seed)` hace el papel de `PRAND`, el generador del POKEY. Con `--seed N` una partida se reproduce exactamente, y eso es imprescindible para depurar la IA.
- **Sin estado global.** Un único objeto `Game` lo contiene todo, y se usan funciones puras siempre que se pueda, para que `--selftest` pueda probarlas.

### Contrato mínimo de `--selftest`

```text
✓ wrap(65535 + 1) == 0 y distancia toroidal simétrica
✓ tanque 1000 / supertanque 3000 / misil 2000 / platillo 5000
✓ vida extra al superar 15000 (no al alcanzarlo); superbonus en 100000
✓ vidas = 2..5 según el DIP
✓ un proyectil por tanque; el proyectil muere a los 127 subpasos
✓ BAKOUT: el jugador nunca queda dentro de un obstáculo
✓ el enemigo no dispara durante la gracia de FTIMER < 0x20
✓ el platillo no aparece con HITS < 2
✓ recorte: ningún vértice proyectado con z ≤ near
```

---

## 5. Hoja de ruta

| Hito | Contenido | Está terminado cuando… |
|---|---|---|
| **M1 — Mundo en alambre** | Cámara, los 21 obstáculos, horizonte, montañas, volcán, controles de tanque, `BAKOUT` | Se puede recorrer el campo de batalla y chocar con las pirámides |
| **M2 — Combate** | Proyectiles con 4 subpasos, tanque enemigo con la IA completa, explosiones, radar, mira, mensajes | Se juega una partida entera, solo contra tanques |
| **M3 — Bestiario** | Misil con picado y levitación, platillo, supertanque, puntuación, bonus, DIP switches | Todos los valores de §3.6–3.8 pasan el `--selftest` |
| **M4 — Recreativa** | Modo attract, parabrisas roto, tabla de récords con iniciales, sonido sintetizado | A simple vista no se distingue de una captura de MAME |
| **M5 — Pulido** | Brillo de fósforo, mando, pantalla completa, récords guardados en `~/.battlezonepy.json` | — |

Método de validación: MAME (driver `bzone`) al lado de battlezonepy, comparando velocidades, ritmo de giro y alcance del proyectil. Cada diferencia acaba como una constante corregida o como una entrada en §6.

---

## 6. Desviaciones conocidas respecto al original

Aquí se documenta cada sitio donde battlezonepy se aparta a propósito de la recreativa. Una desviación sin documentar es un bug.

- *(vacío hasta M1)*

---

## 7. Fuentes de referencia

### Primarias, usadas para este documento

| Fuente | Qué aporta | Confianza |
|---|---|---|
| [historicalsource/battlezone](https://github.com/historicalsource/battlezone) | El fuente original de Atari (unas 8.500 líneas de 6502, *rev 1*), `BZONE.DOC` y `MBUDOC.DOC`. Publicado el 14-oct-2021 | **CONFIRMADO** (leído para este README) |
| [6502disassembly.com — Battlezone](https://6502disassembly.com/va-battlezone/) | El desensamblado anotado de Andy McFadden (**rev 2**, con SourceGen), su decodificador de vectores y notas sobre la Math Box, los objetos 3D y las diferencias rev 1 → rev 2 | **CONFIRMADO** |
| [MAME — `src/mame/atari`](https://github.com/mamedev/mame/tree/master/src/mame/atari) | `bzone.cpp`, `bzone_a.cpp` y `mathbox.cpp` (BSD). Emula Battlezone, Bradley Trainer, Red Baron y Desert Wars. Es el **oráculo de comportamiento** para calibrar | **CONFIRMADO** |

> Ojo: el código de historicalsource es la **rev 1** y el desensamblado de McFadden es la **rev 2**. Donde difieran, battlezonepy sigue la **rev 1**, porque es el fuente legible. Si se adopta alguna diferencia de la rev 2, se apunta en §6.

### Secundarias

| Fuente | Interés | Confianza |
|---|---|---|
| [cbzone (pkgsrc)](https://pkgsrc.se/games/cbzone) | Clon en C para Unix/X11, derivado de un original en Fortran de Justin S. Revenaugh. Sirve para comparar cómo resuelve el recorte | PROBABLE |
| [LittleZone (Arduino)](https://blog.arduino.cc/?p=31591) | La versión para Arduino Uno de Mark Wilson, hecha estudiando el 6502 | FUENTE ÚNICA |
| [Battlezone 2000 — Games That Weren't](https://gamesthatwerent.com/2025/01/battlezone-2000) | Contexto de la Jaguar (código de Hover Strike con «BattleZone Modifications») | FUENTE ÚNICA |
| [AtariProtos — 2600](https://www.atariprotos.com/2600/software/battlezone/battlezone.htm) | La versión para Atari 2600 | Referencia |
| [Lista de fuentes de recreativas Atari](https://6502disassembly.com/other-va.html) | Índice de fuentes de otros juegos vectoriales de Atari | Referencia |

### Fuera de alcance

- **Bradley Trainer** (la versión militar): solo existe la emulación de MAME; no se ha encontrado su código fuente.
- **Battlezone 1998, 98 Redux y Battlezone II**: código cerrado. La copia filtrada que circula **ni se usa ni se consulta**. Además, es otro juego (RTS/FPS).
- **Ports domésticos** (2600, Spectrum, C64, Atari de 8 bits): no hay desensamblados publicados. Tampoco hacen falta: el canon es la recreativa.

---

## 8. Aspectos legales

- El código de battlezonepy tiene licencia **MIT** (ver `LICENSE`).
- **No se incluyen ni se distribuyen ROMs, binarios ni gráficos** de Atari. Los modelos se reconstruyen como coordenadas en Python a partir de la lectura del fuente, que está publicado para su estudio histórico. Si necesitas contrastar con el binario, McFadden explica cómo regenerarlo [aquí](https://6502disassembly.com/va-battlezone/bingen.html).
- «Battlezone» es una marca de Atari. Este proyecto es una recreación de fan, educativa y sin ánimo de lucro. Si alguna vez se distribuye públicamente, el título de la ventana y la pantalla de attract deben dejar claro que es un homenaje no oficial, y hay que evitar cualquier uso del logotipo.

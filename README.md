# battlezonepy

Recreación fiel de **Battlezone (Atari, 1980)** en Python, en **un único fichero: `battlezonepy.py`**.

No es un juego «inspirado en» Battlezone. Reconstruye el comportamiento de la recreativa de 1980 a partir de su **código fuente original**, que publicó el colectivo historicalsource. Las constantes, las reglas de la IA, las puntuaciones, los modelos 3D, las montañas, el radar y las grietas del parabrisas salen de leer `BZONE.MAC` y `BZMTNS.MAC`. Cuando algo es inferencia y no lectura, se dice.

> Marcas de confianza que se usan en el documento:
> **[FUENTE]** leído directamente en el ensamblador original de Atari ·
> **[DERIVADO]** calculado a partir de valores del fuente ·
> **[HIPÓTESIS]** razonable, pero hay que calibrarlo contra MAME ·
> **[DISEÑO]** decisión de este proyecto, no del original.

```bash
uv run battlezonepy.py
```

---

## 1. La regla innegociable: un solo fichero

```
battlezonepy/
├── battlezonepy.py   ← todo el juego: datos, motor, IA, sonido, interfaz y tests (~2.000 líneas)
├── README.md
└── LICENSE           ← MIT
```

Nada de `src/`, ni paquetes, ni carpetas de assets. Los modelos, los textos y los sonidos se **generan en código**, igual que en 1980 vivían como tablas dentro de la ROM.

El motivo: este juego cabía en 16 KB de ROM. Un único fichero, legible y ejecutable, es el homenaje honesto, y además es el mejor formato para estudiarlo.

---

## 2. Stack y ejecución

| Pieza | Elección | Motivo |
|---|---|---|
| Intérprete | **Python ≥ 3.13** (probado en 3.13) | `dataclass(slots=True)`, tipado moderno |
| Gráficos, entrada y audio | **pygame-ce ≥ 2.5** (probado en 2.5.8) | Líneas con antialiasing, mando, `mixer.Sound(buffer=…)` para el sonido sintetizado. La *community edition* es el fork vivo |
| Matemáticas y síntesis | solo la stdlib (`math`, `array`) | El 3D de unos 30 objetos por imagen no necesita NumPy |
| Dependencias | **PEP 723** dentro del script | El fichero declara sus dependencias y `uv run` las resuelve sin `requirements.txt` |

```bash
# Recomendado: uv lee la cabecera del script e instala pygame-ce
uv run battlezonepy.py

# Por la vía clásica
python -m pip install "pygame-ce>=2.5"
python battlezonepy.py

# Opciones
python battlezonepy.py --fullscreen
python battlezonepy.py --lives 3 --bonus 15000 --missile 10000   # DIP switches de la recreativa
python battlezonepy.py --seed 42                                  # partida reproducible
python battlezonepy.py --classic                                  # 15,6 imágenes/s y sin brillo, como en 1980
python battlezonepy.py --mute
python battlezonepy.py --selftest                                 # 17 pruebas sin abrir ventana
```

| Opción | Valores | Por defecto |
|---|---|---|
| `--lives` | 2, 3, 4, 5 | 3 |
| `--bonus` | 0 (ninguna), 15000, 25000, 50000 | 15000 |
| `--missile` | 5000, 10000, 20000, 30000 | 10000 |

Los récords se guardan en `~/.battlezonepy.json`.

### Controles

| Acción | Teclado | Mando |
|---|---|---|
| Empezar partida | `ENTER` o `1` | `START` |
| Modo arcade asistido | flechas | cruceta |
| Modo tanque (las dos palancas) | `Q`/`A` palanca izquierda, `O`/`L` palanca derecha | stick izquierdo y stick derecho (eje vertical) |
| Disparar | `ESPACIO` o `CTRL` | cualquier botón frontal |
| Iniciales del récord | flechas para cambiar la letra, `ESPACIO`/`ENTER` para fijarla | cruceta y botón |
| Pantalla completa / salir | `F11` / `ESC` | — |

Las flechas no son otro sistema de control: se traducen a las mismas combinaciones de palancas de la tabla `MTAB`. Por eso la física es idéntica en los dos modos (§3.4).

---

## 3. Lo que dice el código original

### 3.1 Ficheros y autores [FUENTE]

- `BZONE.MAC` (unas 4.900 líneas): el núcleo del juego. La cabecera dice `DATE INITIATED: 25-APR-79`, `1ST PERSON VIEW OF TRADITIONAL ATARI TANK GAME` y `CONTROLS: ULTRA TANK TYPE CONTROLS`.
- `BZONE.DOC`: hoja de release fechada el **31 de agosto de 1981**. Programador, **Edward Rotberg**; jefe de proyecto, **Morgan Hoff**. Ensamblador `MAC65` y enlazador `LINKM`.
- `BZMTNS.MAC`: horizonte, montañas, luna y volcán; las **tablas de vértices** de todos los modelos (`PYRTBL`, `TNKTBL`, `R2D3TB`, `SAUTBL`, `TR7TBL`…); la mira, el anillo del radar, las grietas del parabrisas y las tablas de DIP switches `BONTBL` y `MISLVL`.
- `BZSOUN.MAC`: el sonido, con el chip POKEY más circuitería discreta.
- `MBUDOC.DOC` y `MBUCOD.V05`: el microcódigo de la **Math Box** (AMD 2901), el coprocesador que hacía rotaciones, productos, divisiones y distancias.

Rotberg explica su método en la cabecera: los **«atomic comments»**, donde cada bloque de 1 a 20 instrucciones lleva una etiqueta `;D` (decisión), `;C` (cálculo), `;B` (salto), `;L` (enlace) o `;S` (sincronización). battlezonepy copia la idea: cada función cita la rutina original que reimplementa (`← ROBOT`, `← COLCHK`…).

Los modelos, el campo de batalla, las montañas y los vectores del HUD no están copiados a mano. Se **extrajeron automáticamente** de `BZMTNS.MAC` y `BZONE.MAC` (vértices de las tablas `.NWORD` y aristas de las listas `TLABS` / `TVCTR` / `BVCTR`) y van incrustados en el bloque `DATOS` del script.

### 3.2 Bucle principal y ritmo [FUENTE: `NMI`, `MAIN`]

La NMI salta cada **4 ms** y cada 16 interrupciones incrementa `SYNC`: el comentario dice «END OF FRAME (64 MS)». El juego avanza, por tanto, a **15,625 ticks por segundo**. Un tick hace, en este orden:

1. Animar el volcán (`VOLCNO`).
2. Dibujar el horizonte y las montañas (`MOUNTS`).
3. `ROTATE`: elegir los objetos visibles y llevarlos al espacio de cámara. `PNTPUT` + `DRAW`: proyectarlos.
4. Parabrisas roto si el jugador ha muerto (`WNSHLD`).
5. Radar (`DRADAR`), mira y mensajes.
6. **Física en 4 subpasos**: `COLCHK` y `SHUPDT` cuatro veces por tick, para que ningún proyectil atraviese un objeto sin tocarlo.
7. `QWIKCK`, `ROBOT` (o `BUZBOM` si es el misil), `SAUCMV`, `BOUND` y `MOTION` (el jugador).

### 3.3 Mundo, escala y cámara [FUENTE + DERIVADO]

- Las posiciones son de **16 bits por eje**. El mundo es un **toro de 65.536 × 65.536**: si sales por un lado, entras por el contrario.
- Los ángulos son de **8 bits** (256 unidades = 360°) más un bit de fracción. Cada `ITANGL`/`DTANGL` gira media unidad.
- La Math Box mide distancias como **`max + 3/8·min`** de |dx| y |dy|. Está confirmado en `mathbox.cpp` de MAME. Los radios de colisión del fuente están en esa métrica.
- **Escala de los modelos [DERIVADO].** La Math Box rota los vértices con un producto que **divide x e y entre 2**, y `ROTPNT` duplica el centro del objeto para compensar. La z (`DDEND`) no pasa por ese producto. Resultado: en unidades del mundo, x e y de un modelo valen ½ de su `.NWORD` y z vale el `.NWORD` entero. Lo confirma una coincidencia exacta: el radio tanque-pirámide de `PROXTB` (0x340 = 832) es justo la media eslora del tanque (576) más la media base de la pirámide (256).
- **Cámara [FUENTE].** El ojo está a la altura z = 0 y el suelo de los modelos en z = −320. `ROTPNT` descarta los objetos a menos de 512 unidades, a más de 15.744 o fuera de un cono de ±45°.
- **Las montañas son un panorama de 8 tramos de 512 unidades** (4.096 = 360°, 16 unidades por paso de ángulo). El volcán está en x = 3.067 del panorama, que se dedujo de `16·TANGLE − 0x605` en `MOUNTS`. Al dibujar el panorama, el cráter cae justo ahí.
- El campo tiene **21 obstáculos** (`PTBLX1` / `PTBLY1` / `PTBLO1`) de cuatro tipos: pirámide, cubo, pirámide grande (`ROCK1`) y losa baja (`ROCK2`). Los proyectiles **pasan por encima de la losa baja**: su radio en `PRXTBL` es 0.

### 3.4 Controles del jugador [FUENTE: `MOTION`, `MTAB`]

| Palanca izq. | Palanca der. | Acción | Giro por tick |
|---|---|---|---|
| ↑ | ↑ | avance a tope (2 × `M.FOR`) | — |
| ↓ | ↓ | retroceso a tope | — |
| ↑ | — | avanza y gira a la derecha | ½ unidad |
| — | ↑ | avanza y gira a la izquierda | ½ unidad |
| ↓ | — | retrocede y gira a la izquierda | ½ unidad |
| — | ↓ | retrocede y gira a la derecha | ½ unidad |
| ↑ | ↓ | **pivota** a la derecha | 1 unidad |
| ↓ | ↑ | **pivota** a la izquierda | 1 unidad |

Cada `M.FOR` avanza `0,75 · 127` unidades (`M.SET`). Al chocar contra un obstáculo (radio 0x480) o contra el otro tanque, `BAKOUT` devuelve el tanque a su posición anterior, suena `BOING`, la cámara rebota (`BOUNCE`) y aparece **«MOTION BLOCKED BY OBJECT»**.

### 3.5 Proyectiles [FUENTE: `MOTION`, `SHUPDT`, `COLCHK`]

- **Un proyectil por tanque a la vez.** Vida: `FIRECT = 0x7F`, es decir, 127 subpasos, unos 32 ticks o **2 segundos**.
- Avanza `2 · 127` unidades por subpaso, lo que da un alcance de unas 32.000 unidades: medio mundo.
- El radio de impacto contra un tanque depende de cómo se miran los dos (`SHRTCK`): un blanco de costado es más ancho que uno de frente.
- La mira cambia a su variante alineada (`XCROS1`) cuando el enemigo está a menos de 2 unidades de ángulo, y parpadea mientras tu proyectil sigue en vuelo.

### 3.6 Enemigos y puntuación [FUENTE: `QWIKCK`, `SAUCHK`, `BUZBOM`, `TR7CHK`]

`HITS` guarda la puntuación **en BCD y en miles**.

| Enemigo | Puntos | Comportamiento |
|---|---|---|
| **Tanque** | 1.000 | IA estándar (§3.7) |
| **Supertanque** (`TR7`) | 3.000 | Avanza al doble de velocidad y gira al doble. Aparece cuando ya han salido 6 misiles en la partida (`NOR2D3 ≥ 5`) |
| **Misil** (`R2D3`, «buzz bomber») | 2.000 | Entra desde lo alto (`STARTZ = 0x1800`), baja 256 unidades por tick y pica en zigzag. Si choca con un obstáculo, **levita** por encima. Solo se le puede dar por debajo de `TOP = 0x200`. Nunca ataca por la espalda. Debajo lleva un anillo de puntos en el suelo (`EXHOBJ`) |
| **Platillo** | 5.000 | Inofensivo. Aparece a partir de 2.000 puntos y deriva al azar. El proyectil enemigo también puede derribarlo, pero sin puntos |

Además hay una regla anti-acampada: si en unos 65 segundos (4 × 256 ticks) no muere nadie, llega un misil aunque no se haya llegado a la puntuación de `MISLVL`.

### 3.7 IA del tanque enemigo [FUENTE: `ROBOT`, `REACT`, `FIREIT`, `ROBCHK`]

- **Decisión** (cuando el temporizador `ACTION` llega a 0):
  - Si lleva demasiado tiempo «portándose bien» (`FTIMER == 0xFF`), si lo decide una moneda al aire o si el jugador tiene 100.000 puntos o más, **ATACA** (`RGOAL = TRACK()`).
  - Si no, compara las bajas del jugador con las suyas: si el jugador va **por delante**, ataca; con **empate**, **huye** en perpendicular (`TRACK() XOR 0x40`); si el jugador va **por detrás**, **vaga** al azar.
- **Giro**: si el error angular supera `SKILL`, pivota rápido; si no, corrige medio paso y avanza. Cuando está alineado con su objetivo, da un segundo paso.
- **Distancia**: no avanza si ya está cerca (byte alto de la distancia < 5; el supertanque, < 8).
- **Disparo** (`FIREIT`): tienen que cumplirse todas estas condiciones: `FTIMER ≥ 0x20` (2 segundos de gracia al aparecer), alineación de menos de 2 unidades, proyectil disponible y jugador vivo. Con un novato (menos de 2.000 puntos), solo dispara si el jugador lo ve y lo tiene cerca.
- **Choque**: retrocede girando hacia un lado al azar y luego elige un rumbo nuevo.
- **Aparición** (`TANKCK` / `ROB1`): a 24.576 unidades (o a la mitad) **delante del jugador**, dentro de una ventana angular que se ensancha cuantas más bajas le saques. Así, a un buen jugador le llegan enemigos por los lados y por detrás. Desde `MISLVL` hay un 50 % de probabilidad de que salga un misil.
- **Rarezas del rev 1 que se conservan.** En partida, `REACT` nunca usa la diferencia de bajas (esa rama es código muerto) y `SKILL` oscila entre 2 y 16. Además, el misil corrige el rumbo de forma asimétrica: +2 cuando el error es pequeño por un lado y +1 cuando es grande por ese mismo lado. Se reproducen tal cual.

### 3.8 Vidas, bonus y DIP switches [FUENTE: `NEWLIF`, `BONTBL`, `MISLVL`]

| Ajuste | Valores | Origen |
|---|---|---|
| Vidas iniciales | `(OPTION & 3) + 2` → **2, 3, 4 o 5** | `PWRON` |
| Vida extra | **ninguna / 15.000 / 25.000 / 50.000** | `BONTBL: .BYTE 0,14,24,49` (en BCD; la vida se da al *superar* el valor) |
| Superbonus | **100.000** | `NEWLIF`, rama `SUPER` |
| El misil aparece desde | **5.000 / 10.000 / 20.000 / 30.000** | `MISLVL: .BYTE 5,10,20,30` |

### 3.9 Pantalla [FUENTE: `ENGMSG`, `INFO`, `DRADAR`, `WNSHLD`, `BATTLE`]

- Textos literales en sus posiciones originales: `ENEMY IN RANGE`, `ENEMY TO LEFT` / `RIGHT` / `REAR`, `MOTION BLOCKED BY OBJECT`, `SCORE`, `HIGH SCORE`, `GAME OVER`, `PRESS START`, `HIGH SCORES`, `GREAT SCORE`, `ENTER YOUR INITIALS`…
- **Radar** centrado en (0, 316), con un barrido de `+0x0B` por tick. El eco se enciende cuando el barrido pasa por encima del enemigo, se apaga poco a poco y va acompañado de un pitido. El alcance es de 32.768 unidades.
- **Muerte**: el parabrisas se resquebraja con las 8 rutinas `CRACK0`–`CRACK7`, una por tick. Después reapareces en una posición aleatoria del mundo.
- **Atracción**: se alternan la tabla de récords (8 s), el **logotipo 3D «BATTLE ZONE»**, que sube desde el suelo y se aleja (12 s; «ZONE» aparece después que «BATTLE», con la «ULTIMATE KLUDGE» del original), y una demostración en la que el tanque conduce solo (16 s). La atracción es muda.
- **Color**: vectores verdes y franja superior roja, como el celofán del monitor original. El brillo baja con la distancia (`DQUE`, con un suelo de 0x30).

---

## 4. Arquitectura de `battlezonepy.py`

```
§0   Cabecera PEP 723, docstring, imports
§1   CONSTANTES   ← cada valor cita su rutina de origen
     DATOS        ← tablas extraídas de BZMTNS.MAC / BZONE.MAC (no editar a mano)
§2   MATEMÁTICA   ángulos de 8 bits, wrap toroidal, distancia de la Math Box, TRACK
§3   MODELOS      escalado de los modelos y panorama de montañas
§4   CAMPO        los 21 obstáculos
§5   ENTIDADES    Body, Shell, Enemy, Piece, Saucer, Ctrl, Options
§6   JUEGO        clase Game: IA, física, explosiones, radar, estados y récords (sin pygame)
§7   RENDER       cámara, recorte contra el plano cercano, proyección, brillo de fósforo
§8   SONIDO       síntesis al estilo POKEY: motor, disparos, explosiones, radar, platillo, misil
§9   ENTRADA      teclado y mando → dos palancas
§10  BUCLE        paso fijo de 64 ms con render interpolado
§11  SELFTEST     el contrato del juego, sin ventana
```

- **La lógica no importa pygame.** `Game` es Python puro y emite eventos de sonido que la interfaz consume. Por eso `--selftest` funciona en un servidor sin pantalla.
- **Paso fijo.** La lógica corre a 15,625 Hz, como el original. El render va a la frecuencia del monitor e interpola posiciones y ángulos entre ticks. `--classic` lo desactiva y deja ver el tirón de 1980.
- **Determinismo.** `random.Random(seed)` sustituye al `PRAND` del POKEY. Con `--seed N` se reproduce una partida exacta.
- **Aritmética de bytes donde importa.** Ángulos de 0 a 255 con medio paso, posiciones módulo 65.536 y comparaciones en BCD (`bcd()`) allí donde el 6502 restaba bytes BCD en binario.

### `--selftest`

```text
✓ wrap(65535 + 1) == 0 · distancia toroidal simétrica · Math Box max + 3/8·min
✓ 21 obstáculos · tick de 64 ms
✓ tanque 1000 / supertanque 3000 / misil 2000 · platillo 5000
✓ vida extra al superar 15000 (no al alcanzarlo) · superbonus en 100000
✓ vidas = 2..5 según el DIP
✓ un proyectil por tanque; vive 127 subpasos
✓ BAKOUT: el jugador nunca queda dentro de un obstáculo
✓ el enemigo no dispara durante la gracia de FTIMER < 0x20
✓ el platillo no aparece con menos de 2000 puntos
✓ recorte: ningún vértice detrás del plano cercano
✓ 3000 ticks de partida y 2000 de atracción sin excepciones
```

---

## 5. Estado

| Hito | Contenido | Estado |
|---|---|---|
| **M1 — Mundo en alambre** | Cámara, los 21 obstáculos, horizonte, montañas, luna, volcán, controles de tanque, `BAKOUT` | hecho |
| **M2 — Combate** | Proyectiles en 4 subpasos, IA completa, explosiones en 6 piezas, radar, mira, mensajes | hecho |
| **M3 — Bestiario** | Misil con picado, zigzag y levitación; platillo; supertanque; puntuación, bonus y DIP switches | hecho |
| **M4 — Recreativa** | Atracción con logotipo 3D, parabrisas roto, récords con iniciales, sonido sintetizado | hecho |
| **M5 — Pulido** | Brillo de fósforo, mando, pantalla completa, récords persistentes | hecho |
| **M6 — Calibración** | Contrastar las [HIPÓTESIS] con MAME (§6) | pendiente |

---

## 6. Desviaciones respecto al original

Cada sitio donde battlezonepy se aparta de la recreativa, a propósito o por falta de datos. Una desviación sin documentar es un bug.

**Hipótesis por calibrar contra MAME:**

- **Focal de la proyección 3D.** Se usa la escala del panorama (4096 / 2π ≈ 652 unidades por radián), para que los objetos no resbalen contra las montañas al girar. El factor exacto de la división de la Math Box no sale del fuente.
- **Amplitud de seno y coseno.** Se supone ±127 en el byte alto. De ella dependen la velocidad del tanque, del proyectil y del misil.
- **Antena del radar del enemigo.** Se coloca en el mástil del modelo (vértices 22–23 de `TNKTBL`). El fuente la desplaza con un cálculo cuya escala no está clara.

**Decisiones de diseño:**

- **Render interpolado** a la frecuencia del monitor, con brillo de fósforo. Con `--classic` se vuelve a 15,6 imágenes por segundo y sin brillo.
- **Tipografía propia.** La original vive en `VGAN`, que no está en el repositorio publicado.
- **Sonido aproximado.** Se sintetiza en lugar de emular el POKEY y la circuitería discreta de `BZSOUN`.
- **Azar.** `PRAND` y el contador de interrupciones (`$INTCT`) se sustituyen por un generador determinista.
- **Partida libre.** No hay monedas ni modos de crédito (`COIN65`), ni test de servicio (`BZSTST`).
- **Récords persistentes** en un fichero JSON. Las iniciales por defecto rinden homenaje a «EDR», la firma de Ed Rotberg que aparece en el código.
- **Estallido de proyectil.** Es el dibujo `EXPIC` escalado por la distancia; la escala exacta de `D.SPCL` es una aproximación.

---

## 7. Fuentes

### Primarias

| Fuente | Qué aporta | Confianza |
|---|---|---|
| [historicalsource/battlezone](https://github.com/historicalsource/battlezone) | El fuente original de Atari (*rev 1*): `BZONE.MAC`, `BZMTNS.MAC`, `BZONE.DOC`, `MBUDOC.DOC`. Publicado el 14-oct-2021 | **CONFIRMADO** (leído y extraído para este proyecto) |
| [MAME — `src/mame/atari`](https://github.com/mamedev/mame/tree/master/src/mame/atari) | `bzone.cpp`, `bzone_a.cpp` y `mathbox.cpp` (BSD). De ahí sale la fórmula de distancia de la Math Box, y es el **oráculo de comportamiento** para calibrar | **CONFIRMADO** |
| [6502disassembly.com — Battlezone](https://6502disassembly.com/va-battlezone/) | El desensamblado anotado de Andy McFadden (**rev 2**), con notas sobre la Math Box, los objetos 3D y las diferencias entre revisiones | **CONFIRMADO** |

> El código de historicalsource es la **rev 1**. Donde la rev 2 de McFadden difiere, battlezonepy sigue la rev 1.

### Secundarias

| Fuente | Interés | Confianza |
|---|---|---|
| [cbzone (pkgsrc)](https://pkgsrc.se/games/cbzone) | Clon en C para Unix/X11, derivado de un original en Fortran de Justin S. Revenaugh | PROBABLE |
| [LittleZone (Arduino)](https://blog.arduino.cc/?p=31591) | Versión para Arduino Uno de Mark Wilson, hecha estudiando el 6502 | FUENTE ÚNICA |
| [Battlezone 2000 — Games That Weren't](https://gamesthatwerent.com/2025/01/battlezone-2000) | Contexto de la Jaguar | FUENTE ÚNICA |
| [AtariProtos — 2600](https://www.atariprotos.com/2600/software/battlezone/battlezone.htm) | La versión para Atari 2600 | Referencia |

### Fuera de alcance

- **Bradley Trainer**: solo existe la emulación de MAME.
- **Battlezone 1998, 98 Redux y Battlezone II**: código cerrado. La copia filtrada que circula **ni se usa ni se consulta**.
- **Ports domésticos**: no hacen falta; el canon es la recreativa.

---

## 8. Aspectos legales

- El código de battlezonepy tiene licencia **MIT** (ver `LICENSE`).
- **No se incluyen ROMs, binarios ni gráficos** de Atari. Los modelos son coordenadas extraídas de la lectura del fuente, que está publicado para su estudio histórico.
- «Battlezone» es una marca de Atari. Este proyecto es una recreación de fan, educativa y sin ánimo de lucro. El título de la ventana la identifica como **homenaje no oficial**, y no se usa el logotipo de Atari.

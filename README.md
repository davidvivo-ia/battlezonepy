# battlezonepy

A faithful recreation of **Battlezone (Atari, 1980)** in Python, in **a single file: `battlezonepy.py`**.

It is not a "game inspired by" Battlezone. The goal is to rebuild the 1980 arcade machine's behaviour from its **original source code**, published by the historicalsource collective. Every constant, AI rule and score in this document comes from reading `BZONE.MAC` and its companions. When something is an inference and not a reading, it says so.

> Confidence labels used throughout the document:
> **[SOURCE]** read directly in Atari's original assembly ·
> **[DERIVED]** calculated from values in the source ·
> **[HYPOTHESIS]** reasonable, but must be calibrated against MAME ·
> **[DESIGN]** a decision of this project, not of the original.

---

## 1. The non-negotiable rule: one file

```
battlezonepy/
├── battlezonepy.py   ← the whole game: engine, models, AI, sound, UI
├── README.md
└── LICENSE           ← MIT
```

No `src/`, no packages, no asset folders. Models, texts and sounds are **generated in code**, just as in 1980 they lived as tables inside the ROM. If the file grows beyond 3,000 lines, the fix is to write better code, not to split it.

Why: an 8 KB ROM held this game. A single, readable, runnable file is the honest tribute, and it is also the best format for studying it.

---

## 2. Stack (Python 2026)

| Piece | Choice | Reason |
|---|---|---|
| Interpreter | **Python ≥ 3.13** (tested on 3.14) | `match`, `dataclass(slots=True)`, modern typing, free-threaded optional |
| Graphics, input and audio | **pygame-ce ≥ 2.5** | Antialiased lines (`draw.aaline`), gamepad, `sndarray` for synthesised sound. The *community edition* is the live fork; classic `pygame` is lagging behind |
| Math | `math` from the stdlib | 3D for ~30 objects per frame does not need NumPy. Optional `numpy` only for generating the sound buffers |
| Dependencies | **PEP 723** inline in the script | The file declares its own dependencies; `uv run` resolves them without a `requirements.txt` |

Header expected in `battlezonepy.py`:

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["pygame-ce>=2.5"]
# ///
"""battlezonepy — Battlezone (Atari 1980) recreated from the original source."""
```

### Running it

```bash
# Recommended: uv resolves dependencies from the script header
uv run battlezonepy.py

# Classic route
python -m pip install "pygame-ce>=2.5"
python battlezonepy.py

# Options
python battlezonepy.py --fullscreen --scale 2
python battlezonepy.py --lives 3 --bonus 15000 --missile 10000   # arcade DIP switches
python battlezonepy.py --selftest                                 # headless tests, no window
```

---

## 3. What the original source tells us

### 3.1 Files and people [SOURCE]

- `BZONE.MAC` (~4,900 lines): the game core. Header: `DATE INITIATED: 25-APR-79`, `1ST PERSON VIEW OF TRADITIONAL ATARI TANK GAME`, `CONTROLS: ULTRA TANK TYPE CONTROLS`.
- `BZONE.DOC`: release sheet dated **31 August 1981**. Programmer **Edward Rotberg**, project leader **Morgan Hoff**. Assembler `MAC65`, linker `LINKM`.
- Link order: `BZMTNS, BZONE, BZSOUN, VGUT, BZSTST`.
- `BZMTNS.MAC`: horizon, mountains, volcano, **3D model tables** (`PYRTBL`, `CUBTBL`, `TNKTBL`, `SHLTBL`...), plus the DIP switch tables `BONTBL` and `MISLVL`.
- `BZSOUN.MAC`: sound over POKEY plus discrete circuitry.
- `MBUDOC.DOC` / `MBUCOD.V05`: microcode for the **Math Box** (AMD 2901), the coprocessor for rotations, products and division.
- `VGUT.MAC` / `VGMC.MAC`: utilities for the vector generator (AVG).

Rotberg documents his own method in the header: **"atomic comments"**, where each block of 1 to 20 instructions is labelled `;D` (decision), `;C` (computation), `;B` (branch), `;L` (linkage) and `;S` (synchronisation). We copy that idea: each function in `battlezonepy.py` names the original routine it reimplements (`# ← ROBOT`, `# ← COLCHK`...), so you can read both side by side.

### 3.2 Main loop [SOURCE: `MAIN`, line ~420]

One tick of the original, in order:

1. Sync with the NMI, animate the volcano (`VOLCNO`).
2. Draw the lower window, the upper window, the mountains and the horizon (`MOUNTS`).
3. `ROTATE`: pick the visible objects, then translate and rotate them into camera space.
4. `PNTPUT` + `DRAW`: project and emit vectors for each object.
5. Cracked windshield if the player has died (`WNSHLD`).
6. Radar (`DRADAR`), crosshair (`XCROSS` / `XCROS1` depending on alignment), messages.
7. **Physics, 4 sub-steps**: `COLCHK` + `SHUPDT` four times per frame. Shells move in substeps so they never "tunnel" through an object. **This must be kept.**
8. `QWIKCK` (fast shell → enemy check), `ROBOT`, `SAUCMV`, `BOUND`, `MOTION` (player).

### 3.3 World, angles and units [SOURCE + DERIVED]

- Positions are **16 bits per axis** (`TPOSX`, `TPOSY`). The world is a **65,536 × 65,536 torus**: leave on one side and you come back on the other. There are no walls.
- Angles are **8 bits** (`TANGLE`, so 256 units = 360°) plus 1 fractional bit (`LANGLE`, steps of `0x80`). One `ITANGL`/`DTANGL` call turns half a unit.
- **21 fixed obstacles** in the playfield (`PTBLX1` / `PTBLY1`, values ×4). Copy them as is: that map *is* Battlezone.
- Models: **pyramid**, **cube** (wide and narrow block), tank, shell, treads, radar, explosion fragments, missile (`R2DOBJ`), saucer (`SAUOBJ`) and supertank (`TR7OBJ`). The vertex tables are in `BZMTNS.MAC` (`PYRTBL`, `CUBTBL`...).

### 3.4 Player controls [SOURCE: `MOTION`, `MTAB`]

Two levers, each forward / neutral / back. The `MTAB` table maps the combinations:

| Left lever | Right lever | Action | Turn per tick |
|---|---|---|---|
| ↑ | ↑ | full forward | — |
| ↓ | ↓ | full reverse | — |
| ↑ | — | forward + turn right | ½ unit |
| — | ↑ | forward + turn left | ½ unit |
| ↓ | — | reverse + turn | ½ unit |
| ↑ | ↓ | **pivot on the spot** right | 1 unit |
| ↓ | ↑ | **pivot on the spot** left | 1 unit |

Movement step: `M.SET` uses `0.75 · sin/cos(angle)` per call, and full forward calls it twice [SOURCE]. When you hit an obstacle, `BAKOUT` restores the previous position, plays `BOING`, applies a camera `BOUNCE` and shows **"MOTION BLOCKED BY OBJECT"**.

Mapping in battlezonepy [DESIGN]:

- **Tank mode (purist)**: `Q`/`A` = left lever, `P`/`L` = right lever (or both analog sticks on a gamepad).
- **Arcade-assisted mode (default)**: arrow keys, plus the space bar to fire. This translates to the `MTAB` combinations above, so the physics stays identical.

### 3.5 Shells [SOURCE: `MOTION` fire, `SHUPDT`, `COLCHK`]

- **One shell per tank at a time** (`FIRECT ≠ 0` blocks a new shot). Lifetime: `FIRECT = 0x7F` = 127 sub-steps, about **32 frames** at 4 sub-steps per frame.
- Velocity is `2·cos/sin(angle)` per sub-step [DERIVED], so range ≈ 127 × 254 ≈ **32,000 units, half the world**.
- A shell that hits an obstacle explodes (`FIRECT = 0xA0`). A shell that hits a tank calls `EXINIT`.

### 3.6 Enemies and scoring [SOURCE: `QWIKCK`, `SAUCHK`, `BUZBOM`]

`HITS` holds the score in **BCD, in thousands**. On screen, `SCORE ... 000` is printed literally.

| Enemy | Points | Behaviour |
|---|---|---|
| **Tank** | 1,000 | Standard AI (§3.7) |
| **Supertank** (`TR7`) | 3,000 | Moves forward at **double speed**, keeps more distance (`MOUTHI ≥ 8` vs `≥ 5`). Appears once `NOR2D3 ≥ 5` (`TR7CHK`) |
| **Missile** (`R2D3`, "buzz bomber") | 2,000 | Swoops at the player in zigzags (`FRAME & 0x1F` modulates the angle). Starts high (`STARTZ = 0x1800`). On hitting an obstacle it **levitates** over it and dives again. Can only be hit below `TOP = 0x200`. Never attacks from behind |
| **Saucer** | 5,000 | Harmless. Appears only with `HITS ≥ 2` (2,000 points). Random drift with a random timer and spins `+8` per tick |

Only one ground enemy at a time (tank, supertank or missile), plus at most one saucer.

### 3.7 Enemy tank AI [SOURCE: `ROBOT`, `REACT`, `FIREIT`, `ROBCHK`]

A state machine driven by `ACTION` (reaction timer) and `STATE`:

- **Decision** (`ACTION == 0`):
  - If it has been "polite" too long (`FTIMER == 0xFF`), or a random coin flip says so, or the player has 100,000 points or more: it switches to **ATTACK** (`RGOAL = TRACK()`, the arctangent towards the player).
  - Otherwise it compares the player's kills (`HITS`) with the enemy's (`HITS+2`):
    - player **ahead**: **ATTACK**
    - **tied**: **EVADE**, heading for `TRACK() XOR 0x40`, a perpendicular escape route
    - player **behind**: **RANDOM**, wandering with `RGOAL ± (rand & 0x1F)`
  - During attract mode, the enemy always wanders randomly.
- **Adaptive difficulty** (`REACT`): reaction time and aim accuracy (`SKILL`) depend on `HITS − HITS_enemy`. The better you play, the faster and more accurate it gets. It is dynamic difficulty balancing in 1979.
- **Steering**: if the angular error is larger than `SKILL`, it pivots 2 steps per tick. If not, it fine-tunes 1 step and advances.
- **Distance**: it does not advance when it is already close (`MOUTHI < 5`), so it "duels" rather than rams.
- **Firing** (`FIREIT`) requires all of the following: `FTIMER ≥ 0x20` (a grace period after spawning), alignment `< 2` units, its shell available, and the player alive. With a beginner player (`HITS < 2`), it only fires if the player can see it.
- **Collision with an obstacle**: `STATE` bit 0 is set, it backs up turning to a random side, then sets a new goal.
- **Spawn** (`ROBCHK`/`TANKCK`): the enemy appears in a **window in front of the player** whose width depends on the score difference. If `HITS ≥ MISLVL[dip]`, there is a 50% chance a **missile** comes instead of a tank.

### 3.8 Lives, bonus and DIP switches [SOURCE: `NEWLIF`, `BONTBL`, `MISLVL`]

| Setting | Values | Source |
|---|---|---|
| Starting lives | `(OPTION & 3) + 2` → **2, 3, 4, 5** | `PWRON` init |
| Bonus life | **none / 15,000 / 25,000 / 50,000** | `BONTBL: .BYTE 0,14,24,49` (BCD, life awarded when you *exceed* the value) |
| Super bonus | **100,000** | `NEWLIF`, branch `SUPER` |
| Missile appears from | **5,000 / 10,000 / 20,000 / 30,000** | `MISLVL: .BYTE 5,10,20,30` |

Recommended defaults [DESIGN]: 3 lives, bonus at 15,000, missile from 10,000.

### 3.9 HUD and messages [SOURCE: `ENGMSG` table]

Literal texts to reproduce: `ENEMY IN RANGE`, `ENEMY TO LEFT` / `RIGHT` / `REAR` (blinking when the enemy is outside the field of view, `PTURN ≥ 22`), `MOTION BLOCKED BY OBJECT`, `GAME OVER`, `PRESS START`, `SCORE … 000`, `HIGH SCORE … 000`, `HIGH SCORES`, `GREAT SCORE`, `ENTER YOUR INITIALS`, `© 1980 ATARI` (in the attract screen only, out of respect for the original).

- **Radar**: a circle in the upper centre with a sweep of `+0x0B` per tick, and a blip for the enemy within range.
- **Crosshair**: two variants (`XCROSS` / `XCROS1`) depending on whether the enemy is aligned.
- **Death**: progressive **cracked windshield** (`CRACKS`, 16 segments drawn in steps), with the image frozen.
- **High scores**: 10 entries with 3 initials, chosen letter by letter.
- **Attract mode**: an AI tank drives around alone, then the `BATTLE ZONE` logo, then the score table.

### 3.10 Colour and display [SOURCE + DESIGN]

The original monitor was a **monochrome green** vector display, with a red cellophane strip over the upper part where the radar and messages sit. Reproduce it like this:

- Background `#000000`, vectors `#33FF33` with intensity modulated by depth (`DQUE`: distant objects are dimmer).
- Upper band (HUD) in `#FF3030`.
- **Phosphor glow** effect [DESIGN, optional]: draw every line twice, the second time wider and at 25% alpha, onto an additive surface.
- Base logical resolution of 1024 × 768 (the AVG's aspect ratio), scaled to the window.

---

## 4. Architecture of `battlezonepy.py` [DESIGN]

Order of sections inside the file, each one introduced by a header comment:

```
§0  Header PEP 723, docstring, imports, CLI (argparse)
§1  CONSTANTS        ← all the original values, with a comment citing the routine/line
§2  FIXED MATH       angle8 ↔ radians, sin/cos tables of 256 entries, toroidal wrap
§3  MODELS           vertices/edges of PYR, CUB, TNK, SHL, R2D, SAU, TR7, TREADS, EXP*
§4  PLAYFIELD        the 21 obstacles of PTBLX1/PTBLY1, mountains and volcano
§5  ENTITIES         @dataclass(slots=True): Tank, Shell, Missile, Saucer, Debris
§6  RENDER           camera, near-plane clipping, perspective projection, glow
§7  AI               robot(), react(), fireit(), buzbom(), saucmv(), robchk()
§8  PHYSICS          motion(), colchk() ×4 substeps, qwikck(), objobj(), bakout()
§9  SOUND            POKEY-style synthesis: engine, shot, explosion, boing, bonus bell
§10 STATES           ATTRACT → PLAY → DEATH(cracks) → GAMEOVER → INITIALS → HISCORES
§11 LOOP             fixed timestep + render with interpolation
§12 SELFTEST         assertions for scoring, wrap, collisions, AI, without opening a window
```

### Technical decisions

- **Fixed timestep.** Logic runs at a constant rate and rendering at the screen's rate. The original synced to the NMI; its effective frequency is **[HYPOTHESIS]** to be measured in MAME (`-debug`, breakpoint on `MAIN`). Until it is measured, use **60 Hz logic** and a single `TICK_HZ` constant so it can be calibrated.
- **Faithful integers where they matter.** Angles stay `int` in `0..255`, plus a half-unit fraction. Positions stay `int` modulo 65536. Projection and drawing use `float`. This reproduces the original "feel" (fixed angular granularity) without suffering 8-bit arithmetic.
- **The Math Box is not emulated.** It is replaced by `math` and a 256-entry sin/cos table. What matters is the result (rotation, distance, `TRACK`'s arctangent), not the AMD 2901 microcode.
- **Toroidal distance.** Always use the minimum difference modulo 65536 on each axis before computing a distance or an angle. Without this the AI goes mad at the world's edge.
- **Clipping against the near plane** before dividing by `z`. Without it, an obstacle right behind the camera produces lines that cross the whole screen.
- **Synthesised sound.** No WAVs. Square waves and LFSR noise generated with `pygame.sndarray` (or `array` without NumPy). Engine at two speeds (`LIDLE` / `HIDLE`), as in the original.
- **Deterministic RNG.** `random.Random(seed)` stands in for POKEY's `PRAND`. `--seed N` reproduces a game exactly; that is essential for debugging the AI.
- **No global state.** One `Game` object that holds everything; pure functions where possible, so `--selftest` can test them.

### Minimum `--selftest` contract

```text
✓ wrap(65535 + 1) == 0 and toroidal distance symmetric
✓ tank 1000 / supertank 3000 / missile 2000 / saucer 5000
✓ bonus life when crossing 15000 (not when reaching it); super bonus at 100000
✓ lives = 2..5 according to DIP
✓ one shell per tank; shell dies at 127 substeps
✓ BAKOUT: the player never ends up inside an obstacle
✓ the enemy does not fire during the FTIMER < 0x20 grace period
✓ the saucer does not appear with HITS < 2
✓ clipping: no projected vertex with z ≤ near
```

---

## 5. Roadmap

| Milestone | Content | Done when… |
|---|---|---|
| **M1 — Wireframe world** | Camera, the 21 obstacles, horizon, mountains, volcano, tank controls, `BAKOUT` | You can drive the battlefield and crash into pyramids |
| **M2 — Combat** | Shells with 4 substeps, enemy tank with full AI, explosions, radar, crosshair, messages | One full game against tanks only |
| **M3 — Bestiary** | Missile with swoop and levitation, saucer, supertank, scoring, bonus, DIP switches | All values in §3.6–3.8 pass `--selftest` |
| **M4 — Arcade** | Attract mode, cracked windshield, high score table with initials, synthesised sound | It is indistinguishable at a glance from a MAME capture |
| **M5 — Polish** | Phosphor glow, gamepad, fullscreen, persistent high scores in `~/.battlezonepy.json` | — |

Validation method: put MAME (`bzone` driver) side by side with battlezonepy and compare speeds, turn rates and shell range. Every difference becomes either a constant fix or an entry in §6.

---

## 6. Known deviations from the original

Document here every place where battlezonepy deliberately departs from the arcade. A deviation that is not documented is a bug.

- *(empty until M1)*

---

## 7. Reference sources

### Primary, used for this document

| Source | What it gives | Confidence |
|---|---|---|
| [historicalsource/battlezone](https://github.com/historicalsource/battlezone) | Original Atari source (~8,500 lines of 6502, *rev 1*), `BZONE.DOC`, `MBUDOC.DOC`. Published 14-Oct-2021 | **CONFIRMED** (read for this README) |
| [6502disassembly.com — Battlezone](https://6502disassembly.com/va-battlezone/) | Andy McFadden's annotated disassembly (**rev 2**, SourceGen), vector decoder, notes on Math Box, 3D objects and rev 1 → rev 2 differences | **CONFIRMED** |
| [MAME — `src/mame/atari`](https://github.com/mamedev/mame/tree/master/src/mame/atari) | `bzone.cpp`, `bzone_a.cpp`, `mathbox.cpp` (BSD). Emulates Battlezone, Bradley Trainer, Red Baron and Desert Wars. It is the **behavioural oracle** for calibration | **CONFIRMED** |

> Note: the historicalsource code is **rev 1**; McFadden's disassembly is **rev 2**. Where they differ, battlezonepy follows **rev 1** (it is the readable source) and lists rev 2 differences in §6 if they are adopted.

### Secondary

| Source | Interest | Confidence |
|---|---|---|
| [cbzone (pkgsrc)](https://pkgsrc.se/games/cbzone) | Unix/X11 clone in C, derived from a Fortran original by Justin S. Revenaugh. Useful to compare clipping solutions | PROBABLE |
| [LittleZone (Arduino)](https://blog.arduino.cc/?p=31591) | Mark Wilson's port to Arduino Uno, made by studying the 6502 | SINGLE SOURCE |
| [Battlezone 2000 — Games That Weren't](https://gamesthatwerent.com/2025/01/battlezone-2000) | Jaguar context (Hover Strike code with "BattleZone Modifications") | SINGLE SOURCE |
| [AtariProtos — 2600](https://www.atariprotos.com/2600/software/battlezone/battlezone.htm) | Atari 2600 port | Reference |
| [Atari arcade sources list](https://6502disassembly.com/other-va.html) | Index of sources for other Atari vector games | Reference |

### Out of scope

- **Bradley Trainer** (military version): only the MAME emulation exists; no source has been found.
- **Battlezone 1998 / 98 Redux / Battlezone II**: closed code. The leaked copy that circulates is **not used or consulted**. It is a different game (RTS/FPS) anyway.
- **Home ports** (2600, Spectrum, C64, Atari 8-bit): no published disassemblies. They are not needed: the arcade is the canon.

---

## 8. Legal

- battlezonepy's code is under the **MIT** licence (see `LICENSE`).
- **No ROMs, binaries or graphics** from Atari are included or distributed. The models are rebuilt as coordinates in Python from reading the source, which is published for historical study. If you need to cross-check the binary, McFadden explains how to regenerate it [here](https://6502disassembly.com/va-battlezone/bingen.html).
- "Battlezone" is a trademark of Atari. This project is a non-commercial, educational fan recreation. If it is ever distributed publicly, the window title and the attract screen must make clear that it is an unofficial tribute, and any use of the logo must be avoided.

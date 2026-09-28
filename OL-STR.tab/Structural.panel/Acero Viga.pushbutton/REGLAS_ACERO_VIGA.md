# Acero - Vigas: reglas de colocación

Reglas acordadas con el usuario para el botón **Acero Viga** (armazón
estructural). Las de estribos son las mismas de columnas
(`Acero.pushbutton/REGLAS_ACERO.md`). **No cambiarlas sin su visto bueno.**

## 1. Viga continua

- Una viga es el conjunto de **tramos del mismo tipo, en el mismo eje, uno
  a continuación del otro** (separados hasta 1.20 m por sus apoyos). El
  modelo parte cada viga en sus apoyos; el plugin la vuelve a juntar.
- Al generar una viga seleccionada se genera **la viga completa**, con
  todos sus tramos.

Código: `revit_mcp/rebar_beams.py` → `beam_lines`.

## 2. Apoyos y luces libres

- Son apoyos: **columnas y muros** que atraviesan la mayor parte del
  peralte de la viga, y **vigas que la cruzan con igual o mayor peralte**.
  Una viga menos peraltada que llega a ella no es un apoyo (la carga ella).
  Un muro solo debajo de la viga (solera) no la corta.
- Las **luces libres** van de cara a cara de los apoyos.

Código: `rebar_beams.py` → `line_supports`, `spans_and_ends`;
`rebar_spec.py` → `clear_spans`.

## 3. Estribos

- En **cada luz libre**, la distribución del tipo (ej. `1@0.05, 10@0.10,
  rto@0.20`) se mide **desde la cara de cada apoyo** hacia el centro, con
  las mismas reglas que en columnas: un conjunto por tramo y extremo
  (1@0.05 Individual, 10@0.10 Número con espaciado, resto @0.20 desde cada
  extremo), el sobrante al centro.
- **Dentro de los apoyos no van estribos de viga.**
- Estribos a la misma cota: apilados un diámetro, juntos sin solaparse.

## 4. Barras superiores e inferiores

- Son las barras del dibujo de la sección (arriba del centro = superiores).
- Corren **continuas por toda la viga**, de la cara lejana del apoyo
  extremo menos el recubrimiento, a la del otro extremo.
- **Anclaje por tipo** ("Anclaje en extremos"):
  - **Gancho 90:** doblan 90° hacia la otra cara (las superiores hacia
    abajo, las inferiores hacia arriba); la pata se escribe en cm por
    diámetro en "3. Empalme y gancho".
  - **Recto:** terminan en el recubrimiento.

## 5. Empalmes

- Si una barra (con sus ganchos) supera la **longitud máxima** (9 m), se
  corta con el **empalme** de su diámetro (cm, en "3. Empalme y gancho"):
  - **superiores:** en el **tercio central** de una luz libre;
  - **inferiores:** en un **tercio extremo**, fuera de la zona de
    confinamiento de estribos.
- La barra que termina en el empalme se desvía un diámetro en vertical
  (1:6), así las dos quedan juntas sin atravesarse.
- El peso de cada barra se reparte entre los tramos que recorre.

Código: `rebar_spec.py` → `beam_lap_zones`, `lap_pieces`, `beam_bar_points`;
`rebar_beams.py` → `generate_line`.

## 6. Vigas de peralte variable (cartelas)

- El plugin lee la forma real de la viga (fondo y tope a lo largo). Si el
  peralte cambia, la trata como **cartela**; el título lo indica
  ("cartela: peralte 0.50 a 0.85 m").
- La sección se **dibuja en el mayor peralte**; en la planta, la línea
  punteada marca la sección menor.
- Cada estribo toma la altura de su posición: sus esquinas de arriba
  conservan la distancia al tope y las de abajo al fondo (el recubrimiento
  se mantiene). Como cada uno tiene su altura, se crean **uno por uno**
  (regla Individual), con la misma distribución.
- Las barras siguen su cara: las inferiores el fondo inclinado, con doblez
  donde cambia la pendiente.
- Supone ancho constante (solo cambia el peralte).

Código: `rebar_spec.py` → `haunch_polyline`, `follow_profile`;
`rebar_beams.py` → `BeamLine._read_profile`, `polygon_at`.

## 7. Pendiente (segunda etapa)

- Bastones (barras adicionales en apoyos y al centro).

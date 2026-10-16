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
- **Voladizo** (un tramo con un extremo libre, sin apoyo): la distribución
  empieza **solo desde la cara de la columna** (sus zonas y luego el
  resto) y sigue hasta el extremo libre (el último a 5 cm de él); no se
  distribuye desde los dos lados. El tramo se rotula "(voladizo)".
- **Dentro de los apoyos no van estribos de viga.**
- Estribos a la misma cota: apilados un diámetro, juntos sin solaparse.

## 4. Barras superiores e inferiores

- Son las barras del dibujo de la sección (arriba del centro = superiores).
- Corren **continuas por toda la viga**, de la cara lejana del apoyo
  extremo menos el recubrimiento, a la del otro extremo.
- **Anclaje por tipo** ("Anclaje en extremos"):
  - **Gancho 90:** doblan 90° hacia la otra cara (las superiores hacia
    abajo, las inferiores hacia arriba); la pata se toma de la tabla por
    diámetro en "3. Empalme y gancho" o, si no está, **12 db** (redondeado
    a 5 cm). En el alzado, el texto "Gancho 0.20" de cada extremo se
    cambia con un clic, solo para esa viga (0 = recto).
  - **Recto:** terminan en el recubrimiento.

## 5. Empalmes

- Si una barra (con sus ganchos) supera la **longitud máxima** (9 m) y su
  diámetro está **marcado** en "3. Empalme y gancho", se corta con su
  **empalme** (cm, precargado según la E.060 clase B, editable); si no está
  marcado queda en una sola barra, con advertencia.
- Dónde va el empalme:
  - **superiores:** en el **tercio central** de una luz libre;
  - **inferiores:** desde la **cara de la columna**, en el **cuarto
    extremo** (L/4) de la luz libre; si el empalme es más largo que L/4,
    empieza igual en la cara y se extiende hacia el tramo.
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

## 7. Por viga: estribos de cada tramo, bastones, balancines y cortes (2026-10-16)

- Se guardan **por viga** (parámetro de instancia `EA_Viga_Linea` en sus
  elementos), no por tipo: cada viga tiene sus propios tramos.
- **Estribos por tramo:** cada luz libre puede tener su propia distribución
  (cota amarilla bajo el tramo en el ALZADO); vacía = la del tipo.
- Se dibujan **a mano alzada** ("Dibujar acero"): clic en el inicio y en el final;
  en la mitad de arriba de la viga es bastón, en la de abajo balancín; se
  ajustan a 5 cm y a las caras de los apoyos. Cada uno tiene **su cota**
  (arriba los bastones, abajo los balancines, en filas): clic en el número
  para la longitud (queda fijo el extremo que está en un apoyo), arrastrar
  sus extremos.
- **Bastón** = acero adicional **superior** sobre un apoyo: por defecto Ln/3
  a cada lado de las caras del apoyo (Ln del tramo de cada lado); en un apoyo
  extremo empieza en el extremo de la viga con el anclaje de las corridas.
- **Balancín** = acero adicional **inferior** en un tramo: por defecto su
  centro, dejando 0.15 Ln libres en cada cara.
- Los adicionales van en la capa de las corridas, en los espacios más anchos,
  si caben con el espacio libre de la E.060 (>= db y 2.5 cm); si no, en una
  segunda capa hacia adentro.
- **Cortes automáticos:** uno en cada zona de cada tramo donde el acero es el
  mismo (cortada en los extremos de bastones y balancines); se arrastran,
  borran (clic derecho) o agregan, y "Cortes automáticos" los recalcula.
  Cada uno muestra su sección con las barras que pasan y el área de acero.

Código: `revit_mcp/beam_extras.py`; `rebar_beams.py` → `generate_line(line_settings=...)`.
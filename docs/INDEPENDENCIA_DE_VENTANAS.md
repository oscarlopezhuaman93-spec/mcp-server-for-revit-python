# Independencia de ventanas (BOKI Estructura)

Cada botón (ventana) es independiente: lo que se cambia para una ventana
nunca debe cambiar lo que hace otra.

## Qué se comparte y quién lo usa

| Módulo compartido (`revit_mcp/`) | Ventanas que lo usan |
| --- | --- |
| `rebar_spec.py` (reglas puras: distribuciones, ganchos, anclajes) | Columna, Viga, Muro, Cimentación, Escalera, Cuadro Columnas |
| `rebar_columns.py` (sección, generación, vecinos, parámetros EA_) | Columna, Muro, Viga, Cimentación, Escalera, Cuadro Columnas |
| `rebar_views.py` (alzado, planta, 3D) | Columna, Viga, Muro, Cimentación, Escalera |
| `rebar_foundation.py` (caras, cortes, mallas) | Cimentación, Muro, Escalera |
| `rebar_stairs.py` | Escalera |
| `column_table.py` | Cuadro Columnas |
| `footing_table.py` (estilos propios "BOKI Zapatas ...") | Cuadro Zapatas |
| `rebar_splice_ui.py` | Columna, Viga, Muro, Cimentación |

## Reglas

1. **Los valores por defecto de un módulo compartido son los de la ventana
   original.** Una ventana nueva que necesita otro comportamiento lo pide
   con un parámetro propio (ejemplo: `column_neighbors(...,
   categories=rc.WALL_NEIGHBOR_CATEGORIES)` en Acero Muro), nunca cambiando
   la lista o el valor que usan las demás.
2. **Lo propio de una ventana vive en su carpeta** (`<Botón>.pushbutton/`):
   textos, colores, categorías, valores iniciales.
3. **Cada ventana guarda su configuración en su propio parámetro**:
   columnas en los `EA_` de tipo, muros en `EA_Muro_Acero` (instancia),
   cimentaciones en `EA_Cim_*` (tipo), escaleras en `EA_Esc_Acero` (instancia).
4. **Antes de entregar un cambio en un módulo compartido**:
   - `uv run --extra test pytest tests/unit -q` (incluye
     `test_window_independence.py`, que vigila estas reglas);
   - en Revit, `tests/revit/smoke_ventanas.py`: abre cada ventana de acero
     con un elemento real y reporta OK / FALLA y los vecinos que ve.

## Historial

- 2026-10-03: Acero Columna mostraba el muro vecino en lugar de la columna:
  se había ampliado `NEIGHBOR_CATEGORIES` (compartido) para Acero Muro.
  Corregido con `WALL_NEIGHBOR_CATEGORIES` solo para Muro; agregadas la
  prueba de independencia y la prueba de humo en Revit.

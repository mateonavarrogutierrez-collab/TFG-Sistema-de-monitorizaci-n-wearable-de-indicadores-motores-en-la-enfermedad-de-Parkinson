# Servidor v2 — qué cambia y cómo usarlo

## Instalar y arrancar
```
cd Desktop\Phyton\v2
..\.venv\Scripts\activate
pip install -r requirements.txt
python recalcular_todo.py --csv resultados_v2.csv   (unos 2 min la primera vez)
python app.py
```
Abre http://localhost:5000 y elige el día en el desplegable de arriba.

La v2 lee los mismos datos de `Phyton\datos_dias` y **no modifica ningún fichero de datos brutos**.
Sus resúmenes se guardan aparte, en `datos_dias\_resumenes_v2`. La v1 (`Phyton\app.py`) sigue funcionando igual.

## Cambios
| Problema en la v1 | Solución en la v2 |
|---|---|
| Registros duplicados por lotes reenviados (hasta 3.500 en un día) | Se eliminan al leer |
| Días 17–24 con paquetes partidos (22–82 muestras en vez de ~116) | Se reconstruye un flujo continuo; las ventanas se definen en segundos |
| fs fija de 22,8 Hz | fs medida en cada tramo (≈ 23,0–23,4 Hz) |
| Temblor con Goertzel en 5 bins: no detectaba 5–10 Hz | FFT con ventana Hann, 3 ejes, banda 4–11 Hz, umbral de fracción (50%) **y** de amplitud (0,02 g RMS) |
| Temblor sin episodios | Episodios con hora, duración, frecuencia y amplitud |
| Marcha y bloqueos calculados sobre todo el día (5.807 "bloqueos" en 5 h) | Solo en tramos detectados como marcha; "interrupciones breves" en lugar de "bloqueos" |
| Asimetría (medía nº de muestras sobre/bajo la media) | Eliminada: no se puede medir con un solo reloj |
| Pasos sobre az con gravedad | Frecuencia de paso × duración en ventanas de marcha; carrera separada |
| "HRV" | Renombrada a "dispersión de la FC" (no hay intervalos RR) |
| Sueño mezclando dos noches del mismo fichero | Una noche = 22:00 del día anterior → 10:00; periodo de sueño detectado por actigrafía; reloj quitado excluido |
| /tendencias reescribía todos los ficheros de datos | Solo lee resúmenes |
| Envío que cruza la medianoche dejaba días sin recalcular | Se recalculan todos los días tocados en el envío |
| `debug=True` en 0.0.0.0 | Desactivado (solo con `FLASK_DEBUG=1`, y entonces en 127.0.0.1) |
| El dashboard solo enseñaba "hoy" | Selector de día |

## Lo que NO está resuelto
- **Cifrado**: sigue sin haber HTTPS ni autenticación.
- **Validación**: los umbrales de temblor y marcha están ajustados con datos de sujeto sano y señales sintéticas (`tests_sinteticos.py`), no con eventos etiquetados.
- **Falsos positivos de temblor**: actividades rítmicas a ~5 Hz (cepillarse los dientes) dan el mismo patrón.

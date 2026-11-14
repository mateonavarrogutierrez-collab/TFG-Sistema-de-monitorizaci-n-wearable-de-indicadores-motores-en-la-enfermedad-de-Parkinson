# ============================================================
#  recalcular_todo.py
#  Recalcula el resumen de TODOS los días con los algoritmos
#  actuales y saca una tabla por consola (útil para el capítulo 4).
#  No modifica los datos brutos, solo datos_dias/_resumenes/.
#
#  USO (desde la carpeta del servidor, con app.py parado):
#    python recalcular_todo.py
#    python recalcular_todo.py --csv resultados.csv
# ============================================================
import sys, time, csv
import procesamiento as P

COLUMNAS = ["fecha", "horas_registradas", "n_duplicados", "minutos_temblor", "n_episodios_temblor",
            "minutos_marcha", "minutos_carrera", "cadencia_ppm", "cv_cadencia_pct", "braceo_rms_g",
            "n_paradas_breves", "pasos", "pasos_firmware", "n_caidas", "media_hr", "dispersion_fc",
            "puntuacion_sueno", "horas_sueno"]

if __name__ == "__main__":
    filas = []
    for fecha in P.listar_fechas_disponibles():
        t0 = time.time()
        r = P.recalcular_resumen(fecha)
        r["fecha"] = fecha
        filas.append(r)
        print(f"{fecha}  {time.time()-t0:5.1f}s  temblor={r['minutos_temblor']} min  "
              f"marcha={r['minutos_marcha']} min  pasos={r['pasos']}/{r['pasos_firmware']}  "
              f"caídas={r['n_caidas']}  sueño={r['puntuacion_sueno']}", flush=True)
    if "--csv" in sys.argv:
        ruta = sys.argv[sys.argv.index("--csv") + 1]
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore", delimiter=";")
            w.writeheader()
            w.writerows(filas)
        print(f"Guardado {ruta}")

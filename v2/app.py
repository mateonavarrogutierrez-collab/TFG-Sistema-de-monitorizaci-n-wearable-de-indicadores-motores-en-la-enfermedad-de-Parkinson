# ============================================================
#  app.py  (v2)
#  Servidor Flask — solo rutas, sin lógica de negocio
#
#  Instalación: pip install flask numpy
#  Uso:         python app.py
#  Interfaz:    http://localhost:5000   (o /?fecha=2026-08-29)
# ============================================================

import os, threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template
import procesamiento as P

app = Flask(__name__)

# Caché de las métricas completas del día mostrado: el dashboard se
# refresca cada 30 s y recalcular un día entero cada vez es caro.
# Se invalida si cambia cualquiera de los ficheros implicados.
_cache = {}
_lock = threading.Lock()

def _metricas(fecha):
    clave = tuple(P._mtime(f) for f in (P._fecha_mas(fecha, -1), fecha, P._fecha_mas(fecha, 1)))
    with _lock:
        if fecha in _cache and _cache[fecha][0] == clave:
            return _cache[fecha][1]
    m = P.calcular_dia(fecha)
    with _lock:
        if len(_cache) > 4:
            _cache.pop(next(iter(_cache)))
        _cache[fecha] = (clave, m)
    return m


@app.route("/")
def index():
    fechas = P.listar_fechas_disponibles()
    if not fechas:
        return render_template("index.html", fecha=None, fechas=[], metricas={}, resumenes=[],
                               noches=[], ref={}, fecha_actual=datetime.now().strftime("%d/%m/%Y %H:%M"))
    hoy = datetime.now().strftime("%Y-%m-%d")
    fecha = request.args.get("fecha") or (hoy if hoy in fechas else fechas[-1])
    if fecha not in fechas:
        fecha = fechas[-1]

    metricas = _metricas(fecha)
    resumenes = P.listar_resumenes()             # del más antiguo al más reciente
    for r in resumenes:
        r["nivel_temblor"] = P.nivel_temblor(r.get("pct_temblor"))
        r["nivel_sueno"] = P.nivel_desde_puntuacion_sueno(r.get("puntuacion_sueno"))

    # Últimas 3 noches hasta la fecha mostrada (desde resúmenes ligeros)
    noches = [r for r in resumenes if r["fecha"] <= fecha][-3:][::-1]

    # Referencia personal (mediana de los días anteriores) para
    # interpretar braceo y cadencia: los umbrales absolutos desde la
    # muñeca no están validados, la evolución del propio paciente sí
    # es interpretable.
    ref = {c: P.referencia_personal(resumenes, c, fecha)
           for c in ("braceo_rms_g", "cadencia_ppm", "cv_cadencia_pct", "minutos_marcha")}

    return render_template("index.html",
        fecha=fecha, fechas=fechas,
        fecha_actual=datetime.now().strftime("%d/%m/%Y %H:%M"),
        metricas=metricas,
        resumenes=resumenes[::-1],
        noches=noches, ref=ref, hoy=hoy,
        hora_inicio=P.HORA_INICIO_SUENO, hora_fin=P.HORA_FIN_SUENO,
        umbral_caida=P.UMBRAL_CAIDA,
    )


@app.route("/datos", methods=["POST"])
def recibir_datos():
    """Recibe un lote de registros desde la app Android.
    { "fecha": "...", "registros": [...], "lote": 1, "total_lotes": 6 }"""
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({"ok": False, "error": "Sin datos"}), 400
        registros = payload.get("registros", [])
        if not registros:
            return jsonify({"ok": False, "error": "Lista de registros vacía"}), 400
        fecha = payload.get("fecha", datetime.now().strftime("%Y-%m-%d"))
        lote, total = payload.get("lote"), payload.get("total_lotes")
        n = P.guardar_sesion_y_resumen(fecha, registros, lote=lote, total_lotes=total)
        print(f"✓ +{n} registros" + (f" (lote {lote}/{total})" if lote else ""))
        return jsonify({"ok": True, "registros": n}), 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400


def _sin_series(m):
    return {k: v for k, v in m.items() if k not in ("pulso_por_minuto",)}

@app.route("/resultados", methods=["GET"])
def resultados():
    fechas = P.listar_fechas_disponibles()
    if not fechas:
        return jsonify({"error": "Sin datos"}), 404
    return jsonify(_sin_series(_metricas(fechas[-1])))

@app.route("/sesion/<fecha>", methods=["GET"])
def sesion_por_fecha(fecha):
    if fecha not in P.listar_fechas_disponibles():
        return jsonify({"error": f"No hay datos para {fecha}"}), 404
    return jsonify(_sin_series(_metricas(fecha)))


# Tendencias: SOLO resúmenes ligeros (no se tocan los datos brutos)
@app.route("/tendencias/semanal", methods=["GET"])
def tendencias_semanal():
    return jsonify({"periodo": "semanal", "dias": 7, "datos": P.calcular_tendencias(7)})

@app.route("/tendencias/mensual", methods=["GET"])
def tendencias_mensual():
    return jsonify({"periodo": "mensual", "dias": 30, "datos": P.calcular_tendencias(30)})


if __name__ == "__main__":
    print("=" * 50)
    print("  Servidor TFG Parkinson")
    print("  Interfaz: http://localhost:5000")
    print("=" * 50)
    # debug=False: con debug=True y host 0.0.0.0, el depurador de
    # Werkzeug permite ejecutar código en este PC desde la red local.
    # Para desarrollar: set FLASK_DEBUG=1 y host 127.0.0.1.
    depurar = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="127.0.0.1" if depurar else "0.0.0.0", port=5000,
            debug=depurar, threaded=True)

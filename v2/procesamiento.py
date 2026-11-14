# ============================================================
#  procesamiento.py  (v2)
#  Persistencia de los datos + orquestación de los cálculos.
#  Los algoritmos están en analisis.py.
#
#  Cambios respecto a la v1:
#   - Los registros duplicados (lotes reenviados) se eliminan al leer.
#   - Un día se analiza con TODOS sus registros, aunque alguno esté
#     guardado en el fichero del día vecino (bug antiguo de fechas).
#   - El sueño se calcula por NOCHE (22:00 del día anterior -> 10:00),
#     no mezclando trozos de dos noches del mismo fichero.
#   - /tendencias y el historial leen solo los resúmenes ligeros; ya
#     no se cargan ni se reescriben los ficheros de datos brutos.
#   - Si un envío reparte registros entre varios días, se recalculan
#     TODOS los días afectados al llegar el último lote.
#   - Los resúmenes llevan versión: los calculados con la v1 se
#     recalculan solos la primera vez que se piden.
# ============================================================

import json, os, threading
from datetime import datetime, date, timedelta

import analisis as A

def _buscar_carpeta_datos():
    """datos_dias junto a este fichero o en la carpeta de arriba
    (la v2 vive en Phyton/v2 y usa los datos de Phyton/datos_dias).
    Se puede forzar con la variable de entorno TFG_DATOS."""
    if os.environ.get("TFG_DATOS"):
        return os.environ["TFG_DATOS"]
    aqui = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(aqui, "datos_dias"), os.path.join(aqui, "..", "datos_dias")):
        if os.path.isdir(c):
            return os.path.normpath(c)
    return os.path.join(aqui, "datos_dias")

CARPETA_DIAS      = _buscar_carpeta_datos()
# Carpeta de resúmenes propia de la v2: no pisa los de la v1
CARPETA_RESUMENES = os.path.join(CARPETA_DIAS, "_resumenes_v2")
VERSION_RESUMEN   = 2

# Reexportados para app.py / plantilla
HORA_INICIO_SUENO = A.HORA_INICIO_SUENO
HORA_FIN_SUENO    = A.HORA_FIN_SUENO
UMBRAL_CAIDA      = A.UMBRAL_CAIDA

os.makedirs(CARPETA_DIAS, exist_ok=True)
os.makedirs(CARPETA_RESUMENES, exist_ok=True)

# Cerrojo global de E/S (en Windows os.replace falla si otro hilo
# tiene el fichero abierto; con threaded=True eso pasa).
_lock_io = threading.Lock()


# ============================================================
# FICHEROS
# ============================================================

def _ruta_dia(fecha):
    return os.path.join(CARPETA_DIAS, f"{fecha}.jsonl")

def _ruta_resumen(fecha):
    return os.path.join(CARPETA_RESUMENES, f"{fecha}.json")

def _fecha_mas(fecha, dias):
    return (datetime.strptime(fecha, "%Y-%m-%d") + timedelta(days=dias)).strftime("%Y-%m-%d")

def listar_fechas_disponibles():
    if not os.path.isdir(CARPETA_DIAS):
        return []
    return sorted(f[:-6] for f in os.listdir(CARPETA_DIAS)
                  if f.endswith(".jsonl") and os.path.isfile(os.path.join(CARPETA_DIAS, f)))

def cargar_registros_dia(fecha):
    """Registros brutos de UN fichero JSONL (líneas corruptas se descartan)."""
    with _lock_io:
        ruta = _ruta_dia(fecha)
        if not os.path.exists(ruta):
            return []
        registros = []
        with open(ruta, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    registros.append(json.loads(linea))
                except json.JSONDecodeError:
                    continue
        return registros

def agregar_registros_dia(fecha, registros_nuevos):
    """Añade al final del JSONL — O(k) sobre los registros nuevos."""
    if not registros_nuevos:
        return
    with _lock_io:
        with open(_ruta_dia(fecha), "a", encoding="utf-8") as f:
            for r in registros_nuevos:
                f.write(json.dumps(r) + "\n")

def _mtime(fecha):
    ruta = _ruta_dia(fecha)
    return os.path.getmtime(ruta) if os.path.exists(ruta) else 0

# Compatibilidad con los scripts de migración/reparación
def cargar_sesion_dia(fecha):
    if not os.path.exists(_ruta_dia(fecha)):
        return None
    return {"fecha": fecha, "registros": cargar_registros_dia(fecha)}

def guardar_sesion_dia(fecha, sesion):
    with _lock_io:
        ruta, tmp = _ruta_dia(fecha), _ruta_dia(fecha) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in sesion.get("registros", []):
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, ruta)
    _invalidar_cache()

def get_registros(sesion):
    return sesion.get("registros", []) if sesion else []


# ============================================================
# FLUJO DE UN DÍA (con caché en memoria)
# ============================================================

_cache_flujos = {}          # fecha_inicio -> (clave_mtimes, flujo)
_lock_cache = threading.Lock()

def _invalidar_cache():
    with _lock_cache:
        _cache_flujos.clear()

def flujo_periodo(fecha):
    """Flujo continuo de 22:00 del día anterior a 24:00 de `fecha`
    (cubre la noche que termina ese día y el día completo). Lee los
    ficheros del día anterior, del día y del siguiente, porque algunos
    registros quedaron guardados en el fichero vecino."""
    fechas = [_fecha_mas(fecha, -1), fecha, _fecha_mas(fecha, 1)]
    clave = tuple(_mtime(f) for f in fechas)
    with _lock_cache:
        if fecha in _cache_flujos and _cache_flujos[fecha][0] == clave:
            return _cache_flujos[fecha][1]
    regs = []
    for f in fechas:
        regs.extend(cargar_registros_dia(f))
    d0 = datetime.strptime(fecha, "%Y-%m-%d")
    t_ini = (d0 - timedelta(hours=24 - A.HORA_INICIO_SUENO)).timestamp()
    t_fin = (d0 + timedelta(days=1)).timestamp()
    regs = [r for r in regs if t_ini - 10 <= (r.get("t") or 0) < t_fin + 10]
    flujo = A.recortar(A.construir_flujo(regs), t_ini, t_fin)
    with _lock_cache:
        if len(_cache_flujos) > 3:
            _cache_flujos.pop(next(iter(_cache_flujos)))
        _cache_flujos[fecha] = (clave, flujo)
    return flujo


# ============================================================
# CÁLCULO COMPLETO DE UN DÍA
# ============================================================

def calcular_dia(fecha):
    """Todas las métricas de un día natural + la noche que termina ese día."""
    fl = flujo_periodo(fecha)
    d0 = datetime.strptime(fecha, "%Y-%m-%d")
    dia = A.recortar(fl, d0.timestamp(), (d0 + timedelta(days=1)).timestamp())
    t_noche_ini = (d0 - timedelta(hours=24 - A.HORA_INICIO_SUENO)).timestamp()
    t_noche_fin = (d0 + timedelta(hours=A.HORA_FIN_SUENO)).timestamp()
    noche = A.recortar(fl, t_noche_ini, t_noche_fin)

    fs = [x[2] for x in dia["tramos"]]
    marcha = A.calcular_marcha(dia)
    return {
        "fecha": fecha,
        "temblor": A.calcular_temblor(dia),
        "marcha": marcha,
        "caidas": A.detectar_caidas(dia),
        "pulso": A.calcular_pulso(dia),
        "sueno": A.calcular_sueno(noche, t_noche_fin - t_noche_ini),
        "pasos": marcha.get("pasos", 0),
        "pasos_firmware": A.pasos_firmware(dia),
        "datos": {
            "n_registros": fl["n_registros"],
            "n_duplicados": fl["n_duplicados"],
            "n_muestras_corruptas": fl["n_muestras_corruptas"],
            "horas_registradas": round(len(dia["t"]) / A.FS_NOMINAL / 3600, 1),
            "fs_media": round(sum(fs) / len(fs), 2) if fs else None,
        },
        "pulso_por_hora": A.pulso_por_hora(dia),
        "pulso_por_minuto": A.pulso_por_minuto(dia),
    }


def resumen_desde_metricas(m):
    """Dict plano y ligero para historial y tendencias."""
    t, mar, p, s = m["temblor"], m["marcha"], m["pulso"], m["sueno"]
    return {
        "version": VERSION_RESUMEN,
        "horas_registradas": m["datos"]["horas_registradas"],
        "n_duplicados": m["datos"]["n_duplicados"],
        "pct_temblor": t.get("pct_temblor"),
        "minutos_temblor": t.get("minutos_temblor"),
        "n_episodios_temblor": t.get("n_episodios"),
        "pasos": m["pasos"], "pasos_firmware": m["pasos_firmware"],
        "minutos_marcha": mar.get("minutos_marcha"),
        "minutos_carrera": mar.get("minutos_carrera"),
        "cadencia_ppm": mar.get("cadencia_ppm"),
        "periodicidad": mar.get("periodicidad"),
        "cv_cadencia_pct": mar.get("cv_cadencia_pct"),
        "braceo_rms_g": mar.get("braceo_rms_g"),
        "n_paradas_breves": mar.get("n_paradas_breves"),
        "paradas_por_hora_marcha": mar.get("paradas_por_hora_marcha"),
        "n_caidas": m["caidas"]["n_caidas"],
        "media_hr": p.get("media_hr"), "dispersion_fc": p.get("dispersion_fc"),
        "min_hr": p.get("min_hr"), "max_hr": p.get("max_hr"),
        "puntuacion_sueno": s.get("puntuacion"),
        "horas_sueno": s.get("horas_sueno"),
        "hr_nocturna": s.get("hr_nocturna"),
        "fragmentacion": s.get("fragmentacion"),
        "movimiento": s.get("movimiento"),
    }


def calcular_resumen_diario(fecha):
    return resumen_desde_metricas(calcular_dia(fecha))


# ============================================================
# RESÚMENES LIGEROS
# ============================================================

def guardar_resumen_dia(fecha, resumen):
    with _lock_io:
        ruta, tmp = _ruta_resumen(fecha), _ruta_resumen(fecha) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(resumen, f, indent=2)
        os.replace(tmp, ruta)

def cargar_resumen_dia(fecha):
    with _lock_io:
        ruta = _ruta_resumen(fecha)
        if not os.path.exists(ruta):
            return None
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)

def recalcular_resumen(fecha):
    r = calcular_resumen_diario(fecha)
    guardar_resumen_dia(fecha, r)
    return r

def obtener_resumen(fecha):
    """Resumen del día; si falta o es de una versión anterior, se recalcula."""
    r = cargar_resumen_dia(fecha)
    if r is None or r.get("version") != VERSION_RESUMEN:
        r = recalcular_resumen(fecha)
    return r

def listar_resumenes():
    """[{fecha, ...resumen}] de todos los días, del más antiguo al más reciente."""
    return [dict(obtener_resumen(f), fecha=f) for f in listar_fechas_disponibles()]

def calcular_tendencias(n_dias):
    return listar_resumenes()[-n_dias:]


# ============================================================
# RECEPCIÓN DE LOTES (POST /datos)
# ============================================================

_fechas_pendientes = set()      # días tocados en el envío en curso
_lock_pendientes = threading.Lock()

def guardar_sesion_y_resumen(fecha, registros_nuevos, lote=None, total_lotes=None):
    """Reparte cada registro al fichero de su fecha REAL (por su
    timestamp) y lo añade en O(k). Los días tocados se acumulan y,
    al llegar el último lote del envío, se recalculan todos (más el
    día siguiente de cada uno, cuya noche empieza a las 22:00)."""
    es_ultimo = (lote is None) or (total_lotes is None) or (lote >= total_lotes)

    grupos = {}
    for r in registros_nuevos:
        t = r.get("t", 0)
        f = datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t and t > 0 else fecha
        grupos.setdefault(f, []).append(r)
    for f, regs in grupos.items():
        agregar_registros_dia(f, regs)

    with _lock_pendientes:
        _fechas_pendientes.update(grupos)
        if not es_ultimo:
            return len(registros_nuevos)
        pendientes = set(_fechas_pendientes)
        _fechas_pendientes.clear()

    _invalidar_cache()
    disponibles = set(listar_fechas_disponibles())
    a_recalcular = set(pendientes)
    a_recalcular.update(_fecha_mas(f, 1) for f in pendientes if _fecha_mas(f, 1) in disponibles)
    for f in sorted(a_recalcular):
        recalcular_resumen(f)
    if len(grupos) > 1:
        print(f"⚠ Lote repartido entre {len(grupos)} días: {sorted(grupos)}")
    return len(registros_nuevos)


# ============================================================
# ETIQUETAS PARA EL DASHBOARD
# ============================================================

def nivel_temblor(pct):
    if pct is None: return "—"
    return "Alto" if pct >= 30 else "Medio" if pct >= 10 else "Bajo"

def nivel_desde_puntuacion_sueno(p):
    if p is None: return "—"
    return "Buena" if p >= 75 else "Regular" if p >= 50 else "Mala"

def referencia_personal(resumenes, campo, hasta_fecha=None):
    """Mediana del campo en los días anteriores a `hasta_fecha`
    (o en todos) — referencia individual para comparar tendencias."""
    vals = [r[campo] for r in resumenes
            if r.get(campo) is not None and (hasta_fecha is None or r["fecha"] < hasta_fecha)]
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]

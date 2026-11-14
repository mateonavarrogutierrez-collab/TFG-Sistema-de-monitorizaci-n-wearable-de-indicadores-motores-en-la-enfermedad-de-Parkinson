# ============================================================
#  analisis.py
#  Algoritmos de análisis clínico (v2) — trabajan sobre un FLUJO
#  CONTINUO de muestras, no sobre "registros" sueltos.
#
#  Por qué: el formato de los paquetes que llegan del reloj NO es
#  constante en los datos reales (días 16 y 26-04/09: ~116 muestras
#  cada 5 s; días 17-24: paquetes partidos de ~22-82 muestras cada
#  1-4 s). Cualquier algoritmo que asuma "1 registro = 5 s = 114
#  muestras" da resultados distintos según el día. Aquí:
#    1. se eliminan registros duplicados (lotes reenviados),
#    2. se ordenan por tiempo y se descartan muestras corruptas,
#    3. se reconstruye un flujo continuo por tramos sin cortes,
#       con su frecuencia de muestreo REAL medida en cada tramo,
#    4. todos los algoritmos usan ventanas de duración fija en
#       SEGUNDOS sobre ese flujo.
#
#  Solo depende de numpy.
# ============================================================

import math
from datetime import datetime
import numpy as np

FS_NOMINAL      = 23.1   # Hz — solo para el primer paquete de cada tramo
GAP_MAX_S       = 10.0   # un salto mayor entre paquetes corta el tramo
VENTANA_S       = 5.0    # ventana de análisis de temblor (s)

# ---------- Temblor ----------
BANDA_TEMBLOR   = (4.0, 11.0)  # Hz (techo por debajo de Nyquist ≈ 11,5 Hz)
UMBRAL_RATIO_TEMBLOR = 0.50    # fracción de potencia dinámica en banda
UMBRAL_RMS_TEMBLOR   = 0.020   # g RMS mínimos en la banda
MIN_VENTANAS_EPISODIO = 2      # episodio >= 10 s
HUECO_EPISODIO  = 1            # ventanas sin temblor toleradas dentro de un episodio

# ---------- Marcha / pasos (detección espectral) ----------
VENTANA_MARCHA_S   = 5.0
BANDA_PASO         = (1.2, 3.0)  # Hz de paso: 72-180 pasos/min
F_CARRERA          = 2.4         # Hz: por encima se considera carrera, no marcha
UMBRAL_DIN_MARCHA  = 0.05        # g RMS dinámico mínimo (0,5-5 Hz)
UMBRAL_PERIODICIDAD = 0.20       # fracción de potencia en el pico de paso
MIN_VENTANAS_TRAMO = 3           # tramo de marcha >= 15 s
MIN_VENTANAS_VECINAS = 4         # para contar una interrupción breve
MAX_VENTANAS_PARADA = 2          # interrupción de 5-10 s

# ---------- Caídas ----------
UMBRAL_CAIDA       = 3.0    # g
VENTANA_POST_S     = 2.0
REPOSO_POST        = 0.3    # |media - 1g|
VENTANA_PRE_S      = 1.0
UMBRAL_VAR_PRE     = 0.05

# ---------- Sueño ----------
HORA_INICIO_SUENO  = 22
HORA_FIN_SUENO     = 10   # franja ampliada: el periodo de sueño real se detecta dentro
EPOCA_SUENO_S      = 30.0
UMBRAL_MOV_EPOCA   = 0.04   # g — el ruido del sensor en sueño profundo ronda 0,02 g (p90 0,031)
MIN_SUENO_H        = 1.0    # periodo de sueño mínimo para puntuar la noche
UMBRAL_NO_PUESTO   = 0.011  # g — por debajo solo hay ruido del sensor: reloj quitado (en la mesa)
HUECO_SUENO_EPOCAS = 30     # despertares de hasta 15 min no parten el periodo de sueño
COBERTURA_MIN_NOCHE = 0.5   # fracción mínima de las 10 h nocturnas con datos


# ============================================================
# 1. FLUJO CONTINUO
# ============================================================

def _muestra_valida(a):
    try:
        v = (float(a["ax"]), float(a["ay"]), float(a["az"]))
        return v if all(math.isfinite(x) and abs(x) < 20 for x in v) else None
    except (KeyError, TypeError, ValueError):
        return None


def deduplicar(registros):
    """Quita registros repetidos (lotes reenviados tras un fallo de
    red). Dos registros son el mismo si coinciden timestamp y
    contenido del acelerómetro. Devuelve (únicos, nº duplicados)."""
    vistos, unicos = set(), []
    for r in registros:
        acel = r.get("acel") or []
        clave = (r.get("t", 0), len(acel),
                 tuple(str(a.get("m")) for a in acel[:6]),
                 tuple(str(a.get("m")) for a in acel[-3:]))
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(r)
    return unicos, len(registros) - len(unicos)


def construir_flujo(registros):
    """Convierte la lista de registros en un flujo continuo.

    Devuelve un dict con:
      t, ax, ay, az, m : arrays por muestra (t en segundos Unix)
      tramos          : lista de (inicio, fin, fs) en índices de muestra
      hr_t, hr        : lecturas de pulso (una por registro, hr > 0)
      steps_t, steps  : contador del podómetro del firmware
      n_duplicados, n_muestras_corruptas
    """
    unicos, n_dup = deduplicar(registros)
    unicos = [r for r in unicos if r.get("t", 0) > 0]
    unicos.sort(key=lambda r: r["t"])          # sort estable: respeta el orden de llegada

    # Agrupar en tramos sin cortes
    grupos, actual, t_prev = [], [], None
    for r in unicos:
        if t_prev is not None and (r["t"] - t_prev > GAP_MAX_S or r["t"] < t_prev):
            grupos.append(actual)
            actual = []
        actual.append(r)
        t_prev = r["t"]
    if actual:
        grupos.append(actual)

    T, AX, AY, AZ, tramos = [], [], [], [], []
    n_corruptas, idx = 0, 0
    for g in grupos:
        muestras = []
        n_primero = 0
        for j, r in enumerate(g):
            for a in r.get("acel") or []:
                v = _muestra_valida(a)
                if v is None:
                    n_corruptas += 1
                    continue
                muestras.append(v)
            if j == 0:
                n_primero = len(muestras)
        n = len(muestras)
        if n == 0:
            continue
        dur = g[-1]["t"] - g[0]["t"]
        fs = (n - n_primero) / dur if dur >= 30 else FS_NOMINAL
        if not (15.0 < fs < 30.0):
            fs = FS_NOMINAL
        t0 = g[-1]["t"] - (n - 1) / fs
        T.append(t0 + np.arange(n) / fs)
        arr = np.asarray(muestras, dtype=np.float64)
        AX.append(arr[:, 0]); AY.append(arr[:, 1]); AZ.append(arr[:, 2])
        tramos.append((idx, idx + n, fs))
        idx += n

    if T:
        t = np.concatenate(T); ax = np.concatenate(AX); ay = np.concatenate(AY); az = np.concatenate(AZ)
    else:
        t = ax = ay = az = np.zeros(0)
    m = np.sqrt(ax * ax + ay * ay + az * az)

    hr = [(r["t"], r["hr"]) for r in unicos if (r.get("hr") or 0) > 0]
    st = [(r["t"], r.get("steps") or 0) for r in unicos]
    return {
        "t": t, "ax": ax, "ay": ay, "az": az, "m": m, "tramos": tramos,
        "hr_t": np.array([x[0] for x in hr], dtype=float),
        "hr": np.array([x[1] for x in hr], dtype=float),
        "steps_t": np.array([x[0] for x in st], dtype=float),
        "steps": np.array([x[1] for x in st], dtype=float),
        "n_registros": len(unicos), "n_duplicados": n_dup,
        "n_muestras_corruptas": n_corruptas,
    }


def recortar(flujo, t_ini, t_fin):
    """Sub-flujo con las muestras en [t_ini, t_fin)."""
    t = flujo["t"]
    sel = (t >= t_ini) & (t < t_fin)
    idx = np.nonzero(sel)[0]
    tramos = []
    for a, b, fs in flujo["tramos"]:
        lo, hi = np.searchsorted(idx, a), np.searchsorted(idx, b)
        if hi > lo:
            tramos.append((int(lo), int(hi), fs))
    out = {k: flujo[k][sel] for k in ("t", "ax", "ay", "az", "m")}
    out["tramos"] = tramos
    for k_t, k_v in (("hr_t", "hr"), ("steps_t", "steps")):
        s = (flujo[k_t] >= t_ini) & (flujo[k_t] < t_fin)
        out[k_t], out[k_v] = flujo[k_t][s], flujo[k_v][s]
    for k in ("n_registros", "n_duplicados", "n_muestras_corruptas"):
        out[k] = flujo.get(k, 0)
    return out


def _media_movil(x, n):
    n = max(1, int(n))
    if n == 1 or len(x) < n:
        return x.copy() if len(x) >= n else np.full_like(x, x.mean() if len(x) else 0.0)
    k = np.ones(n) / n
    pad = n // 2
    xp = np.pad(x, (pad, n - 1 - pad), mode="edge")
    return np.convolve(xp, k, mode="valid")


def _pasa_banda(x, fs, f_lo=0.5, f_hi=3.0):
    """Pasa-banda sencillo con medias móviles (solo numpy):
    paso bajo ~f_hi menos paso bajo ~f_lo."""
    return _media_movil(x, round(fs / (2 * f_hi))) - _media_movil(x, round(fs / f_lo))


def _hora(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S") if t > 0 else "—"


def _ventanas(flujo, dur_s):
    """Itera ventanas de dur_s segundos dentro de cada tramo:
    (i_inicio, i_fin, fs)."""
    for a, b, fs in flujo["tramos"]:
        w = int(round(dur_s * fs))
        if w < 8:
            continue
        for i in range(a, b - w + 1, w):
            yield i, i + w, fs


# ============================================================
# 2. TEMBLOR — potencia espectral en banda 4-11 Hz (FFT)
# ============================================================

def espectro_temblor(ax, ay, az, fs):
    """Para una ventana: (ratio, rms_banda_g, f_pico).
    Se suman los espectros de los tres ejes (independiente de la
    orientación del reloj), con ventana Hann y sin la componente
    continua. ratio = potencia en banda / potencia dinámica total."""
    n = len(ax)
    w = np.hanning(n)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    P = np.zeros(len(f))
    for x in (ax, ay, az):
        x = x - x.mean()
        P += np.abs(np.fft.rfft(x * w)) ** 2
    dinamica = P[f >= 0.5].sum()
    if dinamica <= 0:
        return 0.0, 0.0, 0.0
    banda = (f >= BANDA_TEMBLOR[0]) & (f <= min(BANDA_TEMBLOR[1], fs / 2 - 0.3))
    pb = P[banda].sum()
    rms = math.sqrt(2.0 * pb / (n * (w * w).sum()))
    f_pico = float(f[banda][np.argmax(P[banda])]) if banda.any() else 0.0
    return float(pb / dinamica), rms, f_pico


def calcular_temblor(flujo):
    ventanas = []
    for i, j, fs in _ventanas(flujo, VENTANA_S):
        r, rms, fp = espectro_temblor(flujo["ax"][i:j], flujo["ay"][i:j], flujo["az"][i:j], fs)
        es = r >= UMBRAL_RATIO_TEMBLOR and rms >= UMBRAL_RMS_TEMBLOR
        ventanas.append((flujo["t"][i], flujo["t"][j - 1], r, rms, fp, es))
    if not ventanas:
        return {"disponible": False, "motivo": "Sin datos"}

    # Agrupación en episodios
    episodios, act, huecos = [], [], 0
    for v in ventanas + [None]:
        if v is not None and v[5]:
            act.append(v); huecos = 0
            continue
        if act and v is not None and huecos < HUECO_EPISODIO and v[0] - act[-1][1] < VENTANA_S + 1:
            huecos += 1
            continue
        if len(act) >= MIN_VENTANAS_EPISODIO:
            episodios.append({
                "inicio": _hora(act[0][0]), "fin": _hora(act[-1][1]),
                "t_inicio": float(act[0][0]),
                "duracion_s": int(round(act[-1][1] - act[0][0])),
                "ratio_medio": round(float(np.mean([x[2] for x in act])) * 100, 1),
                "rms_medio_g": round(float(np.mean([x[3] for x in act])), 3),
                "rms_max_g": round(float(max(x[3] for x in act)), 3),
                "frecuencia_hz": round(float(np.median([x[4] for x in act])), 1),
            })
        act, huecos = [], 0

    n_t = sum(1 for v in ventanas if v[5])
    # Serie para la gráfica: reducida a 1 punto por minuto (máximo del minuto)
    serie = {}
    for v in ventanas:
        k = int(v[0] // 60) * 60
        serie[k] = max(serie.get(k, 0.0), v[2] * 100 if v[5] else min(v[2] * 100, 49.9))
    claves = sorted(serie)
    return {
        "disponible": True,
        "n_ventanas": len(ventanas),
        "n_temblor": n_t,
        "pct_temblor": round(n_t / len(ventanas) * 100, 2),
        "minutos_temblor": round(n_t * VENTANA_S / 60, 1),
        "n_episodios": len(episodios),
        "episodios": episodios,
        "serie_minuto_t": [datetime.fromtimestamp(k).strftime("%H:%M") for k in claves],
        "serie_minuto_ratio": [round(serie[k], 1) for k in claves],
        "umbral_ratio": UMBRAL_RATIO_TEMBLOR, "umbral_rms": UMBRAL_RMS_TEMBLOR,
    }


# ============================================================
# 3. MARCHA — detección espectral de la frecuencia de paso
#
# Desde la muñeca, contar picos de la magnitud es poco fiable: cada
# paso no produce un pico limpio y el braceo añade picos propios.
# Se trabaja en frecuencia, igual que el temblor: en cada ventana
# de 5 s se busca el pico del espectro de la magnitud en la banda
# de paso (1,2-3,0 Hz). Una ventana es "marcha" si
#   - hay movimiento suficiente (RMS dinámico >= 0,05 g),
#   - el movimiento es periódico (>= 20% de la potencia 0,5-5 Hz
#     concentrada en el pico de paso),
# y forma parte de un tramo de al menos 3 ventanas seguidas (15 s).
# Agitar la mano (4-6 Hz) queda fuera de la banda de paso; los
# gestos sueltos no son periódicos ni duran 15 s.
# Pasos de la ventana = frecuencia de paso x 5 s.
# ============================================================

def _rasgos_marcha(m, fs):
    n = len(m)
    x = m - m.mean()
    w = np.hanning(n)
    P = np.abs(np.fft.rfft(x * w)) ** 2
    f = np.fft.rfftfreq(n, 1.0 / fs)
    total = P[(f >= 0.5) & (f <= 5.0)].sum()
    if total <= 0:
        return 0.0, 0.0, 0.0
    din = math.sqrt(2.0 * total / (n * (w * w).sum()))
    cand = np.nonzero((f >= BANDA_PASO[0]) & (f <= BANDA_PASO[1]))[0]
    k = int(cand[np.argmax(P[cand])])
    a, b, c = P[k - 1], P[k], P[k + 1] if k + 1 < len(P) else 0.0
    den = a - 2 * b + c
    d = 0.5 * (a - c) / den if den != 0 else 0.0     # interpolación parabólica
    f_paso = (k + max(-0.5, min(0.5, d))) * fs / n
    periodicidad = float(P[k - 1:k + 2].sum() / total)
    return din, f_paso, periodicidad


def clasificar_ventanas_marcha(flujo):
    """Lista de ventanas: (t_ini, t_fin, din_g, f_paso, periodicidad,
    braceo_rms_g, es_candidata)."""
    out = []
    for i, j, fs in _ventanas(flujo, VENTANA_MARCHA_S):
        din, fp, per = _rasgos_marcha(flujo["m"][i:j], fs)
        cand = din >= UMBRAL_DIN_MARCHA and per >= UMBRAL_PERIODICIDAD
        braceo = 0.0
        if cand:
            dyn = 0.0
            for k in ("ax", "ay", "az"):
                y = flujo[k][i:j]
                dyn = dyn + _pasa_banda(y, fs) ** 2
            braceo = float(np.sqrt(np.mean(dyn)))
        out.append([float(flujo["t"][i]), float(flujo["t"][j - 1]), din, fp, per, braceo, cand])
    return out


def _tramos(ventanas):
    """Agrupa ventanas candidatas consecutivas (sin hueco temporal)."""
    tramos, act = [], []
    for v in ventanas:
        if v[6] and (not act or v[0] - act[-1][1] < 1.0):
            act.append(v)
        else:
            if act:
                tramos.append(act)
            act = [v] if v[6] else []
    if act:
        tramos.append(act)
    return [tr for tr in tramos if len(tr) >= MIN_VENTANAS_TRAMO]


def calcular_marcha(flujo):
    ventanas = clasificar_ventanas_marcha(flujo)
    tramos = _tramos(ventanas)
    marcha = [tr for tr in tramos if np.median([v[3] for v in tr]) < F_CARRERA]
    carrera = [tr for tr in tramos if np.median([v[3] for v in tr]) >= F_CARRERA]

    pasos_total = int(round(sum(v[3] * VENTANA_MARCHA_S for tr in tramos for v in tr)))
    min_carrera = round(sum(len(tr) for tr in carrera) * VENTANA_MARCHA_S / 60, 1)
    if not marcha:
        return {"disponible": False, "motivo": "No se detectaron tramos de marcha",
                "pasos": pasos_total, "minutos_carrera": min_carrera, "n_tramos": 0}

    vm = [v for tr in marcha for v in tr]
    cad = np.array([v[3] * 60 for v in vm])
    per = np.array([v[4] for v in vm])
    braceo = np.array([v[5] for v in vm])
    # Variabilidad del ritmo: CV de la cadencia entre ventanas de un mismo tramo
    cvs = [np.std([v[3] for v in tr]) / np.mean([v[3] for v in tr]) * 100 for tr in marcha if len(tr) >= 4]

    # Interrupciones breves: 1-2 ventanas de casi quietud entre dos
    # tramos de marcha de >= 20 s. Candidatas a bloqueo, NO bloqueos
    # confirmados (desde la muñeca no se distinguen de una parada voluntaria).
    paradas = []
    for a, b in zip(marcha, marcha[1:]):
        hueco = b[0][0] - a[-1][1]
        if len(a) >= MIN_VENTANAS_VECINAS and len(b) >= MIN_VENTANAS_VECINAS and \
                0 < hueco <= MAX_VENTANAS_PARADA * VENTANA_MARCHA_S + 1:
            entre = [v for v in ventanas if a[-1][1] < v[0] < b[0][0]]
            if entre and all(v[2] < UMBRAL_DIN_MARCHA for v in entre):
                paradas.append({"hora": _hora(a[-1][1]), "duracion_s": round(hueco, 1)})

    minutos = len(vm) * VENTANA_MARCHA_S / 60
    return {
        "disponible": True,
        "pasos": pasos_total,
        "pasos_marcha": int(round(cad.sum() / 60 * VENTANA_MARCHA_S)),
        "n_tramos": len(marcha),
        "minutos_marcha": round(minutos, 1),
        "minutos_carrera": min_carrera,
        "cadencia_ppm": round(float(np.median(cad)), 1),
        "periodicidad": round(float(np.median(per)), 2),
        "cv_cadencia_pct": round(float(np.median(cvs)), 1) if cvs else None,
        "braceo_rms_g": round(float(np.median(braceo)), 3),
        "n_paradas_breves": len(paradas),
        "paradas_por_hora_marcha": round(len(paradas) / (minutos / 60), 1) if minutos > 0 else None,
        "paradas": paradas[:200],
        "tramo_mas_largo_min": round(max(len(tr) for tr in marcha) * VENTANA_MARCHA_S / 60, 1),
    }


def pasos_firmware(flujo):
    """Pasos del podómetro del reloj en el periodo: suma de
    incrementos (tolera los reinicios del contador a medianoche)."""
    s = flujo["steps"]
    if len(s) < 2:
        return 0
    d = np.diff(s)
    return int(d[d > 0].sum() + s[1:][d < 0].sum())


# ============================================================
# 4. CAÍDAS — pico > 3 g + reposo posterior + calma previa
# ============================================================

def detectar_caidas(flujo):
    m, t = flujo["m"], flujo["t"]
    eventos = []
    for a, b, fs in flujo["tramos"]:
        wpost, wpre = int(VENTANA_POST_S * fs), int(VENTANA_PRE_S * fs)
        picos = np.nonzero(m[a:b] > UMBRAL_CAIDA)[0] + a
        siguiente = a
        for i in picos:
            if i < siguiente or i + wpost > b or i - wpre < a:
                continue
            post, pre = m[i + 1:i + wpost], m[i - wpre:i]
            if abs(post.mean() - 1.0) < REPOSO_POST and pre.var() <= UMBRAL_VAR_PRE:
                eventos.append({"t": float(t[i]), "hora": _hora(t[i]), "magnitud_pico": round(float(m[i]), 2)})
                siguiente = i + wpost
    return {"n_caidas": len(eventos), "eventos": eventos}


# ============================================================
# 5. PULSO
# ============================================================

def calcular_pulso(flujo):
    hr = flujo["hr"]
    if len(hr) == 0:
        return {"disponible": False, "media_hr": None, "dispersion_fc": None, "min_hr": None, "max_hr": None}
    return {
        "disponible": True,
        "media_hr": round(float(hr.mean()), 1),
        # Desviación típica de los bpm a lo largo del periodo. NO es HRV
        # (la HRV necesita intervalos RR latido a latido, que el reloj no da).
        "dispersion_fc": round(float(hr.std()), 1),
        "min_hr": int(hr.min()), "max_hr": int(hr.max()),
        "p5_hr": int(np.percentile(hr, 5)),
    }


def pulso_por_hora(flujo):
    res = []
    horas = np.array([datetime.fromtimestamp(x).hour for x in flujo["hr_t"]]) if len(flujo["hr_t"]) else np.array([])
    for h in range(24):
        v = flujo["hr"][horas == h] if len(horas) else np.array([])
        res.append({"hora": h, "media_hr": round(float(v.mean()), 1) if len(v) else None,
                    "min_hr": int(v.min()) if len(v) else None, "max_hr": int(v.max()) if len(v) else None,
                    "n_lecturas": int(len(v))})
    return res


def pulso_por_minuto(flujo):
    acc = {}
    for tt, h in zip(flujo["hr_t"], flujo["hr"]):
        d = datetime.fromtimestamp(tt); k = d.hour * 60 + d.minute
        acc.setdefault(k, []).append(h)
    return [{"minuto_dia": k, "etiqueta": "%02d:%02d" % (k // 60, k % 60),
             "media_hr": round(float(np.mean(acc[k])), 1) if k in acc else None} for k in range(1440)]


# ============================================================
# 6. SUEÑO — una noche = 22:00 del día anterior a 08:00 del día
# ============================================================

def _periodo_sueno(mov):
    """Índices [ini, fin) del periodo principal de sueño. Se buscan
    bloques tranquilos (en 10 min móviles, < 25% de épocas con
    movimiento) y se unen los separados por despertares de hasta
    15 min; se devuelve el bloque unido más largo."""
    if len(mov) < 20:
        return None
    frac = np.convolve(mov.astype(float), np.ones(20) / 20, mode="same")
    quieto = np.append(frac < 0.25, False)
    bloques, ini = [], None
    for k, q in enumerate(quieto):
        if q and ini is None:
            ini = k
        elif not q and ini is not None:
            if k - ini >= 20:
                bloques.append([ini, k])
            ini = None
    if not bloques:
        return None
    unidos = [bloques[0]]
    for a, b in bloques[1:]:
        if a - unidos[-1][1] <= HUECO_SUENO_EPOCAS:
            unidos[-1][1] = b
        else:
            unidos.append([a, b])
    return tuple(max(unidos, key=lambda x: x[1] - x[0]))


def calcular_sueno(flujo_noche, duracion_noche_s):
    """flujo_noche: flujo recortado a 22:00-08:00. Se localiza el
    periodo principal de sueño (actigrafía por épocas de 30 s) y las
    métricas se calculan DENTRO de él, no sobre toda la franja."""
    epocas = []
    for i, j, fs in _ventanas(flujo_noche, EPOCA_SUENO_S):
        x = _pasa_banda(flujo_noche["m"][i:j], fs, 0.5, 5.0)
        epocas.append((float(flujo_noche["t"][i]), float(x.std())))
    n_total = len(epocas)
    epocas = [e for e in epocas if e[1] >= UMBRAL_NO_PUESTO]   # reloj quitado = sin dato
    no_puesto_h = (n_total - len(epocas)) * EPOCA_SUENO_S / 3600
    cobertura = len(epocas) * EPOCA_SUENO_S / duracion_noche_s
    if cobertura < COBERTURA_MIN_NOCHE:
        return {"disponible": False, "motivo": f"Cobertura nocturna insuficiente ({cobertura*100:.0f}%)",
                "cobertura_pct": round(cobertura * 100), "puntuacion": None}

    t_ep = np.array([e[0] for e in epocas])
    mov = np.array([e[1] > UMBRAL_MOV_EPOCA for e in epocas])
    per = _periodo_sueno(mov)
    if per is None or (per[1] - per[0]) * EPOCA_SUENO_S / 3600 < MIN_SUENO_H:
        return {"disponible": False, "motivo": "No se identificó un periodo de sueño",
                "cobertura_pct": round(cobertura * 100), "puntuacion": None}
    a, b = per
    t_ini, t_fin = t_ep[a], t_ep[b - 1] + EPOCA_SUENO_S
    horas = (t_fin - t_ini) / 3600
    mov_s = mov[a:b]
    pct_mov = float(mov_s.mean() * 100)
    # Despertares: >= 3 épocas seguidas (1,5 min) con movimiento
    desp, racha = 0, 0
    for x in mov_s:
        racha = racha + 1 if x else 0
        if racha == 3:
            desp += 1

    sel = (flujo_noche["hr_t"] >= t_ini) & (flujo_noche["hr_t"] < t_fin)
    hr = flujo_noche["hr"][sel]
    media_hr = float(hr.mean()) if len(hr) else 0.0
    pct_frag = float((hr > media_hr + 15).mean() * 100) if len(hr) else 0.0

    p = 100
    if media_hr > 70:
        p -= min(30, int((media_hr - 70) * 2))
    elif 0 < media_hr < 45:
        p -= 20
    p -= min(30, int(pct_frag * 1.5))
    if pct_mov > 20:
        p -= min(20, int((pct_mov - 20) * 0.5))
    if horas < 6:
        p -= min(20, int((6 - horas) * 10))
    p = max(0, min(100, p))
    return {
        "disponible": True, "puntuacion": p,
        "nivel": "Buena" if p >= 75 else "Regular" if p >= 50 else "Mala",
        "inicio": _hora(t_ini), "fin": _hora(t_fin), "horas_sueno": round(float(horas), 1),
        "hr_nocturna": round(media_hr, 1) if media_hr else None,
        "fragmentacion": round(pct_frag, 1), "movimiento": round(pct_mov, 1),
        "despertares": desp, "cobertura_pct": round(cobertura * 100),
        "horas_no_puesto": round(no_puesto_h, 1),
    }

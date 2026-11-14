# ============================================================
#  tests_sinteticos.py
#  Ensayos con señales sintéticas de los algoritmos de analisis.py
#  (para el apartado de calibración / validación técnica).
#  USO: python tests_sinteticos.py
# ============================================================
import numpy as np
import analisis as A

FS = 23.15
rng = np.random.default_rng(0)


def flujo_sintetico(ax, ay, az, t0=1_786_000_000.0):
    n = len(ax)
    return {"t": t0 + np.arange(n) / FS, "ax": ax, "ay": ay, "az": az,
            "m": np.sqrt(ax ** 2 + ay ** 2 + az ** 2), "tramos": [(0, n, FS)],
            "hr_t": np.array([]), "hr": np.array([]), "steps_t": np.array([]), "steps": np.array([])}


def reposo(seg, ruido=0.004):
    n = int(seg * FS)
    return (rng.normal(0, ruido, n), rng.normal(0, ruido, n), 1.0 + rng.normal(0, ruido, n))


def con_oscilacion(base, f, amp, eje=(0.6, 0.5, 0.62)):
    ax, ay, az = (b.copy() for b in base)
    t = np.arange(len(ax)) / FS
    s = amp * np.sin(2 * np.pi * f * t)
    u = np.array(eje) / np.linalg.norm(eje)
    return ax + u[0] * s, ay + u[1] * s, az + u[2] * s


def marcha(seg, f_paso=1.8):
    """Muñeca al caminar: braceo a f_paso/2 en los ejes + impacto de cada paso en la magnitud."""
    n = int(seg * FS); t = np.arange(n) / FS
    sw = 0.35 * np.sin(2 * np.pi * (f_paso / 2) * t)
    imp = 0.15 * np.sin(2 * np.pi * f_paso * t)
    return (sw + rng.normal(0, 0.02, n), 0.3 * sw + rng.normal(0, 0.02, n), 1.0 + imp + rng.normal(0, 0.02, n))


print("== TEMBLOR (60 s de reposo + oscilación) ==")
print("f (Hz)  amp (g)  min. detectados / 1.0")
for f in (4.5, 5, 6, 8, 10, 11):
    for amp in (0.03, 0.05, 0.1):
        r = A.calcular_temblor(flujo_sintetico(*con_oscilacion(reposo(60), f, amp)))
        print(f"{f:5.1f}  {amp:6.2f}   {r['minutos_temblor']:.2f}  episodios={r['n_episodios']}")
r = A.calcular_temblor(flujo_sintetico(*reposo(600)))
print(f"Reposo puro 10 min: {r['minutos_temblor']} min de temblor (esperado 0)")
r = A.calcular_temblor(flujo_sintetico(*marcha(600)))
print(f"Marcha 10 min a 108 pasos/min: {r['minutos_temblor']} min de temblor (esperado 0)")

print("\n== MARCHA / PASOS ==")
for f_paso in (1.5, 1.8, 2.0):
    r = A.calcular_marcha(flujo_sintetico(*marcha(300, f_paso)))
    print(f"Marcha 5 min a {f_paso*60:.0f} p/min: pasos={r['pasos']} (esperado ~{int(f_paso*300)}) cadencia={r.get('cadencia_ppm')}")
for f in (4, 5, 6):
    r = A.calcular_marcha(flujo_sintetico(*con_oscilacion(reposo(300), f, 0.8)))
    print(f"Agitar la muñeca 5 min a {f} Hz: pasos={r['pasos']} (esperado 0)")

print("\n== CAÍDAS ==")
def caida(previo):
    a = list(previo); n_pre = len(a[0])
    pico = np.zeros(3); pico[2] = 4.5
    post = reposo(5)
    return tuple(np.concatenate([a[k], [pico[k]], post[k]]) for k in range(3))
r = A.detectar_caidas(flujo_sintetico(*caida(reposo(5))))
print(f"Calma -> pico 4,5 g -> reposo: {r['n_caidas']} caída (esperado 1)")
r = A.detectar_caidas(flujo_sintetico(*caida(con_oscilacion(reposo(5), 4, 1.0))))
print(f"Agitación 4 Hz -> pico 4,5 g -> reposo: {r['n_caidas']} caídas (esperado 0)")

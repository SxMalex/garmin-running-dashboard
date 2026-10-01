"""Série de TSS quotidiens qui tombe sur une limite d'arrondi du TSB (tests seulement)."""

import math

import pandas as pd

K_CTL, K_ATL = math.exp(-1 / 42), math.exp(-1 / 7)


def rounding_edge_daily(today: pd.Timestamp, days: int = 60):
    """
    (daily_tss factice, ctl, atl) tel que round(ctl, 1) − round(atl, 1) diffère
    de round(ctl − atl, 1) : l'ancienne et la nouvelle définition du TSB y
    donnent deux chiffres — c'est ce qui rend les tests du TSB unique capables
    d'échouer.
    """
    ctl = atl = 0.0
    for _ in range(days - 1):
        ctl, atl = ctl * K_CTL + 50 * (1 - K_CTL), atl * K_ATL + 50 * (1 - K_ATL)
    for step in range(30000):
        v = step / 100
        c, a = ctl * K_CTL + v * (1 - K_CTL), atl * K_ATL + v * (1 - K_ATL)
        if round(round(c, 1) - round(a, 1), 1) != round(c - a, 1):
            idx = pd.date_range(end=pd.Timestamp(today).normalize(), periods=days, freq="D")
            tss = [50.0] * (days - 1) + [v]
            daily = pd.DataFrame({"day": idx, "tss_run": tss, "tss_cross": 0.0, "tss": tss})
            return daily, c, a
    raise AssertionError("aucune limite d'arrondi trouvée")

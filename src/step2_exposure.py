"""ADIM 2 — Payda (maruziyet) oluşturma: kuyruk-yıl paneli.

SDR verisi yalnızca 'olay' içerir, filo büyüklüğü içermez. Bu yüzden her uçağın (tescil no)
filoda bulunduğu yılları verinin kendisinden çıkarıyoruz:
  * Bir kuyruk, ilk SDR yılı ile son SDR yılı arasındaki HER yıl filoda kabul edilir
    (o yıl hiç rapor vermemiş olsa bile => sıfır-raporlu yıllar paydaya girer).
  * 2026 verisi 15 Eylül'de bittiği için 2026 yılı 258/365 = 0.706 ağırlık alır.
  * Uçuş saati kullanımı (saat/yıl) ve döngü (iniş) kullanımı, aynı kuyruğun farklı
    tarihli raporlarındaki AircraftTotalTime / AircraftTotalCycles artışından tahmin edilir.
  * Uçağın yaşı yerine 'yıl ortasındaki tahmini toplam uçuş saati' kullanılır.

Çıktı: data/processed/tail_year.parquet
"""
import numpy as np
import pandas as pd

df = pd.read_parquet("data/processed/sdr_ba.parquet")
df["operator"] = df.operator.fillna("BILINMIYOR")
END_2026 = pd.Timestamp("2026-09-15")
YEAR_W = {y: 1.0 for y in range(2020, 2026)}
YEAR_W[2026] = (END_2026 - pd.Timestamp("2026-01-01")).days / 365.0

key = ["manufacturer", "tail"]

# ---- 1) Kuyruk başına kullanım tahmini (doğrusal eğim: saat/yıl, döngü/yıl)
def slope(g, col):
    g = g[[col, "date"]].dropna()
    if len(g) < 2:
        return np.nan
    span = (g.date.max() - g.date.min()).days
    if span < 180:
        return np.nan
    x = (g.date - g.date.min()).dt.days.values / 365.25
    y = g[col].values
    # aykırı girişlere dayanıklı olsun diye Theil-Sen benzeri: ikili eğimlerin medyanı (alt örnek)
    idx = np.argsort(x)
    x, y = x[idx], y[idx]
    if len(x) > 60:
        sel = np.linspace(0, len(x) - 1, 60).astype(int)
        x, y = x[sel], y[sel]
    i, j = np.triu_indices(len(x), 1)
    dx = x[j] - x[i]
    ok = dx > 0.25
    if ok.sum() == 0:
        return np.nan
    return float(np.median((y[j] - y[i])[ok] / dx[ok]))

tails = df.groupby(key).agg(gen=("gen", lambda s: s.mode().iat[0]),
                            family=("family", lambda s: s.mode().iat[0]),
                            body=("body", "first"),
                            first_year=("year", "min"), last_year=("year", "max"),
                            n_reports=("year", "size")).reset_index()
util_h = df.groupby(key).apply(lambda g: slope(g, "tt"), include_groups=False).rename("util_h")
util_c = df.groupby(key).apply(lambda g: slope(g, "tc"), include_groups=False).rename("util_c")
tails = tails.merge(util_h, left_on=key, right_index=True).merge(util_c, left_on=key, right_index=True)
tails.loc[(tails.util_h < 200) | (tails.util_h > 6000), "util_h"] = np.nan
tails.loc[(tails.util_c < 50) | (tails.util_c > 3000), "util_c"] = np.nan

# ---- 2) Kuyruk-yıl iskeleti
rows = [(m, t, y) for m, t, a, b in tails[key + ["first_year", "last_year"]].itertuples(index=False)
        for y in range(a, b + 1)]
ty = pd.DataFrame(rows, columns=key + ["year"]).merge(tails, on=key)
ty["w"] = ty.year.map(YEAR_W)

# Yıl bazında işletmeci: o yılın en çok rapor veren işletmecisi; rapor yoksa önceki yıldan taşı
op_y = (df.groupby(key + ["year"]).operator.agg(lambda s: s.mode().iat[0]).rename("operator"))
ty = ty.merge(op_y, left_on=key + ["year"], right_index=True, how="left")
ty = ty.sort_values(key + ["year"])
ty["operator"] = ty.groupby(key).operator.ffill().bfill()
op_name = df.drop_duplicates("operator").set_index("operator").op_name
ty["op_name"] = ty.operator.map(op_name)
ty["role"] = np.where(ty.op_name.notna(), "Yolcu", "Kargo/Diger")

# Eksik kullanım değerlerini işletmeci×nesil, sonra nesil medyanıyla doldur
for c in ["util_h", "util_c"]:
    med_og = ty.groupby(["operator", "gen"])[c].transform("median")
    med_g = ty.groupby("gen")[c].transform("median")
    ty[c + "_src"] = np.where(ty[c].notna(), "kuyruk", np.where(med_og.notna(), "isletmeci", "nesil"))
    ty[c] = ty[c].fillna(med_og).fillna(med_g)

# Yıl ortası tahmini toplam saat (yaş göstergesi): kuyruğun medyan (tarih, tt) noktasından ekstrapolasyon
ref = df.dropna(subset=["tt"]).groupby(key).agg(ref_tt=("tt", "median"),
                                                 ref_date=("date", lambda s: s.sort_values().iloc[len(s)//2]))
ty = ty.merge(ref, left_on=key, right_index=True, how="left")
mid = pd.to_datetime(ty.year.astype(str) + "-07-01")
ty["age_tt"] = (ty.ref_tt + (mid - ty.ref_date).dt.days / 365.25 * ty.util_h).clip(lower=0)
ty = ty.drop(columns=["ref_date"])

# ---- 3) Olay sayıları
flags = {"n_all": None, "n_oper": "operational", "n_flight": "in_flight", "n_disr": "disruptive",
         "n_unsched": "unsched_landing", "n_rto": "rto", "n_engsd": "eng_shutdown"}
cnt = df.assign(one=1)
agg = {}
for k, f in flags.items():
    agg[k] = cnt.groupby(key + ["year"])["one" if f is None else f].sum()
for g in ["Yapı (51-57)", "Sistemler (21-46)", "Motor/APU (49,71-80)"]:
    agg["n_" + g.split()[0][:4].lower()] = cnt[cnt.ata_group == g].groupby(key + ["year"]).one.sum()
cnt_df = pd.DataFrame(agg).fillna(0).astype(int)
ty = ty.merge(cnt_df, left_on=key + ["year"], right_index=True, how="left")
ncols = list(cnt_df.columns)
ty[ncols] = ty[ncols].fillna(0).astype(int)

ty["fh"] = ty.util_h * ty.w       # yıl içindeki tahmini uçuş saati
ty["fc"] = ty.util_c * ty.w       # yıl içindeki tahmini döngü (kalkış-iniş)
ty.to_parquet("data/processed/tail_year.parquet", index=False)

print(ty.shape)
print(ty.groupby(["body", "manufacturer", "gen"]).agg(tails=("tail", "nunique"), ty=("w", "sum"),
      fh=("fh", "sum"), n=("n_all", "sum"), util=("util_h", "median"), age=("age_tt", "median")).round(0))
print(tails.util_h.notna().mean(), tails.util_c.notna().mean())

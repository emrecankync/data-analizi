"""ADIM 1 — Ham SDR CSV'lerini oku, Boeing/Airbus kayıtlarını ayıkla, temizle, sınıflandır.

Kullanım:  python -I src/step1_prepare.py  (proje kökünden)
Girdi   :  data/raw/SDR-*.csv
Çıktı   :  data/processed/sdr_ba.parquet, results/tables/01_veri_akisi.csv
"""
import glob, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_map import parse_model, neo_engine, PAX_OPERATORS, GENERIC  # noqa: E402

RAW = "data/raw"
OUT = "data/processed"
TAB = "results/tables"
os.makedirs(OUT, exist_ok=True)
os.makedirs(TAB, exist_ok=True)

USECOLS = ["OperatorControlNumber", "DifficultyDate", "OperatorDesignator", "SubmitterTypeCode",
           "SDRType", "JASCCode", "NatureOfConditionA", "NatureOfConditionB", "NatureOfConditionC",
           "PrecautionaryProcedureA", "PrecautionaryProcedureB", "PrecautionaryProcedureC",
           "PrecautionaryProcedureD", "StageOfOperationCode", "HowDiscoveredCode", "RegistryNNumber",
           "AircraftMake", "AircraftModel", "AircraftSerialNumber", "AircraftTotalTime",
           "AircraftTotalCycles", "EngineMake", "EngineModel", "PartName", "PartCondition",
           "Discrepancy"]

flow = []
files = sorted(glob.glob(f"{RAW}/SDR-*.csv"))
df = pd.concat([pd.read_csv(f, dtype=str, usecols=USECOLS, low_memory=False) for f in files],
               ignore_index=True)
flow.append(("Ham kayıt (tüm üreticiler, 2020-2026)", len(df)))

df = df.drop_duplicates("OperatorControlNumber")
flow.append(("Tekrarlı kontrol numarası çıkarıldıktan sonra", len(df)))

df["AircraftMake"] = df["AircraftMake"].str.strip().str.upper()
df = df[df.AircraftMake.isin(["BOEING", "AIRBUS"])].copy()
flow.append(("Yalnızca BOEING + AIRBUS", len(df)))

df["date"] = pd.to_datetime(df.DifficultyDate, format="%m/%d/%Y", errors="coerce")
df = df[df.date.notna()]
df["year"] = df.date.dt.year
df["tail"] = df.RegistryNNumber.str.strip().str.upper().str.lstrip("N")
df = df[df["tail"].notna() & (df["tail"] != "")]
flow.append(("Geçerli tarih + tescil (N) numarası olan", len(df)))

# --- Model -> aile/nesil/gövde
parsed = [parse_model(mk, md) for mk, md in zip(df.AircraftMake, df.AircraftModel)]
df["family"] = [p[0] if p else None for p in parsed]
df["gen"] = [p[1] if p else None for p in parsed]
df["body"] = [p[2] if p else None for p in parsed]

# 737 MAX doğrulaması: kod 'müşteri kodsuz' 7378/7379 olsa bile bazı işletmeciler (ör. Delta,
# hiç MAX işletmediği halde) NG -800/-900ER için bu kısa kodu kullanıyor. MAX ilk teslimatı 2017;
# bir uçak yılda en fazla ~5.000 saat uçabilir. Toplam saati bu sınırı aşan "MAX" kaydı NG'dir.
tt_tmp = pd.to_numeric(df.AircraftTotalTime, errors="coerce")
yrs = (df.date - pd.Timestamp("2017-05-01")).dt.days / 365.25
bad_max = (df.gen == "B737 MAX") & (tt_tmp > 5000 * yrs.clip(lower=0) + 1000)
# Delta 2020-2026 arasında hiç 737 MAX işletmedi (MAX 10 siparişi henüz teslim edilmedi):
# DALA'nın '7379' kodlu yeni -900ER'leri saat kuralına takılmıyor, bu yüzden açıkça düzeltilir.
bad_max |= (df.gen == "B737 MAX") & (df.OperatorDesignator.str.strip() == "DALA")
df.loc[bad_max, "gen"] = "B737 NG"
flow.append((f"Toplam saati MAX için imkansız olan {int(bad_max.sum())} kayıt NG'ye düzeltildi", len(df)))

# Kuyruk bazında tek tip: her tescil no'nun baskın modeli tüm kayıtlarına atanır.
#  - Genel kodlu ('737*', 'A320*', '767' ...) kayıtları doldurur
#  - Aynı uçağa farklı nesil yazılmış tutarsız girişleri düzeltir
known = df[df.gen.notna()]
n_generic = int(df.gen.isna().sum())
tail_mode = (known.groupby(["AircraftMake", "tail"])[["family", "gen", "body"]]
             .agg(lambda s: s.value_counts().index[0]))
key = pd.MultiIndex.from_arrays([df["AircraftMake"], df["tail"]])
fill = tail_mode.reindex(key)
before = df.gen.copy()
for c in ["family", "gen", "body"]:
    df[c] = fill[c].values
df["model_imputed"] = before.isna() & df.gen.notna()
n_fixed = int((before.notna() & (before != df.gen)).sum())
df = df[df.gen.notna()]
flow.append((f"Kuyruk baskın modeli uygulandı (genel kodlu {n_generic} kayıttan "
             f"{int(df.model_imputed.sum())} dolduruldu, {n_fixed} tutarsız kayıt düzeltildi; "
             f"model atanamayanlar çıkarıldı)", len(df)))

# Kapsam dışı eski/küçük tipler: 727, A310, A380 (çok az kayıt)
df = df[~df.family.isin(["B727", "A310", "A380"])]
flow.append(("727 / A310 / A380 (n<250) çıkarıldı", len(df)))

# --- Sayısal alanlar ve makul olmayan değerler
df["tt"] = pd.to_numeric(df.AircraftTotalTime, errors="coerce")
df["tc"] = pd.to_numeric(df.AircraftTotalCycles, errors="coerce")
df.loc[(df.tt <= 0) | (df.tt > 160_000), "tt"] = np.nan   # ticari jette ~140k saat üstü yok
df.loc[(df.tc <= 0) | (df.tc > 120_000), "tc"] = np.nan

# --- Rol: yolcu ana hat vs kargo/diğer
df["operator"] = df.OperatorDesignator.str.strip().str.upper()
df["op_name"] = df.operator.map(PAX_OPERATORS)
df["role"] = np.where(df.op_name.notna(), "Yolcu", "Kargo/Diger")
df["neo_engine"] = [neo_engine(m) if g == "A320neo ailesi" else None
                    for m, g in zip(df.AircraftModel, df.gen)]
df["engine_model"] = df.EngineModel

# --- Olay şiddeti bayrakları
proc_cols = ["PrecautionaryProcedureA", "PrecautionaryProcedureB",
             "PrecautionaryProcedureC", "PrecautionaryProcedureD"]
procs = df[proc_cols].fillna("")
# K = Yok, O = Diğer, boş = bildirilmemiş. Diğer tüm harfler somut bir uçuş-içi tedbiri gösterir:
# A plansız iniş, B acil alçalma, C kalkış iptali, D kapıya/bloka dönüş, E motor kapatma,
# F yangın söndürücü kullanımı, G oksijen maskesi, H/I/J/L diğer acil/anormal prosedürler
df["disruptive"] = (~procs.isin(["", "K", "O"])).any(axis=1)
df["unsched_landing"] = (procs == "A").any(axis=1)
df["rto"] = (procs == "C").any(axis=1)
df["eng_shutdown"] = (procs == "E").any(axis=1)
IN_FLIGHT = {"TO", "CL", "CR", "DE", "AP", "LD", "HO", "AA", "EX", "FF", "AG"}
df["stage"] = df.StageOfOperationCode.fillna("NR")
df["in_flight"] = df.stage.isin(IN_FLIGHT)
df["operational"] = df.stage.isin(IN_FLIGHT | {"TX"})  # uçuş + taksi
df["jasc"] = df.JASCCode.str.strip().str.zfill(4)
df["ata"] = pd.to_numeric(df.jasc.str[:2], errors="coerce")


def ata_group(a):
    if pd.isna(a):
        return "Bilinmiyor"
    a = int(a)
    if 51 <= a <= 57 or a == 5:
        return "Yapı (51-57)"
    if 71 <= a <= 80 or a == 49 or a == 61:
        return "Motor/APU (49,71-80)"
    return "Sistemler (21-46)"


df["ata_group"] = df.ata.map(ata_group)
df["manufacturer"] = df.AircraftMake.str.title()

keep = ["OperatorControlNumber", "date", "year", "operator", "op_name", "role", "manufacturer",
        "family", "gen", "body", "AircraftModel", "model_imputed", "neo_engine", "engine_model",
        "tail", "tt", "tc", "stage", "in_flight", "operational", "disruptive", "unsched_landing",
        "rto", "eng_shutdown", "jasc", "ata", "ata_group", "NatureOfConditionA",
        "PrecautionaryProcedureA", "HowDiscoveredCode", "PartName", "PartCondition",
        "Discrepancy"]
df[keep].to_parquet(f"{OUT}/sdr_ba.parquet", index=False)
pd.DataFrame(flow, columns=["adim", "kayit_sayisi"]).to_csv(f"{TAB}/01_veri_akisi.csv", index=False)
print(pd.DataFrame(flow, columns=["adim", "kayit_sayisi"]).to_string(index=False))
print(df.groupby(["body", "manufacturer", "gen"]).size())

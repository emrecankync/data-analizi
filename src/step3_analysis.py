"""ADIM 3 — Oranlar, regresyon modelleri, duyarlılık analizleri, tablolar ve grafikler.

Girdi : data/processed/sdr_ba.parquet, data/processed/tail_year.parquet
Çıktı : results/tables/*.csv, results/figures/*.png, results/summary.json
"""
import json
import os
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

warnings.filterwarnings("ignore")
TAB, FIG = "results/tables", "results/figures"
os.makedirs(TAB, exist_ok=True)
os.makedirs(FIG, exist_ok=True)
rng = np.random.default_rng(42)

ev = pd.read_parquet("data/processed/sdr_ba.parquet")
ty = pd.read_parquet("data/processed/tail_year.parquet")
ty["airbus"] = (ty.manufacturer == "Airbus").astype(int)
ty["log_w"] = np.log(ty.w)
ty["log_fh"] = np.log(ty.fh.clip(lower=1))
ty["log_fc"] = np.log(ty.fc.clip(lower=1))
AGE_BINS = [-1, 10_000, 25_000, 40_000, 60_000, 1e9]
AGE_LAB = ["<10k", "10-25k", "25-40k", "40-60k", ">60k"]
ty["age_bin"] = pd.cut(ty.age_tt.fillna(ty.groupby("gen").age_tt.transform("median")),
                       AGE_BINS, labels=AGE_LAB).astype(str)
# küçük işletmecileri tek grupta topla (sabit etki tahmini için)
op_ty = ty.groupby("operator").w.sum()
ty["op_grp"] = np.where(ty.operator.map(op_ty) >= 30, ty.operator, "DIGER")
BODY_TR = {"Dar": "Dar gövde", "Genis": "Geniş gövde"}
ty["n_fail"] = ty["n_sist"] + ty["n_moto"]   # yapısal muayene bulguları hariç arıza raporları
OUTCOMES = {"n_all": "Tüm SDR", "n_fail": "Sistem+motor arızası (yapı hariç)",
            "n_oper": "Operasyonel (uçuş+taksi)", "n_disr": "Aksatan olay (tedbir uygulanan)"}
summary = {}

# ------------------------------------------------------------------ 1. Betimsel oranlar
def boot_rate(g, num, den, B=300):
    """Kuyruk düzeyinde kümelenmiş bootstrap ile oran ve %95 GA."""
    per_tail = g.groupby("tail")[[num, den]].sum().values
    est = per_tail[:, 0].sum() / per_tail[:, 1].sum()
    n = len(per_tail)
    if n < 5:
        return est, np.nan, np.nan
    idx = rng.integers(0, n, size=(B, n))
    bs = per_tail[idx, 0].sum(1) / per_tail[idx, 1].sum(1)
    return est, *np.percentile(bs, [2.5, 97.5])


def describe(groupcols, data=ty):
    out = []
    for keys, g in data.groupby(groupcols):
        keys = keys if isinstance(keys, tuple) else (keys,)
        row = dict(zip(groupcols, keys))
        row.update(kuyruk=g["tail"].nunique(), kuyruk_yil=g.w.sum(), ucus_saati_mn=g.fh.sum() / 1e6,
                   dongu_mn=g.fc.sum() / 1e6, sdr=g.n_all.sum(), operasyonel=g.n_oper.sum(),
                   aksatan=g.n_disr.sum(), medyan_toplam_saat=g.age_tt.median())
        for c, lab in [("n_all", "sdr"), ("n_fail", "ariza"), ("n_oper", "oper"), ("n_disr", "aksatan")]:
            e, lo, hi = boot_rate(g, c, "w")
            row[f"{lab}_100ky"], row[f"{lab}_100ky_lo"], row[f"{lab}_100ky_hi"] = 100 * e, 100 * lo, 100 * hi
        row["sdr_10bin_saat"] = 1e4 * g.n_all.sum() / g.fh.sum()
        row["aksatan_10bin_saat"] = 1e4 * g.n_disr.sum() / g.fh.sum()
        row["aksatan_10bin_dongu"] = 1e4 * g.n_disr.sum() / g.fc.sum()
        full = g[g.year <= 2025]
        row["P_yillik_aksatan_olay"] = (full.n_disr > 0).mean()
        row["P_yillik_herhangi_sdr"] = (full.n_all > 0).mean()
        out.append(row)
    return pd.DataFrame(out)


d_body = describe(["body", "manufacturer"])
d_body_role = describe(["role", "body", "manufacturer"])
d_gen = describe(["body", "manufacturer", "gen"])
d_body.round(3).to_csv(f"{TAB}/02_govde_uretici_oranlar.csv", index=False)
d_body_role.round(3).to_csv(f"{TAB}/03_rol_govde_uretici_oranlar.csv", index=False)
d_gen.round(3).to_csv(f"{TAB}/04_nesil_oranlar.csv", index=False)

# ------------------------------------------------------------------ 2. NB regresyonları
def fit_nb(formula, data, offset_col):
    m = smf.negativebinomial(formula, data=data, offset=data[offset_col])
    try:
        r = m.fit(disp=0, maxiter=300, method="bfgs", cov_type="cluster",
                  cov_kwds={"groups": pd.factorize(data["tail"])[0]})
    except Exception:
        r = m.fit(disp=0, maxiter=300, method="nm", cov_type="cluster",
                  cov_kwds={"groups": pd.factorize(data["tail"])[0]})
    return r


def irr(r, term="airbus"):
    b, se = r.params[term], r.bse[term]
    return np.exp(b), np.exp(b - 1.96 * se), np.exp(b + 1.96 * se), r.pvalues[term]


SPECS = [
    ("M0 Ham (sadece üretici)", "airbus", "log_w"),
    ("M1 + yıl", "airbus + C(year)", "log_w"),
    ("M2 + yıl + işletmeci", "airbus + C(year) + C(op_grp)", "log_w"),
    ("M3 + yıl + işletmeci + yaş (ANA MODEL)", "airbus + C(year) + C(op_grp) + C(age_bin)", "log_w"),
    ("M4 = M3, payda uçuş saati", "airbus + C(year) + C(op_grp) + C(age_bin)", "log_fh"),
    ("M5 = M3, payda döngü (iniş)", "airbus + C(year) + C(op_grp) + C(age_bin)", "log_fc"),
]
reg_rows = []
for body in ["Dar", "Genis"]:
    sub = ty[ty.body == body]
    for oc, oc_lab in OUTCOMES.items():
        for spec, rhs, off in SPECS:
            r = fit_nb(f"{oc} ~ {rhs}", sub, off)
            e, lo, hi, p = irr(r)
            reg_rows.append(dict(govde=BODY_TR[body], sonuc=oc_lab, model=spec, IRR_Airbus_vs_Boeing=e,
                                 ga_alt=lo, ga_ust=hi, p=p, n_kuyruk_yil=len(sub)))
            print(body, oc, spec, round(e, 3), round(lo, 3), round(hi, 3))
reg = pd.DataFrame(reg_rows)
reg.round(4).to_csv(f"{TAB}/05_nb_regresyon_IRR.csv", index=False)

# ------------------------------------------------------------------ 3. Duyarlılık analizleri (ana model M3)
MAIN = "airbus + C(year) + C(op_grp) + C(age_bin)"
SENS = {
    "Ana model (tüm veri)": lambda d: d,
    "Southwest hariç": lambda d: d[d.operator != "SWAA"],
    "Yalnızca yolcu ana hat": lambda d: d[d.role == "Yolcu"],
    "Yalnızca kargo/diğer": lambda d: d[d.role != "Yolcu"],
    "Yalnızca 2022-2026 (COVID sonrası)": lambda d: d[d.year >= 2022],
    "Ortak yaş aralığı (5k-60k saat)": lambda d: d[d.age_tt.between(5_000, 60_000)],
    "Her iki üreticiyi işleten işletmeciler": lambda d: d[d.operator.isin(
        d.groupby("operator").manufacturer.nunique().loc[lambda s: s == 2].index)],
    "Kullanımı kendi verisinden ölçülen kuyruklar": lambda d: d[d.util_h_src == "kuyruk"],
}
sens_rows = []
for body in ["Dar", "Genis"]:
    for name, f in SENS.items():
        sub = f(ty[ty.body == body])
        if sub.airbus.nunique() < 2 or len(sub) < 200:
            continue
        for oc in ["n_all", "n_fail", "n_disr"]:
            try:
                rhs = MAIN if sub.op_grp.nunique() > 1 else MAIN.replace(" + C(op_grp)", "")
                r = fit_nb(f"{oc} ~ {rhs}", sub, "log_w")
                e, lo, hi, p = irr(r)
            except Exception as ex:  # noqa
                print("fail", body, name, oc, ex)
                continue
            sens_rows.append(dict(govde=BODY_TR[body], analiz=name, sonuc=OUTCOMES[oc], IRR=e,
                                  ga_alt=lo, ga_ust=hi, p=p, n_kuyruk_yil=len(sub)))
sens = pd.DataFrame(sens_rows)
sens.round(4).to_csv(f"{TAB}/06_duyarlilik.csv", index=False)
print(sens.round(3).to_string())

# ------------------------------------------------------------------ 4. Nesil eşleştirmeleri
PAIRS = [("Dar", "B737 NG", "A320ceo ailesi", "Mevcut nesil dar gövde"),
         ("Dar", "B737 MAX", "A320neo ailesi", "Yeni nesil dar gövde (bugün alınacak/kiralanacak)"),
         ("Dar", "B717", "A220", "100-150 koltuk sınıfı"),
         ("Dar", "B757", "A320ceo ailesi", "757 vs A321ceo dahil A320ceo"),
         ("Genis", "B787", "A350", "Yeni nesil geniş gövde"),
         ("Genis", "B787", "A330neo", "787 vs A330neo"),
         ("Genis", "B777", "A330ceo", "Büyük ikiz motor (eski nesil)"),
         ("Genis", "B767", "A330ceo", "Orta geniş gövde"),
         ("Genis", "B767", "A300", "Kargo ağırlıklı orta geniş gövde")]
gen_rows = []
for body in ["Dar", "Genis"]:
    sub = ty[ty.body == body].copy()
    for oc in ["n_all", "n_fail", "n_disr"]:
        r = fit_nb(f"{oc} ~ C(gen) + C(year) + C(op_grp) + C(age_bin)", sub, "log_w")
        names = list(r.params.index)
        for b_, bg, ag, lab in PAIRS:
            if b_ != body:
                continue
            v = np.zeros(len(names))
            for g, sgn in [(ag, 1), (bg, -1)]:
                t = f"C(gen)[T.{g}]"
                if t in names:
                    v[names.index(t)] = sgn
            b = v @ r.params.values
            se = np.sqrt(v @ r.cov_params().values @ v)
            gen_rows.append(dict(karsilastirma=lab, boeing=bg, airbus=ag, sonuc=OUTCOMES[oc],
                                 IRR_Airbus_vs_Boeing=np.exp(b), ga_alt=np.exp(b - 1.96 * se),
                                 ga_ust=np.exp(b + 1.96 * se)))
gen_cmp = pd.DataFrame(gen_rows)
gen_cmp.round(3).to_csv(f"{TAB}/07_nesil_karsilastirma_IRR.csv", index=False)
print(gen_cmp.round(3).to_string())

# Katı eşleştirme: yalnızca İKİ nesli de (her biri >=30 uçak-yılı) işleten işletmeciler kullanılır.
# Böylece Airbus/Boeing katsayısı tamamen aynı şirket içi farklardan tahmin edilir.
strict_rows = []
for b_, bg, ag, lab in PAIRS:
    sub = ty[ty.gen.isin([bg, ag])]
    k = sub.groupby(["operator", "gen"]).w.sum().unstack()
    ops_ok = k[(k.get(bg, 0) >= 30) & (k.get(ag, 0) >= 30)].index.tolist() if bg in k and ag in k else []
    ops_ok = [o for o in ops_ok if o != "BILINMIYOR"]
    if not ops_ok:
        continue
    sub = sub[sub.operator.isin(ops_ok)]
    # yaş grupları iki üretici arasında hiç örtüşmüyorsa yaş düzeltmesi tanımsızdır (tam çoklu doğrusallık)
    overlap = set(sub[sub.airbus == 1].age_bin) & set(sub[sub.airbus == 0].age_bin)
    age_term = " + C(age_bin)" if overlap else ""
    for oc in ["n_all", "n_fail", "n_disr"]:
        rhs = "airbus + C(year)" + age_term + (" + C(operator)" if len(ops_ok) > 1 else "")
        r = fit_nb(f"{oc} ~ {rhs}", sub, "log_w")
        e, lo, hi, p = irr(r)
        strict_rows.append(dict(karsilastirma=lab, boeing=bg, airbus=ag, sonuc=OUTCOMES[oc],
                                isletmeciler=",".join(ops_ok), yas_duzeltmesi=bool(overlap), IRR_Airbus_vs_Boeing=e, ga_alt=lo, ga_ust=hi,
                                p=p, airbus_ky=sub[sub.airbus == 1].w.sum(), boeing_ky=sub[sub.airbus == 0].w.sum()))
strict = pd.DataFrame(strict_rows)
strict.round(4).to_csv(f"{TAB}/07b_nesil_kati_eslestirme_IRR.csv", index=False)
print(strict.round(3).to_string())

# ------------------------------------------------------------------ 5. Aynı işletmeci içinde karşılaştırma
op_rows = []
for (op, body), g in ty.groupby(["operator", "body"]):
    if g.manufacturer.nunique() < 2:
        continue
    s = g.groupby("manufacturer").agg(ky=("w", "sum"), sdr=("n_all", "sum"), dis=("n_disr", "sum"),
                                      age=("age_tt", "median"))
    if (s.ky < 40).any():
        continue
    row = dict(isletmeci=op, isim=g.op_name.iloc[0] if pd.notna(g.op_name.iloc[0]) else op,
               govde=BODY_TR[body])
    for m in ["Boeing", "Airbus"]:
        row[f"{m}_kuyruk_yil"] = s.loc[m, "ky"]
        row[f"{m}_medyan_saat"] = s.loc[m, "age"]
        row[f"{m}_sdr_100ky"] = 100 * s.loc[m, "sdr"] / s.loc[m, "ky"]
        row[f"{m}_aksatan_100ky"] = 100 * s.loc[m, "dis"] / s.loc[m, "ky"]
    row["oran_sdr_A_B"] = row["Airbus_sdr_100ky"] / row["Boeing_sdr_100ky"]
    row["oran_aksatan_A_B"] = row["Airbus_aksatan_100ky"] / max(row["Boeing_aksatan_100ky"], 1e-9)
    op_rows.append(row)
ops = pd.DataFrame(op_rows).sort_values(["govde", "Boeing_kuyruk_yil"], ascending=[True, False])
ops.round(3).to_csv(f"{TAB}/08_ayni_isletmeci_karsilastirma.csv", index=False)
print(ops.round(2).to_string())

# İşletmeci raporlama kültürü (aynı tip içinde işletmeciler arası fark)
cult = (ty[ty.op_name.notna()].groupby(["op_name", "gen"])
        .agg(kuyruk_yil=("w", "sum"), sdr=("n_all", "sum")).query("kuyruk_yil >= 100"))
cult["sdr_100ky"] = 100 * cult.sdr / cult.kuyruk_yil
cult.round(1).reset_index().to_csv(f"{TAB}/09_isletmeci_raporlama_kulturu.csv", index=False)

# ------------------------------------------------------------------ 6. ATA bölümü
ATA_NAMES = {21: "Klima/Basınçlandırma", 22: "Otopilot", 23: "Haberleşme", 24: "Elektrik",
             25: "Kabin donanımı", 26: "Yangın koruma", 27: "Uçuş kumandaları", 28: "Yakıt",
             29: "Hidrolik", 30: "Buz/yağmur koruma", 31: "Gösterge/kayıt", 32: "İniş takımı",
             33: "Işıklar", 34: "Seyrüsefer", 35: "Oksijen", 36: "Pnömatik", 38: "Su/atık",
             45: "Bakım bilgisayarı", 49: "APU", 51: "Yapı (genel)", 52: "Kapılar", 53: "Gövde", 54: "Motor kaporta/pilon",
             55: "Stabilizerler", 56: "Pencereler", 57: "Kanatlar", 71: "Motor (genel)", 72: "Motor",
             73: "Motor yakıt/kontrol", 74: "Ateşleme", 75: "Motor havası", 76: "Motor kontrolü",
             77: "Motor göstergeleri", 78: "Egzoz/ters itki", 79: "Motor yağı", 80: "Marş"}
ata_rows = []
for body in ["Dar", "Genis"]:
    base = ty[(ty.body == body) & (ty.role == "Yolcu")]
    expo = base.groupby("manufacturer").w.sum()
    e = ev[(ev.body == body) & (ev.role == "Yolcu")]
    for kind, sel in [("tum", e), ("aksatan", e[e.disruptive])]:
        c = sel.groupby(["ata", "manufacturer"]).size().unstack(fill_value=0)
        for m in ["Boeing", "Airbus"]:
            c[m] = 100 * c.get(m, 0) / expo[m]
        c = c.reset_index()
        c["govde"], c["tur"] = BODY_TR[body], kind
        ata_rows.append(c)
ata = pd.concat(ata_rows)
ata["ata_adi"] = ata.ata.map(ATA_NAMES)
ata["fark_A_eksi_B"] = ata.Airbus - ata.Boeing
ata.round(3).to_csv(f"{TAB}/10_ata_bolum_100ky.csv", index=False)

# ------------------------------------------------------------------ 7. A320neo motor ayrımı ve MAX
neo_t = ev[ev.gen == "A320neo ailesi"].groupby("tail").neo_engine.agg(
    lambda s: s.dropna().mode().iat[0] if s.notna().any() else None)
nt = ty[ty.gen.isin(["A320neo ailesi", "B737 MAX"])].copy()
nt["alt"] = np.where(nt.gen == "B737 MAX", "B737 MAX (CFM LEAP-1B)",
                     "A320neo + " + nt["tail"].map(neo_t).fillna("bilinmiyor"))
neo = describe(["alt"], nt)
eng_cnt = (ev[ev.gen.isin(["A320neo ailesi", "B737 MAX"])].assign(
    alt=lambda d: np.where(d.gen == "B737 MAX", "B737 MAX (CFM LEAP-1B)",
                           "A320neo + " + d["tail"].map(neo_t).fillna("bilinmiyor")))
    .query("ata >= 71 and ata <= 80").groupby("alt").size().rename("motor_sdr"))
neo = neo.merge(eng_cnt, left_on="alt", right_index=True, how="left")
neo["motor_sdr_100ky"] = 100 * neo.motor_sdr / neo.kuyruk_yil
neo.round(3).to_csv(f"{TAB}/11_yeni_nesil_motor.csv", index=False)
print(neo[["alt", "kuyruk", "kuyruk_yil", "sdr_100ky", "aksatan_100ky", "motor_sdr_100ky"]].round(2))


# Yeni nesil: motor tipine göre düzeltilmiş model (referans: 737 MAX)
nt["alt_k"] = nt.alt.map({"B737 MAX (CFM LEAP-1B)": "a_MAX", "A320neo + CFM LEAP-1A": "b_neoLEAP",
                          "A320neo + PW1100G (GTF)": "c_neoGTF"}).fillna("d_bilinmiyor")
nt["n_eng"] = 0
eng_ty = (ev[ev.gen.isin(["A320neo ailesi", "B737 MAX"]) & ev.ata.between(71, 80)]
          .groupby(["manufacturer", "tail", "year"]).size().rename("n_eng_"))
nt = nt.merge(eng_ty, left_on=["manufacturer", "tail", "year"], right_index=True, how="left")
nt["n_eng"] = nt.n_eng_.fillna(0).astype(int)
neo_rows = []
for oc, lab in list(OUTCOMES.items()) + [("n_eng", "Motor SDR (ATA 71-80)")]:
    r = fit_nb(f"{oc} ~ C(alt_k) + C(year) + C(op_grp) + C(age_bin)", nt, "log_w")
    for k, nm in [("b_neoLEAP", "A320neo (LEAP-1A)"), ("c_neoGTF", "A320neo (PW1100G GTF)")]:
        t = f"C(alt_k)[T.{k}]"
        e, lo, hi, p = irr(r, t)
        neo_rows.append(dict(karsilastirma=f"{nm} / 737 MAX", sonuc=lab, IRR=e, ga_alt=lo, ga_ust=hi, p=p))
neo_irr = pd.DataFrame(neo_rows)
neo_irr.round(4).to_csv(f"{TAB}/11b_yeni_nesil_motor_IRR.csv", index=False)
print(neo_irr.round(3).to_string())

# Tek işletmeci modelleri: aynı şirket, aynı bakım programı, aynı raporlama alışkanlığı
single_rows = []
for op, body in [("DALA", "Dar"), ("CALA", "Dar"), ("AALA", "Dar"), ("DALA", "Genis")]:
    sub = ty[(ty.operator == op) & (ty.body == body)]
    for oc, lab in OUTCOMES.items():
        for adj, rhs in [("ham", "airbus"), ("yıl+yaş", "airbus + C(year) + C(age_bin)")]:
            r = fit_nb(f"{oc} ~ {rhs}", sub, "log_w")
            e, lo, hi, p = irr(r)
            single_rows.append(dict(isletmeci=sub.op_name.iloc[0], govde=BODY_TR[body], sonuc=lab,
                                    duzeltme=adj, IRR=e, ga_alt=lo, ga_ust=hi, p=p,
                                    airbus_ky=sub[sub.airbus == 1].w.sum(), boeing_ky=sub[sub.airbus == 0].w.sum()))
single = pd.DataFrame(single_rows)
single.round(4).to_csv(f"{TAB}/08b_tek_isletmeci_modelleri.csv", index=False)
print(single.round(3).to_string())

# ------------------------------------------------------------------ 8. Yaş eğrisi & yıllık trend
age_curve = describe(["body", "manufacturer", "age_bin"])
age_curve.round(3).to_csv(f"{TAB}/12_yas_egrisi.csv", index=False)
trend = describe(["body", "manufacturer", "year"])
trend.round(3).to_csv(f"{TAB}/13_yillik_trend.csv", index=False)

# Olay tedbir türleri dağılımı
proc_map = {"A": "Plansız iniş", "B": "Acil alçalma", "C": "Kalkış iptali", "D": "Bloka/kapıya dönüş",
            "E": "Motor kapatma", "F": "Yangın söndürücü", "G": "Oksijen maskesi"}
pp = (ev[ev.disruptive].assign(t=lambda d: d.PrecautionaryProcedureA.map(proc_map).fillna("Diğer acil/anormal"))
      .groupby(["body", "manufacturer", "t"]).size().unstack(0).fillna(0).astype(int))
pp.to_csv(f"{TAB}/14_tedbir_turleri.csv")

# ------------------------------------------------------------------ 9. Grafikler
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

C = {"Boeing": "#2a78d6", "Airbus": "#eb6834"}
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
                     "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
                     "axes.titlesize": 11, "axes.titleweight": "bold", "legend.frameon": False})


from matplotlib.ticker import NullFormatter, FuncFormatter  # noqa: E402


def logx(ax):
    """Log eksende yalnızca ana etiketleri sade sayı olarak göster."""
    ax.set_xscale("log")
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))


def save(fig, name):
    fig.savefig(f"{FIG}/{name}", dpi=200, bbox_inches="tight")
    plt.close(fig)


# F1: gövde × üretici oranları (3 sonuç)
fig, axes = plt.subplots(1, 4, figsize=(15, 3.8))
for ax, (lab, title) in zip(axes, [("sdr", "Tüm SDR"), ("ariza", "Sistem+motor (yapı hariç)"),
                                   ("oper", "Operasyonel (uçuş + taksi)"),
                                   ("aksatan", "Aksatan olay (tedbir uygulanan)")]):
    for i, body in enumerate(["Dar", "Genis"]):
        for j, m in enumerate(["Boeing", "Airbus"]):
            r = d_body[(d_body.body == body) & (d_body.manufacturer == m)].iloc[0]
            x = i + (j - 0.5) * 0.36
            ax.bar(x, r[f"{lab}_100ky"], 0.34, color=C[m], label=m if i == 0 else None, zorder=3)
            ax.errorbar(x, r[f"{lab}_100ky"], yerr=[[r[f"{lab}_100ky"] - r[f"{lab}_100ky_lo"]],
                        [r[f"{lab}_100ky_hi"] - r[f"{lab}_100ky"]]], color=INK2, capsize=3, lw=1, zorder=4)
            ax.text(x, r[f"{lab}_100ky_hi"], f"{r[f'{lab}_100ky']:.0f}", ha="center", va="bottom",
                    fontsize=8, color=INK2)
    ax.set_xticks([0, 1], ["Dar gövde", "Geniş gövde"])
    ax.set_title(title)
    ax.grid(axis="x", visible=False)
axes[0].set_ylabel("Olay / 100 uçak-yılı")
axes[0].legend(loc="upper left", bbox_to_anchor=(0, 0.9))
fig.suptitle("Şekil 1 — Ham oranlar (düzeltilmemiş), %95 kümelenmiş bootstrap GA", x=0.01, ha="left",
             fontsize=11, color=INK2)
save(fig, "fig1_ham_oranlar.png")

# F2: Orman grafiği — modeller
fig, axes = plt.subplots(1, 2, figsize=(12, 6.8), sharex=True)
for ax, body in zip(axes, ["Dar gövde", "Geniş gövde"]):
    sub = reg[reg.govde == body].reset_index(drop=True)
    ylabels = []
    for k, (_, r) in enumerate(sub.iterrows()):
        y = -k
        col = {"Tüm SDR": INK2, "Operasyonel (uçuş+taksi)": "#6250d6",
               "Sistem+motor arızası (yapı hariç)": "#1baf7a",
               "Aksatan olay (tedbir uygulanan)": "#e34948"}[r.sonuc]
        ax.plot([r.ga_alt, r.ga_ust], [y, y], color=col, lw=2)
        ax.plot(r.IRR_Airbus_vs_Boeing, y, "o", color=col, ms=6, mec=SURF, mew=1.5)
        ylabels.append(f"{r.sonuc.split(' (')[0][:20]} · {r.model.split(' ')[0]}")
    ax.set_yticks(-np.arange(len(sub)), ylabels, fontsize=7.5)
    ax.axvline(1, color=INK, lw=1)
    logx(ax)
    ax.set_xticks([0.5, 0.7, 1, 1.4, 2, 3], ["0.5", "0.7", "1", "1.4", "2", "3"])
    ax.set_title(body)
    ax.set_xlabel("IRR (Airbus / Boeing)  ←  Airbus daha az · Airbus daha çok  →")
fig.suptitle("Şekil 2 — Negatif binom modelleri: Airbus/Boeing olay oranı (M0 ham → M3 tam düzeltilmiş)",
             x=0.01, ha="left", fontsize=11, color=INK2)
fig.tight_layout()
save(fig, "fig2_regresyon_orman.png")

# F3: yaş eğrisi
fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True)
for i, body in enumerate(["Dar", "Genis"]):
    for j, (lab, title) in enumerate([("sdr", "Tüm SDR"), ("aksatan", "Aksatan olay")]):
        ax = axes[j, i]
        for m in ["Boeing", "Airbus"]:
            s = age_curve[(age_curve.body == body) & (age_curve.manufacturer == m)].set_index("age_bin")
            s = s.reindex(AGE_LAB)
            ok = s.kuyruk_yil >= 20
            x = np.arange(len(AGE_LAB))[ok.values]
            ax.fill_between(x, s[f"{lab}_100ky_lo"][ok], s[f"{lab}_100ky_hi"][ok], color=C[m], alpha=0.15, lw=0)
            ax.plot(x, s[f"{lab}_100ky"][ok], "-o", color=C[m], lw=2, ms=6, mec=SURF, mew=1.5, label=m)
        ax.set_title(f"{BODY_TR[body]} — {title}")
        ax.set_xticks(range(len(AGE_LAB)), AGE_LAB)
        if i == 0:
            ax.set_ylabel("Olay / 100 uçak-yılı")
        if j == 1:
            ax.set_xlabel("Uçağın toplam uçuş saati (yaş göstergesi)")
axes[0, 0].legend()
fig.suptitle("Şekil 3 — Yaşa göre olay oranı: aynı yaştaki uçaklar karşılaştırıldığında", x=0.01, ha="left",
             fontsize=11, color=INK2)
fig.tight_layout()
save(fig, "fig3_yas_egrisi.png")

# F4: aynı işletmeci içinde
CARGO_NAMES = {"FDEA": "FedEx (FDEA)", "IPXA": "UPS (IPXA)"}
o = ops[ops.isletmeci != "BILINMIYOR"].copy()
o["isim"] = o.isletmeci.map(CARGO_NAMES).fillna(o.isim)
o["etiket"] = o.isim + " · " + o.govde.str.replace(" gövde", "")
fig, axes = plt.subplots(1, 2, figsize=(12, 0.45 * len(o) + 1.6), sharey=True)
for ax, (lab, title) in zip(axes, [("sdr", "Tüm SDR / 100 uçak-yılı"), ("aksatan", "Aksatan olay / 100 uçak-yılı")]):
    for k, (_, r) in enumerate(o.iterrows()):
        b, a = r[f"Boeing_{lab}_100ky"], r[f"Airbus_{lab}_100ky"]
        ax.plot([b, a], [k, k], color=GRID, lw=3, zorder=1)
        ax.plot(b, k, "o", color=C["Boeing"], ms=8, mec=SURF, mew=1.5, zorder=3, label="Boeing" if k == 0 else None)
        ax.plot(a, k, "o", color=C["Airbus"], ms=8, mec=SURF, mew=1.5, zorder=3, label="Airbus" if k == 0 else None)
    ax.set_yticks(range(len(o)), o.etiket)
    logx(ax)
    ax.set_title(title)
    ax.invert_yaxis()
axes[0].legend(loc="lower right")
fig.suptitle("Şekil 4 — Aynı işletmeci, aynı bakım ve raporlama kültürü içinde Boeing vs Airbus",
             x=0.01, ha="left", fontsize=11, color=INK2)
fig.tight_layout()
save(fig, "fig4_ayni_isletmeci.png")

# F5: nesil karşılaştırma IRR — dolu nokta: katı eşleştirme (aynı işletmeci), boş: tüm-veri modeli
fig, axes = plt.subplots(1, 3, figsize=(14, 0.55 * len(PAIRS) + 1.8), sharey=True)
labels = [f"{ag} vs {bg}" for _, bg, ag, _ in PAIRS]
for ax, oc in zip(axes, ["Tüm SDR", "Sistem+motor arızası (yapı hariç)", "Aksatan olay (tedbir uygulanan)"]):
    for k, (_, bg, ag, lab) in enumerate(PAIRS):
        for df_, dy, filled in [(gen_cmp, 0.15, False), (strict, -0.15, True)]:
            r = df_[(df_.boeing == bg) & (df_.airbus == ag) & (df_.sonuc == oc)]
            if r.empty:
                continue
            r = r.iloc[0]
            col = INK if filled else INK2
            ax.plot([r.ga_alt, r.ga_ust], [k + dy] * 2, color=col, lw=2 if filled else 1.2)
            ax.plot(r.IRR_Airbus_vs_Boeing, k + dy, "o", ms=7, mec=col, mew=1.5,
                    color=col if filled else SURF,
                    label=("Aynı işletmeci (katı eşleştirme)" if filled else "Tüm veri, işletmeci etkisi düzeltilmiş")
                    if k == 1 else None)
    ax.axvline(1, color="#e34948", lw=1)
    logx(ax)
    ax.set_xticks([0.25, 0.5, 1, 2, 4], ["0.25", "0.5", "1", "2", "4"])
    ax.set_title(oc.split(" (")[0])
    ax.set_xlabel("IRR (Airbus / Boeing)")
axes[0].set_yticks(range(len(labels)), labels)
axes[0].invert_yaxis()
axes[0].legend(loc="lower left", fontsize=8)
fig.suptitle("Şekil 5 — Nesil bazında karşılaştırma (yıl + yaş düzeltilmiş). <1: Airbus daha az olay",
             x=0.01, ha="left", fontsize=11, color=INK2)
fig.tight_layout()
save(fig, "fig5_nesil_karsilastirma.png")

# F6: ATA bölümleri (dar gövde yolcu, tüm SDR) — en büyük 14 bölüm
a = ata[(ata.govde == "Dar gövde") & (ata.tur == "tum")].copy()
a["top"] = a.Boeing + a.Airbus
a = a.nlargest(14, "top").sort_values("top")
fig, ax = plt.subplots(figsize=(9, 6))
y = np.arange(len(a))
ax.barh(y + 0.19, a.Boeing, 0.36, color=C["Boeing"], label="Boeing", zorder=3)
ax.barh(y - 0.19, a.Airbus, 0.36, color=C["Airbus"], label="Airbus", zorder=3)
ax.set_yticks(y, [f"ATA {int(r.ata)} {r.ata_adi or ''}" for _, r in a.iterrows()])
ax.set_xlabel("SDR / 100 uçak-yılı (yolcu ana hat, dar gövde)")
ax.legend(loc="lower right")
ax.grid(axis="y", visible=False)
ax.set_title("Şekil 6 — Hangi sistemler fark yaratıyor? (ham, düzeltilmemiş)", loc="left", color=INK2,
             fontweight="normal")
save(fig, "fig6_ata_bolumleri.png")

# F7: yıllık trend
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=False)
for ax, body in zip(axes, ["Dar", "Genis"]):
    for m in ["Boeing", "Airbus"]:
        s = trend[(trend.body == body) & (trend.manufacturer == m)]
        ax.plot(s.year, s.aksatan_100ky, "-o", color=C[m], lw=2, ms=6, mec=SURF, mew=1.5, label=m)
    ax.set_title(f"{BODY_TR[body]} — aksatan olay / 100 uçak-yılı")
    ax.set_xticks(range(2020, 2027), [str(v) if v < 2026 else "2026*" for v in range(2020, 2027)])
axes[0].legend()
fig.suptitle("Şekil 7 — Yıllara göre aksatan olay oranı (*2026: 15 Eylül'e kadar, ağırlıklı)",
             x=0.01, ha="left", fontsize=11, color=INK2)
fig.tight_layout()
save(fig, "fig7_yillik_trend.png")

# F8: yeni nesil motor ayrımı
order = ["B737 MAX (CFM LEAP-1B)", "A320neo + CFM LEAP-1A", "A320neo + PW1100G (GTF)"]
nn = neo.set_index("alt").reindex(order)
cols8 = [C["Boeing"], "#f2a07e", C["Airbus"]]
fig, axes = plt.subplots(1, 3, figsize=(13, 3.6))
for ax, (c, title) in zip(axes, [("sdr_100ky", "Tüm SDR"), ("motor_sdr_100ky", "Motor SDR (ATA 71-80)"),
                                 ("aksatan_100ky", "Aksatan olay")]):
    ax.bar(range(3), nn[c], 0.6, color=cols8, zorder=3)
    for k, v in enumerate(nn[c]):
        ax.text(k, v, f"{v:.0f}", ha="center", va="bottom", fontsize=9, color=INK2)
    ax.set_xticks(range(3), ["737 MAX\nLEAP-1B", "A320neo\nLEAP-1A", "A320neo\nPW GTF"])
    ax.set_title(title)
    ax.grid(axis="x", visible=False)
axes[0].set_ylabel("Olay / 100 uçak-yılı")
fig.suptitle("Şekil 8 — Yeni nesil dar gövde: farkın kaynağı motor seçimi (ham oranlar)", x=0.01, ha="left",
             fontsize=11, color=INK2)
fig.tight_layout()
save(fig, "fig8_yeni_nesil_motor.png")

# ------------------------------------------------------------------ 10. Özet
main = reg[reg.model.str.startswith("M3")]
summary["ana_model"] = main.round(4).to_dict("records")
summary["betimsel"] = d_body.round(3).to_dict("records")
with open("results/summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=1, default=float)
print(main.round(3).to_string())

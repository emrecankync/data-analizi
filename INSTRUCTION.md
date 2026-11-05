# INSTRUCTION — FAA SDR verisiyle Boeing vs Airbus arıza analizi (adım adım)

Bu belge, analizde kullanılan **her adımı** sırasıyla anlatır. Amaç, başka birinin
(ya da bir yapay zekâ ajanının) aynı veriyle aynı sonuçlara ulaşabilmesidir. Her adımda
**ne yapıldığı**, **neden yapıldığı** ve **hangi dosyanın üretildiği** yazılıdır.

> Araştırma sorusu: *Bir havayolu dar gövde ya da geniş gövde uçak satın alacak veya
> kiralayacaksa, Boeing mi Airbus mu daha az arıza çıkarır?*

---

## 0. Ortam ve klasör yapısı

```
data-analizi/
├── data/raw/            # FAA SDR CSV'leri (git'e girmez, .gitignore)
├── data/processed/      # ara parquet dosyaları (git'e girmez)
├── src/
│   ├── model_map.py     # model kodu → aile / nesil / gövde eşleme kuralları
│   ├── step1_prepare.py # ADIM 1-4: okuma, temizlik, sınıflandırma, olay bayrakları
│   ├── step2_exposure.py# ADIM 5: payda (kuyruk-yıl paneli, uçuş saati, döngü)
│   └── step3_analysis.py# ADIM 6-11: oranlar, modeller, duyarlılık, grafikler
├── results/tables/      # tüm sonuç tabloları (CSV)
├── results/figures/     # tüm grafikler (PNG)
├── run_all.sh           # tek komutla tüm boru hattı
├── INSTRUCTION.md       # bu belge
└── MAKALE.md            # sonuçları anlatan makale
```

Kurulum (Python 3.11+):

```bash
pip install -r requirements.txt
```

Veriyi yerleştirme: FAA SDR yıllık CSV'leri (`SDR-2020.csv` … `SDR-2026.csv`) `data/raw/`
klasörüne konur. Bu projede kullanıcı üç dosya verdi: `SDR-2022.zip` (2020-2022),
`SDR-2025.zip` (2023-2025 + 2026) ve ayrıca `SDR-2026.csv`. Zip'ler açıldı.
`SDR-2026.csv`'nin zip içindekiyle bayt düzeyinde aynı olduğu `cmp` ile doğrulandı.
Kaynak: FAA Service Difficulty Reporting System (sdrs.faa.gov), 77 sütunlu standart dışa aktarım.

Tüm analizi çalıştırma (~2,5 dakika):

```bash
bash run_all.sh
```

---

## ADIM 1 — Veriyi tanı (keşif)

**Ne yapıldı:** 7 CSV metin olarak (`dtype=str`) okundu. Satır sayıları, sütunlar, üretici
dağılımı, kod alanlarının frekansları ve tarih aralığı incelendi.

**Bulgular:**
- 417.528 kayıt; tarih aralığı 01.01.2020 – 15.09.2026 (2026 kısmi yıl).
- `OperatorControlNumber` hiç tekrar etmiyor, dolayısıyla birincil anahtar olarak kullanılabilir.
- `AircraftMake`: BOEING 215.446, AIRBUS 92.161, diğerleri (CRJ, Embraer, MD vb.) kapsam dışı.
- SDR kayıtlarının %77'si `StageOfOperationCode = IN` (muayene/bakım sırasında bulunan).
  Yani SDR'lerin çoğu uçuşta yaşanan arıza değil, **bakımda bulunan kusur** (korozyon, çatlak).
  Analizde bu ayrım önemli olduğu için ayrı sonuç değişkenleri tanımlandı (Adım 4).

**Neden:** SDR bir "olay kaydı" sistemidir. Filo büyüklüğü, uçuş saati ya da uçak yaşı
doğrudan verilmez. Hangi sütunlardan payda türetilebileceğini görmek için keşif şarttı.

---

## ADIM 2 — Kod alanlarının anlamını doğrula

FAA kod listeleri bazen eksik veya belirsizdir. Bu yüzden her önemli kod için
rastgele `Discrepancy` (serbest metin) örnekleri okunarak anlamı doğrulandı:

| Alan | Kod | Doğrulanan anlam (metin örneklerinden) |
|---|---|---|
| `PrecautionaryProcedure*` | A | Plansız iniş (divert / ATB, "returned to …") |
| | B | Acil alçalma (basınç kaybı, maske düşmesi) |
| | C | Kalkış iptali (RTO, "rejected takeoff") |
| | D | Bloka/kapıya dönüş (BTB) |
| | E | Uçuşta motor kapatma |
| | F | Yangın söndürücü kullanımı |
| | G | Oksijen maskesi açılması |
| | H, I, J, L | Diğer acil/anormal prosedürler (acil durum ilanı, go-around vb.) |
| | K | Tedbir yok |
| | O | Diğer |
| `StageOfOperationCode` | IN | Muayene / bakım |
| | TX | Taksi |
| | TO, CL, CR, DE, AP, LD | Kalkış, tırmanış, seyir, alçalma, yaklaşma, iniş |
| | NR, UK | Bildirilmemiş, bilinmiyor |
| `JASCCode` | ilk 2 hane | ATA bölümü (21 klima, 32 iniş takımı, 53 gövde, 72 motor …) |

---

## ADIM 3 — Temizlik ve kapsam (`src/step1_prepare.py`)

Sırasıyla uygulanan filtreler. Her adımdaki kayıt sayısı `results/tables/01_veri_akisi.csv` dosyasına yazılır:

| # | İşlem | Kalan kayıt |
|---|---|---|
| 1 | Ham kayıt (tüm üreticiler) | 417.528 |
| 2 | `OperatorControlNumber` tekrarları çıkar | 417.528 |
| 3 | Yalnızca `AircraftMake ∈ {BOEING, AIRBUS}` | 307.607 |
| 4 | Geçerli tarih + boş olmayan tescil (N) numarası | 307.168 |
| 5 | MAX/NG düzeltmesi (aşağıda) | 307.168 |
| 6 | Kuyruk bazında baskın model ataması, atanamayanları çıkar | 306.977 |
| 7 | 727, A310, A380 çıkar (her biri <250 kayıt) | **306.764** |

### 3a. Model kodunu aile / nesil / gövdeye eşleme (`src/model_map.py`)
SDR'de model alanı serbest biçimlidir (`7378H4`, `A321253NX`, `737*` …). Kurallar:

- **Boeing:** `737` + seri hanesi + müşteri kodu. Örneğin `7378H4` = 737-8H4 (NG -800, Southwest).
  - Seri 2/3/4/5 → **737 Classic**; seri 6/7/8/9 + müşteri kodu → **737 NG**.
  - Müşteri kodu olmayan `7377`, `7378`, `7379`, `73710`, `7378200` → **737 MAX**.
  - `717` → B717, `757` → B757 (dar gövde). `767`, `777`, `787`, `747` → geniş gövde.
- **Airbus:** `A319/A320/A321` + motor kodu; sonu `N`/`NX` → **A320neo ailesi**, diğerleri **A320ceo**.
  `A3309xx` → A330neo, diğer `A330` → A330ceo; `A300`, `A350`. `BD500-1A10/11` → **A220**
  (eski Bombardier CSeries, SDR'de AIRBUS altında). Airbus Helicopters modelleri (EC135, H160…) dışarıda.
- **A320neo motoru:** Airbus model kodunun motor hanesi `7` → **PW1100G (GTF)**, `5` → **CFM LEAP-1A**
  (ör. `A321271NX` = PW1133G, `A321253NX` = LEAP-1A33).

### 3b. 737 MAX / NG karışıklığının düzeltilmesi
Bazı işletmeciler NG uçakları için de müşteri kodsuz `7378`/`7379` yazıyor. İki kural uygulandı:
1. **Fiziksel imkânsızlık kuralı:** MAX ilk teslimatı 2017. Bir uçak yılda en fazla ~5.000 saat
   uçabilir. `Toplam saat > 5000 × (2017-05-01'den beri geçen yıl) + 1000` ise kayıt MAX olamaz → NG.
2. **Delta kuralı:** Delta 2020-2026 arasında hiç 737 MAX işletmedi. `DALA` + MAX kodlu kayıtlar,
   yani yeni -900ER'ler, NG'ye çevrildi.
Toplam 1.370 kayıt düzeltildi.

### 3c. Kuyruk bazında tek model
Aynı tescil numarası (`RegistryNNumber`, baştaki "N" atılarak) için en sık görülen nesil o
kuyruğun **tüm** kayıtlarına atandı. Bu işlem:
- genel kodlu 13.666 kaydın (`737*`, `A320*`, `767` …) 13.475'ini doldurdu,
- aynı uçağa farklı nesil yazılmış 688 tutarsız kaydı düzeltti.

### 3d. Sayısal alanlar
`AircraftTotalTime` ≤0 veya >160.000 saat, `AircraftTotalCycles` ≤0 veya >120.000 ise
NaN yapıldı (ticari jette bu değerler fiziksel olarak mümkün değil; veri giriş hatası).

### 3e. Yolcu / kargo rolü
Filo profilinden ana hat yolcu işletmecileri belirlendi: American (AALA), Delta (DALA),
United (CALA), Southwest (SWAA), Alaska (ASAA), JetBlue (YENA), Spirit (GTIA), Hawaiian (HALA),
Allegiant (WX0A), Sun Country (SCNA), Frontier (F3LA). Diğer işletmeciler (FedEx FDEA,
UPS IPXA, Atlas, ABX, Kalitta vb.) "Kargo/Diğer" olarak gruplandı.

---

## ADIM 4 — "Arıza" ne demek? Dört sonuç değişkeni

"Arıza çıkarma ihtimali" tek bir sayı değildir. SDR içinde dört katman tanımlandı.
En geniş katmandan en dar ve işletme açısından en önemli katmana doğru:

| Değişken | Tanım | Neyi ölçer |
|---|---|---|
| `n_all` — **Tüm SDR** | Her rapor | Toplam bildirim yükü (bakım bulguları dahil) |
| `n_fail` — **Sistem+motor arızası** | ATA 21-46 (sistemler) + 49, 71-80 (APU/motor); **yapı (51-57) hariç** | Korozyon/çatlak gibi yaşlanma bulgularından arındırılmış gerçek bileşen arızası |
| `n_oper` — **Operasyonel** | Safha ∈ {taksi, kalkış, tırmanış, seyir, alçalma, yaklaşma, iniş} | Uçak hizmetteyken ortaya çıkan arıza |
| `n_disr` — **Aksatan olay** | `PrecautionaryProcedureA-D` içinde K/O/boş dışında herhangi bir kod | Plansız iniş, kalkış iptali, motor kapatma gibi **operasyonu bozan** arıza |

**Karar için birincil ölçü:** "Aksatan olay" ve "Sistem+motor arızası". Bir havayolunun
parasını ve itibarını doğrudan etkileyen bunlardır. "Tüm SDR" bakım programının ne kadar
titiz olduğunu da yansıttığı için ikincil tutuldu.

---

## ADIM 5 — Payda oluşturma: kuyruk-yıl paneli (`src/step2_exposure.py`)

SDR'de filo büyüklüğü yok. FAA tescil veritabanına bu ortamdan erişilemedi (ağ politikası).
Bu yüzden payda **verinin kendisinden** türetildi:

1. **Filoda bulunma:** Her uçak (üretici + tescil no) ilk SDR yılı ile son SDR yılı arasındaki
   **her yıl** filoda sayıldı. O yıl rapor vermemiş olsa da sayıldı, böylece sıfır-raporlu
   yıllar paydaya girdi. Sonuç: 7.253 uçak, 34.679 uçak-yılı.
2. **2026 ağırlığı:** Veri 15 Eylül 2026'da bitiyor → 2026 yılı 258/365 = **0,706 uçak-yılı**.
3. **Yıllık işletmeci:** O yıl en çok rapor veren işletmeci. Rapor yoksa önceki yıldan taşındı.
4. **Kullanım tahmini (saat/yıl ve iniş/yıl):** Aynı uçağın farklı tarihli raporlarındaki
   `AircraftTotalTime` ve `AircraftTotalCycles` değerlerine Theil-Sen tipi dayanıklı eğim
   (ikili eğimlerin medyanı) uyduruldu. Koşullar: en az 2 gözlem ve 180 gün. Makul aralık:
   200-6.000 saat/yıl, 50-3.000 iniş/yıl. Kuyrukların %85'inde doğrudan ölçüldü. Kalanlar
   işletmeci×nesil, o da yoksa nesil medyanıyla dolduruldu.
5. **Yaş göstergesi:** Takvim yaşı SDR'de yok. Bunun yerine her yılın ortasında
   uçağın **tahmini toplam uçuş saati** kullanıldı ve 5 gruba bölündü: <10k, 10-25k, 25-40k, 40-60k, >60k.
6. **Olay sayımları:** Her kuyruk-yıl için `n_all, n_fail, n_oper, n_disr` (ve ATA grubu) sayıldı.

**Doğrulama (sağduyu kontrolü):** Türetilen filo büyüklükleri kamuya açık bilgilerle uyumlu.
Southwest 737NG ≈ 650, American A320 ailesi ≈ 400 uçak, Delta geniş gövde uçakları ≈ 4.400-4.800
saat/yıl, FedEx 757 besleme uçakları ≈ 700 saat/yıl.

---

## ADIM 6 — Betimsel oranlar (`step3_analysis.py`, bölüm 1)

Gövde×üretici, rol×gövde×üretici ve nesil düzeyinde şunlar hesaplandı:
- olay / 100 uçak-yılı (4 sonuç için),
- olay / 10.000 uçuş saati ve / 10.000 iniş,
- **yıllık olasılık:** tam yıllarda (2020-2025) en az bir aksatan olay yaşayan uçak-yılı oranı.

Güven aralıkları **kuyruk düzeyinde kümelenmiş bootstrap** ile hesaplandı (300 tekrar).
Aynı uçağın raporları birbirinden bağımsız olmadığı için bu gerekli.
→ `02_…`, `03_…`, `04_…csv`, Şekil 1.

---

## ADIM 7 — Ana model: negatif binom regresyonu (bölüm 2)

Birim: **kuyruk-yıl**. Sayım verisi aşırı yayılımlı olduğu için (varyans ≫ ortalama)
Poisson yerine **negatif binom** kullanıldı:

```
olay_sayısı ~ Airbus + C(yıl) + C(işletmeci) + C(yaş_grubu),  offset = log(uçak-yılı)
```

- Standart hatalar **kuyruk düzeyinde kümelenmiş** (cluster-robust).
- `exp(β_Airbus)` = **IRR (Incidence Rate Ratio)**, yani Airbus/Boeing olay oranı.
  IRR < 1 ise Airbus daha az olay üretiyor, IRR > 1 ise Boeing daha az.
- Modeller adım adım kuruldu, böylece her düzeltmenin etkisi görülebiliyor:
  M0 ham → M1 +yıl → M2 +işletmeci → **M3 +yaş (ana model)** → M4 payda uçuş saati → M5 payda iniş.
- 30 uçak-yılından küçük işletmeciler "DİĞER" grubunda toplandı.

**Neden işletmeci etkisi şart?** İşletmeciler arasındaki raporlama farkı, üreticiler arasındaki
farktan çok daha büyük. Örneğin aynı 737NG için Southwest yılda 18,7, United 5,4 SDR/uçak
raporluyor (`09_isletmeci_raporlama_kulturu.csv`). Southwest neredeyse hiç önleyici tedbir kodu
girmiyor (aksatan olay 6-12 / 100 uçak-yılı, diğerlerinde 25-80). Bu düzeltme yapılmazsa
sonuç "Boeing mi Airbus mu" değil, "Southwest mi değil mi" sorusuna cevap verir.
→ `05_nb_regresyon_IRR.csv`, Şekil 2.

---

## ADIM 8 — Duyarlılık analizleri (bölüm 3)

Ana model (M3) şu alt örneklerde tekrar kuruldu, her biri `n_all`, `n_fail`, `n_disr` için:
Southwest hariç · yalnızca yolcu · yalnızca kargo · yalnızca 2022-2026 (COVID sonrası) ·
ortak yaş aralığı (5k-60k saat) · yalnızca iki üreticiyi de işleten işletmeciler ·
yalnızca kullanımı kendi verisinden ölçülen kuyruklar.
→ `06_duyarlilik.csv`

---

## ADIM 9 — Nesil eşleştirmeleri (bölüm 4)

Satın alma/kiralama kararı "Boeing" ile "Airbus" arasında değil, belirli tipler arasında
verilir. Bu yüzden şu eşleştirmeler yapıldı:

| Eşleştirme | Anlamı |
|---|---|
| 737 NG ↔ A320ceo | ikinci el / kiralık mevcut nesil dar gövde |
| 737 MAX ↔ A320neo | bugün yeni alınacak dar gövde |
| 717 ↔ A220 | 100-150 koltuk |
| 757 ↔ A320ceo (A321) | uzun menzilli dar gövde |
| 787 ↔ A350, 787 ↔ A330neo | yeni nesil geniş gövde |
| 777 ↔ A330ceo, 767 ↔ A330ceo, 767 ↔ A300 | eski nesil geniş gövde / kargo |

İki yöntem kullanıldı:
1. **Tüm veri modeli:** `olay ~ C(nesil) + yıl + işletmeci + yaş`, ardından iki nesil katsayısının
   farkı alındı (kontrast). Güven aralıkları kovaryans matrisinden hesaplandı. Uyarı: Bu yöntemde
   işletmeci etkisi diğer filolardan "köprü" ile tahmin edilir. Örneğin A350 yalnızca Delta'da,
   787 ise United/American'da uçuyor.
2. **Katı eşleştirme (tercih edilen):** Yalnızca **her iki nesli de** (her biri ≥30 uçak-yılı)
   işleten işletmeciler kullanıldı. Böylece karşılaştırma tamamen aynı şirket içinde yapıldı.
   İki üreticinin yaş grupları hiç örtüşmüyorsa yaş düzeltmesi tanımsız olduğundan çıkarıldı
   (717↔A220, tablodaki `yas_duzeltmesi` sütunu).
→ `07_…`, `07b_…csv`, Şekil 5.

---

## ADIM 10 — İşletmeci içi, ATA bölümü ve motor analizleri (bölüm 5-7)

- **Aynı işletmeci içinde:** Her iki üreticiyi aynı gövde sınıfında ≥40 uçak-yılı işleten
  işletmeciler için ham oranlar hesaplandı (`08_…`, Şekil 4). Delta, United ve American için ayrıca
  tek işletmeci NB modeli kuruldu (ham ve yıl+yaş düzeltmeli, `08b_…`).
- **ATA bölümü:** Yolcu filolarında, ATA bölümü başına 100 uçak-yılı başına SDR ve aksatan olay
  hesaplandı (`10_…`, Şekil 6). Amaç: farkın hangi sistemden geldiğini görmek.
- **Yeni nesil motor ayrımı:** 737 MAX (LEAP-1B), A320neo+LEAP-1A ve A320neo+PW1100G GTF ayrı
  gruplar olarak karşılaştırıldı. Hem ham oran hem NB modeli kullanıldı (referans: MAX), ek sonuç
  olarak motor SDR'si (ATA 71-80) eklendi (`11_…`, `11b_…`, Şekil 8).

---

## ADIM 11 — Yaş eğrisi, yıllık trend, grafikler

- Yaş grubu × üretici × gövde oranları → Şekil 3 (aynı yaştaki uçakları kıyaslamak için).
- Yıl × üretici × gövde aksatan olay oranı → Şekil 7.
- Grafik kuralları: Boeing her yerde mavi (#2a78d6), Airbus her yerde turuncu (#eb6834).
  Renk körlüğü testinden geçmiş palet kullanıldı. IRR grafikleri log ölçekli, 1 çizgisi referans.

---

## ADIM 12 — Yorumlama kuralları

1. Karar için önce **aksatan olay** ve **sistem+motor** sonuçlarına bakılır. "Tüm SDR"
   bakım titizliğini de ölçtüğü için tek başına "daha çok arıza" anlamına gelmez.
2. Bir sonuç ancak **(a)** ana modelde, **(b)** duyarlılık analizlerinin çoğunda ve **(c)** aynı
   işletmeci içi karşılaştırmada aynı yönü gösteriyorsa "sağlam" kabul edilir.
3. Yöntemler çeliştiğinde (ör. MAX↔neo) sonuç "belirsiz" olarak raporlanır ve çelişkinin
   kaynağı açıklanır.
4. Güven düzeyi açıkça yazılır: yüksek / orta / düşük.

---

## Çıktı listesi

| Dosya | İçerik |
|---|---|
| `results/tables/01_veri_akisi.csv` | Temizlik adımları ve kalan kayıt sayısı |
| `02_govde_uretici_oranlar.csv` | Gövde × üretici oranları, GA, yıllık olasılık |
| `03_rol_govde_uretici_oranlar.csv` | Yolcu/kargo ayrımlı oranlar |
| `04_nesil_oranlar.csv` | Nesil (tip) bazında oranlar |
| `05_nb_regresyon_IRR.csv` | M0-M5 modelleri, 4 sonuç, 2 gövde |
| `06_duyarlilik.csv` | Duyarlılık analizleri |
| `07_nesil_karsilastirma_IRR.csv` / `07b_nesil_kati_eslestirme_IRR.csv` | Nesil eşleştirmeleri |
| `08_ayni_isletmeci_karsilastirma.csv` / `08b_tek_isletmeci_modelleri.csv` | İşletmeci içi |
| `09_isletmeci_raporlama_kulturu.csv` | İşletmeci × tip SDR yoğunluğu |
| `10_ata_bolum_100ky.csv` | ATA bölümü bazında oranlar |
| `11_yeni_nesil_motor.csv` / `11b_yeni_nesil_motor_IRR.csv` | MAX vs neo-LEAP vs neo-GTF |
| `12_yas_egrisi.csv`, `13_yillik_trend.csv`, `14_tedbir_turleri.csv` | Ek tablolar |
| `results/figures/fig1…fig8*.png` | Makaledeki 8 şekil |
| `results/summary.json` | Ana model ve betimsel özet |

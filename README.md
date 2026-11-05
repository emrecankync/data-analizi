# Boeing vs Airbus — FAA SDR arıza analizi (2020-2026)

Bu proje, FAA Service Difficulty Report (SDR) verisiyle dar ve geniş gövdede hangi üreticinin
daha az arıza çıkardığını inceler.

- **[MAKALE.md](MAKALE.md)**: yöntem, bulgular, karar matrisi ve sınırlılıklar.
- **[INSTRUCTION.md](INSTRUCTION.md)**: analizin adım adım tarifi, tekrar üretmek için.
- `src/`: kod · `results/tables/`: sonuç tabloları · `results/figures/`: şekiller.

```bash
pip install -r requirements.txt   # ardından SDR-2020…2026.csv dosyalarını data/raw/ içine koyun
bash run_all.sh
```

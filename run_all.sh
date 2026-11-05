#!/usr/bin/env bash
# Tüm analizi baştan sona çalıştırır. Proje kökünden: bash run_all.sh
set -euo pipefail
python3 -I src/step1_prepare.py
python3 -I src/step2_exposure.py
python3 -I src/step3_analysis.py
echo "Bitti: results/tables, results/figures, results/summary.json"

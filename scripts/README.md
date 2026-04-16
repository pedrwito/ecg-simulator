# Scripts — Signal preprocessing and data exploration

## Signal data sources

The simulator uses ECG recordings from multiple sources. The preprocessed
signals are stored in `data/signals.json` (committed to the repo). The raw
source datasets are NOT committed (too large) — download them if you need
to re-process or add new signals.

### Source 1: Original dataset (`old - original/signals.csv`)

- **What:** 12-lead ECG recordings at 500Hz, 10 seconds each
- **Rhythms:** SR (Sinus), AFIB (Atrial Fibrillation), PACE (Pacemaker), SVTAC (SVT)
- **Origin:** Pre-existing dataset, origin unknown. Stored in the repo under `old - original/`
- **Used for:** AFib, Pacemaker, and legacy SVT recordings

### Source 2: PhysioNet ECG-Arrhythmia Database

- **What:** 45,000+ 12-lead ECGs at 500Hz, 10 seconds each, labeled with SNOMED-CT codes
- **URL:** https://physionet.org/content/ecg-arrhythmia/1.0.0/
- **Rhythms extracted:** AF (Atrial Flutter), LBBB, RBBB, 1AVB, 3AVB, VPB, WPW, SVT, AFIB (fast)
- **How to download:** Use `wfdb` library or download from PhysioNet website
- **Used for:** All conduction abnormalities, flutter, fast AFib/SVT replacements

### Source 3: Cardially ECG Dataset (VFib)

- **What:** 260 single-lead ECG recordings from out-of-hospital cardiac arrest patients.
  Each file contains 9 seconds of ventricular fibrillation before a defibrillation shock.
- **URL:** https://data.mendeley.com/datasets/wpr5nzyn2z/1
- **Paper:** Benini et al., "ECG waveform dataset for predicting defibrillation outcome
  in out-of-hospital cardiac arrested patients", Data in Brief, 2020
- **How to download:** Download from Mendeley Data, extract to `cardially-ecg-dataset/`
- **Used for:** Real VFib waveform display. ROEA folder = successful defibrillation cases,
  noROEA = unsuccessful. We use ROEA recordings.
- **Note:** The txt files only contain the pre-shock VFib (9s). Post-shock recovery
  waveforms exist only as scanned PDFs (not digitized). The post-defibrillation
  animation in the simulator is synthetic.

### Synthetic signals (generated in the browser)

- **Ritmo Sinusal, Taquicardia Sinusal, Bradicardia Sinusal:** Gaussian PQRST model
  in `js/signals.js`. HR is dynamically adjustable by the professor.
- **Post-defibrillation recovery:** Synthetic biphasic shock artifact + post-shock
  pause + gradual sinus return in `js/monitor.js:triggerDefibrillation()`.
- **PPG (Plethysmograph):** Generated from ECG R-peak positions with ~250ms delay
  for beat-to-beat synchronization. For synthetic rhythms, generated independently.
- **Respiratory:** Simple sinusoidal model at the configured respiratory rate.
- **CO2 (Capnography):** Trapezoidal breath cycle model (inspiration/plateau/expiration).

## Scripts

### `preprocess_signals.py`

Converts raw signal data into the web app's `data/signals.json`.

**Pipeline per signal:**
1. Median filter baseline removal (removes baseline wander)
2. Bandpass filter 0.5–50Hz (removes DC offset and high-frequency noise)
3. Downsample 500Hz → 150Hz (matches the web app's virtual sample rate)
4. Normalize amplitude to [-1, 1]
5. Detect R-peaks using Pan-Tompkins algorithm
6. Save signal + R-peak indices as JSON

**Usage:**
```bash
source .venv/bin/activate
python scripts/preprocess_signals.py
```

**To add new signals:**
1. Append rows to `old - original/signals.csv` (one row per lead, 5000 samples at 500Hz)
2. Append matching rows to `old - original/labels.csv` (format: `LABEL,PATIENT_ID,LEAD`)
3. Run `python scripts/preprocess_signals.py`
4. Add the new rhythm to `js/config.js` RHYTHMS registry
5. Add an `<option>` to both dropdowns in `index.html`

### `explore_physionet.py`

Search, preview, and extract ECG signals from the PhysioNet ECG-Arrhythmia database.

**Usage:**
```bash
# List available arrhythmia codes
python scripts/explore_physionet.py --list

# Search for records (downloads headers, may take 1-2 min)
python scripts/explore_physionet.py --search AF --count 5

# Preview a specific record
python scripts/explore_physionet.py --preview JS00803 --all-leads

# Extract a record to the CSV files
python scripts/explore_physionet.py --extract JS00803 --label AF

# Then rebuild:
python scripts/preprocess_signals.py
```

### `browse_arrhythmias.py`

Visual exploration tool — downloads samples and plots them with matplotlib.

**Usage:**
```bash
# Browse specific arrhythmia types
python scripts/browse_arrhythmias.py AFIB LBBB 3AVB

# More samples, all leads
python scripts/browse_arrhythmias.py --count 5 --all-leads WPW
```

## Requirements

```bash
pip install numpy scipy wfdb matplotlib
```

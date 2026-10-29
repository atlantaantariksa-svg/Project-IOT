# SitSmart IoT

Contoh proyek IoT untuk belajar: **2 HP sebagai edge**, **accelerometer + gyroscope**, **2 proses pengolahan data**, **MQTT Cloud**, dan **dashboard di laptop**. Data berasal dari sensor fisik HP.

Ide: memantau **postur duduk** dan **durasi duduk**. Satu HP dipasang di badan (mengukur kemiringan), satu HP ditaruh di kursi (mengukur apakah kamu masih duduk). Dashboard memberi peringatan saat kamu membungkuk terlalu lama atau tidak bergerak terlalu lama.

## Cara kerja

```
HP 1 (badan)  --MQTT-->                     
HP 2 (kursi)  --MQTT-->  Broker MQTT Cloud  --telemetry-->  Laptop: detector.py + dashboard :8000
HP 1 (badan)  <--alert--                    <--status/alert--
```

| Node | Tugas | Pengolahan data |
|------|-------|-----------------|
| Edge 1 · HP badan | baca accelerometer + gyroscope | **Proses 1**: RMS gerakan, rata-rata vektor gravitasi, total rotasi (per 1 detik) |
| Edge 2 · HP kursi | baca accelerometer + gyroscope | **Proses 1**: sama, di HP kursi |
| Node 3 · Laptop | gabungkan data, putuskan status | **Proses 2**: sudut condong terhadap postur tegak + mesin keputusan |

### Status yang dihasilkan

| Status | Kondisi |
|--------|---------|
| `GOOD` | Duduk dengan postur baik |
| `SLOUCH` | Badan condong ≥ 20° selama ≥ 5 detik → HP badan bergetar |
| `BREAK` | Tidak bergerak ≥ 30 menit (ubah `SIT_LIMIT_S`) → ingatkan berdiri |
| `AWAY` | HP badan bergerak besar sementara HP kursi diam → kamu berdiri/pergi, timer istirahat jalan |
| `COMMON_MOTION` | Kedua HP bergerak besar bersamaan (meja digebrak, dll) → diabaikan |
| `CALIBRATE` / `OFFLINE` / `PAUSED` | Belum kalibrasi / data belum masuk / monitoring dijeda |

## 1. Laptop: jalankan detector & dashboard

Butuh Python 3.10+ dan internet.

```bash
cd sitsmart-iot
python -m pip install -r requirements.txt
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

Ganti `PROJECT_ID` di `.env` menjadi nama unik (misal `sitsmart_kelompok_07`). Broker publik HiveMQ tidak memakai akun, jadi **jangan kirim data rahasia**.

```bash
python detector.py
```

Buka <http://localhost:8000>.

## 2. Dua HP: browser (tanpa Termux)

1. Publish folder `web-edge/` ke hosting **HTTPS** (Netlify Drop atau GitHub Pages). Sensor gerak di browser hanya diizinkan lewat HTTPS.
2. Buka link tersebut di **dua HP**.
3. HP A: pilih **Edge 1 · HP badan**. HP B: pilih **Edge 2 · HP kursi**.
4. Isi `PROJECT_ID` sama persis dengan di laptop.
5. Tekan **Mulai membaca sensor** dan beri izin gerak (iPhone akan menanyakan).

Pasang HP badan di kantong dada atau ikat di punggung dengan posisi tetap. Taruh HP kursi rata di dudukan kursi.

## 3. Coba sistemnya

1. Duduk tegak, tekan **Kalibrasi** di dashboard (ini menyimpan postur tegakmu sebagai acuan).
2. Membungkuk ≥ 5 detik → status `SLOUCH`, HP badan bergetar dan menampilkan peringatan.
3. Berdiri dan jalan → `AWAY`. Kembali duduk → timer duduk mulai dari nol bila istirahat ≥ 60 detik.
4. Untuk demo cepat `BREAK`, set `SIT_LIMIT_S=60` di `.env`, lalu jalankan ulang detector.

Semua perangkat harus punya internet. Dashboard `localhost` hanya bisa dibuka di laptop.

### Debug tanpa HP

```bash
python scripts/simulate.py tegak      # atau: bungkuk | pergi
```

Simulator hanya untuk debugging, **bukan** edge fisik. Tekan Kalibrasi saat skenario `tegak` jalan, lalu ganti ke `bungkuk`.

## Pelajari kodenya

- `detector.py` → NODE 3 (laptop + dashboard localhost)
- `web-edge/index.html` → NODE 1 / NODE 2 lewat browser (Proses 1 dalam JavaScript)
- `shared/processing.py` → Proses 1 (RMS, vektor rata-rata) dan Proses 2 (`angle_between`, `PostureEngine`)
- `dashboard/index.html` → tampilan status, grafik sudut, riwayat alert
- `tests/test_processing.py` → unit test: `python -m unittest discover -s tests`

## Topik MQTT

| Topik | Arah | Isi |
|-------|------|-----|
| `sitsmart/<PROJECT_ID>/edge/<1\|2>/telemetry` | HP → broker | `{rms, rot, gravity:[x,y,z]}` tiap 1 detik |
| `sitsmart/<PROJECT_ID>/status` | detector → dashboard | status + metrik (retained) |
| `sitsmart/<PROJECT_ID>/alerts` | detector → dashboard & HP badan | `{type, risk, reason}` |
| `sitsmart/<PROJECT_ID>/control` | dashboard → detector | `{cmd: calibrate\|monitor_on\|monitor_off\|reset_timer}` |

> Proyek pembelajaran, bukan alat medis atau ergonomi profesional. Sensor dan izin tiap HP/browser bisa berbeda. Broker publik tidak punya autentikasi/ACL untuk topik kelompok.

## HP tidak terdeteksi?

- Cek angka **Gerakan (RMS)** dan **Pesan terkirim** di halaman edge. Kalau RMS selalu 0 atau muncul "tidak ada data gerak", browser tidak memberi izin sensor (pastikan HTTPS).
- Status `OFFLINE` berarti salah satu HP belum mengirim data dalam 5 detik terakhir, atau `PROJECT_ID` berbeda.
- Status `CALIBRATE` berarti belum ada acuan postur tegak: duduk tegak lalu tekan Kalibrasi.

"""Pengolahan data SitSmart.

Dua proses pengolahan data:
  1. Proses EDGE   -> window_rms() dan mean_vector(): dihitung di tiap HP (lihat
                      web-edge/index.html, versi JavaScript-nya sama persis).
  2. Proses DETECTOR -> angle_between() dan PostureEngine: menggabungkan data dua HP
                      menjadi status postur (GOOD / SLOUCH / BREAK / AWAY ...).

Modul ini murni Python (tanpa library tambahan) supaya mudah dipelajari dan dites.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence


# ---------------------------------------------------------------- Proses 1: fitur sinyal
def window_rms(magnitudes: Sequence[float]) -> float:
    """Getaran/gerakan = simpangan baku magnitudo akselerasi dalam satu jendela.

    Dikurangi rata-rata dulu supaya gravitasi (9.8 m/s^2) tidak ikut terhitung.
    HP diam -> ~0.0x, HP digerakkan -> > 0.3.
    """
    n = len(magnitudes)
    if n == 0:
        return 0.0
    mean = sum(magnitudes) / n
    return math.sqrt(sum((m - mean) ** 2 for m in magnitudes) / n)


def mean_vector(vectors: Sequence[Sequence[float]]) -> list[float]:
    """Rata-rata vektor [x, y, z] (dipakai untuk memperkirakan arah gravitasi)."""
    n = len(vectors)
    if n == 0:
        return [0.0, 0.0, 0.0]
    return [sum(v[i] for v in vectors) / n for i in range(3)]


# ---------------------------------------------------------------- Proses 2: geometri postur
def angle_between(a: Sequence[float], b: Sequence[float]) -> float:
    """Sudut (derajat) antara dua vektor gravitasi = seberapa miring HP dari posisi tegak."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    cos = sum(x * y for x, y in zip(a, b)) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


# ---------------------------------------------------------------- Mesin keputusan
@dataclass
class Params:
    lean_threshold_deg: float = 20.0  # condong >= ini dianggap membungkuk
    lean_hold_s: float = 5.0          # harus bertahan selama ini baru SLOUCH
    move_rms: float = 0.30            # RMS >= ini = "bergerak" (peregangan/ganti posisi)
    walk_rms: float = 1.20            # RMS >= ini = gerakan besar (berdiri/berjalan)
    still_rms: float = 0.15           # RMS <= ini = diam
    away_hold_s: float = 3.0          # gerak besar HP badan + kursi diam selama ini = AWAY
    sit_limit_s: float = 1800.0       # tanpa gerak sebanyak ini -> BREAK (30 menit)
    break_reset_s: float = 60.0       # pergi dari kursi selama ini = istirahat dianggap cukup
    stale_s: float = 5.0              # data lebih tua dari ini = edge dianggap offline


@dataclass
class Sample:
    t: float                          # waktu terima (detik, dari detector)
    rms: float
    gravity: list[float]
    rot: float = 0.0


@dataclass
class PostureEngine:
    params: Params = field(default_factory=Params)
    baseline: Optional[list[float]] = None
    monitoring: bool = True
    body: Optional[Sample] = None
    seat: Optional[Sample] = None
    _recent_gravity: list[list[float]] = field(default_factory=list)
    _slouch_since: Optional[float] = None
    _away_since: Optional[float] = None
    _last_move: Optional[float] = None
    _sit_start: Optional[float] = None

    # --- input
    def feed(self, edge: int, sample: Sample) -> None:
        if edge == 1:
            self.body = sample
            self._recent_gravity.append(sample.gravity)
            del self._recent_gravity[:-5]
            if self._last_move is None:
                self._last_move = sample.t
            if sample.rms >= self.params.move_rms:
                self._last_move = sample.t
        elif edge == 2:
            self.seat = sample

    def calibrate(self) -> bool:
        """Simpan postur tegak sebagai acuan (rata-rata 5 sampel gravitasi terakhir)."""
        if not self._recent_gravity:
            return False
        self.baseline = mean_vector(self._recent_gravity)
        self._slouch_since = None
        return True

    def reset_timer(self, now: float) -> None:
        self._last_move = now
        self._sit_start = now

    # --- keputusan
    def evaluate(self, now: float) -> dict:
        p = self.params
        body_ok = self.body is not None and now - self.body.t <= p.stale_s
        seat_ok = self.seat is not None and now - self.seat.t <= p.stale_s
        lean = None
        if body_ok and self.baseline is not None:
            lean = angle_between(self.body.gravity, self.baseline)
        still_s = (now - self._last_move) if self._last_move is not None else 0.0

        def out(state: str, reason: str, risk: float) -> dict:
            return {
                "state": state,
                "reason": reason,
                "risk": round(max(0.0, min(1.0, risk)), 2),
                "lean_deg": None if lean is None else round(lean, 1),
                "still_s": round(still_s, 1),
                "sit_s": round(now - self._sit_start, 1) if self._sit_start else 0.0,
                "monitoring": self.monitoring,
                "calibrated": self.baseline is not None,
            }

        if not self.monitoring:
            return out("PAUSED", "Monitoring dijeda", 0.0)
        if not body_ok or not seat_ok:
            missing = " & ".join(n for n, ok in (("HP Badan", body_ok), ("HP Kursi", seat_ok)) if not ok)
            return out("OFFLINE", f"Menunggu data dari {missing}", 0.0)
        if self.baseline is None:
            return out("CALIBRATE", "Duduk tegak lalu tekan Kalibrasi", 0.0)

        body, seat = self.body, self.seat
        # Gerakan besar di kedua HP sekaligus = guncangan bersama (misal meja digebrak)
        if body.rms >= p.walk_rms and seat.rms >= p.walk_rms:
            self._away_since = None
            return out("COMMON_MOTION", "Kedua HP bergerak bersamaan (diabaikan)", 0.0)

        # HP badan bergerak besar, kursi diam -> pengguna berdiri / pergi
        if body.rms >= p.walk_rms and seat.rms <= p.still_rms:
            if self._away_since is None:
                self._away_since = now
        else:
            if self._away_since is not None and now - self._away_since >= p.break_reset_s:
                self._sit_start = now
                self._last_move = now
            if self._away_since is not None and self._sit_start is None:
                self._sit_start = now
            self._away_since = None

        if self._away_since is not None and now - self._away_since >= p.away_hold_s:
            self._last_move = now
            if now - self._away_since >= p.break_reset_s:
                self._sit_start = None
            return out("AWAY", "Pengguna meninggalkan kursi (istirahat)", 0.0)

        if self._sit_start is None:
            self._sit_start = now

        if lean is not None and lean >= p.lean_threshold_deg:
            if self._slouch_since is None:
                self._slouch_since = now
            held = now - self._slouch_since
            if held >= p.lean_hold_s:
                return out("SLOUCH", f"Badan condong {lean:.0f} derajat selama {held:.0f} detik",
                           lean / (2 * p.lean_threshold_deg))
        else:
            self._slouch_since = None

        if still_s >= p.sit_limit_s:
            return out("BREAK", f"Tidak bergerak {still_s / 60:.0f} menit, saatnya berdiri",
                       still_s / (1.5 * p.sit_limit_s))

        return out("GOOD", "Postur baik", 0.0)

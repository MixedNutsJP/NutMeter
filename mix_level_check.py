"""Check level differences across a whole mixdown in one run (core of the GUI, also usable from the command line).

What it reports (in Japanese)
  1. Whole file: integrated loudness (LUFS, BS.1770), loudness range (LRA), sample / true peak, clipped samples,
     stereo correlation.
  2. Per section: loudness, difference from the loudest section, jump from the previous section, tonal balance.
  3. Per bar: bars whose loudness differs from the rest of their section by more than the threshold.
  4. Vocal lift per section, when an off-vocal file is given (mix loudness minus off-vocal loudness).
  5. Change per section against the previous version, when one is given.
  6. Note velocity per track and section, when a MIDI export is given.

usage
  python mix_level_check.py <audio> [--prev <audio>] [--offvocal <audio>] [--midi <mid>] [--tempo 120]
                            [--sections "イントロ 1-16, サビ1 17-32"] [--bar-threshold 3] [--report <out.txt>]
  Audio may be WAV, FLAC or MP3. Without --prev / --offvocal, they are looked up next to the file
  (*_vX.Y one version earlier, *Off-Vocal*).
  Without --sections, the song is cut into 8-bar blocks.
"""
import argparse
import collections
import glob
import os
import re
import sys
import unicodedata

import numpy as np
import soundfile as sf
from scipy.signal import lfilter, resample_poly

BANDS = [("超低域", 20, 120), ("低中域", 120, 500), ("中域", 500, 2000), ("高域", 2000, 6000), ("超高域", 6000, 20000)]
SILENT = -60.0                       # LUFS; quieter bars are left out of the bar check
STREAMING_TARGET = -14.0             # LUFS that streaming services normalise to
TRUE_PEAK_LIMIT = -1.0               # dBTP
CHORUS_SPREAD = 1.0                  # LU between choruses before it is flagged
VOCAL_SPREAD = 2.0                   # dB of vocal lift between sections before it is flagged
VELOCITY_SPREAD = 15                 # velocity between sections before a track is flagged
VERSION_CHANGE = 1.0                 # dB a section may move against the comparison file before it is flagged
RED_ON, RED_OFF = "\x01", "\x02"      # analyze() wraps the values behind a warning in these; see plain()
WARN_MARK = "  ← 要確認"


# ---------- loudness (ITU-R BS.1770-4) ----------

def k_weight(x, sr):
    """K-weighting: high shelf followed by the RLB high-pass, coefficients for any sample rate."""
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = np.tan(np.pi * f0 / sr)
    vh = 10 ** (g / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    b1 = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    a1 = [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / sr)
    a0 = 1 + k / q + k * k
    b2, a2 = [1, -2, 1], [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return lfilter(b2, a2, lfilter(b1, a1, x, axis=0), axis=0)


def block_power(kw, sr, length, hop):
    """Mean square summed over channels for blocks of `length` seconds every `hop` seconds."""
    n, h = int(length * sr), int(hop * sr)
    if len(kw) < n:
        return np.array([np.mean(kw ** 2, axis=0).sum()]) if len(kw) else np.array([])
    cs = np.vstack([np.zeros((1, kw.shape[1])), np.cumsum(kw ** 2, axis=0)])
    starts = np.arange(0, len(kw) - n + 1, h)
    return ((cs[starts + n] - cs[starts]) / n).sum(axis=1)


def lufs(p):
    return -0.691 + 10 * np.log10(np.maximum(p, 1e-20))


def integrated(kw, sr):
    p = block_power(kw, sr, 0.4, 0.1)
    p = p[lufs(p) > -70]
    if not len(p):
        return -np.inf
    p = p[lufs(p) > lufs(p.mean()) - 10]
    return float(lufs(p.mean()))


def loudness_range(kw, sr):
    p = block_power(kw, sr, 3.0, 1.0)
    p = p[lufs(p) > -70]
    if len(p) < 2:
        return 0.0
    p = p[lufs(p) > lufs(p.mean()) - 20]
    s = lufs(p)
    return float(np.percentile(s, 95) - np.percentile(s, 10))


def band_shares(x, sr):
    """Share of the energy per band in dB (0 = all energy in that band)."""
    n = 1 << 15
    acc = np.zeros(n // 2 + 1)
    for i in range(0, max(1, len(x) - n), n // 2):
        seg = x[i:i + n]
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)))
        acc += np.abs(np.fft.rfft(seg * np.hanning(n))) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    tot = acc.sum() + 1e-20
    return [10 * np.log10(acc[(f >= lo) & (f < hi)].sum() / tot + 1e-12) for _, lo, hi in BANDS]


# ---------- helpers ----------

def load(path):
    y, sr = sf.read(path, always_2d=True)
    return y.astype(np.float64), sr


def span(sr, bar_s, a, b, n):
    return int((a - 1) * bar_s * sr), min(n, int(b * bar_s * sr))


AUDIO_EXT = (".wav", ".flac", ".mp3")                 # what the GUI offers; soundfile reads all three
VERSION_RE = re.compile(r"_v(\d+)\.(\d+)$", re.I)


def audio_files(folder):
    return [os.path.join(folder, f) for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in AUDIO_EXT]


def version_of(path):
    m = VERSION_RE.search(os.path.splitext(os.path.basename(path))[0])
    return (int(m.group(1)), int(m.group(2))) if m else None


def prefer(paths, ext):
    """Same extension as the target first, then wav, flac, mp3."""
    order = [ext] + [e for e in AUDIO_EXT if e != ext]
    return sorted(paths, key=lambda p: order.index(os.path.splitext(p)[1].lower()))


def find_previous(mix):
    """The *_vX.Y file one version before `mix` in the same folder (same format preferred), or None."""
    folder, ext = os.path.dirname(os.path.abspath(mix)), os.path.splitext(mix)[1].lower()
    me = os.path.normcase(os.path.abspath(mix))
    mine = version_of(mix)
    cands = [(version_of(p), p) for p in audio_files(folder)
             if version_of(p) and os.path.normcase(os.path.abspath(p)) != me and (mine is None or version_of(p) < mine)]
    if not cands:
        return None
    best = max(v for v, _ in cands)
    return prefer([p for v, p in cands if v == best], ext)[0]


def find_offvocal(mix):
    folder, ext = os.path.dirname(os.path.abspath(mix)), os.path.splitext(mix)[1].lower()
    me = os.path.normcase(os.path.abspath(mix))
    hits = [p for p in audio_files(folder) if os.path.normcase(os.path.abspath(p)) != me
            and re.search(r"off[-_ ]?vocal|オフボ|インスト|inst", os.path.basename(p), re.I)]
    return prefer(hits, ext)[0] if hits else None


def midi_tempo(path):
    """First tempo in a MIDI file (BPM), or None."""
    import mido
    for tr in mido.MidiFile(path).tracks:
        for m in tr:
            if m.type == "set_tempo":
                return round(mido.tempo2bpm(m.tempo), 2)
    return None


SECTION_LINE = re.compile(r"^(.*?)[\s,:：、　]+(\d+)\s*[-–―〜~～ー]\s*(\d+)\s*$")


def parse_sections(text):
    """'name start-end' per line (or comma separated). Returns [(name, start, end)], raises ValueError."""
    out = []
    for raw in re.split(r"[\n\r]+|,(?=[^,]*\d+\s*[-–―〜~～ー]\s*\d+)", text or ""):
        line = raw.strip()
        if not line:
            continue
        m = SECTION_LINE.match(line)
        if not m or not m.group(1).strip():
            raise ValueError(f"セクションの書き方が読み取れません: 「{line}」\n例: サビ1 34-49")
        a, b = int(m.group(2)), int(m.group(3))
        if a < 1 or b < a:
            raise ValueError(f"小節の範囲が正しくありません: 「{line}」")
        out.append((m.group(1).strip(), a, b))
    return out


def auto_sections(n_bars, size=8):
    return [(f"{a}-{min(a + size - 1, n_bars)}小節", a, min(a + size - 1, n_bars)) for a in range(1, n_bars + 1, size)]


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in s)


def pad(s, n, right=False):
    gap = " " * max(0, n - width(s))
    return gap + s if right else s + gap


def num(v, n=7, d=1, sign=False):
    if v is None or not np.isfinite(v):
        return pad("-", n, True)
    return f"{v:+{n}.{d}f}" if sign else f"{v:{n}.{d}f}"


def red(s):
    return f"{RED_ON}{s}{RED_OFF}"


def plain(text):
    """Report without the red markers; lines that had red parts get WARN_MARK at the end."""
    out = []
    for line in text.split("\n"):
        if RED_ON in line:
            line = line.replace(RED_ON, "").replace(RED_OFF, "") + (WARN_MARK if not line.lstrip().startswith(RED_ON + "・") else "")
        out.append(line)
    return "\n".join(out)


def is_chorus(name):
    return name.lower().startswith("chorus") or "サビ" in name


def section_loudness(y, sr, bar_s, sections):
    kw = k_weight(y, sr)
    res = []
    for name, a, b in sections:
        s, e = span(sr, bar_s, a, b, len(y))
        if e - s < sr:
            continue
        res.append((name, a, b, integrated(kw[s:e], sr)))
    return res, kw


# ---------- report ----------

def analyze(mix, prev=None, offvocal=None, midi=None, tempo=120.0, sections=None, bar_threshold=3.0, progress=None):
    """Return the report as text. Values behind a warning are wrapped in RED_ON / RED_OFF (use plain() for files).
    `progress(message)` is called between steps."""
    step = progress or (lambda m: None)
    lines, warns = [], []
    say = lines.append
    bar_s = 240 / float(tempo)

    step("オーディオファイルを読み込んでいます")
    y, sr = load(mix)
    mono = y.mean(axis=1)
    length_bars = len(y) / sr / bar_s
    n_bars = int(np.ceil(length_bars - 0.05))           # a few ms past a bar line is not another bar
    secs = sections or auto_sections(n_bars)
    w = max(width("セクション"), max(width(n) for n, *_ in secs)) + 2

    # 1. whole file
    step("全体のラウドネスを計算しています")
    peak = 20 * np.log10(np.abs(y).max() + 1e-12)
    tp = 20 * np.log10(np.abs(resample_poly(y, 4, 1, axis=0)).max() + 1e-12)
    clip = int(np.sum(np.abs(y) >= 0.999))
    sec, kw = section_loudness(y, sr, bar_s, secs)
    il, lra = integrated(kw, sr), loudness_range(kw, sr)
    corr = float(np.corrcoef(y[:, 0], y[:, 1])[0, 1]) if y.shape[1] == 2 else 1.0
    say("[1] ファイル全体")
    say(f"  ラウドネス          {il:6.1f} LUFS" + (f"   (配信サービスでは約 {il - STREAMING_TARGET:.1f} dB 下げて再生されます)"
                                                   if il > STREAMING_TARGET else ""))
    say(f"  ラウドネスレンジ    {lra:6.1f} LU     (曲の中の音量の幅。小さいほど強弱が少ない)")
    say(f"  サンプルピーク      {peak:6.2f} dBFS")
    hot = lambda s, bad: red(s) if bad else s
    say("  トゥルーピーク      " + hot(f"{tp:6.2f} dBTP", tp > TRUE_PEAK_LIMIT))
    say("  クリップしたサンプル " + hot(f"{clip:5d}", clip > 0))
    say("  左右の相関          " + hot(f"{corr:6.2f}", corr < 0.3) + "    (1 に近いほどモノラルに近い)")
    if tp > TRUE_PEAK_LIMIT:
        warns.append(f"トゥルーピークが {tp:.2f} dBTP で、{TRUE_PEAK_LIMIT:g} dBTP を超えています。MP3 などにすると音が割れることがあります。")
    if clip:
        warns.append(f"0 dBFS に張り付いたサンプルが {clip} 個あります(クリップ)。")
    if corr < 0.3:
        warns.append(f"左右の相関が {corr:.2f} と低く、モノラル再生で音が痩せる可能性があります。")

    # 2. sections
    step("セクションごとに計算しています")
    loudest = max((v for *_, v in sec if np.isfinite(v)), default=0.0)
    say("")
    say("[2] セクションごと(LUFS。「最大との差」は一番大きいセクションとの差、「段差」は前のセクションからの変化)")
    say("  帯域の割合は dB(0 に近いほど、その帯域が音量を占めている)")
    say("  " + pad("セクション", w) + pad("小節", 9) + pad("LUFS", 8, True) + pad("最大との差", 12, True) + pad("段差", 8, True)
        + "  | " + "".join(pad(n, 8, True) for n, *_ in BANDS))
    ch = [(n, v) for n, *_, v in sec if is_chorus(n) and np.isfinite(v)]
    spread = max(v for _, v in ch) - min(v for _, v in ch) if len(ch) > 1 else 0.0
    ch_odd = {min(ch, key=lambda c: c[1])[0], max(ch, key=lambda c: c[1])[0]} if spread > CHORUS_SPREAD else set()
    last = None
    for name, a0, b0, v in sec:
        s, e = span(sr, bar_s, a0, b0, len(y))
        stp = v - last if last is not None and np.isfinite(v) and np.isfinite(last) else None
        lv = red(num(v, 8)) if name in ch_odd else num(v, 8)
        say("  " + pad(name, w) + pad(f"{a0}-{b0}", 9) + lv + num(v - loudest, 12, sign=True) + num(stp, 8, sign=True)
            + "  | " + "".join(f"{x:8.1f}" for x in band_shares(mono[s:e], sr)))
        last = v
    if len(ch) > 1:
        say("  サビ同士の差: " + (red(f"{spread:.1f} LU") if spread > CHORUS_SPREAD else f"{spread:.1f} LU"))
        if spread > CHORUS_SPREAD:
            warns.append(f"サビ同士のラウドネスが {spread:.1f} LU 違います({CHORUS_SPREAD:g} LU 以内が目安)。")

    # 3. bars
    step("小節ごとに計算しています")
    bar_l = lufs(block_power(kw, sr, bar_s, bar_s))
    say("")
    say(f"[3] セクションの中で {bar_threshold:g} dB 以上浮いている・沈んでいる小節(かっこ内はセクションの中央値)")
    odd_all, lines_at = [], {}
    for name, a0, b0, _ in sec:
        idx = [b for b in range(a0, b0 + 1) if b - 1 < len(bar_l) and bar_l[b - 1] > SILENT]
        if len(idx) < 3:
            continue
        med = float(np.median([bar_l[b - 1] for b in idx]))
        odd = [(b, bar_l[b - 1] - med) for b in idx if abs(bar_l[b - 1] - med) > bar_threshold]
        if odd:
            odd_all += [b for b, _ in odd]
            say("  " + pad(name, w) + f"[{med:6.1f}]  "
                + "  ".join(red(f"{b}小節: {d:+.1f}") if b < n_bars else f"{b}小節: {d:+.1f}" for b, d in odd))
    if not odd_all:
        say("  なし")
    else:
        tail = [b for b in odd_all if b >= n_bars]          # the last bar holds only the decay
        rest = [b for b in odd_all if b < n_bars]
        if rest:
            warns.append(f"セクションの中で音量が浮いている・沈んでいる小節があります: {', '.join(map(str, rest))} 小節"
                         "(ブレイクなど意図したものなら問題ありません)。")
        if tail:
            say("  ※最後の小節は余韻だけなので、小さく出るのが普通です。")
    say("  小節ごとのラウドネス(LUFS、1行 8 小節):")
    for i in range(0, len(bar_l), 8):
        cells = []
        for j, v in enumerate(bar_l[i:i + 8]):
            c = f"{v:6.1f}" if v > SILENT else "   ---"
            cells.append(red(c) if i + j + 1 in odd_all and i + j + 1 < n_bars else c)
        say(f"    {i + 1:3d}: " + " ".join(cells))

    # 4. vocal lift
    say("")
    say("[4] ボーカルの押し出し具合(ミックスの LUFS − オフボーカルの LUFS。大きいほどボーカルが前に出ている)")
    if offvocal:
        step("オフボーカルを計算しています")
        yo, so = load(offvocal)
        if so != sr:
            say(f"  飛ばしました: オフボーカルが {so} Hz で、対象({sr} Hz)と違います。")
        else:
            off_sec, kwo = section_loudness(yo, so, bar_s, secs)
            ov = {n: v for n, *_, v in off_sec}
            say(f"  オフボーカル全体: {integrated(kwo, so):.1f} LUFS")
            say("  " + pad("セクション", w) + pad("小節", 9) + pad("ミックス", 10, True) + pad("オフボーカル", 14, True) + pad("押し出し", 10, True))
            rows = [(name, a0, b0, v, v - ov[name]) for name, a0, b0, v in sec if name in ov]
            sung = [d for *_, d in rows if d > 0.5]
            wide = len(sung) > 1 and max(sung) - min(sung) > VOCAL_SPREAD
            for name, a0, b0, v, d in rows:
                lift = num(d, 10, sign=True)
                say("  " + pad(name, w) + pad(f"{a0}-{b0}", 9) + num(v, 10) + num(ov[name], 14)
                    + (red(lift) if wide and d in (min(sung), max(sung)) else lift))
            if len(sung) > 1:
                rng = f"{min(sung):+.1f} 〜 {max(sung):+.1f} dB"
                say("  ボーカルのあるセクションでの幅: " + (red(rng) if wide else rng))
                if wide:
                    warns.append(f"ボーカルの押し出し具合がセクションによって {max(sung) - min(sung):.1f} dB 違います"
                                 "(伴奏が薄いセクションで大きくなるのは自然です。サビ同士などで揃っているかを見てください)。")
    else:
        say("  飛ばしました: オフボーカルのファイルが指定されていません。")

    # 5. previous version
    say("")
    say("[5] 比較用オーディオファイルとの比較")
    if prev:
        step("比較用オーディオファイルを計算しています")
        yp, sp = load(prev)
        if sp != sr:
            say(f"  飛ばしました: 比較用オーディオファイルが {sp} Hz で、対象({sr} Hz)と違います。")
        else:
            sp_sec, kwp = section_loudness(yp, sp, bar_s, secs)
            pv = {n: v for n, *_, v in sp_sec}
            say(f"  {os.path.basename(prev)} → {os.path.basename(mix)}")
            il_p = integrated(kwp, sp)
            whole = il - il_p
            line = f"{il_p:.1f} → {il:.1f} LUFS({whole:+.1f})"
            say("  全体: " + (red(line) if abs(whole) >= VERSION_CHANGE else line))
            if abs(whole) >= VERSION_CHANGE:
                warns.append(f"全体のラウドネスが比較用から {whole:+.1f} dB 変わっています(意図した変更なら問題ありません)。")
            # a section is flagged when it moved differently from the whole file, so a plain gain change flags nothing
            say("  " + pad("セクション", w) + pad("小節", 9) + pad("変化", 10, True) + pad("全体との差", 12, True))
            moved = []
            for name, a0, b0, v in sec:
                if name in pv:
                    d = v - pv[name]
                    rel = num(d - whole, 12, sign=True)
                    if abs(d - whole) >= VERSION_CHANGE:
                        moved.append(f"{name}({d - whole:+.1f})")
                        rel = red(rel)
                    say("  " + pad(name, w) + pad(f"{a0}-{b0}", 9) + num(d, 10, sign=True) + rel)
            if moved:
                warns.append(f"全体の変化と比べて {VERSION_CHANGE:g} dB 以上違う動きをしたセクションがあります: " + "、".join(moved)
                             + "(意図した変更なら問題ありません)。")
    else:
        say("  飛ばしました: 比較用オーディオファイルが指定されていません。")

    # 6. MIDI velocities
    if midi:
        step("MIDI を読み込んでいます")
        midi_report(midi, secs, say, warns)

    head = [f"■ 対象: {os.path.basename(mix)}  ({len(y) / sr:.1f} 秒 / {len(y) / sr / bar_s:.1f} 小節 / {sr} Hz / {y.shape[1]} ch / {tempo:g} BPM)"]
    for label, p in (("比較用", prev), ("オフボーカル", offvocal), ("MIDI", midi)):
        head.append(f"  {label}: {os.path.basename(p) if p else '(なし)'}")
    if not sections:
        head.append("  セクション: 指定がないため 8 小節ごとに区切っています")
    head.append("")
    head.append(f"■ 要確認({len(warns)} 件)")
    head += [f"  {red('・' + x)}" for x in warns] or ["  特になし"]
    head.append("")
    step("完了")
    return "\n".join(head + lines) + "\n"


def midi_report(path, sections, say, warns):
    import mido
    mid = mido.MidiFile(path)
    tick_bar = mid.ticks_per_beat * 4
    say("")
    say(f"[6] トラック × セクションごとのベロシティ(中央値。- は音符なし。差 = セクション間の最大差)")
    names = [n for n, *_ in sections]
    tw = max(12, max((width(t.name) for t in mid.tracks), default=0) + 2)
    say("  " + pad("トラック", tw) + "".join(pad(n[:8], 10, True) for n in names) + pad("差", 6, True))
    flagged = []
    for tr in mid.tracks:
        t, per = 0, collections.defaultdict(list)
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                b = t // tick_bar + 1
                name = next((n for n, x, z in sections if x <= b <= z), None)
                if name:
                    per[name].append(m.velocity)
        if not per:
            continue
        med = {n: float(np.median(v)) for n, v in per.items()}
        spread = max(med.values()) - min(med.values())
        cell = f"{spread:6.0f}"
        say("  " + pad(tr.name or "(名前なし)", tw) + "".join(f"{med[n]:10.0f}" if n in med else pad("-", 10, True) for n in names)
            + (red(cell) if spread >= VELOCITY_SPREAD else cell))
        if spread >= VELOCITY_SPREAD:
            flagged.append(f"{tr.name}({spread:.0f})")
    if flagged:
        warns.append("ベロシティがセクションによって大きく違うトラックがあります: " + "、".join(flagged) + "(意図したものなら問題ありません)。")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mix")
    ap.add_argument("--prev")
    ap.add_argument("--offvocal")
    ap.add_argument("--midi")
    ap.add_argument("--tempo", type=float)
    ap.add_argument("--sections", default="")
    ap.add_argument("--bar-threshold", type=float, default=3.0)
    ap.add_argument("--report")
    a = ap.parse_args()
    tempo = a.tempo or (midi_tempo(a.midi) if a.midi else None) or 120.0
    text = analyze(a.mix, prev=a.prev or find_previous(a.mix), offvocal=a.offvocal or find_offvocal(a.mix), midi=a.midi,
                   tempo=tempo, sections=parse_sections(a.sections), bar_threshold=a.bar_threshold)
    sys.stdout.reconfigure(encoding="utf-8")
    text = plain(text)
    print(text, end="")
    if a.report:
        with open(a.report, "w", encoding="utf-8-sig") as f:
            f.write(text)
        print(f"\nレポートを保存しました: {a.report}")


if __name__ == "__main__":
    main()

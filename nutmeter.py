"""NutMeter - Tk front end for mix_level_check: pick the files, set tempo and sections, read the report, save it.
Settings are remembered in %APPDATA%\\NutMeter\\settings.json.
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import mix_level_check as core

APP = "NutMeter"
VERSION = "1.0.0"
SETTINGS = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NutMeter", "settings.json")
SECTION_HINT = "1行に1セクション「名前 開始小節-終了小節」。空欄なら 8 小節ごとに区切ります。"
SECTION_EXAMPLE = "イントロ 1-16\nAメロ 17-32\nサビ1 33-48\n間奏 49-56\nサビ2 57-72\nアウトロ 73-80"


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP} {VERSION} - ミックス音量チェック")
        self.geometry("1100x780")
        self.minsize(820, 560)
        try:                                                    # bundled next to the exe, or assets/ when run from source
            here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            ico = next(p for p in (os.path.join(here, "nutmeter.ico"), os.path.join(here, "assets", "nutmeter.ico")) if os.path.exists(p))
            self.iconbitmap(ico)
        except Exception:
            pass
        self.q = queue.Queue()
        self.report = ""
        s = load_settings()

        self.mix = tk.StringVar(value=s.get("mix", ""))
        self.prev = tk.StringVar(value=s.get("prev", ""))
        self.off = tk.StringVar(value=s.get("off", ""))
        self.midi = tk.StringVar(value=s.get("midi", ""))
        self.tempo = tk.StringVar(value=str(s.get("tempo", "120")))
        self.threshold = tk.StringVar(value=str(s.get("threshold", "3")))
        self.status = tk.StringVar(value="オーディオファイルを選んで「チェックする」を押してください。")

        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        r = 0
        for label, var, kind, note in (("オーディオファイル(必須)", self.mix, "audio", "チェックしたい曲(WAV / FLAC / MP3)"),
                                       ("比較用オーディオファイル", self.prev, "audio", "比べたい曲。前のバージョンなど(なくても可)"),
                                       ("オフボーカル", self.off, "audio", "ボーカルなしの曲(なくても可)"),
                                       ("MIDI", self.midi, "mid", "DAW から書き出した MIDI(なくても可)")):
            ttk.Label(root, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(root, textvariable=var).grid(row=r, column=1, sticky="ew", pady=2)
            ttk.Button(root, text="選ぶ…", command=lambda v=var, k=kind: self.pick(v, k)).grid(row=r, column=2, padx=4)
            ttk.Button(root, text="クリア", command=lambda v=var: v.set("")).grid(row=r, column=3)
            ttk.Label(root, text=note, foreground="#666").grid(row=r, column=4, sticky="w", padx=8)
            r += 1

        opts = ttk.Frame(root)
        opts.grid(row=r, column=0, columnspan=5, sticky="w", pady=(8, 2))
        ttk.Label(opts, text="テンポ (BPM)").pack(side="left")
        ttk.Entry(opts, textvariable=self.tempo, width=8).pack(side="left", padx=(4, 4))
        ttk.Label(opts, text="※MIDI を選ぶと自動で入ります", foreground="#666").pack(side="left", padx=(0, 20))
        ttk.Label(opts, text="小節チェックのしきい値 (dB)").pack(side="left")
        ttk.Entry(opts, textvariable=self.threshold, width=5).pack(side="left", padx=4)
        r += 1

        sec = ttk.LabelFrame(root, text="セクション", padding=6)
        sec.grid(row=r, column=0, columnspan=5, sticky="ew", pady=6)
        sec.columnconfigure(0, weight=1)
        ttk.Label(sec, text=SECTION_HINT, foreground="#666").grid(row=0, column=0, sticky="w")
        ttk.Button(sec, text="サンプル", command=self.example).grid(row=0, column=1, padx=4)
        self.sections = tk.Text(sec, height=5, font=("Meiryo UI", 10), undo=True)
        self.sections.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.sections.insert("1.0", s.get("sections", ""))
        r += 1

        bar = ttk.Frame(root)
        bar.grid(row=r, column=0, columnspan=5, sticky="ew", pady=(2, 6))
        self.run_btn = ttk.Button(bar, text="チェックする", command=self.run)
        self.run_btn.pack(side="left")
        self.save_btn = ttk.Button(bar, text="結果を保存…", command=self.save, state="disabled")
        self.save_btn.pack(side="left", padx=8)
        self.copy_btn = ttk.Button(bar, text="結果をコピー", command=self.copy, state="disabled")
        self.copy_btn.pack(side="left")
        self.bar = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.bar.pack(side="right")
        r += 1

        root.rowconfigure(r, weight=1)
        self.out = ScrolledText(root, font=("MS Gothic", 10), wrap="none", state="disabled")
        self.out.grid(row=r, column=0, columnspan=5, sticky="nsew")
        xs = ttk.Scrollbar(root, orient="horizontal", command=self.out.xview)
        xs.grid(row=r + 1, column=0, columnspan=5, sticky="ew")
        self.out.configure(xscrollcommand=xs.set)
        self.out.tag_configure("warn", foreground="#b00020")
        self.out.tag_configure("head", font=("MS Gothic", 10, "bold"))
        ttk.Label(root, textvariable=self.status, anchor="w").grid(row=r + 2, column=0, columnspan=5, sticky="ew", pady=(6, 0))

        self.mix.trace_add("write", lambda *_: self.mix_changed())
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ---------- inputs ----------

    def pick(self, var, kind):
        types = [("オーディオファイル (WAV / FLAC / MP3)", "*.wav *.flac *.mp3")] if kind == "audio" else [("MIDI ファイル", "*.mid *.midi")]
        start = os.path.dirname(var.get() or self.mix.get()) or None
        path = filedialog.askopenfilename(parent=self, filetypes=types + [("すべてのファイル", "*.*")], initialdir=start)
        if not path:
            return
        var.set(os.path.normpath(path))
        if var is self.midi:
            try:
                bpm = core.midi_tempo(path)
                if bpm:
                    self.tempo.set(f"{bpm:g}")
                    self.status.set(f"MIDI からテンポ {bpm:g} BPM を読み取りました。")
            except Exception as ex:
                messagebox.showwarning(APP, f"MIDI を読み込めませんでした。\n{ex}", parent=self)

    def mix_changed(self):
        """Fill in the previous version and the off-vocal from the mix's folder."""
        mix = self.mix.get().strip().strip('"')
        if not os.path.isfile(mix):
            return
        found = []
        prev = core.find_previous(mix)
        if prev and os.path.normcase(prev) != os.path.normcase(mix):
            self.prev.set(os.path.normpath(prev))
            found.append("比較用オーディオファイル")
        else:
            self.prev.set("")
        off = core.find_offvocal(mix)
        if off and os.path.normcase(off) != os.path.normcase(mix):
            self.off.set(os.path.normpath(off))
            found.append("オフボーカル")
        else:
            self.off.set("")
        if found:
            self.status.set("同じフォルダから" + "と".join(found) + "を自動で選びました。違う場合は選び直してください。")

    def example(self):
        if self.sections.get("1.0", "end").strip() and not messagebox.askyesno(APP, "今のセクションをサンプルで置き換えますか?", parent=self):
            return
        self.sections.delete("1.0", "end")
        self.sections.insert("1.0", SECTION_EXAMPLE)

    def collect(self):
        clean = lambda v: v.get().strip().strip('"')
        mix = clean(self.mix)
        if not mix:
            raise ValueError("オーディオファイルを選んでください。")
        for label, p in (("オーディオファイル", mix), ("比較用オーディオファイル", clean(self.prev)), ("オフボーカル", clean(self.off)), ("MIDI", clean(self.midi))):
            if p and not os.path.isfile(p):
                raise ValueError(f"{label}のファイルが見つかりません。\n{p}")
        try:
            tempo = float(self.tempo.get())
            assert 20 <= tempo <= 400
        except Exception:
            raise ValueError("テンポは 20〜400 の数字で入れてください。")
        try:
            th = float(self.threshold.get())
            assert th > 0
        except Exception:
            raise ValueError("しきい値は 0 より大きい数字で入れてください(普通は 3)。")
        sections = core.parse_sections(self.sections.get("1.0", "end"))
        return dict(mix=mix, prev=clean(self.prev) or None, offvocal=clean(self.off) or None, midi=clean(self.midi) or None,
                    tempo=tempo, sections=sections, bar_threshold=th)

    # ---------- run ----------

    def run(self):
        try:
            args = self.collect()
        except ValueError as ex:
            messagebox.showerror(APP, str(ex), parent=self)
            return
        self.remember()
        self.run_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self.copy_btn.configure(state="disabled")
        self.bar.start(12)
        self.show("")

        def work():
            try:
                text = core.analyze(progress=lambda m: self.q.put(("status", m)), **args)
                self.q.put(("done", text))
            except Exception as ex:
                self.q.put(("error", f"{type(ex).__name__}: {ex}", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self.poll)

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "status":
                    self.status.set(item[1] + "…")
                elif item[0] == "done":
                    self.finish()
                    self.report = item[1]
                    self.show(item[1])
                    self.save_btn.configure(state="normal")
                    self.copy_btn.configure(state="normal")
                    self.status.set("チェックが終わりました。赤字の「要確認」から見てください。")
                    return
                elif item[0] == "error":
                    self.finish()
                    self.status.set("エラーで止まりました。")
                    self.show("エラーが起きました。ファイルが WAV / FLAC / MP3 として読めるか、開いたままの別アプリがないか確認してください。\n\n" + item[2])
                    messagebox.showerror(APP, "チェックできませんでした。\n" + item[1], parent=self)
                    return
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def finish(self):
        self.bar.stop()
        self.run_btn.configure(state="normal")

    def show(self, text):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        in_warn = False
        for line in text.splitlines(True):
            if line.startswith("■ 要確認"):
                in_warn = True
                self.out.insert("end", line, "head")
                continue
            if line.startswith("■") or line.startswith("["):
                in_warn = False
                self.out.insert("end", line, "head")
                continue
            if in_warn and line.strip() == "":
                in_warn = False
            self.out.insert("end", line, "warn" if in_warn and line.strip().startswith("・") else ())
        self.out.configure(state="disabled")

    # ---------- output ----------

    def save(self):
        base = os.path.splitext(os.path.basename(self.mix.get()))[0] or "audio"
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", initialfile=f"{base}_音量チェック.txt",
                                            initialdir=os.path.dirname(self.mix.get()) or None, filetypes=[("テキスト", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(self.report)
            self.status.set(f"保存しました: {path}")
        except Exception as ex:
            messagebox.showerror(APP, f"保存できませんでした。\n{ex}", parent=self)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.report)
        self.status.set("結果をクリップボードにコピーしました。")

    def remember(self):
        save_settings(dict(mix=self.mix.get(), prev=self.prev.get(), off=self.off.get(), midi=self.midi.get(),
                           tempo=self.tempo.get(), threshold=self.threshold.get(), sections=self.sections.get("1.0", "end").strip()))

    def close(self):
        self.remember()
        self.destroy()


def selftest(mix, out):
    """Run without the window (used to check the packaged exe): python nutmeter.py --selftest <audio> <out.txt>"""
    try:
        text = core.analyze(mix, prev=core.find_previous(mix), offvocal=core.find_offvocal(mix), tempo=120.0)
    except Exception:
        text = "ERROR\n" + traceback.format_exc()
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write(text)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2], sys.argv[3])
    else:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # sharp text on high-DPI screens
        except Exception:
            pass
        App().mainloop()

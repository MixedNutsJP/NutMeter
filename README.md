# NutMeter

ミックスダウンの音量差を、1回の実行でまとめて確認するツールです。MixedNuts 製。

曲全体のラウドネスとピーク、セクションごと・小節ごとの音量差、ボーカルの押し出し具合、前の版からの変化を、日本語のレポートとして出力します。

- **NutMeter.exe(GUI 版)**: ファイルを選んでボタンを押すだけで使えます。Python は不要です。
- **mix_level_check.py(コマンド版)**: 同じ解析をコマンドラインから実行できます。

## 配布用 ZIP

[Releases](https://github.com/MixedNutsJP/NutMeter/releases) から `NutMeter_v<バージョン>.zip` をダウンロードして渡してください。中身は次のとおりです。

```
NutMeter/
  NutMeter.exe                  ← ダブルクリックで起動
  はじめにお読みください.txt     ← 利用者向けの説明(起動方法・使い方・結果の見方)
  _internal/                    ← exe が使う部品(Python と各ライブラリ)
```

利用者向けの説明は `はじめにお読みください.txt` にまとめてあります。渡すときに伝えるのは、次の2点だけで足ります。

- ZIP は「すべて展開」してから使うこと(ZIP を開いただけでは動きません)
- 初回に「Windows によって PC が保護されました」と出たら、「詳細情報」→「実行」を押すこと(署名のない exe のため)

## 画面の使い方

| 項目 | 内容 |
|---|---|
| オーディオファイル(必須) | 調べる曲(WAV / FLAC / MP3)。選ぶと、同じフォルダから比較用とオフボーカルを自動で探す |
| 比較用オーディオファイル | 比べたい曲(任意)。前のバージョンなど |
| オフボーカル | ボーカルなしの曲(任意)。[4] の計算に使う |
| MIDI | DAW から書き出した MIDI(任意)。選ぶとテンポが自動で入り、[6] が出る |
| テンポ (BPM) | 小節の位置の計算に使う。曲と合っていないと小節がずれる |
| 小節チェックのしきい値 | [3] で「浮いている」とみなす差(dB)。普通は 3 |
| セクション | 1行に1つ「名前 開始小節-終了小節」。空欄なら 8 小節ごとに区切る。「サンプル」で書き方の例が入る |

結果は「結果を保存…」でテキストに保存、「結果をコピー」でクリップボードにコピーできます。入力内容は `%APPDATA%\NutMeter\settings.json` に保存され、次回の起動時に復元されます。

### 自動で選ばれるファイル

| ファイル | 名前の決まり |
|---|---|
| 比較用 | `<曲名>_v<メジャー>.<マイナー>`(例: `_v2.2.wav`)。選んだファイルの一つ前の版 |
| オフボーカル | 名前に `Off-Vocal`、`オフボ`、`インスト`、`inst` のどれかを含むファイル |

対象は `.wav`、`.flac`、`.mp3` です。同じ版が複数の形式であるときは、選んだファイルと同じ形式を優先し、次に WAV、FLAC、MP3 の順で選びます。

MP3 も読めますが、エンコードでピークなどが変わります(v2.2 では、サンプルピークが WAV の −3.00 dBFS から −2.01 dBFS に上がりました。ラウドネスは同じでした)。正確に測るときは WAV か FLAC を使ってください。

## コマンド版の使い方

```
python -X utf8 mix_level_check.py <オーディオファイル> [オプション]
```

| オプション | 内容 | 省略時 |
|---|---|---|
| `--prev` | 比較用オーディオファイル | 同じフォルダの一つ前の `_vX.Y` |
| `--offvocal` | オフボーカル | 同じフォルダのオフボーカルらしい名前のファイル |
| `--midi` | MIDI。ベロシティを集計し、テンポも読み取る | 集計しない |
| `--tempo` | テンポ(BPM) | MIDI のテンポ、なければ 120 |
| `--sections` | セクション。`"イントロ 1-16, サビ1 17-32"` のようにカンマ区切り | 8 小節ごと |
| `--bar-threshold` | [3] のしきい値(dB) | 3 |
| `--report` | レポートをテキストファイルにも保存する | 画面に出すだけ |

必要なパッケージ: `numpy`、`scipy`、`soundfile`、`mido`(`pip install numpy scipy soundfile mido`)

## レポートの読み方

先頭の「要確認」に、目安を外れた項目が並びます。GUI では赤字で表示されます。

| 項目 | 内容 | 「要確認」に出る条件 |
|---|---|---|
| [1] ファイル全体 | ラウドネス(LUFS)、ラウドネスレンジ(LRA)、サンプルピーク、トゥルーピーク、クリップ数、左右の相関 | トゥルーピーク > −1 dBTP、クリップあり、相関 < 0.3 |
| [2] セクションごと | ラウドネス、最大のセクションとの差、前のセクションからの段差、帯域ごとのエネルギーの割合 | 名前に「サビ」(または `chorus`)を含むセクション同士の差 > 1 LU |
| [3] 小節ごと | セクションの中央値からしきい値以上ずれた小節と、全小節のラウドネスの一覧 | 該当する小節がある(最後の小節は余韻なので除く) |
| [4] ボーカルの押し出し具合 | 対象の LUFS − オフボーカルの LUFS(セクションごと) | ボーカルのあるセクション間の差 > 2 dB |
| [5] 比較用オーディオファイルとの比較 | 全体とセクションごとの増減 | なし |
| [6] ベロシティ | トラック × セクションごとのベロシティの中央値 | セクション間の差が 15 以上のトラック |

補足:

- ラウドネスが −14 LUFS より大きい場合は、配信サービスで再生時に下げられる量も表示します。
- [3] には、ブレイクなどわざと音を減らした小節も出ます。意図したものかどうかを確認する用途です。
- [4] は、ミックスとオフボーカルを同じマスター設定で書き出している前提です。ミックスからオフボーカルを引き算する方法は使っていません。マスターにリミッターなどがあると、2つのファイルでかかり方が変わり、引き算の結果がボーカルにならないためです。
- [6] で分かるのは打ち込みの強弱だけです。フェーダーやエフェクトの設定は MIDI に含まれません。

しきい値は `mix_level_check.py` 冒頭の定数(`TRUE_PEAK_LIMIT`、`CHORUS_SPREAD`、`VOCAL_SPREAD`、`VELOCITY_SPREAD` など)で変えられます。

## 計算方法

- ラウドネスは ITU-R BS.1770-4 に沿って計算しています(K 特性フィルター、400 ms ブロック、−70 LUFS の絶対ゲートと −10 LU の相対ゲート)。997 Hz・0 dBFS の正弦波を片チャンネルに入れると、規格どおり −3.01 LUFS になることを確認しています。
- LRA は EBU Tech 3342 の方法(3 秒ブロック、−20 LU の相対ゲート、10〜95 パーセンタイルの幅)です。
- トゥルーピークは 4 倍にオーバーサンプリングして求めています。
- 自前の実装で、認証を受けた測定器ではありません。納品用の数値は、マスタリング用のメーターでも確認してください。
- 比べるファイルは、サンプルレートが同じである必要があります。違う場合、その項目は飛ばします。
- 小節の位置は、曲の頭から一定のテンポで計算します。途中でテンポが変わる曲には対応していません。

## ファイル構成

| ファイル | 内容 |
|---|---|
| `mix_level_check.py` | 解析本体とコマンド版 |
| `nutmeter.py` | GUI(Tk) |
| `assets/nutmeter.ico` | アイコン(`assets/make_icon.py` で生成) |
| `assets/はじめにお読みください.txt` | ZIP に同梱する利用者向けの説明 |
| `release/` | ビルドした配布用 ZIP の置き場(Git の管理外。ZIP は Releases に添付する) |

## ビルド手順

Python 3.12 と PyInstaller で exe を作ります。不要なライブラリが入らないよう、専用の仮想環境でビルドします。

```
python -m venv buildenv
buildenv\Scripts\pip install numpy scipy soundfile mido pyinstaller
buildenv\Scripts\pyinstaller --noconfirm --clean --windowed --onedir --name NutMeter ^
  --icon assets\nutmeter.ico --add-data "assets\nutmeter.ico;." ^
  --exclude-module matplotlib --exclude-module PIL --exclude-module pandas --exclude-module IPython ^
  nutmeter.py
```

できた `dist\NutMeter\` に `assets\はじめにお読みください.txt` をコピーし、`NutMeter` フォルダごと ZIP にします。ZIP は、パスの区切りが `/` になるツール(Python の `zipfile` など)で作ってください。Windows PowerShell 5.1 の `Compress-Archive` などは `\` で書き込むことがあり、解凍ツールによっては正しく展開できません。 作った ZIP は `gh release create v<バージョン> <ZIP>` で Releases に添付します。

exe が動くかは、画面を出さずに解析だけ実行するモードで確認できます。

```
dist\NutMeter\NutMeter.exe --selftest <オーディオファイル> <結果.txt>
```

バージョンを上げるときは、`nutmeter.py` の `VERSION`、`はじめにお読みください.txt` の1行目、ZIP のファイル名を揃えて変えてください。

## ライセンス / License

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。
See [LICENSE](LICENSE) for details.

再配布・改変は自由ですが、**著作権表示(`Copyright (c) 2026 MixedNuts`)とライセンス文を必ず残してください。**
MIT ライセンスの条件です。この表示が、元の作者が MixedNuts であることのクレジットになります。

You are free to redistribute and modify this, but **the copyright notice
(`Copyright (c) 2026 MixedNuts`) and the license text must be retained** — that is a
condition of the MIT License, and it credits MixedNuts as the original author.

#!/usr/bin/env python3
"""Build the README demo assets: the neutral demo deck, its slide PNGs, and the demo GIF/screenshots.

Needs python-pptx and Pillow (not project dependencies):  python3 -m pip install python-pptx Pillow

  deck     write examples/demo-deck.pptx and docs/demo/slide-N.png (needs the Nanum Gothic font files, SIL OFL 1.1)
  times    print the --snapshot-at list to use (one snapshot just after each change of the bar, few enough not to stall the app)
  compose  put real subtitle-bar snapshots from the app over the slide PNGs -> docs/demo.gif and docs/demo/demo-*.png

Capture the snapshots first with the real app on a 2560x1080 screen (the deck uses the same size), with no microphone
and no network:

  PYTHONPATH=src python3 -m livecast_hud --config examples/demo-talk.json --mode demo --exit-on-end \
      --log /tmp/demo.jsonl --snapshot-dir /tmp/snaps --snapshot-at "$(python3 scripts/make_demo_assets.py times)"   # run it 2-3 times

compose reads the window position and the moments the bar changed from that log, and pastes the app's own pixels at the
window's real position.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 2560, 1080                       # the capture screen; the slides use the same size so the bar sits where it really does
EMU_PER_PX = 9525                       # 96 dpi
FONT_FAMILY = "Nanum Gothic"

BG, INK, MUTED, ACCENT, PILL = "#F4F1EA", "#1F2937", "#6B7280", "#0F766E", "#E5E1D6"
STEPS = ["듣기", "맞추기", "보여주기"]

# Timing of run_demo_mode in src/livecast_hud/cli.py: first sentence after 1.0 s, 0.35 s per word, then a 3.2 s read hold
DEMO_START, DEMO_WORD, DEMO_HOLD = 1.0, 0.35, 3.2


def slides():
    """Each slide is a list of items: ("rect", x, y, w, h, fill), ("text", x, y, string, size_px, bold, color, centered_in_w)"""
    tag = ("text", 160, 90, "데모용 예시 덱 · 가상의 내용", 36, False, MUTED, None)

    def steps(current):
        items = []
        for i, name in enumerate(STEPS):
            x = 160 + i * 400
            on = i == current
            items.append(("rect", x, 170, 360, 84, ACCENT if on else PILL))
            items.append(("text", x, 190, f"{i + 1}  {name}", 44, on, "#FFFFFF" if on else MUTED, 360))
        return items

    def step_slide(i, bullets):
        items = [("rect", 0, 0, W, H, BG), tag, *steps(i), ("text", 160, 330, f"{i + 1}  {STEPS[i]}", 140, True, INK, None)]
        for n, line in enumerate(bullets):
            items.append(("text", 160, 580 + n * 120, f"•  {line}", 64, False, INK, None))
        return items

    return [
        [("rect", 0, 0, W, H, BG), tag,
         ("rect", 160, 250, 24, 380, ACCENT),
         ("text", 240, 250, "LiveCast HUD는", 140, True, INK, None),
         ("text", 240, 420, "이렇게 작동합니다", 140, True, INK, None),
         ("text", 240, 700, "한국어 발표에 실시간 영어 자막을", 64, False, ACCENT, None)],
        step_slide(0, ["발표 음성을 한국어 문장으로 인식합니다", "마이크 또는 오디오 파일을 입력으로 씁니다"]),
        step_slide(1, ["준비한 대본의 영어를 그대로 보여 줍니다", "용어집으로 이름과 용어를 고정합니다"]),
        step_slide(2, ["전체 화면 슬라이드 위에 얇은 자막 바를 띄웁니다", "클릭은 그대로 통과해서 발표 조작을 방해하지 않습니다"]),
    ]


def load_fonts(font_dir):
    d = Path(font_dir)
    paths = {False: d / "NanumGothic-Regular.ttf", True: d / "NanumGothic-Bold.ttf"}
    for p in paths.values():
        if not p.is_file():
            sys.exit(f"missing font file {p} (Nanum Gothic, SIL OFL 1.1: https://github.com/google/fonts/tree/main/ofl/nanumgothic)")
    return lambda size, bold: ImageFont.truetype(str(paths[bold]), size)


def render_png(items, font):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for it in items:
        if it[0] == "rect":
            _, x, y, w, h, fill = it
            d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2 if h < 100 else 0, fill=fill)
        else:
            _, x, y, s, size, bold, color, width = it
            f = font(size, bold)
            if width:
                x += (width - d.textlength(s, font=f)) / 2
            d.text((x, y), s, font=f, fill=color)
    return img


def build_pptx(items_per_slide, out):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Pt

    def rgb(h):
        return RGBColor.from_string(h.lstrip("#"))

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(W * EMU_PER_PX), Emu(H * EMU_PER_PX)
    for items in items_per_slide:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = rgb(BG)
        for it in items:
            if it[0] == "rect":
                _, x, y, w, h, fill = it
                if w >= W:
                    continue                                        # full-slide background is the slide background above
                shape = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if h < 100 else MSO_SHAPE.RECTANGLE,
                                           Emu(x * EMU_PER_PX), Emu(y * EMU_PER_PX), Emu(w * EMU_PER_PX), Emu(h * EMU_PER_PX))
                if h < 100:
                    shape.adjustments[0] = 0.5
                shape.fill.solid()
                shape.fill.fore_color.rgb = rgb(fill)
                shape.line.fill.background()
            else:
                _, x, y, text, size, bold, color, width = it
                box = s.shapes.add_textbox(Emu(x * EMU_PER_PX), Emu(y * EMU_PER_PX), Emu((width or 2000) * EMU_PER_PX), Emu(int(size * 1.4) * EMU_PER_PX))
                tf = box.text_frame
                tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
                tf.word_wrap = False
                p = tf.paragraphs[0]
                if width:
                    p.alignment = PP_ALIGN.CENTER
                r = p.add_run()
                r.text = text
                r.font.size, r.font.bold, r.font.name = Pt(size * 0.75), bold, FONT_FAMILY
                r.font.color.rgb = rgb(color)
                rpr = r._r.get_or_add_rPr()
                ea = rpr.makeelement(qn("a:ea"), {"typeface": FONT_FAMILY})
                rpr.append(ea)
    cp = prs.core_properties                                        # replace the template's leftover author and dates
    cp.title, cp.author, cp.last_modified_by = "LiveCast HUD demo deck (made-up content)", "LiveCast HUD", "LiveCast HUD"
    cp.comments, cp.subject, cp.keywords = "", "", ""
    cp.created = cp.modified = datetime.datetime(2026, 1, 1)
    prs.save(out)


def cmd_deck(args):
    font = load_fonts(args.font_dir)
    items_per_slide = slides()
    png_dir = ROOT / "docs" / "demo"
    png_dir.mkdir(parents=True, exist_ok=True)
    for n, items in enumerate(items_per_slide, 1):
        render_png(items, font).save(png_dir / f"slide-{n}.png", optimize=True)
    build_pptx(items_per_slide, ROOT / "examples" / "demo-deck.pptx")
    print(f"wrote {len(items_per_slide)} slides")


def demo_schedule():
    """Nominal (start, partial times, final) of each demo sentence, from the demo script and the pacing in run_demo_mode"""
    data = json.loads((ROOT / "examples" / "demo-talk.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in data["sentences"]}
    t, out = DEMO_START, []
    for sid in data["talks"]["1"]["demo"]:
        n = len(by_id[sid]["ko"].split())
        out.append(([t + k * DEMO_WORD for k in range(n)], t + n * DEMO_WORD))
        t += n * DEMO_WORD + DEMO_HOLD
    return out, t


def cmd_times(args):
    schedule, end = demo_schedule()
    offsets = [float(o) for o in args.offsets.split(",")]
    times = [0.6] + [round(e + o, 2) for partials, final in schedule for e in [*partials, final] for o in offsets] + [round(end - 0.9, 2)]
    print(",".join(f"{t:g}" for t in times))


def read_run(log_path, snap_dir):
    """One captured run: the window rectangle, when each bar state appeared, and the snapshots with their real times.

    Qt's snapshot timers are coarse (a few percent off), so the real time of a snapshot comes from the app's log, not its file name."""
    rows = [json.loads(line) for line in Path(log_path).read_text(encoding="utf-8").splitlines()]
    start = next((r for r in rows if r.get("ev") == "start"), None)
    if start is None:
        sys.exit(f"no start event in {log_path}")
    shown = [r for r in rows if r.get("ev") == "shown"]
    snaps = [(r["t"] - start["t"], Path(snap_dir) / Path(r["path"]).name) for r in rows if r.get("ev") == "snapshot"]
    return {"rect": tuple(start["window"]["geometry"]),
            "starts": [r["t"] - start["t"] for r in shown],                     # when each bar state appeared
            "new_sentence": [r["kind"] == "partial" and " " not in r["ko"] for r in shown],
            "end": next((r["t"] - start["t"] for r in rows if r.get("ev") == "end"), None),
            "snaps": snaps}


def cmd_compose(args):
    runs = [read_run(log, snaps) for snaps, log in args.run]
    ref = runs[0]
    n_states = len(ref["starts"])
    if any(len(r["starts"]) != n_states or r["rect"] != ref["rect"] for r in runs):
        sys.exit("the runs differ in the number of bar states or in the window rectangle")
    x, y, w, h = ref["rect"]

    # bar picture for each state (state -1 = the empty bar before the first word): any run's snapshot taken while it was on screen
    def pick(i):
        for r in runs:
            lo = r["starts"][i] if i >= 0 else 0.0
            hi = r["starts"][i + 1] if i + 1 < n_states else (r["end"] or lo + 3.0)
            inside = [(abs(t - (lo + hi) / 2), p) for t, p in r["snaps"] if lo + 0.02 <= t < hi - 0.02]
            if inside:
                return min(inside)[1]
        return None

    bars = {i: pick(i) for i in range(-1, n_states)}
    missing = [i for i, p in bars.items() if p is None]
    if missing:
        sys.exit(f"no snapshot caught bar state(s) {missing}: capture another run and pass it with one more --run")

    slide_png = [Image.open(ROOT / "docs" / "demo" / f"slide-{n}.png").convert("RGBA") for n in range(1, sum(ref["new_sentence"]) + 1)]
    sentence_of, k = [], 0
    for new in ref["new_sentence"]:
        k += new
        sentence_of.append(k - 1)

    def frame(i):
        img = slide_png[sentence_of[i] if i >= 0 else 0].copy()
        bar = Image.open(bars[i]).convert("RGBA")
        if bar.size != (w, h):
            sys.exit(f"{bars[i].name} is {bar.size}, the window is {(w, h)}")
        img.alpha_composite(bar, (x, y))                            # the app's own pixels, at the window's real position
        return img.convert("RGB")

    t0 = args.gif_from
    marks = [t0] + ref["starts"]
    ends = ref["starts"] + [min(ref["end"], t0 + args.gif_seconds) if ref["end"] else t0 + args.gif_seconds]
    frames = []                                                     # [image, seconds shown]
    for i, (a, b) in enumerate(zip(marks, ends), start=-1):
        b = min(b, t0 + args.gif_seconds)
        if b <= a:
            continue
        im = frame(i)
        if frames and frames[-1][0].tobytes() == im.tobytes():
            frames[-1][1] += b - a
        else:
            frames.append([im, b - a])

    gw = args.gif_width
    small = [f.resize((gw, round(H * gw / W)), Image.LANCZOS).quantize(colors=128, method=Image.Quantize.MEDIANCUT,
                                                                        dither=Image.Dither.NONE) for f, _ in frames]
    small[0].save(args.gif, save_all=True, append_images=small[1:], duration=[max(20, round(d * 1000)) for _, d in frames],
                  loop=0, optimize=True, disposal=1)
    total = sum(d for _, d in frames)
    print(f"gif: {len(frames)} frames, {total:.1f} s, {Path(args.gif).stat().st_size / 1e6:.2f} MB, {gw}x{round(H * gw / W)}")

    last_of = {}
    for i in range(n_states):
        last_of[sentence_of[i]] = i                                 # the last state of a sentence = its final line
    for n, k in enumerate(args.shot_sentences, 1):
        frame(last_of[k - 1]).save(ROOT / "docs" / "demo" / f"demo-{n}.png", optimize=True)
        print(f"screenshot demo-{n}.png: sentence {k} complete")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deck")
    d.add_argument("--font-dir", required=True, help="folder with NanumGothic-Regular.ttf and NanumGothic-Bold.ttf")
    d.set_defaults(fn=cmd_deck)
    c = sub.add_parser("compose")
    c.add_argument("--run", nargs=2, action="append", required=True, metavar=("SNAPSHOT_DIR", "LOG"),
                   help="a captured run: its snapshot folder and its --log file (repeat for more runs; the first sets the timing)")
    c.add_argument("--gif", default=str(ROOT / "docs" / "demo.gif"))
    c.add_argument("--gif-width", type=int, default=1920)
    c.add_argument("--gif-from", type=float, default=0.6, help="seconds after launch where the GIF starts")
    c.add_argument("--gif-seconds", type=float, default=19.8, help="GIF length")
    c.add_argument("--shot-sentences", type=int, nargs="+", default=[2, 4], help="demo sentences (1-based) for the full-size screenshots")
    c.set_defaults(fn=cmd_compose)
    t = sub.add_parser("times")
    t.add_argument("--offsets", default="0.08,0.24", help="seconds after each change of the bar at which to take a snapshot")
    t.set_defaults(fn=cmd_times)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

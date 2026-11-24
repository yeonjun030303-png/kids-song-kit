"""에피소드 제작 키트 생성기 (무료, 외부 음악 AI 없음)
가사(한 줄 8음절) -> 따라 부르기 쉬운 오리지널 멜로디+반주 -> mp3, 자막(SRT), 컷 구성표(CSV),
Flow 프롬프트, 업로드 정보를 한 번에 만듭니다.
사용: python build_kit.py ep01.json --out kit_ep01
"""
import argparse, csv, json, pathlib, re, subprocess, wave
import numpy as np

SR = 44100
SCALE = [0, 2, 4, 7, 9]  # 도 레 미 솔 라
PROG = [48, 53, 55, 48]   # 반주 코드 루트: C F G C
HANGUL = re.compile(r"[가-힣]")
SYL_PER_LINE = 8


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def midi_of(i):
    return 72 + 12 * (i // 5) + SCALE[i % 5]


# ---------- 소리 합성 ----------
def bell(f, d, v):
    t = np.arange(int(SR * d)) / SR
    env = np.exp(-t * 4.0) * (1 - np.exp(-t * 300))
    s = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 12)
         + 0.12 * np.sin(2 * np.pi * 5.4 * f * t) * np.exp(-t * 20))
    return v * s * env


def bass(f, d, v):
    t = np.arange(int(SR * d)) / SR
    env = np.exp(-t * 2.5) * (1 - np.exp(-t * 200))
    return v * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * env


def pad(f, d, v):
    t = np.arange(int(SR * d)) / SR
    env = np.minimum(t / 0.08, 1) * np.exp(-t * 0.8) * np.minimum((d - t) / 0.15, 1)
    return v * np.sin(2 * np.pi * f * t) * env


def kick(v=0.5):
    t = np.arange(int(SR * 0.22)) / SR
    ph = 2 * np.pi * (55 * t + (110 / 28) * (1 - np.exp(-28 * t)))
    return v * np.sin(ph) * np.exp(-t * 14)


def hat(rng, v=0.1):
    t = np.arange(int(SR * 0.05)) / SR
    return v * rng.standard_normal(len(t)) * np.exp(-t * 70)


def clap(rng, v=0.2):
    t = np.arange(int(SR * 0.14)) / SR
    return v * rng.standard_normal(len(t)) * np.exp(-t * 28)


# ---------- 가사 -> 멜로디 ----------
def syllables(line):
    return HANGUL.findall(line)


def line_melody(rng, sy, high, cadence):
    lo, hi = (2, 6) if high else (0, 4)
    idx = int(rng.integers(lo, lo + 3))
    out = []
    for k, ch in enumerate(sy):
        if k > 0:
            if ch == sy[k - 1]:
                pass  # 같은 글자는 같은 음 (콩콩콩, 짝짝짝)
            elif k >= 2 and ch == sy[k - 2] and (k < 3 or sy[k - 1] == sy[k - 3]):
                idx = out[k - 2]  # 안녕 안녕 처럼 반복되는 말은 같은 멜로디
            else:
                step = int(rng.choice([-1, 0, 1, 1, -1, 2, -2]))
                step = abs(step) if (k < len(sy) // 2 and rng.random() < 0.6) else step
                step = -abs(step) if (k >= len(sy) // 2 and rng.random() < 0.6) else step
                idx = int(np.clip(idx + step, lo, hi))
        out.append(idx)
    out[-1] = {"open": 3, "end": 5 if high else 0}[cadence]
    return out


def build(ep, out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    bpm, seed = ep["bpm"], ep["seed"]
    beat = 60.0 / bpm
    bar = beat * 4
    rng = np.random.default_rng(seed)

    for name, sec in ep["sections"].items():
        for ln in sec["lines"]:
            n = len(syllables(ln))
            if n != SYL_PER_LINE:
                raise ValueError(f"[{name}] '{ln}' 은 {n}음절 (필요: {SYL_PER_LINE})")

    melodies = {}
    for name, sec in ep["sections"].items():
        high = name.startswith("C")
        sub = np.random.default_rng(seed + sum(map(ord, name)))
        melodies[name] = [line_melody(sub, syllables(ln), high, "end" if j == 3 else "open")
                          for j, ln in enumerate(sec["lines"])]

    intro, outro = 2, 2
    structure = ep["structure"]
    total_bars = intro + 8 * len(structure) + outro
    buf = np.zeros(int((total_bars * bar + 3) * SR))

    def put(sig, t):
        s = int(t * SR)
        buf[s:s + len(sig)] += sig[:max(0, len(buf) - s)]

    is_chorus = [False] * intro
    for name in structure:
        is_chorus += [name.startswith("C")] * 8
    is_chorus += [False] * outro
    triads = {48: (60, 64, 67), 53: (65, 69, 72), 55: (67, 71, 74)}
    for b in range(total_bars):
        t0 = b * bar
        root = PROG[b % 4]
        put(bass(hz(root), beat * 1.8, 0.35), t0)
        put(bass(hz(root + 7), beat * 1.8, 0.26), t0 + 2 * beat)
        for m in triads[root]:
            put(pad(hz(m), bar, 0.05), t0)
        for k in range(4):
            if k % 2 == 0:
                put(kick(), t0 + k * beat)
            elif is_chorus[b]:
                put(clap(rng), t0 + k * beat)
            put(hat(rng), t0 + k * beat + beat / 2)

    timeline = []
    for si, name in enumerate(structure):
        sec = ep["sections"][name]
        for j, ln in enumerate(sec["lines"]):
            start = (intro + si * 8 + j * 2) * bar
            sy_times = []
            for k, idx in enumerate(melodies[name][j]):
                t = start + k * beat
                put(bell(hz(midi_of(idx)), beat * 1.5, 0.5), t)
                sy_times.append(round(t, 3))
            timeline.append({"section": name, "line": j, "text": ln, "start": round(start, 3),
                             "end": round(start + 8 * beat, 3), "syllable_times": sy_times})
    end_t = (total_bars - outro) * bar
    for m, v in [(72, 0.5), (76, 0.35), (79, 0.3)]:
        put(bell(hz(m), 2.5, v), end_t)
    buf = buf[: int((total_bars * bar + 1.0) * SR)]
    buf = buf / (np.max(np.abs(buf)) + 1e-9) * 0.7

    wav = out / "song.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((buf * 32767).astype(np.int16).tobytes())
    subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-b:a", "192k", str(out / "song.mp3")],
                   capture_output=True, check=True)
    wav.unlink()
    duration = round(len(buf) / SR, 2)
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    return timeline, beat, duration


# ---------- 산출물 ----------
def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(timeline, out):
    lines = []
    for i, e in enumerate(timeline, 1):
        lines.append(f"{i}\n{srt_time(e['start'])} --> {srt_time(e['end'] - 0.15)}\n{e['text']}\n")
    (out / "lyrics.srt").write_text("\n".join(lines), encoding="utf-8")


def mmss(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


def write_cutsheet(ep, timeline, beat, out):
    rows, n = [], 1
    for e in timeline:
        name = e["section"]
        sec = ep["sections"][name]
        chorus = name.startswith("C")
        cut_beats = 2 if chorus else 4
        ncut = 8 // cut_beats
        clip = f"{name}-{e['line'] + 1}"
        for c in range(ncut):
            s = e["start"] + c * cut_beats * beat
            off = c * cut_beats * beat
            fx = sec["fx"][e["line"]] if c == 0 else ("2번째 컷은 130% 줌인" if not chorus else "하드컷, 컷마다 110% 줌인/원본 번갈아")
            rows.append([n, mmss(s), mmss(s + cut_beats * beat), name, e["text"], round(cut_beats * beat, 2),
                         clip, f"{off:.2f}~{off + cut_beats * beat:.2f}", fx])
            n += 1
    with open(out / "cutsheet.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["컷", "시작", "끝", "구간", "가사", "컷길이(초)", "사용 클립", "클립 안 구간(초)", "편집 효과"])
        w.writerows(rows)
    return len(rows)


def write_flow_prompts(ep, char, beat, out):
    chars = " ".join(f"{c['name']} is {c['look']}." for c in char["characters"])
    seen, md = set(), ["# Flow 클립 프롬프트\n",
                      f"- 클립 1개는 **8초**로 생성하고, 그 중 앞 {8 * beat:.1f}초만 씁니다 (크레딧은 4/6/8초가 같으면 8초가 이득. Flow 설정에서 확인).",
                      "- 먼저 Whisk/ImageFX로 캐릭터 시트 이미지를 만들고, Flow에서 그 이미지를 시작 프레임으로 넣어 영상화하세요 (캐릭터 고정).",
                      f"\n## 0. 캐릭터 시트 (이미지 생성용, 영어)\n\n```\nCharacter sheet, both characters side by side, full body, plain white background. {chars} {char['style']}\n```\n"]
    order = []
    for name in ep["structure"]:
        if name not in seen:
            seen.add(name)
            order.append(name)
    for name in order:
        sec = ep["sections"][name]
        md.append(f"\n## {name} 구간\n")
        for j, (ln, sc) in enumerate(zip(sec["lines"], sec["scenes"])):
            cid = f"{name}-{j + 1}"
            md.append(f"### {cid}  ·  가사: {ln}\n\n```\n{char['style']}. {chars} {sc}. "
                      f"Smooth bouncy character animation, expressive happy faces, steady camera, no text, no subtitles. 8 seconds.\n```\n")
    (out / "flow_prompts.md").write_text("\n".join(md), encoding="utf-8")
    return sum(len(ep["sections"][n]["lines"]) for n in order)


def write_upload_info(ep, out):
    uniq = []
    for name in ep["structure"]:
        if name not in uniq:
            uniq.append(name)
    lyrics = [f"[{n}]\n" + "\n".join(ep["sections"][n]["lines"]) for n in uniq]
    title = ep.get("yt_title") or ep["title"]
    intro = ep.get("desc_intro", "몽이와 콩이와 함께 따라 불러요!")
    desc = (intro + "\n\n" + "\n\n".join(lyrics) +
            "\n\n※ 이 영상은 AI 도구의 도움을 받아 제작되었습니다.\n#동요 #율동동요 #아기동요 #키즈")
    tags = ",".join(ep.get("tags") or ["동요", "율동동요", "아기동요", "유아동요", "키즈", "어린이노래", "따라부르기", "몽이와콩이"])
    thumb = ("Thumbnail, bright pastel meadow, close-up of a cream-colored baby bunny and a light-brown bear cub waving at the camera "
             "with huge happy eyes and big open smiles, soft 3D cartoon, centered faces, empty space at top for Korean title text, no text")
    (out / "upload_info.md").write_text(
        f"# 업로드 정보\n\n## 제목\n{title}\n\n## 설명\n{desc}\n\n## 태그\n{tags}\n\n"
        f"## 썸네일 프롬프트\n```\n{thumb}\n```\n\n"
        "## 업로드 설정 체크\n- 아동용 콘텐츠: **예**\n- AI 생성/변형 콘텐츠 표시: 스튜디오 안내에 맞게 체크\n"
        "- 예약 게시: 오전 9시(KST)\n", encoding="utf-8")


def write_plan(clips, cuts, duration, out):
    per_day = 5
    days = -(-clips // per_day)
    (out / "작업순서.md").write_text(f"""# 에피소드 작업 순서

완성 길이 약 {duration/60:.1f}분 / 컷 {cuts}개 / 필요한 Flow 클립 {clips}개 (후렴 클립은 3번 재사용)

## 0일차 (15분)
1. Whisk 또는 ImageFX에서 flow_prompts.md의 [캐릭터 시트] 프롬프트로 몽이·콩이 이미지 생성 → 마음에 드는 1장 고정

## 1~{days}일차 (하루 20분, 무료 크레딧 하루 5클립 기준)
- Flow에서 캐릭터 이미지를 시작 프레임으로 넣고 flow_prompts.md 순서대로 클립 생성: C-1~C-4(후렴)부터, 그다음 V1, V2, V3
- 하루 크레딧이 끝나면 멈추고 다음 날 이어서 (크레딧은 이월 안 됨)

## 조립 (CapCut, 1시간)
1. song.mp3를 타임라인에 올림
2. lyrics.srt 가져오기 → 자막 스타일: 하단 중앙, 흰 글씨 + 굵은 색 외곽선
3. cutsheet.csv 순서대로 클립 배치 (컷 길이는 표에 있음, 박자 단위라 음악과 맞음)
4. 편집 효과 열대로 줌인/바운스/페이드 적용
5. 내보내기 1080p → 유튜브 스튜디오에서 예약 업로드 (upload_info.md 참고)

※ 크레딧 수량·가격·클립 길이는 바뀔 수 있으니 Flow 설정에서 확인하세요.
""", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--out", default="kit")
    a = ap.parse_args()
    here = pathlib.Path(__file__).parent
    ep = json.loads(pathlib.Path(a.episode).read_text(encoding="utf-8"))
    char = json.loads((here / "character.json").read_text(encoding="utf-8"))
    out = pathlib.Path(a.out)
    timeline, beat, duration = build(ep, out)
    write_srt(timeline, out)
    cuts = write_cutsheet(ep, timeline, beat, out)
    clips = write_flow_prompts(ep, char, beat, out)
    write_upload_info(ep, out)
    write_plan(clips, cuts, duration, out)
    print(json.dumps({"duration_sec": duration, "cuts": cuts, "flow_clips": clips}, ensure_ascii=False))

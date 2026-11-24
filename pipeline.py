"""주 2회 실행: 에피소드 생성 -> 노래(ACE-Step, 실패 시 반주곡) -> 가사 시간 맞추기 -> 키트 zip -> Gmail 발송"""
import csv, datetime as dt, difflib, json, os, pathlib, re, shutil, smtplib, ssl, subprocess, sys, time, traceback
from email.message import EmailMessage
import requests

import build_kit as bk

HERE = pathlib.Path(__file__).parent
OUT = HERE / "out"
HIST = HERE / "history.json"
KST = dt.timezone(dt.timedelta(hours=9))
TEST = os.environ.get("TEST", "").lower() in ("1", "true", "yes")
ACE_DIR = pathlib.Path(os.environ.get("ACE_DIR", HERE.parent / "ACE-Step-1.5"))
BASE = "http://127.0.0.1:8001"
HANGUL = re.compile(r"[가-힣]")
NOTES = []


def log(*a):
    print(dt.datetime.now(KST).strftime("[%H:%M:%S]"), *a, flush=True)


# ---------- 1. 노래: ACE-Step ----------
def ace_song(ep, dest):
    bpm = ep["bpm"]
    sec_line = 8 * 60 / bpm
    n_lines = 4 * len(ep["structure"])
    duration = int(n_lines * sec_line + 12)
    lyr = []
    for name in ep["structure"]:
        lyr.append(("[Chorus]" if name.startswith("C") else "[Verse]") + "\n" + "\n".join(ep["sections"][name]["lines"]))
    prompt = ("Korean children's nursery song, cheerful sweet kids vocals, bright xylophone and ukulele, bouncy simple drums, "
              "catchy sing-along chorus, major key, 4/4, very simple melody, narrow vocal range, joyful")
    env = dict(os.environ, ACESTEP_DEVICE=os.environ.get("ACESTEP_DEVICE", "cpu"), ACESTEP_OFFLOAD_TO_CPU="true",
               ACESTEP_CONFIG_PATH="acestep-v15-turbo")
    logf = open(OUT / "ace_server.log", "w")
    srv = subprocess.Popen(["uv", "run", "acestep-api"], cwd=ACE_DIR, env=env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while time.time() - t0 < 1800:  # 모델 내려받기 포함 최대 30분 대기
            try:
                if requests.get(f"{BASE}/health", timeout=5).status_code == 200:
                    break
            except Exception:
                pass
            if srv.poll() is not None:
                raise RuntimeError("ACE-Step 서버가 종료됨")
            time.sleep(10)
        else:
            raise RuntimeError("ACE-Step 서버 시작 시간 초과")
        log("ACE-Step 준비 완료", int(time.time() - t0), "초")
        payload = {"prompt": prompt, "lyrics": "\n\n".join(lyr), "audio_duration": duration, "bpm": bpm,
                   "key_scale": "C Major", "time_signature": "4", "vocal_language": "ko", "batch_size": 1,
                   "inference_steps": 8, "thinking": False, "use_format": False, "use_random_seed": False,
                   "seed": int(ep.get("seed", 7)), "audio_format": "mp3"}
        tid = requests.post(f"{BASE}/release_task", json=payload, timeout=60).json()["data"]["task_id"]
        t1 = time.time()
        limit = int(os.environ.get("ACE_TIMEOUT", "5400"))
        while True:
            item = requests.post(f"{BASE}/query_result", json={"task_id_list": [tid]}, timeout=60).json()["data"][0]
            if item["status"] in (1, 2):
                break
            if time.time() - t1 > limit:
                raise RuntimeError("노래 생성 시간 초과")
            time.sleep(15)
        if item["status"] == 2:
            raise RuntimeError("ACE-Step 생성 실패")
        res = json.loads(item["result"])[0]
        audio = requests.get(BASE + res["file"], timeout=300)
        audio.raise_for_status()
        dest.write_bytes(audio.content)
        log(f"노래 생성 완료 ({int(time.time() - t1)}초 소요)")
        return duration
    finally:
        srv.terminate()
        try:
            srv.wait(30)
        except Exception:
            srv.kill()


def audio_duration(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True, check=True).stdout.strip())


# ---------- 2. 가사 시간 맞추기 ----------
def flat_lines(ep):
    out = []
    for name in ep["structure"]:
        for j, ln in enumerate(ep["sections"][name]["lines"]):
            out.append((name, j, ln))
    return out


def align(ep, mp3):
    lines = flat_lines(ep)
    n = len(lines)
    L = 8 * 60 / ep["bpm"]
    dur = audio_duration(mp3)
    starts = [None] * n
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(mp3), language="ko", vad_filter=False)
        segs = [(s.start, s.end, "".join(HANGUL.findall(s.text))) for s in segs]
        ptr = 0
        for i, (_, _, ln) in enumerate(lines):
            target = "".join(HANGUL.findall(ln))
            best, bi = 0.0, None
            for k in range(ptr, min(ptr + 4, len(segs))):
                r = difflib.SequenceMatcher(None, target, segs[k][2]).ratio()
                if r > best:
                    best, bi = r, k
            if bi is not None and best >= 0.35:
                starts[i] = segs[bi][0]
                ptr = bi + 1
        known = sum(s is not None for s in starts)
        log(f"가사 시간 인식: {known}/{n}줄")
        if known < n * 0.5:
            raise RuntimeError("인식된 줄이 너무 적음")
        # 비어 있는 줄은 앞뒤 값 사이를 균등 분할
        idx = [i for i, s in enumerate(starts) if s is not None]
        for i in range(n):
            if starts[i] is None:
                prev = max([k for k in idx if k < i], default=None)
                nxt = min([k for k in idx if k > i], default=None)
                if prev is not None and nxt is not None:
                    starts[i] = starts[prev] + (starts[nxt] - starts[prev]) * (i - prev) / (nxt - prev)
                elif prev is not None:
                    starts[i] = starts[prev] + L * (i - prev)
                else:
                    starts[i] = starts[nxt] - L * (nxt - i)
        NOTES.append("가사 시간은 음성 인식으로 맞췄습니다. 자막이 어긋나면 CapCut에서 조금만 밀어 주세요.")
    except Exception as e:
        log("음성 인식 실패, 균등 분할로 대체:", e)
        intro, outro = 4.0, 6.0
        step = (dur - intro - outro) / n
        starts = [intro + i * step for i in range(n)]
        NOTES.append("가사 시간 자동 인식에 실패해 균등하게 나눴습니다. 자막/컷 시간은 노래를 들으며 CapCut에서 맞춰 주세요.")
    tl = []
    for i, (name, j, ln) in enumerate(lines):
        end = (starts[i + 1] - 0.1) if i + 1 < n else min(starts[i] + L, dur - 0.5)
        tl.append({"section": name, "line": j, "text": ln, "start": round(starts[i], 3), "end": round(end, 3)})
    return tl


def beat_times(mp3, bpm):
    try:
        import librosa
        y, sr = librosa.load(str(mp3), sr=22050, mono=True)
        _, beats = librosa.beat.beat_track(y=y, sr=sr, start_bpm=bpm)
        return [float(t) for t in librosa.frames_to_time(beats, sr=sr)]
    except Exception as e:
        log("박자 분석 실패:", e)
        return []


# ---------- 3. 컷 구성표 ----------
def cutsheet(ep, tl, beats, out):
    rows, n = [], 1
    for e in tl:
        chorus = e["section"].startswith("C")
        k = 4 if chorus else 2
        span = e["end"] - e["start"]
        pts = [e["start"] + span * c / k for c in range(k)] + [e["end"]]
        if beats:
            for c in range(1, k):
                nb = min(beats, key=lambda b: abs(b - pts[c]))
                if abs(nb - pts[c]) < 0.3:
                    pts[c] = nb
        off = 0.0
        for c in range(k):
            d = pts[c + 1] - pts[c]
            if c == 0:
                fx = ep["sections"][e["section"]]["fx"][e["line"]]
            else:
                fx = "컷마다 110% 줌인/원본 번갈아" if chorus else "130% 줌인"
            rows.append([n, bk.mmss(pts[c]), bk.mmss(pts[c + 1]), e["section"], e["text"], round(d, 2),
                         f"{e['section']}-{e['line'] + 1}", f"{off:.2f}~{off + d:.2f}", fx])
            off += d
            n += 1
    with open(out / "cutsheet.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["컷", "시작", "끝", "구간", "가사", "컷길이(초)", "사용 클립", "클립 안 구간(초)", "편집 효과"])
        w.writerows(rows)
    return len(rows)


# ---------- 4. 메일 ----------
def send_mail(subject, body, files=()):
    if not os.environ.get("GMAIL_ADDRESS"):
        log("(메일 설정 없음: 발송 생략)", subject)
        return
    msg = EmailMessage()
    me = os.environ["GMAIL_ADDRESS"]
    msg["From"], msg["To"], msg["Subject"] = me, os.environ.get("MAIL_TO", me), subject
    msg.set_content(body)
    for p in files:
        p = pathlib.Path(p)
        if p.exists() and p.stat().st_size < 22 * 1024 * 1024:
            sub = {"zip": "zip", "mp3": "mpeg"}.get(p.suffix[1:], "octet-stream")
            mt = "audio" if p.suffix == ".mp3" else "application"
            msg.add_attachment(p.read_bytes(), maintype=mt, subtype=sub, filename=p.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(me, os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    history = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else []
    try:
        if TEST:
            ep = json.loads((HERE / "ep_example.json").read_text(encoding="utf-8"))
            ep["structure"] = ["V1", "C"]
            ep["yt_title"] = "[테스트] 안녕 안녕 아침이야"
            NOTES.append("※ 테스트 실행입니다(예시 가사, 짧은 곡).")
        else:
            import episode_gen
            ep = episode_gen.generate(history)
        (OUT / "episode.json").write_text(json.dumps(ep, ensure_ascii=False, indent=2), encoding="utf-8")
        log("제목:", ep["title"])

        song = OUT / "song.mp3"
        timeline = None
        try:
            ace_song(ep, song)
            kind = "ACE-Step 노래(가수 목소리 포함)"
        except Exception as e:
            log("ACE-Step 실패 -> 반주곡으로 대체:", e)
            NOTES.append(f"AI 노래 생성에 실패해 반주 멜로디로 대체했습니다. (원인: {str(e)[:150]})")
            sub = OUT / "fallback"
            timeline, _, _ = bk.build(ep, sub)
            shutil.copy(sub / "song.mp3", song)
            kind = "반주 멜로디(목소리 없음)"
        if timeline is None:
            timeline = align(ep, song)
        beats = beat_times(song, ep["bpm"])
        total = audio_duration(song)

        char = json.loads((HERE / "character.json").read_text(encoding="utf-8"))
        bk.write_srt(timeline, OUT)
        cuts = cutsheet(ep, timeline, beats, OUT)
        clips = bk.write_flow_prompts(ep, char, 60 / ep["bpm"], OUT)
        bk.write_upload_info(ep, OUT)
        bk.write_plan(clips, cuts, total, OUT)
        (OUT / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")

        stamp = dt.datetime.now(KST).strftime("%m%d")
        kit = OUT / f"kit_{stamp}"
        kit.mkdir()
        for f in ["song.mp3", "lyrics.srt", "cutsheet.csv", "flow_prompts.md", "upload_info.md", "작업순서.md"]:
            shutil.copy(OUT / f, kit / f)
        zpath = shutil.make_archive(str(OUT / f"kit_{stamp}"), "zip", kit)
        if not TEST:
            history.append({"date": dt.datetime.now(KST).strftime("%Y-%m-%d"), "title": ep["title"], "theme": ep.get("theme")})
            HIST.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        body = (f"새 에피소드 키트가 완성됐어요.\n\n제목: {ep['title']}\n노래: {kind}, 길이 {total / 60:.1f}분\n"
                f"컷 {cuts}개 / Flow 클립 {clips}개\n\n" + "\n".join(NOTES) +
                "\n\n첨부 zip 안에: 노래, 자막(srt), 컷 구성표(csv), Flow 프롬프트, 업로드 정보, 작업순서.\n"
                "다음 할 일: 작업순서.md대로 Flow에서 클립 생성 → CapCut 조립 → 유튜브 스튜디오 9시 예약.")
        send_mail(f"[키즈채널] 새 에피소드 키트: {ep['title']}", body, [zpath, song])
        log("메일 발송 완료")
    except Exception:
        err = traceback.format_exc()
        log(err)
        try:
            send_mail("[키즈채널] 이번 키트 제작 실패", err[-3500:])
        except Exception:
            log("실패 메일도 못 보냄")
        sys.exit(1)


if __name__ == "__main__":
    main()

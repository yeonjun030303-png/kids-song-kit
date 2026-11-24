"""Gemini로 새 에피소드(가사 4줄x4구간, 장면 프롬프트, 제목)를 만들고 8음절 규칙을 검사합니다."""
import json, os, pathlib, random, re, time
import requests

HERE = pathlib.Path(__file__).parent
HANGUL = re.compile(r"[가-힣]")
API = "https://generativelanguage.googleapis.com/v1beta"
KEY = os.environ.get("GEMINI_API_KEY", "")


def pick_model():
    if os.environ.get("TEXT_MODEL"):
        return os.environ["TEXT_MODEL"]
    try:
        r = requests.get(f"{API}/models", params={"key": KEY, "pageSize": 200}, timeout=30).json()
        best = None
        for m in r.get("models", []):
            name = m["name"].split("/")[-1]
            mt = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash", name)
            if mt and "generateContent" in m.get("supportedGenerationMethods", []):
                v = float(mt.group(1))
                if not best or v > best[0]:
                    best = (v, name)
        if best:
            return best[1]
    except Exception:
        pass
    return "gemini-flash-latest"


def ask(model, prompt, temp=1.0):
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": temp}}
    for i in range(4):
        r = requests.post(f"{API}/models/{model}:generateContent", params={"key": KEY}, json=body, timeout=180)
        if r.status_code == 200:
            return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        time.sleep(8 * (i + 1))
    raise RuntimeError(f"Gemini 호출 실패: {r.status_code} {r.text[:200]}")


def bad_lines(ep):
    bad = []
    for name in ("V1", "C", "V2", "V3"):
        for ln in ep["sections"][name]["lines"]:
            n = len(HANGUL.findall(ln))
            if n != 8:
                bad.append(f"[{name}] '{ln}' = {n}음절")
        if len(ep["sections"][name]["lines"]) != 4 or len(ep["sections"][name]["scenes"]) != 4:
            bad.append(f"[{name}] lines/scenes는 각각 정확히 4개여야 함")
    return bad


def generate(history):
    char = json.loads((HERE / "character.json").read_text(encoding="utf-8"))
    example = json.loads((HERE / "ep_example.json").read_text(encoding="utf-8"))
    used = [h.get("theme") for h in history[-8:]]
    themes = [t for t in char["themes"] if t not in used] or char["themes"]
    theme = random.choice(themes)
    past = [h["title"] for h in history[-30:]]
    names = ", ".join(c["name"] for c in char["characters"])
    base = f"""너는 만 2~5세용 유튜브 율동 동요 작가야. 주제: {theme}
캐릭터(고정): {names}  (몽이=아기 토끼, 콩이=아기 곰)
규칙:
- V1, V2, V3(절)와 C(후렴) 각 구간은 가사 정확히 4줄. 각 줄은 한글 음절 정확히 8개(띄어쓰기/특수문자 제외).
- 쉬운 단어, 의성어/의태어(콩콩콩, 짝짝짝), 질문-대답이나 같은 말 반복, 후렴에 율동 지시(손 흔들기, 박수, 점프).
- 무섭거나 위험하거나 슬픈 내용 금지. 따뜻하고 무해하게. 교훈은 한 가지.
- scenes는 영어, 해당 줄의 구도/행동/배경만(캐릭터 외형 설명 금지, 이름 대신 bunny, bear cub 사용 가능).
- fx는 CapCut 편집 효과 한국어 짧은 메모 4개씩.
- structure는 ["V1","C","V2","C","V3","C","C"] 그대로.
- bpm은 112~120 정수, seed는 임의의 정수.
- 이미 만든 제목과 겹치지 않기: {past}
출력은 아래 예시와 똑같은 JSON 구조 (title, yt_title(이모지 포함 60자 이내), desc_intro, tags(12개 한국어), bpm, seed, structure, sections).
예시:
{json.dumps(example, ensure_ascii=False)}"""
    model = pick_model()
    feedback = ""
    for attempt in range(6):
        ep = ask(model, base + feedback)
        problems = bad_lines(ep)
        if problems:
            feedback = "\n\n지난 시도의 오류를 고쳐서 다시 전체를 출력해:\n" + "\n".join(problems)
            continue
        chk = ask(model, '아래 어린이 영상 대본이 만 5세 이하에게 안전하고 무해한지 검수해줘. 문제 있으면 {"ok":false,"reason":"..."} 없으면 {"ok":true}.\n'
                  + json.dumps(ep, ensure_ascii=False), temp=0.2)
        if chk.get("ok"):
            ep["theme"] = theme
            ep["structure"] = ["V1", "C", "V2", "C", "V3", "C", "C"]
            ep["bpm"] = int(ep.get("bpm") or 116)
            return ep
        feedback = f"\n\n안전 검수 탈락 사유: {chk.get('reason')}. 다른 내용으로 다시."
    raise RuntimeError("가사 생성 실패(8음절 규칙/안전 검수를 6번 통과 못함): " + "; ".join(bad_lines(ep)))


if __name__ == "__main__":
    print(json.dumps(generate([]), ensure_ascii=False, indent=1))

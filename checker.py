# -*- coding: utf-8 -*-
"""주얼리 위시리스트 정기 확인 스크립트 (Claude 정기 작업용).

사용법:
  python3 -I checker.py --db DB_DIR --out OUT_DIR --now 2026-10-21T07:52+09:00 \
      --auG 23360 --ptG 9449 --jpyKrw 8.43 --usdKrw 1329.49

DB_DIR 에는 ArtifactData list(out_dir) 결과가 있어야 한다: DB_DIR/items/*.json, DB_DIR/meta/*.json
출력(OUT_DIR):
  writes/<collection>__<doc_id>.json  ArtifactData update/set 에 file_path 로 넣을 내용
  plan.json                             어떤 문서를 update/set 할지, 업로드할 이미지, 직접 확인할 상품
  message.md                            사용자에게 보낼 한국어 알림 초안 (알릴 것이 없으면 빈 파일)
  push.txt                              휴대폰 알림용 한 줄 요약 (알릴 것이 없으면 빈 파일)
표준 출력에는 짧은 요약만 출력한다.
"""
import argparse, glob, html, json, os, re, subprocess, sys, time, urllib.parse

T_START = time.time()  # 시간 예산은 스크립트 시작 기준 (정기 작업의 Bash 제한 600초 안에 끝내기 위함)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
GOLD_RE = r"K18|K14|18K|14K|750|585"


def gold_info(text):
    """금 순도(K18 .75 / K14 .585)와 한국어 소재명. K18·K14 금이 아니면 (None, "")."""
    t = text or ""
    if not re.search(GOLD_RE, t) or re.search(r"Pt|PT|プラチナ|K10|375|K24", t): return None, ""
    k, pur = ("K14", .585) if re.search(r"K14|14K|585", t) else ("K18", .75)
    if re.search(r"コンビ|×|/K|・K", t) or len(set(re.findall(r"WG|YG|PG|RG|CG", t))) > 1: c = "콤비골드"
    elif re.search(r"WG|ホワイト", t): c = "화이트골드"
    elif re.search(r"PG|ピンク", t): c = "핑크골드"
    elif re.search(r"RG|ローズ", t): c = "로즈골드"
    elif re.search(r"CG|シャンパン", t): c = "샴페인골드"
    elif re.search(r"YG|イエロー", t): c = "옐로우골드"
    else: c = "골드"
    return pur, f"{k} {c}"


def strip_gold(t):
    return re.sub(r"(K18|K14|18K|14K|750|585)\s*(WG|YG|PG|RG|CG)?", "", t or "")


DROP_WORDS_OLD = r"ダイヤ|パール|真珠|サファイア|ルビー|エメラルド|トルマリン|アメジスト|トパーズ|オパール|ガーネット|ペリドット|アクア|シトリン|スピネル|ラブラドライト|キュービック|ジルコニア|ペンダント|トップ|モチーフ|イニシャル|ハート|クロス|珊瑚|サンゴ|コーラル|翡翠|ヒスイ|オニキス|ターコイズ|ビーズ|K10|375|Pt|PT|プラチナ|SV|シルバー|ブレス|リング|ピアス|イヤリング|アンクレット|チャーム"


def curl(url, timeout=40):
    """(http_code, final_url, body) 를 돌려준다. 실패하면 한 번 재시도."""
    for attempt in range(2):
        r = subprocess.run(["curl", "-sS", "-L", "-A", UA, "--max-time", str(timeout),
                            "-w", "\n__META__%{http_code} %{url_effective}", url],
                           capture_output=True, text=True, errors="ignore")
        out = r.stdout
        i = out.rfind("\n__META__")
        if i >= 0:
            meta = out[i + 9:].split(" ", 1)
            code = int(meta[0]) if meta[0].isdigit() else 0
            if code in (200, 404):
                return code, (meta[1] if len(meta) > 1 else url), out[:i]
        time.sleep(3)
    return 0, url, ""


def text_of(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


def num(s):
    return int(re.sub(r"[^\d]", "", s)) if s and re.search(r"\d", s) else None


# ---------- 사이트별 파서 ----------

def komehyo(url, detail=False):
    code, final, t = curl(url)
    res = {"ok": False}
    if code == 404 or (code == 200 and "/product/" not in final):
        return {"ok": True, "status": "품절", "price": None}
    if code != 200:
        return res
    m = re.search(r"価格【￥([\d,]+)】", t)
    o = re.search(r'p-txt--strikethrough">￥([\d,]+)', t)
    if "カートに入れる" in t:
        st = "판매중"
    elif re.search(r"SOLD OUT|売り切れ|在庫なし", t):
        st = "품절"
    else:
        return res
    res = {"ok": True, "status": st, "price": num(m.group(1)) if m else None,
           "origPrice": num(o.group(1)) if o else None}
    if res["price"] is None and st == "판매중":
        return {"ok": False}
    if detail:
        i = t.find("商品の概要")
        s = text_of(t[i:i + 20000]) if i >= 0 else ""
        s = re.sub(r"サイズについて.*?裄 きものの背縫いの最上部から、袖口までの寸法", "", s)
        g = lambda p: (lambda mm: mm.group(1).strip() if mm else "")(re.search(p, s))
        ti = re.search(r"<title>コメ兵｜(.*?)｜", t)
        img = re.search(r'og:image" content="([^"]+)', t)
        res.update(jpTitle=ti.group(1) if ti else "", material=g(r"材質 (.*?) 商品ランク"),
                   rank=g(r"商品ランク (\S+)"), size=g(r"サイズ\s+([^ ]*cm)"),
                   actual=g(r"実寸サイズ (.*?) (?:使用石|重さ|在庫店舗)"), stones=g(r"使用石 (.*?) (?:重さ|在庫店舗)"),
                   weight=g(r"重さ\s*[^\d]{0,4}([\d.]+)\s*g"), store=g(r"在庫店舗 (.*?) 詳細説明"),
                   img=img.group(1) if img else "")
    return res


def greeber(url, detail=False):
    code, final, t = curl(url)
    if code == 404:
        return {"ok": True, "status": "품절", "price": None}
    if code != 200:
        return {"ok": False}
    m = re.search(r'fs-c-price__value">([\d,]+)<', t)
    if not m:
        return {"ok": False}
    st = "품절" if ("outOfStock" in t or "在庫なし" in t) else "판매중"
    res = {"ok": True, "status": st, "price": num(m.group(1)), "origPrice": None}
    if detail:
        s = text_of(t)
        g = lambda k: (lambda mm: mm.group(1).strip() if mm else "")(re.search("【" + k + r"】\s*(.*?)\s*(?=【|$)", s))
        ti = re.search(r'fs-c-productNameHeading__name">(.*?)<', t, re.S) or re.search(r"<title>(.*?)</title>", t, re.S)
        img = re.search(r"https://greebershop1\.itembox\.cloud/product/[^\"'?]+-01\.jpg", t)
        w = re.search(r"([\d.]+)\s*g", g("重量"))
        res.update(jpTitle=html.unescape(ti.group(1)).strip() if ti else "", material=g("素材")[:60],
                   weight=w.group(1) if w else "", size=g("サイズ")[:120], stones=g("石目")[:80],
                   rank=g("商品ランク")[:20], store="", img=img.group(0) if img else "")
    return res


def selby_rank(text, tags=None):
    m = re.search(r"(?:コンディション|ランク)[：:\s]*(SA|NS|[NSABC])(?![A-Za-z])", text or "")
    if m: return m.group(1)
    tl = tags.split(", ") if isinstance(tags, str) else (tags or [])
    for x in tl:
        if x in ("NS", "SA", "S", "A", "B", "C", "N"): return x
    return ""


def selby(url, detail=False):
    h = re.search(r"/products/([^?/#]+)", url)
    if not h:
        return {"ok": False}
    h = h.group(1)
    code, _, t = curl(f"https://www.selby.shop/products/{h}.js")
    if code == 404:
        return {"ok": True, "status": "품절", "price": None}
    try:
        j = json.loads(t)
    except Exception:
        return {"ok": False}
    res = {"ok": True, "status": "판매중" if j.get("available") else "품절",
           "price": round(j["price"] / 100), "origPrice": None}
    cmp_ = j.get("compare_at_price")
    if cmp_ and cmp_ > j["price"]:
        res["origPrice"] = round(cmp_ / 100)
    if detail:
        code, _, t2 = curl(f"https://www.selby.shop/products/{h}.json")
        try:
            p = json.loads(t2)["product"]
        except Exception:
            p = {}
        b = text_of(p.get("body_html") or "")
        g = lambda k: (lambda mm: mm.group(1).strip() if mm else "")(re.search(k + r"[：:\s]*([^ ]{1,40})", b))
        tags = p.get("tags") or ""
        store = ([x for x in (tags.split(", ") if isinstance(tags, str) else tags) if x.endswith("店")] or [""])[0]
        w = re.search(r"重量[^\d]{0,10}([\d.]+)\s*g", b)
        L = re.search(r"(?:チェーンの長さ|長さ)[^\d]{0,10}([\d.]+)\s*cm", b)
        res.update(jpTitle=p.get("title", j.get("title", "")), material=g("素材"), weight=w.group(1) if w else "",
                   size=(L.group(1) + "cm") if L else "", stones="", rank=selby_rank(b, tags), store=store,
                   img=((p.get("images") or [{}])[0].get("src", "")))
    return res


def site_of(url):
    if "komehyo.jp" in url: return "코메효"
    if "greeber.net" in url: return "그리버"
    if "selby.shop" in url: return "셀비"
    return None


FETCH = {"코메효": komehyo, "그리버": greeber, "셀비": selby}


# ---------- 가성비·관세 ----------

def purity(material):
    m = material or ""
    if re.search(r"Pt950", m) and re.search(r"Pt850", m): return 0.9, "pt"
    for k, p in (("Pt950", .95), ("Pt900", .9), ("Pt850", .85)):
        if k in m: return p, "pt"
    for k, p in (("K10", .417), ("K14", .585), ("K24", 1.0), ("K18", .75), ("750", .75)):
        if k in m: return p, "au"
    return None, None


def grade(r):
    return "좋음" if r <= 1.6 else ("보통" if r <= 2.4 else "나쁨")


def value(it, A):
    try:
        w = float(re.sub(r"[^\d.]", "", str(it.get("weight") or "")) or 0)
    except ValueError:
        w = 0
    pur, kind = purity(it.get("material"))
    if not w or not pur or it.get("price") is None:
        return None
    unit = A.auG if kind == "au" else A.ptG
    val = w * pur * unit
    p = it["price"] if it.get("currency") != "USD" else it["price"] * A.usdKrw / A.jpyKrw
    r = p / val
    sv = it.get("stoneValue")
    rt = p / (val + sv) if isinstance(sv, (int, float)) else None
    return r, rt


def krw(it, n):
    return n * (A.usdKrw if it.get("currency") == "USD" else A.jpyKrw)


def money(it, n):
    if n is None: return "—"
    return f"US${n:,.2f}" if it.get("currency") == "USD" else f"¥{round(n):,}"


def won(n):
    return f"₩{int(round(n / 1000.0) * 1000):,}"


def duty(it):
    if it.get("price") is None: return ""
    usd = it["price"] if it.get("currency") == "USD" else it["price"] * A.jpyKrw / A.usdKrw
    if it.get("currency") == "USD" and it.get("site") == "이베이":
        return f"약 US${usd:,.0f} (이베이가 수입세를 결제 때 함께 받음)"
    if usd <= 150:
        return f"약 US${usd:,.0f} → **관부가세 면세 직구 가능**" + (" (한도 근접)" if usd > 135 else "")
    return f"약 US${usd:,.0f} → 관부가세 약 {won(krw(it, it['price']) * 0.188)} 추정"


def describe(it, prev=None):
    v = value(it, A)
    parts = []
    if prev is not None and prev != it.get("price") and it.get("price") is not None and prev:
        d = it["price"] - prev
        parts.append(f"{money(it, prev)} → {money(it, it['price'])} ({'−' if d < 0 else '+'}{money(it, abs(d))}, {abs(d) / prev * 100:.0f}%{'↓' if d < 0 else '↑'})")
    else:
        parts.append(money(it, it.get("price")))
    if it.get("price") is not None:
        parts.append("약 " + won(krw(it, it["price"])))
        parts.append(duty(it))
    if v:
        r, rt = v
        parts.append(f"가성비 전체 중량 대비 {rt:.2f}배 {grade(rt)} / 금 중량 대비 {r:.2f}배 {grade(r)}" if rt is not None
                     else f"가성비 금 중량 대비 {r:.2f}배 {grade(r)}")
    return " · ".join(p for p in parts if p)


def cond_ok(c):
    c = re.sub(r"\(.*?\)", "", str(c or "")).strip()
    return bool(re.search(r"(?:^|[\s。])(?:S|SA|A)$", c) or re.search(r"미사용|未使用|新品|^신품", c))


def must_buy(it):
    """꼭 사야 할 템: 판매중이면서 아래 세 조건 중 2개 이상 — 가성비(원석 포함, 없으면 금) 1.10배 이하 · S·A급 · 최근 30일 안에 가격이 내려감."""
    from datetime import datetime, timedelta
    if it.get("status") != "판매중" or it.get("price") is None: return False, ""
    hits = []
    v = value(it, A)
    if v:
        r = v[1] if v[1] is not None else v[0]
        if r <= 1.10: hits.append(f"가성비 {r:.2f}배")
    if cond_ok(it.get("condition")):
        c = re.sub(r"\(.*?\)", "", str(it.get("condition"))).strip()
        g = re.sub(r'.*[。 ]', '', c) or c
        hits.append(g if re.search(r"미사용|未使用|新品|신품", g) else f"{g}급")
    p = it["price"]; drop = None
    if it.get("origPrice") and it["origPrice"] > p: drop = it["origPrice"]
    try:
        lim = datetime.fromisoformat(A.now) - timedelta(days=30)
        for h in it.get("history") or []:
            if h.get("price") and h["price"] > p and datetime.fromisoformat(h["t"]) >= lim:
                drop = max(drop or 0, h["price"])
    except (ValueError, TypeError):
        pass
    if drop: hits.append(f"{money(it, drop)}에서 {(drop - p) / drop * 100:.0f}% 인하")
    if len(hits) < 2: return False, ""
    return True, " · ".join(hits) + (" (3개 모두 충족)" if len(hits) == 3 else "")


# ---------- 새 입고 감시 ----------

KR_RANK = {"中古品A": "중고 A", "中古品B": "중고 B", "中古品S": "중고 S", "中古品SA": "중고 SA", "新品": "신품", "未使用品": "미사용"}

# 감시 대상에서 빼는 것: 반지·발찌·브로치·찝는 귀걸이·속이 빈(할로우) 제품·진주/산호 같은 유기질 보석·은/K10
EXCL = (r"リング|指輪|アンクレット|ブローチ|イヤリング|イヤーカフ|イヤカフ|ノンホール|クリップ|カフス|タイピン|"
        r"中空|ホロー|ホロウ|パイプ|[Hh][Oo][Ll][Ll][Oo][Ww]|パール|真珠|珊瑚|サンゴ|コーラル|翡翠|ヒスイ|オニキス|ターコイズ|"
        r"ビーズ|カメオ|シェル|べっ甲|SV|シルバー|K10|375|時計")
CHAIN_RE = r"チェーン|キヘイ|喜平|ベネチアン|アズキ|あずき|スクリュー|ロープ|フィガロ|スネーク|ボールチェーン"
COLOR_RE = r"サファイア|サファイヤ|ラピス|ジルコン|ヘミモ|クンツ|アイオライト|ムーンストーン|ルチル|モルガ|スフェーン|パライバ|ラリマー|ベリル|クリソ|フローライト|プレナイト|クォーツ|クオーツ|水晶|ヘマタイト|ロードライト|カルセドニー|アゲート|メノウ|ジェイド|ルビー|エメラルド|トルマリン|アメジスト|トパーズ|オパール|ガーネット|ペリドット|アクアマリン|シトリン|スピネル|タンザナイト|アレキサンドライト|クォーツ|カラーストーン|色石"
FAKE_RE = r"キュービック|ジルコニア|CZ|ガラス|合成"
KIND_LABEL = {"목걸이": "목걸이", "체인": "체인 목걸이", "펜던트": "펜던트", "팔찌": "팔찌", "귀걸이": "귀걸이"}


def classify(name):
    """상품명으로 종류를 정한다. 대상이 아니면 None."""
    n = name or ""
    if re.search(EXCL, n): return None
    if "ピアス" in n: return "귀걸이"
    if re.search(r"ブレス|バングル", n): return "팔찌"
    if re.search(r"ペンダント|トップ", n): return "펜던트"
    if re.search(r"ネックレス|" + CHAIN_RE, n):
        return "체인" if re.search(CHAIN_RE, n) and not re.search(r"ダイヤ|" + COLOR_RE, n) else "목걸이"
    return None


def stone_est(stones, name, kind=""):
    """원석 추정 가치(엔), 근거, 제목용 짧은 표기. 보수적으로(낮게) 잡는다.
    - 캐럿은 다이아에 붙은 것만 센다(D0.30ct, ダイヤ…0.30ct). 유색석·컬러 처리 다이아(BD·ブラウン·トリート 등)는 작은 유색석으로 본다.
    - 한 알 단가(0.1–0.29ct ¥100,000 / 0.3ct 이상 ¥150,000)는 一粒·ソリティア·1P 처럼 한 알임이 적혀 있을 때만, 나머지(귀걸이 포함)는 여러 알 ¥60,000/ct."""
    st = stones or ""
    t = st + " " + (name or "")
    DIA = r"(?<![A-Za-z])(?:D|MD)\s?(?=\d)|ダイヤ|ダイア"
    COLORDIA = r"BD|ブラック|ブラウン|トリート|アイスブルー|イエローダイヤ|ピンクダイヤ|ブルーダイヤ|カラーダイヤ|シャンパン"
    has_color = bool(re.search(COLOR_RE + r"|" + COLORDIA + r"|クオーツ|ストーン|ライト|ナイト|ナイト|サイト|ガーネット|ルチル|モルガ|スフェーン|ムーン", t))
    if not re.search(DIA + r"|" + COLOR_RE + "|" + FAKE_RE, t) and not st.strip():
        return 0, "원석 없음", ""
    src = st if re.search(r"\d\.\d+\s*ct", st, re.I) else (name or "")  # 같은 캐럿을 두 번 세지 않도록 한쪽만 본다
    pref = [float(m.group(1)) for m in re.finditer(r"(?<![A-Za-z])(?:D|MD)\s?(\d+\.\d+)\s*(?:ct)?", src, re.I)]
    if pref:
        dct = pref  # D0.30ct 처럼 다이아 표시가 붙은 캐럿만 센다
    elif not has_color and re.search(r"ダイヤ|ダイア", src):
        dct = [float(m.group(1)) for m in re.finditer(r"(\d+\.\d+)\s*ct", src, re.I)]
    else:
        dct = []
    if re.search(r"ダイヤ|ダイア|\bD\d", t) and not (has_color and not dct):
        if dct:
            ct = sum(dct)
            single = len(dct) == 1 and kind != "귀걸이" and re.search(r"一粒|1粒|ひと粒|ソリテ|1P|ワンストーン", t) and not re.search(r"計|合計|TTL|メレ|×|ｘ", t)
            rate = 60000 if (not single or ct < 0.1) else (100000 if ct < 0.3 else 150000)
            return round(ct * rate), f"다이아 {ct:.2f}ct × ¥{rate:,}/ct" + ("" if rate > 60000 else " (여러 알 기준)"), f"다이아 {ct:.2f}ct"
        if not has_color:
            return 1800, "캐럿 미표기 작은 다이아 약 0.03ct 추정", "다이아"
    if has_color or re.search(COLOR_RE, t):
        return 500, "유색석(작은 유색석 기준 추정)", "유색석"
    if re.search(FAKE_RE, t):
        return 0, "큐빅 등(가치 없음)", "큐빅"
    return 0, "원석 없음", ""


def max_len(s):
    ns = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*(?:cm|ｃｍ)?", s or "") if 20 <= float(x) <= 120]
    return max(ns) if ns else 0


def len_ok(kind, L, minL):
    if kind in ("목걸이", "체인"): return L >= minL
    if kind == "펜던트": return not (20 <= L < minL)  # 체인이 달린 펜던트는 45cm 이상만
    return True


def info_from(kind, name, material, weight, L, stones):
    """상세 정보에서 감시용 정보 리스트 [중량, 길이, 순도, 소재, 종류, 원석가치, 근거, 원석표기]. 대상이 아니면 None."""
    if not kind: return None
    pur, mat = gold_info(material or "")
    if not pur: pur, mat = gold_info(name)
    if not pur: return None
    try:
        w = float(weight)
    except (TypeError, ValueError):
        return None
    if not w: return None
    sv, sb, stl = stone_est(stones, name, kind)
    return [w, L, pur, mat, kind, sv, sb, stl]


def watch_scan(existing_urls, alerted, crit, items_max_order, quick=False):
    found, best, failed = [], {}, []
    wc = dict(crit.get("wcache") or {}) if crit.get("wver") == 4 else {}
    wc = {k: v for k, v in wc.items() if v is None or (isinstance(v, list) and len(v) == 8)}  # 예전 형식 캐시는 버림  # 상품 id -> 감시용 정보(이미 본 상품은 상세를 다시 열지 않음)
    newwc = {}
    T0 = T_START
    maxr = float(crit.get("maxRatio", 1.4)); minL = float(crit.get("minLengthCm", 45)); maxP = float(crit.get("maxPrice", 80000))
    au = A.auG
    order = items_max_order

    def ratio(price, inf):
        return price / (inf[0] * inf[2] * au + inf[5])

    def consider(site, did, url, price, inf, avail, extra):
        nonlocal order
        if not inf or not price or price > maxP or not len_ok(inf[4], inf[1], minL): return
        r = ratio(price, inf)
        if callable(avail):
            if r > maxr or did in alerted or url in existing_urls: return
            d2 = avail()
            if not d2.get("ok") or d2.get("status") != "판매중": return
            price = d2.get("price") or price
            if price > maxP: return
            r = ratio(price, inf)
            extra = d2
            avail = True
        if not avail: return
        line = f"{site} {did[1:]} {inf[3]} {KIND_LABEL[inf[4]]} ¥{price:,} {inf[0]:g}g {r:.2f}배"
        if site not in best or r < best[site][0]:
            best[site] = (r, line)
        if r <= maxr and did not in alerted and url not in existing_urls:
            order += 1
            w, L, pur, mat, kind, sv, sb, stl = inf
            neck = kind in ("목걸이", "체인")
            title = f"{mat} {stl + ' ' if stl else ''}{KIND_LABEL[kind]}" + (f" {L:g}cm" if neck and L else "") + f" ({w:g}g)"
            doc = {"order": order, "kind": kind, "site": site, "title": title, "jpTitle": extra.get("jpTitle") or "",
                   "url": url, "price": price, "currency": "JPY", "status": "판매중",
                   "history": [{"t": A.now, "price": price, "status": "판매중"}], "checkedAt": A.now,
                   "addedAt": A.now_utc, "img": "", "material": mat, "weight": f"{w:g}g",
                   "length": f"{L:g}cm" if L and (neck or kind == "펜던트") else "", "size": extra.get("size", ""),
                   "stones": (extra.get("stones") or stl or "")[:80], "stoneValue": sv, "stoneBasis": sb,
                   "condition": extra.get("rank", ""), "store": extra.get("store", ""),
                   "note": f"새 입고 감시로 자동 추가 (가성비 {r:.2f}배, K18·K14·¥8만 이하)",
                   "valueNote": "원석 가치는 대략적인 추정치" if sv else "원석 없는 금 제품이라 금값 비교가 정확한 편"}
            found.append((did, doc, extra.get("img", ""), r))

    # 비슷한 디자인 감시 (사용자 요청 2026-10-22: 품절된 그리버 197074와 비슷한 밀그레인 다이아 목걸이)
    DSN = crit.get("design") or {}
    dmaxP = float(DSN.get("maxPrice", 50000))
    dseen = set(DSN.get("seen") or [])

    def dmatch(text):
        return bool(re.search(r"ミルグレイン|ミルグレン|ミル打|ミル装飾", text or "")) and "ダイヤ" in (text or "")

    def add_design(site, did, url, price, inf, extra):
        nonlocal order
        if not inf or inf[4] not in ("목걸이", "펜던트") or not price or price > dmaxP: return
        if did in alerted or url in existing_urls or any(f[0] == did for f in found): return
        order += 1
        w, L, pur, mat, kind, sv, sb, stl = inf
        title = f"{mat} {stl + ' ' if stl else ''}밀그레인 {KIND_LABEL[kind]}" + (f" {L:g}cm" if L else "") + f" ({w:g}g)"
        doc = {"order": order, "kind": kind, "site": site, "title": title, "jpTitle": extra.get("jpTitle") or "",
               "url": url, "price": price, "currency": "JPY", "status": "판매중",
               "history": [{"t": A.now, "price": price, "status": "판매중"}], "checkedAt": A.now,
               "addedAt": A.now_utc, "img": "", "material": mat, "weight": f"{w:g}g",
               "length": f"{L:g}cm" if L else "", "size": extra.get("size", ""),
               "stones": (extra.get("stones") or stl or "")[:80], "stoneValue": sv, "stoneBasis": sb,
               "condition": extra.get("rank", ""), "store": extra.get("store", ""),
               "similarTo": "g197074",
               "note": "비슷한 디자인 감시로 자동 추가 (품절된 그리버 밀그레인 다이아 목걸이와 비슷, ¥5만 이하)",
               "valueNote": "원석 가치는 대략적인 추정치" if sv else ""}
        found.append((did, doc, extra.get("img", ""), ratio(price, inf)))

    def kd(url):
        d = komehyo(url, detail=True)
        if d.get("ok"):
            d["_L"] = max_len(d.get("size", ""))
            a = d.get("actual", "")
            d["size"] = ("체인 폭 " + re.sub(r".*チェーン最大幅約:\s*", "약 ", a)) if "チェーン最大幅" in a else a[:80]
            d["rank"] = KR_RANK.get(d.get("rank"), d.get("rank"))
        return d

    def gd(url):
        d = greeber(url, detail=True)
        if d.get("ok"):
            d["_L"] = max_len(re.sub(r"(?:ネックレス|チェーン)?幅.*", "", d.get("size", "")))
            wid = re.search(r"幅:?約?([\d.]+mm)", d.get("size", ""))
            d["size"] = ("폭 약 " + wid.group(1)) if wid else ""
        return d

    queries = [f"{k} {c}" for c in ("ネックレス", "ブレスレット", "ペンダント", "ピアス") for k in ("K18", "K14")]

    # (1) 코메효
    try:
        cands = {}
        for q in queries:
            for page in ((1,) if quick else (1, 2, 3)):
                code, _, t = curl("https://komehyo.jp/search/?" + urllib.parse.urlencode({"q": q, "sortKey": "01", "page": page}))
                if code != 200: raise RuntimeError("komehyo search")
                cards = t.split('class="p-lists__item"')[1:]
                for c in cards:
                    h = re.search(r'href="(/product/[\d-]+/)"', c)
                    nm = re.search(r'productsname">(.*?)<', c, re.S)
                    sz = re.search(r'サイズ：<span class="p-link__txt--rating">(.*?)<', c)
                    pr = re.findall(r'txt--price[^"]*">￥([\d,]+)', c)
                    if not (h and nm and pr): continue
                    name = html.unescape(nm.group(1)).strip()
                    price = num(pr[0])
                    if not price or price > maxP or not gold_info(name)[0] or not classify(name): continue
                    L = max_len(sz.group(1) if sz else "")
                    if classify(name) in ("목걸이", "체인") and L and L < minL: continue
                    cands[h.group(1)] = (name, price, L)
                if len(cards) < 50: break
        for path, (name, price, L) in cands.items():
            url = "https://komehyo.jp" + path
            did = "k" + re.sub(r"\D", "", path)
            if did in wc:
                newwc[did] = wc[did]
                consider("코메효", did, url, price, wc[did], (lambda u=url: kd(u)), {})
                continue
            if time.time() - T0 > 260: continue  # 시간 초과 방지: 나머지는 다음 실행 때 확인
            d = kd(url)
            if not d.get("ok"): continue
            kind = classify(name + " " + (d.get("jpTitle") or ""))
            L2 = max(L, d.get("_L", 0))
            inf = info_from(kind, name, d.get("material"), d.get("weight"), L2, d.get("stones"))
            if inf and re.search(EXCL, d.get("material", "") + d.get("actual", "")): inf = None
            newwc[did] = inf
            consider("코메효", did, url, d.get("price") or price, inf, d.get("status") == "판매중",
                     {"jpTitle": d.get("jpTitle") or name, "size": d.get("size"), "rank": d.get("rank"),
                      "store": d.get("store"), "img": d.get("img"), "stones": d.get("stones")})
    except Exception:
        failed.append("코메효")
    print(f"[time] komehyo {time.time()-T0:.0f}s", file=sys.stderr)

    # (2) 그리버
    try:
        cands = {}
        for q in queries:
            for page in ((1,) if quick else (1, 2)):
                code, _, t = curl("https://www.greeber.net/p/search?" + urllib.parse.urlencode({"keyword": q, "sort": "latest", "page": page}))
                if code != 200: raise RuntimeError("greeber search")
                for c in t.split("fs-c-productListItem__imageContainer")[1:]:
                    h = re.search(r'href="(/c/[a-z]+/\d+)"', c)
                    nm = re.search(r'fs-c-productName__name">(.*?)<', c, re.S)
                    pr = re.search(r'fs-c-price__value">([\d,]+)<', c)
                    if not (h and nm and pr): continue
                    name = html.unescape(nm.group(1)).strip()
                    price = num(pr.group(1))
                    if not price or price > maxP or not gold_info(name)[0] or not classify(name): continue
                    cands[h.group(1)] = (name, price)
        for path, (name, price) in cands.items():
            url = "https://www.greeber.net" + path
            did = "g" + path.rsplit("/", 1)[1]
            if did in wc:
                newwc[did] = wc[did]
                consider("그리버", did, url, price, wc[did], (lambda u=url: gd(u)), {})
                continue
            if time.time() - T0 > 380: continue
            d = gd(url)
            if not d.get("ok"): continue
            kind = classify(name)
            inf = info_from(kind, name, d.get("material"), d.get("weight"), d.get("_L", 0), d.get("stones"))
            if inf and re.search(EXCL, d.get("material", "")): inf = None
            if d.get("status") != "판매중": inf = None
            newwc[did] = inf
            consider("그리버", did, url, d.get("price") or price, inf, d.get("status") == "판매중",
                     {"jpTitle": d.get("jpTitle") or name, "size": d.get("size"), "rank": d.get("rank"),
                      "store": "", "img": d.get("img"), "stones": d.get("stones")})
    except Exception:
        failed.append("그리버")
    print(f"[time] greeber {time.time()-T0:.0f}s", file=sys.stderr)

    # (2b) 비슷한 디자인: 코메효·그리버 키워드 검색
    if DSN.get("on", True):
        for site, fn in (("코메효", kd), ("그리버", gd)):
            try:
                cands = {}
                for q in ("ミルグレイン ネックレス", "ミルグレイン ペンダント", "ミル打ち ネックレス"):
                    if site == "코메효":
                        code, _, t = curl("https://komehyo.jp/search/?" + urllib.parse.urlencode({"q": q, "sortKey": "01", "page": 1}))
                        if code != 200: raise RuntimeError("search")
                        for c in t.split('class="p-lists__item"')[1:]:
                            h = re.search(r'href="(/product/[\d-]+/)"', c); nm = re.search(r'productsname">(.*?)<', c, re.S)
                            pr = re.findall(r'txt--price[^"]*">￥([\d,]+)', c)
                            if h and nm and pr: cands["https://komehyo.jp" + h.group(1)] = ("k" + re.sub(r"\D", "", h.group(1)), html.unescape(nm.group(1)).strip(), num(pr[0]))
                    else:
                        code, _, t = curl("https://www.greeber.net/p/search?" + urllib.parse.urlencode({"keyword": q, "sort": "latest", "page": 1}))
                        if code != 200: raise RuntimeError("search")
                        for c in t.split("fs-c-productListItem__imageContainer")[1:]:
                            h = re.search(r'href="(/c/[a-z]+/\d+)"', c); nm = re.search(r'fs-c-productName__name">(.*?)<', c, re.S)
                            pr = re.search(r'fs-c-price__value">([\d,]+)<', c)
                            if h and nm and pr: cands["https://www.greeber.net" + h.group(1)] = ("g" + h.group(1).rsplit("/", 1)[1], html.unescape(nm.group(1)).strip(), num(pr.group(1)))
                for url, (did, name, price) in cands.items():
                    if did in dseen or did in alerted or url in existing_urls: continue
                    if not price or price > dmaxP or not gold_info(name)[0] or classify(name) not in ("목걸이", "펜던트"): continue
                    if time.time() - T0 > 470: break
                    d = fn(url)
                    if not d.get("ok"): continue
                    if d.get("status") != "판매중": continue  # 품절이면 다음에 다시 봄
                    dseen.add(did)
                    full = name + " " + (d.get("jpTitle") or "") + " " + (d.get("actual") or "")
                    if not dmatch(full): continue
                    kind = classify(name + " " + (d.get("jpTitle") or ""))
                    inf = info_from(kind, name, d.get("material"), d.get("weight"), d.get("_L", 0), d.get("stones"))
                    if inf and re.search(EXCL, d.get("material", "")): inf = None
                    add_design(site, did, url, d.get("price") or price, inf,
                               {"jpTitle": d.get("jpTitle") or name, "size": d.get("size"), "rank": d.get("rank"),
                                "store": d.get("store", ""), "img": d.get("img"), "stones": d.get("stones")})
            except Exception:
                failed.append(site + "(비슷한 디자인)")
        print(f"[time] design {time.time()-T0:.0f}s", file=sys.stderr)

    # (3) 셀비 (전체 상품 목록을 한 번에 훑음)
    try:
        for col, page in ([("necklaces", 1), ("bracelets", 1), ("pierces", 1)] if quick else [("all", p) for p in range(1, 40)]):
            if time.time() - T0 > 520: break
            ps = None
            for k in range(4):
                code, _, t = curl(f"https://www.selby.shop/collections/{col}/products.json?limit=250&page={page}")
                try:
                    ps = json.loads(t)["products"]; break
                except Exception:
                    time.sleep(8)
            if ps is None: raise RuntimeError("selby")
            if not ps:
                if col == "all": break
                continue
            time.sleep(1.5)
            for x in ps:
                ti = x["title"]
                kind = classify(ti)
                if not kind: continue
                b = text_of(x.get("body_html") or "")
                if re.search(r"中空|ホロー|パイプ", b[:3000]): continue
                pur = gold_info(ti)[0] or gold_info(b[:3000])[0]
                if not pur: continue
                price = round(float(x["variants"][0]["price"]))
                if price > maxP: continue
                w = re.search(r"重量[^\d]{0,15}([\d.]+)\s*g", b); L = re.search(r"(?:チェーンの長さ|長さ)[^\d]{0,15}([\d.]+)\s*cm", b)
                if not w: continue
                stn = re.search(r"(?:石|ダイヤ)[^ ]{0,10}[：:]\s*([^ ]{1,40})", b)
                inf = info_from(kind, ti, ti if gold_info(ti)[0] else b[:3000], w.group(1), float(L.group(1)) if L else 0,
                                (stn.group(1) if stn else "") + (" ダイヤ" if "ダイヤ" in ti else ""))
                avail = any(v.get("available") for v in x.get("variants", []))
                url = "https://www.selby.shop/products/" + x["handle"]
                if DSN.get("on", True) and avail and price <= dmaxP and dmatch(ti + " " + b[:3000]):
                    add_design("셀비", "s" + x["handle"].replace("-", ""), url, price, inf,
                               {"jpTitle": ti, "size": "", "rank": selby_rank(b, x.get("tags")), "store": "", "stones": stn.group(1) if stn else "",
                                "img": ((x.get("images") or [{}])[0].get("src", ""))})
                consider("셀비", "s" + x["handle"].replace("-", ""), url, price, inf, avail,
                         {"jpTitle": ti, "size": "", "rank": selby_rank(b, x.get("tags")), "store": "", "stones": stn.group(1) if stn else "",
                          "img": ((x.get("images") or [{}])[0].get("src", ""))})
    except Exception:
        failed.append("셀비")
    print(f"[time] selby {time.time()-T0:.0f}s", file=sys.stderr)
    crit["_dseen"] = sorted(dseen)[-800:]
    return found, best, failed, newwc


# ---------- 메인 ----------

def main():
    global A
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--out", required=True); ap.add_argument("--now", required=True)
    ap.add_argument("--auG", type=float); ap.add_argument("--ptG", type=float)
    ap.add_argument("--jpyKrw", type=float); ap.add_argument("--usdKrw", type=float)
    ap.add_argument("--no-watch", action="store_true")
    ap.add_argument("--thumbs", type=int, default=12, help="썸네일 없는 상품을 한 번에 몇 개까지 채울지")
    A = ap.parse_args()
    from datetime import datetime, timezone
    A.now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    os.makedirs(os.path.join(A.out, "writes"), exist_ok=True); os.makedirs(os.path.join(A.out, "img"), exist_ok=True)

    items = {}
    for f in glob.glob(os.path.join(A.db, "items", "*.json")):
        items[os.path.basename(f)[:-5]] = json.load(open(f))
    meta = {}
    for f in glob.glob(os.path.join(A.db, "meta", "*.json")):
        meta[os.path.basename(f)[:-5]] = json.load(open(f))

    fresh = fetch_rates(meta)
    plan = {"update": [], "set": [], "images": [], "manual": [], "pending": [], "unknown": []}
    events = {"fav": [], "new_watch": [], "similar": [], "down": [], "sold": [], "up": [], "other": []}

    def write(coll, did, data):
        p = os.path.join(A.out, "writes", f"{coll}__{did}.json")
        json.dump(data, open(p, "w"), ensure_ascii=False)
        return p

    for did, it in sorted(items.items(), key=lambda kv: kv[1].get("order") or 0):
        site = site_of(it.get("url", ""))
        if it.get("pending"):
            if site:
                d = FETCH[site](it["url"], detail=True)
                if d.get("ok"):
                    d["imgPath"] = ""
                    if d.get("img"):
                        d["imgPath"] = fetch_img(d["img"], did)
                    p = os.path.join(A.out, f"pending__{did}.json"); json.dump(d, open(p, "w"), ensure_ascii=False)
                    plan["pending"].append({"id": did, "raw": p, "url": it["url"], "site": site})
                    continue
            plan["pending"].append({"id": did, "raw": None, "url": it["url"], "site": it.get("site")})
            continue
        if not site:
            plan["manual"].append({"id": did, "url": it.get("url"), "site": it.get("site"), "price": it.get("price"),
                                   "currency": it.get("currency"), "status": it.get("status"), "fav": bool(it.get("fav"))})
            continue
        fill = site == "셀비" and not it.get("condition")
        d = FETCH[site](it["url"], detail=True) if fill else FETCH[site](it["url"])
        if not d.get("ok"):
            plan["unknown"].append(f"{it.get('site')} {it.get('title')}"); continue
        price = d["price"] if d.get("price") is not None else it.get("price")
        st = d["status"]
        orig = d.get("origPrice")
        changed = price != it.get("price") or st != it.get("status") or (orig is not None and orig != it.get("origPrice"))
        if not changed:
            if fill and d.get("rank"):
                plan["update"].append({"collection": "items", "doc_id": did, "file_path": write("items", did, {"condition": d["rank"]})})
            continue
        h = list(it.get("history") or []) + [{"t": A.now, "price": price, "status": st}]
        data = {"price": price, "status": st, "checkedAt": A.now, "history": h[-30:]}
        if orig is not None: data["origPrice"] = orig
        if fill and d.get("rank"): data["condition"] = d["rank"]
        plan["update"].append({"collection": "items", "doc_id": did, "file_path": write("items", did, data)})
        new = dict(it, **data)
        line = f"{'★ ' if it.get('fav') else ''}{it.get('site')} {it.get('title')}: " + describe(new, it.get("price"))
        if st != it.get("status"):
            line += f" · 상태 {it.get('status')} → {st}"
        line += f"\n  {it.get('url')}"
        key = "fav" if it.get("fav") else ("sold" if st == "품절" else ("down" if (price or 0) < (it.get("price") or 0) or (st == "판매중" and it.get("status") == "품절") else ("up" if (price or 0) > (it.get("price") or 0) else "other")))
        events[key].append(line)

    watch = meta.get("watch", {})
    found, best, wfail, newwc = ([], {}, [], None)
    if not A.no_watch and watch:
        urls = {it.get("url") for it in items.values()}
        max_order = max([it.get("order") or 0 for it in items.values()] + [0])
        found, best, wfail, newwc = watch_scan(urls, set(watch.get("alerted", [])), watch, max_order)
        alerted = list(watch.get("alerted", []))
        for did, doc, img, r in sorted(found, key=lambda x: {"코메효": 0, "그리버": 1, "셀비": 2}[x[1]["site"]]):
            ip = fetch_img(img, did) if img else ""
            if ip:
                th = thumb_of(ip)
                if th: doc["thumb"] = th
            plan["set"].append({"collection": "items", "doc_id": did, "file_path": write("items", did, doc)})
            if ip: plan["images"].append({"doc_id": did, "path": ip})
            alerted.append(did)
            if doc.get("similarTo"):
                events["similar"].append(f"{doc['site']} {doc['title']} · " + describe(doc) + "".join(" · " + x for x in (doc['size'], doc['store']) if x) + f"\n  {doc['url']}")
                continue
            events["new_watch"].append(f"새 입고: {doc['site']} {doc['title']} · " + describe(doc) + "".join(" · " + x for x in (doc['size'], doc['store']) if x) + f"\n  {doc['url']}")
        wdata = {"lastScan": A.now, "alerted": alerted, "wcache": newwc, "wver": 4, "maxPrice": int(watch.get("maxPrice", 80000)),
                 "criteria": "K18·K14 골드(색상 무관) 목걸이(45cm 이상)·팔찌·펜던트·귀걸이(찝는 형식 제외), 할로우(속 빈) 제품 제외, 가성비(금+원석 추정 가치 대비) 1.40배 이하, ¥80,000 이하 · 코메효 → 그리버 → 셀비"}
        wdata["design"] = dict(watch.get("design") or {}, on=(watch.get("design") or {}).get("on", True), maxPrice=int((watch.get("design") or {}).get("maxPrice", 50000)),
                               seen=watch.get("_dseen", []), ref="g197074",
                               criteria="밀그레인(ミルグレイン·ミル打ち) + 다이아 목걸이·펜던트, K18·K14(색상 무관), ¥50,000 이하, 길이 제한 없음 · 코메효·그리버·셀비")
        if best: wdata["lastBest"] = " / ".join(v[1] for k, v in sorted(best.items(), key=lambda kv: {"코메효": 0, "그리버": 1, "셀비": 2}[kv[0]]))
        plan["update"].append({"collection": "meta", "doc_id": "watch", "file_path": write("meta", "watch", wdata)})

    # 썸네일 채우기 (없는 상품만, 한 번에 A.thumbs개까지)
    budget = A.thumbs
    for did, it in sorted(items.items(), key=lambda kv: kv[1].get("order") or 0):
        if budget <= 0: break
        if it.get("thumb") or it.get("pending"): continue
        budget -= 1
        src = img_src_for(it)
        ip = fetch_img(src, did + "_t") if src else ""
        th = thumb_of(ip) if ip else ""
        if not th: continue
        ex = [u for u in plan["update"] if u["collection"] == "items" and u["doc_id"] == did]
        if ex:
            d0 = json.load(open(ex[0]["file_path"])); d0["thumb"] = th; json.dump(d0, open(ex[0]["file_path"], "w"), ensure_ascii=False)
        else:
            plan["update"].append({"collection": "items", "doc_id": did, "file_path": write("items", did, {"thumb": th})})

    # 꼭 사야 할 템: 가성비 1.10배 이하 · 상태 S·A급 · 최근 30일 가격 인하 중 2개 이상(판매중)
    events["must"] = []
    upd = {u["doc_id"]: u for u in plan["update"] if u["collection"] == "items"}
    setd = {s["doc_id"]: s for s in plan["set"]}
    for did, it in list(items.items()) + [(f[0], f[1]) for f in found]:
        if it.get("pending"): continue
        cur = dict(it)
        if did in upd: cur.update(json.load(open(upd[did]["file_path"])))
        ok, why = must_buy(cur)
        if ok == bool(it.get("mustBuy")) and (not ok or why == it.get("mustBuyWhy")): continue
        add = {"mustBuy": True, "mustBuyWhy": why} if ok else {"mustBuy": {"__delete__": True}, "mustBuyWhy": {"__delete__": True}}
        if did in setd:
            p = setd[did]["file_path"]; doc = json.load(open(p))
            if ok: doc.update(add)
            json.dump(doc, open(p, "w"), ensure_ascii=False)
        elif did in upd:
            p = upd[did]["file_path"]; data = json.load(open(p)); data.update(add); json.dump(data, open(p, "w"), ensure_ascii=False)
        else:
            plan["update"].append({"collection": "items", "doc_id": did, "file_path": write("items", did, add)})
        if ok and not it.get("mustBuy"):
            events["must"].append(f"{'★ ' if cur.get('fav') else ''}{cur.get('site')} {cur.get('title')} · " + describe(cur) + f" · {why}\n  {cur.get('url')}")

    # meta/fx·metal·run
    if fresh.get("metal"):
        plan["update"].append({"collection": "meta", "doc_id": "metal", "file_path": write("meta", "metal", {"auG": A.auG, "ptG": A.ptG, "checkedAt": A.now, "rateDate": A.metalDate, "source": "다나카귀금속 소매가(세금 포함, 1g)"})})
    if fresh.get("fx"):
        plan["update"].append({"collection": "meta", "doc_id": "fx", "file_path": write("meta", "fx", {"jpyKrw": A.jpyKrw, "usdKrw": A.usdKrw, "checkedAt": A.now, "rateDate": A.fxDate, "source": "Frankfurter (ECB)"})})
    n_changes = sum(len(v) for v in events.values())
    summary_bits = []
    for k, lab in (("must", "꼭 사야 할 템"), ("fav", "즐겨찾기 변동"), ("new_watch", "새 입고"), ("similar", "비슷한 디자인"), ("down", "인하·재판매"), ("sold", "품절"), ("up", "인상")):
        if events[k]: summary_bits.append(f"{lab} {len(events[k])}")
    run = {"lastRun": A.now}
    if n_changes: run["lastChange"] = " · ".join(summary_bits)
    plan["update"].append({"collection": "meta", "doc_id": "run", "file_path": write("meta", "run", run)})

    # 알림 초안
    msg = []
    titles = {"must": "꼭 사야 할 템 (가성비 1.10배 이하 · S·A급 · 최근 가격 인하 중 2개 이상)", "fav": "즐겨찾기", "new_watch": "새 입고 (K18·K14 목걸이·팔찌·펜던트·귀걸이, 가성비 1.40배 이하·¥8만 이하)", "similar": "비슷한 디자인 (품절된 그리버 밀그레인 다이아 목걸이와 비슷 · ¥5만 이하)", "down": "가격 인하·재판매", "sold": "품절", "up": "가격 인상", "other": "기타 변동"}
    for k in ("must", "fav", "similar", "new_watch", "down", "sold", "up", "other"):
        if events[k]:
            msg.append(f"**{titles[k]}**")
            msg += ["- " + e for e in events[k]]
            msg.append("")
    tail = []
    if plan["unknown"]: tail.append("확인 불가: " + ", ".join(plan["unknown"]))
    if wfail: tail.append("새 입고 감시 확인 불가: " + ", ".join(wfail))
    first = ([("꼭 사야 할 템: " + e) for e in events["must"]] + events["fav"] + [("비슷한 디자인: " + e) for e in events["similar"]] + events["new_watch"] + events["down"] + events["sold"] + events["up"] + events["other"] + [""])[0].split("\n")[0]
    if msg:
        msg = [first, ""] + msg + tail + ["", f"적용 환율: ¥100 = ₩{A.jpyKrw * 100:,.0f}, US$1 = ₩{A.usdKrw:,.0f} · 금 ¥{A.auG:,.0f}/g",
                                          "리스트: https://claude.ai/artifact/C2sX8ERzSBzHYC7zHyn6Yf"]
    open(os.path.join(A.out, "message.md"), "w").write("\n".join(msg))
    short = re.sub(r" · 가성비.*", "", first)[:140]
    open(os.path.join(A.out, "push.txt"), "w").write((short + (f" 외 {n_changes - 1}건" if n_changes > 1 else "")) if n_changes else "")
    plan["counts"] = {"items": len(items), "changes": n_changes, "watchFound": len(found)}
    plan["tail"] = tail
    json.dump(plan, open(os.path.join(A.out, "plan.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"items": len(items), "changes": n_changes, "watchFound": len(found), "pending": len(plan["pending"]),
                      "manual": [m["id"] for m in plan["manual"]], "unknown": plan["unknown"], "watchFail": wfail,
                      "rates": {"jpyKrw": A.jpyKrw, "usdKrw": A.usdKrw, "auG": A.auG, "ptG": A.ptG, "fxNew": bool(fresh.get("fx")), "metalNew": bool(fresh.get("metal"))},
                      "updates": [u["collection"] + "/" + u["doc_id"] for u in plan["update"]],
                      "sets": [s["doc_id"] for s in plan["set"]], "images": plan["images"]}, ensure_ascii=False))


def fetch_rates(meta):
    """환율·금 시세를 스크립트가 직접 가져온다. 실패하면 명령줄 값, 그것도 없으면 meta 값을 쓴다."""
    fresh = {}
    try:
        _, _, t1 = curl("https://api.frankfurter.dev/v1/latest?from=JPY&to=KRW,USD", timeout=20)
        _, _, t2 = curl("https://api.frankfurter.dev/v1/latest?from=USD&to=KRW", timeout=20)
        j1, j2 = json.loads(t1), json.loads(t2)
        A.jpyKrw, A.usdKrw, A.fxDate = float(j1["rates"]["KRW"]), float(j2["rates"]["KRW"]), j1.get("date", "")
        fresh["fx"] = True
    except Exception:
        pass
    try:
        _, _, t = curl("https://gold.tanaka.co.jp/commodity/souba/", timeout=30)
        au = re.search(r"金</t[dh]>\s*<td[^>]*>([\d,]+)", t); pt = re.search(r"プラチナ</t[dh]>\s*<td[^>]*>([\d,]+)", t)
        dt = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})公表", t)
        if au and pt:
            A.auG, A.ptG = float(au.group(1).replace(",", "")), float(pt.group(1).replace(",", ""))
            A.metalDate = f"{dt.group(1)}-{int(dt.group(2)):02d}-{int(dt.group(3)):02d} {dt.group(4)}" if dt else A.now[:10]
            fresh["metal"] = True
    except Exception:
        pass
    fx, mt = meta.get("fx", {}), meta.get("metal", {})
    if not fresh.get("fx"):
        A.jpyKrw = A.jpyKrw or fx.get("jpyKrw"); A.usdKrw = A.usdKrw or fx.get("usdKrw")
        if A.jpyKrw and A.usdKrw and (A.jpyKrw != fx.get("jpyKrw") or A.usdKrw != fx.get("usdKrw")):
            A.fxDate = A.now[:10]; fresh["fx"] = True
    if not fresh.get("metal"):
        A.auG = A.auG or mt.get("auG"); A.ptG = A.ptG or mt.get("ptG")
        if A.auG and A.ptG and (A.auG != mt.get("auG") or A.ptG != mt.get("ptG")):
            A.metalDate = A.now[:10]; fresh["metal"] = True
    print(f"[rates] fx={A.jpyKrw}/{A.usdKrw} ({'new' if fresh.get('fx') else 'kept'}) metal={A.auG}/{A.ptG} ({'new' if fresh.get('metal') else 'kept'})", file=sys.stderr)
    return fresh


def thumb_of(path):
    """작은 썸네일을 data: URI로 만든다 (아이패드 사파리처럼 첨부 이미지를 못 불러오는 화면용)."""
    try:
        import base64, io
        from PIL import Image
        im = Image.open(path).convert("RGB"); im.thumbnail((360, 360))
        b = io.BytesIO(); im.save(b, "JPEG", quality=72, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    except Exception:
        return ""


def img_src_for(it):
    url = it.get("url") or ""
    site = site_of(url)
    if site:
        d = FETCH[site](url, detail=True)
        return d.get("img") if d.get("ok") else ""
    if "ebay." in url or "rakuten.co.jp" in url:
        code, _, t = curl(url)
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', t or "") or re.search(r'content="([^"]+)"[^>]+property="og:image"', t or "")
        return html.unescape(m.group(1)) if m else ""
    return ""


def fetch_img(src, did):
    if src.startswith("//"): src = "https:" + src
    raw = os.path.join(A.out, "img", did + "_raw")
    out = os.path.join(A.out, "img", did + ".jpg")
    r = subprocess.run(["curl", "-sSL", "-A", UA, "--max-time", "40", "-o", raw, src], capture_output=True)
    if r.returncode != 0 or not os.path.exists(raw): return ""
    try:
        from PIL import Image
        im = Image.open(raw).convert("RGB"); im.thumbnail((800, 800)); im.save(out, "JPEG", quality=85)
        return out
    except Exception:
        return ""


A = None
if __name__ == "__main__":
    main()

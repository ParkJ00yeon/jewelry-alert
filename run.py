# -*- coding: utf-8 -*-
"""GitHub Actions에서 주얼리 가격을 확인하고 ntfy로 알림을 보낸다.
1) checker.py 실행  2) 결과를 db/ 에 반영  3) 변동이 있으면 ntfy 전송
"""
import json, os, re, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB, OUT = os.path.join(ROOT, "db"), os.path.join(ROOT, "out")
LIST_URL = "https://claude.ai/artifact/C2sX8ERzSBzHYC7zHyn6Yf"


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and v.get("__delete__") is True:
            dst.pop(k, None)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def ntfy(topic, title, body, click, priority):
    data = {"topic": topic, "title": title, "message": body, "tags": ["gem"],
            "priority": {"low": 2, "default": 3, "high": 4}[priority], "click": click}
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def unpack():
    """db.json(한 파일)을 db/ 폴더로 풀어 checker.py가 읽게 한다."""
    data = json.load(open(os.path.join(ROOT, "db.json")))
    for col, docs in data.items():
        os.makedirs(os.path.join(DB, col), exist_ok=True)
        for did, doc in docs.items():
            json.dump(doc, open(os.path.join(DB, col, did + ".json"), "w"), ensure_ascii=False)


def pack():
    data = {}
    for col in sorted(os.listdir(DB)):
        data[col] = {}
        for f in sorted(os.listdir(os.path.join(DB, col))):
            if f.endswith(".json"):
                data[col][f[:-5]] = json.load(open(os.path.join(DB, col, f)))
    json.dump(data, open(os.path.join(ROOT, "db.json"), "w"), ensure_ascii=False, indent=1)


def main():
    unpack()
    now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M+09:00")
    r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "checker.py"), "--db", DB, "--out", OUT,
                        "--now", now, "--thumbs", "0"], capture_output=True, text=True)
    sys.stderr.write(r.stderr[-3000:])
    if r.returncode != 0:
        topic = os.environ.get("NTFY_TOPIC")
        if topic and os.environ.get("ALERT_ERRORS") == "1":
            ntfy(topic, "주얼리 확인 오류", "확인 스크립트가 실패했어요. GitHub Actions 기록을 확인해 주세요.", LIST_URL, "low")
        sys.exit(1)
    print(r.stdout.strip().splitlines()[-1])

    plan = json.load(open(os.path.join(OUT, "plan.json")))
    for u in plan.get("update", []):
        p = os.path.join(DB, u["collection"], u["doc_id"] + ".json")
        cur = json.load(open(p)) if os.path.exists(p) else {}
        merge(cur, json.load(open(u["file_path"])))
        json.dump(cur, open(p, "w"), ensure_ascii=False, indent=1)
    for s in plan.get("set", []):
        doc = json.load(open(s["file_path"]))
        doc.setdefault("note", "")
        doc["note"] = (doc["note"] + " · 리스트 페이지에는 Claude 정기 확인 때 추가됨").strip(" ·")
        json.dump(doc, open(os.path.join(DB, s["collection"], s["doc_id"] + ".json"), "w"), ensure_ascii=False, indent=1)

    pack()

    msg = open(os.path.join(OUT, "message.md")).read().strip()
    push = open(os.path.join(OUT, "push.txt")).read().strip()
    topic = os.environ.get("NTFY_TOPIC")
    if msg and topic:
        lines = msg.split("\n")[2:]  # 첫 줄(요약)은 제목으로 씀
        body = re.sub(r"\*\*", "", "\n".join(lines)).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)
        if len(body.encode("utf-8")) > 3800:
            body = body.encode("utf-8")[:3700].decode("utf-8", "ignore") + "\n… (나머지는 리스트에서 확인)"
        urls = re.findall(r"https://\S+", msg)
        click = urls[0] if urls else LIST_URL
        high = msg.startswith("★") or msg.startswith("꼭 사야") or msg.startswith("비슷한 디자인")
        ntfy(topic, push or "주얼리 변동", body, click, "high" if high else "default")
        print("ntfy sent")


if __name__ == "__main__":
    main()

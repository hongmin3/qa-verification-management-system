"""B2 Generator 연동 이슈 증거 수집 (Section 9.6-8, 29.10-8).

영상 파일명 시각(녹화 시작) + 영상 길이 ± 여유 분으로 시간 창을 만들고, 아래 기본 증거 중 **로그 안의 시각**이
시간 창과 겹치는 파일만 고른다. 파일 수정 시각은 실제 기록 시각과 다를 수 있어 쓰지 않는다.

  1) 영상               Documents\\PicPick (지정한 영상)
  2) Viewer 로그        <C:|D:>\\Database\\log\\Viewer\\YYYY_MM_DD.log (UTF-16)
  3) VSS 로그           <C:|D:>\\Database\\log\\VSS.Vieworks\\Vieworks_YYYY-MM-DD-HH-MM-SS.log (세션별, 재실행 전·후 모두)
  (추가) 에뮬레이터 시리얼 로그 등 연동별 로그는 --extra로 지정할 때만 포함

쓰기 중인 파일도 공유 읽기로 복사하고, 공유폴더용 이름(01_현상요약.mp4, 02_Viewer_날짜.log,
03_VSS_재실행전_시각.log …)으로 저장한 뒤 원본·복사본 크기 비교표를 만든다.

예:
    python collect_evidence.py --video "C:\\Users\\me\\Documents\\PicPick\\2026-11-12 13 55 44.mp4" --margin 3 --out out\\25575 --label 현상요약 --restart
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (PICPICK_DIR, VIEWER_LOG_DIR, VSS_LOG_DIR, content_range, copy_shared,  # noqa: E402
                     fmt, line_times, overlaps, say, video_start_from_name, vss_session_start, window,
                     write_json, write_utf8)


def video_duration(path: Path) -> float | None:
    try:
        from _common import ensure_package
        cv2 = ensure_package("cv2", ["opencv-python-headless"])
        cap = cv2.VideoCapture(str(path))
        fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        return count / fps if fps else None
    except Exception:
        return None


def pick(args):
    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S") if args.start else video_start_from_name(args.video)
    if not start:
        raise SystemExit("[오류] 영상 파일명에서 녹화 시작 시각을 읽지 못함. --start \"YYYY-MM-DD HH:MM:SS\"를 지정하세요.")
    duration = args.duration if args.duration is not None else video_duration(args.video)
    if duration is None:
        raise SystemExit("[오류] 영상 길이를 확인하지 못함. --duration 초를 지정하세요.")
    end = start + timedelta(seconds=duration)
    w_start, w_end = window(start, end, args.margin)
    chosen, skipped = [], []

    # Viewer 로그: 날짜 파일. 창이 자정을 넘으면 두 날짜 모두 본다.
    days = {w_start.date(), w_end.date()}
    for day in sorted(days):
        p = args.viewer_dir / f"{day:%Y_%m_%d}.log"
        if not p.is_file():
            skipped.append({"kind": "Viewer", "file": str(p), "reason": "파일 없음"})
            continue
        hits = [t for t, _ in line_times(p, "viewer") if w_start <= t <= w_end]
        if hits:
            chosen.append({"kind": "Viewer", "src": p, "range": (min(hits), max(hits)), "name_key": f"{day:%Y_%m_%d}"})
        else:
            skipped.append({"kind": "Viewer", "file": str(p), "reason": "시간 창 안의 로그 줄 없음"})

    # VSS 로그: 세션 파일. 세션 시작=파일명, 끝=내용 마지막 시각.
    sessions = []
    for p in sorted(args.vss_dir.glob("Vieworks_*.log")) if args.vss_dir.is_dir() else []:
        s0 = vss_session_start(p)
        if not s0 or s0.date() < w_start.date() - timedelta(days=1):
            continue
        a, b = content_range(p, "vss")
        a = a or s0
        if overlaps(a, b, w_start, w_end):
            sessions.append({"kind": "VSS", "src": p, "range": (a, b), "name_key": f"{a:%H-%M-%S}"})
        else:
            skipped.append({"kind": "VSS", "file": p.name, "reason": f"내용 {fmt(a)}~{fmt(b)} 창 밖"})
    sessions.sort(key=lambda x: x["range"][0])
    if args.restart and len(sessions) >= 2:
        for i, s in enumerate(sessions):
            s["label"] = "재실행전" if i == 0 else ("재실행후" if len(sessions) == 2 else f"재실행후{i}")
    chosen.extend(sessions)
    if not args.vss_dir.is_dir():
        skipped.append({"kind": "VSS", "file": str(args.vss_dir), "reason": "폴더 없음"})

    # 추가 로그(예: 에뮬레이터 시리얼 로그). 연동마다 다르므로 지정한 경우에만 본다. 내용 시각으로 판단(수정 시각 무시).
    for extra in args.extra:
        if not extra.is_file():
            skipped.append({"kind": "extra", "file": str(extra), "reason": "파일 없음"})
            continue
        times = [t for kind in ("vss", "viewer", "teraterm") for t, _ in line_times(extra, kind)]
        mtime = datetime.fromtimestamp(extra.stat().st_mtime)
        if not times:
            chosen.append({"kind": "extra", "src": extra, "range": (None, None), "mtime": mtime,
                           "note": "로그 안 시각 형식을 읽지 못해 지정대로 포함"})
            continue
        a, b = min(times), max(times)
        if any(w_start <= t <= w_end for t in times):
            chosen.append({"kind": "extra", "src": extra, "range": (a, b), "mtime": mtime})
        else:
            skipped.append({"kind": "extra", "file": str(extra),
                            "reason": f"내용 {fmt(a)}~{fmt(b)} 창 밖 (수정 시각 {fmt(mtime)}는 판단에 쓰지 않음)"})
    return start, end, (w_start, w_end), chosen, skipped


def target_name(i: int, item: dict, args) -> str:
    n = f"{i:02d}"
    if item["kind"] == "video":
        return f"{n}_{args.label}{args.video.suffix}"
    if item["kind"] == "Viewer":
        return f"{n}_Viewer_{item['name_key']}.log"
    if item["kind"] == "VSS":
        mid = f"_{item['label']}" if item.get("label") else ""
        return f"{n}_VSS{mid}_{item['name_key']}.log"
    return f"{n}_{item['src'].name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, required=True, help="PicPick 영상(파일명 = 녹화 시작 시각)")
    ap.add_argument("--start", help="녹화 시작 시각 직접 지정(YYYY-MM-DD HH:MM:SS)")
    ap.add_argument("--duration", type=float, help="영상 길이(초). 생략하면 영상에서 읽음")
    ap.add_argument("--margin", type=float, default=3.0, help="앞뒤 여유(분), 기본 3")
    ap.add_argument("--out", type=Path, required=True, help="복사본·결과 폴더")
    ap.add_argument("--label", default="현상요약", help="영상 이름 내용 부분(01_<label>.mp4)")
    ap.add_argument("--restart", action="store_true", help="재실행 이슈: VSS 세션을 재실행전/재실행후로 표시")
    ap.add_argument("--viewer-dir", type=Path, default=VIEWER_LOG_DIR)
    ap.add_argument("--vss-dir", type=Path, default=VSS_LOG_DIR)
    ap.add_argument("--extra", type=Path, action="append", default=[],
                    help="추가로 넣을 로그(예: DK 에뮬레이터 Downloads\\teraterm.log). 여러 번 지정 가능")
    ap.add_argument("--no-copy", action="store_true", help="선택 결과만 출력")
    args = ap.parse_args(argv)
    if not args.video.is_file():
        cand = sorted(PICPICK_DIR.glob("*.mp4")) if PICPICK_DIR.is_dir() else []
        say(f"[오류] 영상 없음: {args.video}" + (f" / PicPick 최근: {cand[-1].name}" if cand else ""))
        return 2

    start, end, (w0, w1), chosen, skipped = pick(args)
    items = [{"kind": "video", "src": args.video, "range": (start, end)}] + chosen
    rows = []
    for i, it in enumerate(items, 1):
        name = target_name(i, it, args)
        row = {"no": i, "kind": it["kind"], "source": str(it["src"]), "target_name": name,
               "content_range": f"{fmt(it['range'][0])} ~ {fmt(it['range'][1])}"}
        if it.get("mtime"):
            row["source_mtime"] = fmt(it["mtime"])
        if not args.no_copy:
            row.update(copy_shared(it["src"], args.out / name))
        rows.append(row)

    md = ["# 증거 수집 결과", "",
          f"- 녹화: {fmt(start)} ~ {fmt(end)} (파일명 시각 = 녹화 시작)",
          f"- 시간 창: {fmt(w0)} ~ {fmt(w1)} (여유 {args.margin:g}분, 로그 안의 시각 기준)", "",
          "| No | 종류 | 원본 | 공유폴더 이름 | 내용 시각 | 원본 크기 | 복사본 크기 | 일치 | 비고 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['no']} | {r['kind']} | {Path(r['source']).name} | {r['target_name']} | {r['content_range']} | "
                  f"{r.get('source_size_before', '-')} | {r.get('copy_size', '-')} | "
                  f"{'일치' if r.get('match') else ('-' if args.no_copy else '불일치')} | "
                  f"{r.get('note', '')}{(' 수정시각 ' + r['source_mtime']) if r.get('source_mtime') else ''} |")
    if skipped:
        md += ["", "## 제외", ""] + [f"- {s['kind']}: {s['file']} — {s['reason']}" for s in skipped]
    out_md = write_utf8(args.out / "evidence.md", "\n".join(md) + "\n")
    write_json(args.out / "evidence.json", {"window": [fmt(w0), fmt(w1)], "files": rows, "skipped": skipped})
    bad = [r for r in rows if not args.no_copy and not r.get("match")]
    say(f"[완료] {len(rows)}개 선택, 제외 {len(skipped)}개, 크기 불일치 {len(bad)}개 -> {out_md}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

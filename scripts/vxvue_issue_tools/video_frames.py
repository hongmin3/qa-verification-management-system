"""B1 영상 프레임·몽타주·확대 추출 (Section 9.6-1~3).

1단계: N초(기본 3초) 간격 프레임과 시각이 찍힌 몽타주로 전체 흐름을 본다.
2단계: 화면이 바뀌거나 팝업이 뜨는 구간을 --range로 지정해 1초 이하 간격으로 다시 뽑고,
       촬영화면의 상시 확인 영역(Bucky·상태바·Dose)을 원본 해상도에서 잘라 확대한다.

PicPick 파일명 시각(`2026-11-12 13 55 44.mp4`)을 녹화 시작 시각으로 보고 프레임마다 실제 시각을 붙인다.

예:
    python video_frames.py "D:\\영상\\2026-11-12 13 55 44.mp4" --out out\\v1
    python video_frames.py 영상.mp4 --out out\\v1 --range 40-75 --fine 0.5 --crop bucky status dose
    python video_frames.py 영상.mp4 --out out\\v1 --range 00:40-01:15 --crop 10,60,300,120
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ensure_package, fmt, say, video_start_from_name, write_json, write_utf8  # noqa: E402

# 촬영화면 상시 확인 영역. 화면 크기 비율(x, y, w, h)로 정의해 해상도가 달라도 같은 위치를 자른다.
# 2026-11-12 DK INNOGEN 재실행 영상(1920x1080 VXvue 촬영화면)에서 실측한 위치다.
CROP_PRESETS = {
    "bucky": (0.000, 0.195, 0.160, 0.070),   # 좌상단 Bucky 아이콘·선택 테두리(Bucky 1/2)
    "status": (0.000, 0.944, 1.000, 0.056),  # 하단 상태바(Ready·CR mode·Dose/Acc·시각)
    "dose": (0.000, 0.063, 0.955, 0.127),    # 상단 Generator 조작부(kVp·mA·mAs·ms·Dose Mode·Focus)
}


def parse_seconds(text: str) -> float:
    parts = [float(p) for p in text.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_range(text: str):
    a, b = text.split("-", 1)
    return parse_seconds(a), parse_seconds(b)


def parse_crop(token: str, width: int, height: int):
    if token in CROP_PRESETS:
        fx, fy, fw, fh = CROP_PRESETS[token]
        return token, (int(fx * width), int(fy * height), int(fw * width), int(fh * height))
    x, y, w, h = (int(v) for v in token.split(","))
    return f"crop_{x}_{y}_{w}_{h}", (x, y, w, h)


def label_of(start, sec: float) -> str:
    rel = f"+{int(sec // 60):02d}:{sec % 60:04.1f}"
    return f"{(start + timedelta(seconds=sec)).strftime('%H:%M:%S')} ({rel})" if start else rel


def grab(cv2, cap, sec: float):
    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000.0)
    ok, frame = cap.read()
    return frame if ok else None


def stamp(cv2, img, text: str):
    out = img.copy()
    cv2.rectangle(out, (0, 0), (min(out.shape[1], 12 + 14 * len(text)), 34), (0, 0, 0), -1)
    cv2.putText(out, text, (6, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
    return out


def montage(cv2, np, frames, cols: int, thumb_w: int):
    if not frames:
        return None
    h0, w0 = frames[0][1].shape[:2]
    thumb_h = int(h0 * thumb_w / w0)
    tiles = [stamp(cv2, cv2.resize(f, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA), lab) for lab, f in frames]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    return np.vstack(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="결과 폴더")
    ap.add_argument("--interval", type=float, default=3.0, help="1단계 몽타주 간격(초), 기본 3")
    ap.add_argument("--range", dest="ranges", action="append", default=[],
                    help="2단계 구간 START-END (초 또는 MM:SS). 여러 번 지정 가능")
    ap.add_argument("--fine", type=float, default=1.0, help="2단계 추출 간격(초), 기본 1 (1 이하 권장)")
    ap.add_argument("--crop", nargs="*", default=[],
                    help=f"확대 영역: {', '.join(CROP_PRESETS)} 또는 x,y,w,h(픽셀). 2단계 프레임에 적용")
    ap.add_argument("--scale", type=float, default=3.0, help="확대 배율, 기본 3")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--thumb", type=int, default=480, help="몽타주 썸네일 너비(px)")
    ap.add_argument("--start", help="녹화 시작 시각(YYYY-MM-DD HH:MM:SS). 생략하면 파일명에서 읽음")
    args = ap.parse_args(argv)

    cv2 = ensure_package("cv2", ["opencv-python-headless"])
    import numpy as np

    from datetime import datetime
    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S") if args.start else video_start_from_name(args.video)
    # cv2는 비ASCII 경로를 못 여는 경우가 있어 바이트 경로 대신 원본 경로를 시도하고, 실패하면 임시 사본을 쓴다.
    cap = cv2.VideoCapture(str(args.video))
    tmp_copy = None
    if not cap.isOpened():
        import shutil
        import tempfile
        tmp_copy = Path(tempfile.gettempdir()) / ("vxvue_video" + args.video.suffix)
        shutil.copyfile(args.video, tmp_copy)
        cap = cv2.VideoCapture(str(tmp_copy))
    if not cap.isOpened():
        say(f"[오류] 영상을 열 수 없음: {args.video}")
        return 2
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = count / fps if fps else 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    args.out.mkdir(parents=True, exist_ok=True)

    def write_img(path: Path, img):
        ok, buf = cv2.imencode(".png", img)  # imwrite는 한글 경로에서 실패할 수 있다
        if ok:
            path.write_bytes(buf.tobytes())
        return path

    # 1단계
    frames, sec = [], 0.0
    while sec <= duration:
        f = grab(cv2, cap, sec)
        if f is not None:
            frames.append((label_of(start, sec), f))
        sec += args.interval
    stage1 = []
    per_sheet = args.cols * 4
    for i in range(0, len(frames), per_sheet):
        sheet = montage(cv2, np, frames[i:i + per_sheet], args.cols, args.thumb)
        stage1.append(str(write_img(args.out / f"montage_{args.interval:g}s_{i // per_sheet + 1:02d}.png", sheet)))

    # 2단계
    stage2 = []
    crops = [parse_crop(c, width, height) for c in args.crop]
    for r in args.ranges:
        a, b = parse_range(r)
        folder = args.out / f"range_{int(a)}-{int(b)}s"
        folder.mkdir(exist_ok=True)
        fine, sec = [], a
        while sec <= min(b, duration):
            f = grab(cv2, cap, sec)
            if f is not None:
                lab = label_of(start, sec)
                tag = f"{sec:07.2f}s"
                fine.append((lab, f))
                item = {"sec": round(sec, 2), "time": lab, "frame": str(write_img(folder / f"frame_{tag}.png", f)), "crops": {}}
                for name, (x, y, w, h) in crops:
                    part = f[max(0, y):y + h, max(0, x):x + w]
                    if part.size:
                        big = cv2.resize(part, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_CUBIC)
                        item["crops"][name] = str(write_img(folder / f"{name}_{tag}.png", big))
                stage2.append(item)
            sec += args.fine
        sheet = montage(cv2, np, fine, args.cols, args.thumb)
        if sheet is not None:
            write_img(folder / f"montage_{args.fine:g}s.png", sheet)

    cap.release()
    if tmp_copy:
        tmp_copy.unlink(missing_ok=True)
    manifest = {
        "video": str(args.video), "recording_start": fmt(start) if start else "파일명에서 확인 못 함",
        "start_source": "옵션 --start" if args.start else "PicPick 파일명(녹화 시작 시각)",
        "fps": round(fps, 3), "frames": int(count), "duration_sec": round(duration, 2),
        "resolution": f"{width}x{height}",
        "stage1": {"interval_sec": args.interval, "frames": len(frames), "montages": stage1},
        "stage2": {"ranges": args.ranges, "interval_sec": args.fine,
                   "crops": {n: r for n, r in crops}, "frames": stage2},
        "limitations": ["샘플 프레임 확인이며 연속 재생 확인이 아님(Section 9.6-1)",
                        "작은 몽타주 글자로 문구를 확정하지 말고 확대·원본 프레임으로 읽을 것(Section 9.6-2)"],
    }
    write_json(args.out / "manifest.json", manifest)
    lines = [f"# 영상 프레임 추출 결과", "", f"- 영상: {args.video}", f"- 녹화 시작: {manifest['recording_start']} ({manifest['start_source']})",
             f"- 길이: {manifest['duration_sec']}초, {manifest['resolution']}, {manifest['fps']}fps",
             f"- 1단계: {args.interval:g}초 간격 {len(frames)}프레임 → 몽타주 {len(stage1)}장",
             f"- 2단계: 구간 {', '.join(args.ranges) or '없음'} / {args.fine:g}초 간격 {len(stage2)}프레임, 확대 {', '.join(n for n, _ in crops) or '없음'}"]
    write_utf8(args.out / "summary.md", "\n".join(lines) + "\n")
    say(f"[완료] {args.out / 'summary.md'} (1단계 {len(frames)}프레임, 2단계 {len(stage2)}프레임)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

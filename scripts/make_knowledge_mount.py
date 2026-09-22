"""지식 폴더 마운트 설정을 생성한다 (fstab 라인 + systemd Environment + 검증 명령).

한글 폴더명과 공백 때문에 손으로 쓰면 틀리기 쉽다. `fstab` 은 공백을 `\\040` 으로 써야 하고,
`iocharset=utf8` 이 없으면 한글 파일명이 깨져 **모든 파일이 분류되지 않는다.** 이 스크립트가
그 변환을 대신한다.

    python scripts/make_knowledge_mount.py                       # 등록된 전 제품
    python scripts/make_knowledge_mount.py --product VXvue
    python scripts/make_knowledge_mount.py --share "//10.13.0.5/qa" --product VXvue

`--share` 를 주면 그 공유 아래로 경로를 만들고, 주지 않으면 제품 설정의 `knowledge_source.dir`
에서 폴더 이름만 가져와 `//<서버>/<공유>` 자리표를 남긴다.

앱은 이 폴더를 **읽기만** 하므로 `ro` 로 마운트한다.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.core.product_config import list_product_configs  # noqa: E402
from app.core.product_knowledge import product_slug, resolve_config  # noqa: E402

FSTAB_SPACE = "\\040"
CREDENTIALS_FILE = "/etc/qa-knowledge.cred"


def escape_fstab(path: str) -> str:
    """`fstab` 이 요구하는 이스케이프. 공백·탭이 필드 구분자이므로 8진 표기로 바꾼다."""
    return path.replace("\\", "/").replace(" ", FSTAB_SPACE).replace("\t", "\\011")


def env_var_name(product: str) -> str:
    """제품명 → 환경변수 이름. `Bellalun Viewer` → `QA_KNOWLEDGE_DIR_BELLALUN_VIEWER`."""
    return "QA_KNOWLEDGE_DIR_" + product_slug(product).replace("-", "_").upper()


def remote_path(source_dir: str, share: str) -> str:
    """PC 의 로컬 경로에서 폴더 이름만 취해 공유 경로를 만든다.

    로컬 경로 전체를 그대로 쓰지 않는다 — 서버의 공유 구조는 PC 의 디렉터리 구조와 다르다.
    제품 폴더와 그 부모(제품 이름) 두 단계만 옮긴다. 실제 공유 구조가 다르면 사용자가 고친다.
    """
    normalized = unicodedata.normalize("NFC", source_dir)
    pure = PureWindowsPath(normalized.replace("/", "\\"))
    # 드라이브(`C:`)와 anchor(`C:\`)를 빼야 서버 경로에 `C:` 가 섞이지 않는다.
    parts = [part for part in pure.parts if part not in (pure.drive, pure.anchor, "\\", "/")]
    tail = parts[-2:]
    base = share.rstrip("/") if share else "//<서버>/<공유>"
    return str(PurePosixPath(base, *tail)) if tail else base


def mount_point(product: str) -> str:
    return f"/srv/knowledge/{product_slug(product)}"


def render(product: str, source_dir: str, share: str, app_user: str) -> str:
    target = mount_point(product)
    remote = remote_path(source_dir, share)
    lines = [
        f"# ===== {product} =====",
        f"# PC 경로 : {source_dir or '(설정되지 않음)'}",
        f"# 마운트  : {target}",
        "",
        "# ① 마운트 지점",
        f"sudo mkdir -p {target}",
        "",
        "# ② /etc/fstab 에 한 줄 추가 (공백은 \\040, 한글이 깨지지 않게 iocharset=utf8)",
        f"{escape_fstab(remote)}  {target}  cifs  "
        f"ro,credentials={CREDENTIALS_FILE},iocharset=utf8,uid={app_user},file_mode=0444,dir_mode=0555,_netdev  0  0",
        "",
        "# ③ 자격증명 파일 (권한 600)",
        f"#   sudo tee {CREDENTIALS_FILE} > /dev/null <<'CRED'",
        "#   username=<계정>",
        "#   password=<비밀번호>",
        "#   domain=<도메인>",
        "#   CRED",
        f"#   sudo chmod 600 {CREDENTIALS_FILE}",
        "",
        "# ④ 마운트하고 한글 파일명이 정상인지 먼저 확인",
        f"sudo mount {target} && ls -1 {target} | head",
        "",
        "# ⑤ 앱에 경로 알려주기 (systemd)",
        f"#   sudo systemctl edit qa-verification   →   Environment={env_var_name(product)}={target}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="지식 폴더 마운트 설정 생성")
    parser.add_argument("--product", action="append", default=[], help="제품명 또는 slug (여러 번 지정 가능)")
    parser.add_argument("--share", default="", help='공유 루트. 예: "//10.13.0.5/qa"')
    parser.add_argument("--app-user", default="<앱계정>", help="앱을 실행하는 OS 계정 (uid 옵션에 들어간다)")
    args = parser.parse_args()

    products = args.product or [config.product for config in list_product_configs() if config.knowledge_source.dir]
    if not products:
        print("config/products/ 에 knowledge_source.dir 이 설정된 제품이 없습니다.")
        return 1

    print("# 지식 폴더 마운트 설정 (생성됨 — 서버에서 실행)")
    print("# 앱은 이 폴더를 읽기만 하므로 ro 로 마운트합니다.")
    print("#")
    print("# 한글 파일명이 깨져도 앱이 NFD 정규화와 mojibake 복구를 시도하지만, 그때는")
    print("# 결과에 '마운트 옵션(iocharset=utf8)을 확인하세요' 메모가 남습니다 — 근본 원인을 고치세요.")
    print()
    for product in products:
        config = resolve_config(product)
        if config is None:
            print(f"# [건너뜀] '{product}' 제품 설정이 없습니다.\n")
            continue
        print(render(config.product, config.knowledge_source.dir, args.share, args.app_user))

    print("# ===== 확인 =====")
    print("# 1. 파일명이 정상 표시되는지:  ls -1 /srv/knowledge/<slug>")
    print("# 2. 앱이 인식하는지:          curl -s 'localhost:24357/qa-agent/readiness?product=VXvue'")
    print("# 3. 분류 결과와 제외 이유:     curl -s 'localhost:24357/knowledge/source/VXvue'")
    print("# 4. 수집 실행:                /knowledge 화면의 '지금 수집'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

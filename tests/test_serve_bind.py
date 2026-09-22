"""listen 포트의 단일 원본이 `config.yaml` 이라는 것을 지키는 테스트.

Validates: REQ-DEPLOY-001

세 가지를 분리해서 본다. 무엇을 증명하고 무엇을 증명하지 못하는지 헷갈리면 이 파일이
"포트가 맞다"는 착각을 주는 쪽으로 쓰이게 된다.

1. `app_bind()` 가 실제로 `config.yaml` 을 읽는가 — 임시 루트에 다른 포트를 적은
   config.yaml 을 놓고 진짜 `build_settings()` 를 통과시켜 확인한다. 이건 동작 검증이다.
2. 배포 산출물이 자기 포트를 따로 들고 있지 않은가 — 텍스트 검사다. 이 검사는 파일에
   포트 리터럴이 없다는 것만 증명하지, 서버가 그 포트에 뜬다는 것은 증명하지 않는다.
3. nginx upstream 포트가 `config.yaml` 과 같은가 — 역시 텍스트 대조다. nginx 는 YAML 을
   읽지 못해 자동으로 따라올 수 없으므로, 둘이 갈라지는 순간을 여기서 잡는다.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from app.core.config import DEFAULT_APP_HOST, DEFAULT_APP_PORT, app_bind, app_self_url, build_settings

ROOT = Path(__file__).resolve().parents[1]


def _config_port() -> int:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return int(raw["app"]["port"])


def _settings_with(tmp_path: Path, **app_overrides) -> object:
    """실제 `config.yaml` 을 임시 루트로 복사하고 `app:` 절만 바꿔 진짜 설정을 만든다.

    합성 dict 를 흉내 내지 않고 `build_settings()` 를 그대로 통과시킨다 — 그래야 YAML
    파싱과 키 경로까지 함께 검증된다.
    """
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["app"].update(app_overrides)
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return build_settings(root=tmp_path)


def test_app_bind_reads_the_port_from_config_yaml(tmp_path):
    """config.yaml 을 고치면 바인딩 주소가 따라온다 — 이 성질이 이 변경의 전부다."""
    settings = _settings_with(tmp_path, host="127.0.0.5", port=24358)
    assert app_bind(settings) == ("127.0.0.5", 24358)


def test_app_bind_falls_back_only_when_the_key_is_absent(tmp_path):
    settings = _settings_with(tmp_path, host="", port="")
    assert app_bind(settings) == (DEFAULT_APP_HOST, DEFAULT_APP_PORT)


def test_app_bind_refuses_a_non_numeric_port(tmp_path):
    """오타 난 포트는 조용히 기본값으로 떨어지지 않는다 — 엉뚱한 자리에 뜨는 것보다 낫다."""
    settings = _settings_with(tmp_path, port="24357번")
    with pytest.raises(ValueError):
        app_bind(settings)


def test_app_self_url_uses_loopback_with_the_configured_port(tmp_path):
    settings = _settings_with(tmp_path, host="0.0.0.0", port=24359)
    assert app_self_url(settings) == "http://127.0.0.1:24359"


@pytest.mark.parametrize(
    "artifact",
    ["scripts/run.sh", "scripts/run.ps1", "deploy/systemd/qa-verification.service"],
)
def test_launchers_do_not_carry_their_own_port(artifact):
    """진입점이 포트를 직접 들면 config.yaml 이 다시 장식이 된다.

    텍스트 검사다 — 서버가 어느 포트에 뜨는지는 증명하지 않는다. 증명하는 것은
    "이 파일에 앱을 바인딩하는 포트 지정이 없다" 뿐이고, 그게 여기서 막고 싶은 회귀다.
    """
    text = (ROOT / artifact).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--port" not in body, f"{artifact} 가 uvicorn 포트를 직접 지정하고 있다"
    assert "app.serve" in body, f"{artifact} 가 python -m app.serve 로 앱을 띄우지 않는다"


def test_nginx_upstream_matches_config_yaml():
    """nginx 는 YAML 을 읽지 못하므로 자동으로 따라오지 않는다. 갈라지면 502 다."""
    conf = (ROOT / "deploy/nginx/qa-platform.conf").read_text(encoding="utf-8")
    block = re.search(r"upstream\s+qa_verification_app\s*\{(.*?)\}", conf, re.S)
    assert block, "qa_verification_app upstream 블록을 찾지 못했다"
    ports = re.findall(r"server\s+127\.0\.0\.1:(\d+)\s*;", block.group(1))
    assert ports == [str(_config_port())], (
        f"nginx upstream 포트 {ports} 가 config.yaml 의 app.port"
        f" {_config_port()} 와 다르다"
    )

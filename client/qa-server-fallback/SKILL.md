---
name: qa-server-fallback
description: QA 서버(10.13.0.222) 장애 때 ALM·PLM·VXvue·XIPL·벨라룬 Skill을 로컬 자료와 기존 로컬 경로로 전환하는 공통 규칙과 명령. 서버 조회가 실패하거나 서버 도구(vxvue-ai·bellalun_·xipl_)가 보이지 않을 때, 또는 위 Skill의 "qa-server-fallback" 안내를 만났을 때 사용한다.
---
<!-- qa-server-fallback-managed: 원본은 qa-verification-management-system/client/qa-server-fallback. 이 폴더를 직접 고치지 말고 원본을 고쳐 install로 다시 설치한다. -->

# QA 서버 장애 시 로컬 전환

ALM·PLM·VXvue·XIPL·Bellalun 작업의 서버 장애 대응 규칙은 이 파일 하나에 둔다. 각 Skill 끝의 `qa-server-fallback` 블록은 이 파일을 가리키기만 한다. 이 파일의 규칙은 각 Skill의 "실패하면 중단·로컬 전환 전에 사용자에게 묻는다", "로컬 사본으로 임의 대체하지 않는다" 문장보다 **서버 장애로 판정된 경우에 한해** 우선한다. 사용자가 2026-11-24 요청으로 서버 장애 때의 로컬 전환을 미리 승인했으므로 장애 판정 뒤에는 다시 묻지 않는다.

명령의 Python은 `{{PYTHON}}`이다(`py` 런처는 쓰지 않는다). 아래 `$fb`는 `"{{PYTHON}}" -X utf8 "{{SKILL_DIR}}/scripts/qa_fallback.py"`를 뜻한다.

## 1. 작업마다 먼저 서버를 쓴다

1. 새 작업은 평소처럼 해당 Skill의 서버 경로로 시작한다(ALM·PLM `knowledge.py status`, VXvue `get_status`, Bellalun `bellalun_get_status`, XIPL `xipl_get_status`).
2. 서버 조회가 연결 실패·timeout·5xx로 실패하거나, 서버 MCP 도구가 세션에 없으면 `$fb probe`를 한 번 실행한다. 재시도·시간 상한은 명령이 지킨다(기본 3회, 1초 간격, 시도당 5초, 전체 30초). 명령 밖에서 같은 조회를 반복하거나 기다리지 않는다.
3. 결과의 `decision.<제품>`에 따른다.

교육자료 서버와 운영 ALM·Teamcenter 연결은 별개로 확인한다. Native pipe·기존 Edge 제어 오류는 브라우저 도구 연결 문제이며 QA 서버 장애 판정으로 대체하지 않는다. 정상 로그인된 실제 탭·주소·객체를 읽을 수 없으면 지원된 재연결을 확인하고 쓰기를 중단한다. 조사만 할 때는 `$fb probe --no-repair`를 사용해 설치본을 변경하지 않는다.

| decision | 할 일 |
|---|---|
| `server` | 서버 경로를 계속 쓴다. 서버 MCP 도구가 세션에 없다면 서버 장애가 아니라 연결 문제다. 사용자에게 `/mcp` 재연결(Claude Code) 또는 CLI 재시작을 안내하고, 로컬 전환은 사용자가 원할 때만 한다 |
| `local` | 묻지 않고 로컬 모드로 바꾼다(아래 2~4절) |
| `stop` | 인증 실패(`auth_error`)·무결성 오류(`integrity_error`)·서버 오류(`server_error`)다. 로컬로 우회하지 않는다. `targets`의 `state`·`detail`을 그대로 알리고 멈춘다 |

서버 MCP 호출이 401·403·인증·권한 오류를 돌려주면 probe 결과와 관계없이 `stop`으로 다룬다. 해시 불일치·`read_complete: false`도 같다.

## 2. 로컬 모드 공통 규칙

- 정기 보고서 작성·기존 TC/문서 갱신에는 [기존 보고서·기록 변경 전후 검사](references/legacy-validation.md)와 이 Skill에 포함된 `scripts/qa_document_guard.py`를 사용한다. 동일 제품 승인본·실제 시험 증거·허용 변경·전체 대조 원칙은 서버 모드와 같다. 제품의 공용 모듈 지원 조건은 제품별 근거에서 확인한다.

- 답의 맨 앞에 한 줄로 적는다: `로컬 모드 — QA 서버 장애 확인(<probe checked_at>, <state>). 사용 자료: <제품> 로컬 판 <snapshot> (<자료 기준일>)`.
- `$fb local status --product <제품>`을 먼저 실행해 파일·판 표기·수정일·SHA256, `missing`(있어야 하는데 없는 자료), `not_local`(로컬에 원래 없는 자료), `excluded`(지난 판 폴더)를 확인하고 답에 필요한 만큼 밝힌다.
- 한 작업에서는 처음 정한 모드와 같은 `snapshot`만 쓴다. 후속 명령의 `snapshot`이 바뀌면 원문이 바뀐 것이니 알리고 새로 검색한다. 작업 도중 서버가 돌아와도 서버 결과를 섞지 않는다. 서버 기준 답이 필요하면 작업을 처음부터 서버 모드로 다시 한다.
- 로컬 자료로 확인할 수 없는 것은 `서버 장애로 미확인`으로 적는다. 서버 자료가 더 최신일 수 있으므로 로컬 결과를 "최신 서버 기준"이라고 말하지 않는다.
- 빠진 서버 자료를 인터넷 검색·제조사 일반 문서·기억으로 채우지 않는다. 사내 절차·설정·사양은 서버 자료나 이 PC의 로컬 자료로만 답한다. 사용자가 일반 제품 설명을 따로 원하면 `사내 교육자료 아님`으로 나눠 적는다.
- 검색 없음은 기능 없음이 아니다. 근거는 원본 파일명 + PDF 물리 페이지 / DOCX 문단·표 / XLSX 시트·행으로 남긴다.

## 3. 로컬 자료 명령 (VXvue·XIPL·Bellalun)

```text
$fb local status   --product vxvue
$fb local search   --product vxvue --query "Reject" --query "거부" [--file "(사양서)"] [--limit 20] [--mode all]
$fb local get      --product vxvue --id <검색 결과 id> [--neighbors 2]
$fb local page     --product vxvue --file "<파일명 일부>" --page <PDF 물리 페이지>
$fb local revisions --product xipl --file "XIPL User Manual"
```

`--product`는 `vxvue`·`xipl`·`bellalun`이다. 검색 엔진은 Bellalun 로컬 Skill의 `knowledge.py`를 그대로 쓰고, 제품마다 지식 폴더와 캐시를 나눈다. 같은 제품의 명령은 순차 실행한다.

## 4. 제품별 로컬 범위

**ALM** — 교육자료 서버 장애는 운영 ALM 작업을 막지 않는다. 이슈 등록·수정·댓글은 `alm` Skill의 [개인 PAT 절차](../alm/references/local-issue-api.md), TC·Test Run·LiveDoc·Import는 [로그인 브라우저 절차](../alm/references/browser-operations.md)를 그대로 쓴다. 운영 Polarion 자체의 401·403은 장애가 아니므로 멈춘다. 교육자료의 장·슬라이드·영상이 근거로 필요한 판단은 `교육자료 미확인(서버 장애)`로 남기고 절차를 추측하지 않는다. 설치본 `references/`의 문서는 사용할 수 있다.

**PLM** — 로그인된 Teamcenter 브라우저와 `plm` Skill의 `references/browser-operations.md`·`work-examples.md`로 작업한다. 최신 교육자료(장·P 슬라이드·영상·샘플 D1/D2)가 필요한 판단은 `교육자료 미확인(서버 장애)`로 표시하고, 확인 전 확정하지 않는다.

**VXvue** — `--product vxvue`로 사양서·매뉴얼·TC를 검색한다. 업무 규칙은 지식 폴더의 `[Skill 지침] VXvue.md`와 `[QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드.md`를 `local get`이나 직접 읽기로 전부 읽고 적용한다(서버 `get_workflow` 대신). Polarion VP 상세·필터, VXvue Redmine, Generator 근거, 매뉴얼 검토 보고서 도구는 서버 전용이라 `서버 장애로 미확인`이다.

**XIPL** — `--product xipl`로 로컬 XIPL User Manual(DOCX)만 조회한다. XIPL.STUDIO 문서와 서버의 개정 이력은 로컬에 없으므로 `조회하지 못함`으로 적는다. XIPL 공용 기능 설명과 VXvue·Bellalun의 제품별 지원·라이선스·UI 적용 조건을 나눠 쓰고, 제품별 조건은 `--product vxvue` 또는 `--product bellalun` 원문으로 따로 확인한다. 공용 매뉴얼로 제품 지원을 추론하지 않는다.

**Bellalun** — `bellalun-local-knowledge` Skill(호출명 `bellalun-ai`)과 로컬 `bellalun-ai` MCP로 바꾼다. 그 Skill의 SKILL.md와 `references/bellalun-qa-guide.md`의 작업 유형 section을 읽고, 검색은 그 Skill의 절차 또는 `$fb local ... --product bellalun`(같은 엔진·같은 캐시)을 쓴다. Bellalun Redmine은 로컬 `scripts/redmine.py`(별도 Redmine 서버)로 조회할 수 있다. 서버 경유 Polarion VP 조회는 `서버 장애로 미확인`이다. 기존 Junction과 공통 원본 구조는 건드리지 않는다.

## 5. 서버 복구

다음 작업은 다시 1절부터 시작한다. 장애 상태를 저장해 두고 계속 로컬을 쓰지 않는다. `probe`가 `server`를 돌려주면 서버 우선으로 돌아간다.

## 6. 설치 상태

`$fb check`는 두 CLI의 공통 Skill·각 Skill 끝 블록·전역 지침 블록이 맞는지 보고한다. `probe`는 서버 Skill 갱신으로 사라진 블록을 백업 후 다시 붙이고 `overlay_repair`에 적는다. 원본 수정과 재설치는 저장소의 `client/qa-server-fallback`에서 `install`로 한다.

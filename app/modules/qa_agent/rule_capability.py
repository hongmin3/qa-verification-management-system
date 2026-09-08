"""규칙 절별 구현 방식 표 — "이 규칙을 자동화로 어디까지 할 수 있는가"의 답을 코드로 둔다.

`[QA 작성 규칙] <제품> TC 설계 및 자체검토 가이드`의 절마다 다음을 기록한다.

- `mode`   — 무엇으로 구현하는가 (결정적 코드 / 코드+LLM / QA 입력 / 자동화 대상 아님)
- `status` — 지금 구현돼 있는가
- `where`  — 구현 위치 (status=IMPLEMENTED 면 실제 파일이 있어야 한다. 테스트가 확인한다)

이 표를 문서(산문) 대신 코드로 두는 이유는 두 가지다.

1. 규칙 Rev 가 올라가 새 절이 생기면 `tests/test_rule_capability.py` 가 깨진다. 새 규칙이
   조용히 미구현으로 남는 것을 막는다.
2. `status=IMPLEMENTED` 인데 `where` 파일이 없으면 테스트가 깨진다. "구현했다"는 주장과
   실제 코드가 어긋나지 않게 한다.

**중요 — 규칙 §55 자동화 금지 원칙.** 규칙 자체가 자동으로 확정하면 안 되는 것을 정해 두었다
(Issue 자동 Close, 근거 없는 SRS 자동 연결, TC 이력 자동 삭제, 필수 관찰 수단 없이 Pass,
QA 승인 없이 기존 TC 덮어쓰기, 근거 불충분 상태의 Expected 자동 생성). 그래서 "규칙 전체
구현"은 "규칙 전체 자동 판정"이 아니다. 조사·비교·초안까지가 자동화 범위이고 확정은 QA다.
그 경계를 `mode=DRAFT_ONLY` 로 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --- 구현 방식 ---------------------------------------------------------------

#: 결정적 코드로 완전 구현. LLM 을 부르지 않는다 (토큰 0).
MODE_CODE = "CODE"
#: 검색·비교·검증은 코드가 하고, 의미 판단만 LLM 이 한다.
MODE_CODE_LLM = "CODE+LLM"
#: QA 가 화면에서 값을 줘야 성립한다 (환경, 수행 가능 범위, Test Data 등).
MODE_QA_INPUT = "QA_INPUT"
#: 초안·후보까지만 만들고 확정은 QA 가 한다 (규칙 §55 가 자동 확정을 금지한 항목).
MODE_DRAFT_ONLY = "DRAFT_ONLY"
#: 런타임 자동화 대상이 아니다 (규칙 설계자용 체크리스트, 변경 이력, 운영 프로세스).
MODE_OUT_OF_SCOPE = "OUT_OF_SCOPE"

MODE_LABELS = {
    MODE_CODE: "결정적 코드",
    MODE_CODE_LLM: "코드 + LLM 판정",
    MODE_QA_INPUT: "QA 입력 필수",
    MODE_DRAFT_ONLY: "초안까지만 (확정은 QA)",
    MODE_OUT_OF_SCOPE: "런타임 자동화 대상 아님",
}

# --- 구현 상태 ---------------------------------------------------------------

STATUS_IMPLEMENTED = "IMPLEMENTED"
STATUS_PLANNED = "PLANNED"
STATUS_NOT_PLANNED = "NOT_PLANNED"

STATUS_LABELS = {
    STATUS_IMPLEMENTED: "구현됨",
    STATUS_PLANNED: "예정",
    STATUS_NOT_PLANNED: "구현 안 함",
}


@dataclass(frozen=True)
class RuleCapability:
    section: str
    title: str
    mode: str
    status: str
    where: str = ""
    note: str = ""

    @property
    def paths(self) -> list[str]:
        """`where` 에 적힌 저장소 상대 경로들. `path::symbol` 형태에서 경로만 뽑는다."""
        return [part.split("::")[0].strip() for part in self.where.split(",") if part.strip()]


def _cap(section: str, title: str, mode: str, status: str, where: str = "", note: str = "") -> tuple[str, RuleCapability]:
    return section, RuleCapability(section=section, title=title, mode=mode, status=status, where=where, note=note)


# 규칙 Rev1.12 기준 56개 최상위 절. 절 번호는 제품 규칙 문서의 최상위 번호를 그대로 쓴다.
RULE_CAPABILITY: dict[str, RuleCapability] = dict(
    [
        _cap("1", "전체 Agentic Workflow", MODE_CODE, STATUS_PLANNED,
             note="19단계 절차를 파이프라인 단계로 옮긴다. §54가 정한 10단계 순서를 상위 골격으로 삼는다."),
        _cap("2", "Agent Skill Architecture", MODE_CODE, STATUS_PLANNED,
             note="Skill 을 '업무 책임의 경계'로 두는 구조. Skill 레지스트리로 구현한다."),
        _cap("3", "Skill Registry (S01~S11)", MODE_CODE_LLM, STATUS_PLANNED,
             note="MVP 는 S01/S02/S03/S05. 나머지는 레지스트리에 등록만 하고 미구현으로 보고한다."),
        _cap("4", "작업 범위 제한", MODE_CODE, STATUS_PLANNED,
             note="요청에 범위(Sheet/버전/파일)가 있으면 그 범위만 조사하고 '제한된 범위 기준'을 결과에 표시한다."),
        _cap("5", "작업 전 필수 정보 확인 Gate", MODE_QA_INPUT, STATUS_PLANNED,
             note="G1 이 필수 항목 충족을 코드로 판정하되, 값 자체(환경·수행 가능 범위·Test Data)는 QA 가 입력한다."),
        _cap("6", "Issue 산출물 유형 결정 Gate", MODE_CODE, STATUS_PLANNED,
             note="Polarion rndReviewResult 가 유형과 1:1 대응한다(lab_fixed→Program Fixed 등). LLM 없이 분류된다."),
        _cap("7", "정보 우선순위", MODE_CODE, STATUS_PLANNED,
             note="6단 우선순위를 근거 랭킹 함수로 구현. Expected 근거 채택 순서를 코드가 정한다."),
        _cap("8", "근거 수준", MODE_CODE, STATUS_PLANNED,
             note="Evidence 에 근거 수준(공식 사양/매뉴얼/기존 TC/실행 결과)을 필드로 남긴다."),
        _cap("9", "문서 조사 (조사 키·근거 위치·조사 제한)", MODE_CODE, STATUS_PLANNED,
             note="§9.1 조사 키를 exact 검색 키로, §9.3 근거 위치를 Evidence 필수 필드(파일→절/Sheet/행)로 구현한다."),
        _cap("10", "분할 사양서 전수조사", MODE_CODE, STATUS_IMPLEMENTED,
             where="app/core/storage.py::Storage.active_documents",
             note="사양서 1~5를 하나의 집합으로 취급한다. '최신 1개만' 규칙은 과거에 오류로 판명돼 뒤집혔다."),
        _cap("11", "사양 유효 상태", MODE_CODE_LLM, STATUS_PLANNED,
             where="app/parsers/pdf_parser.py::detect_revision_marks",
             note="취소선·Deleted 표시 탐지는 이미 있다. 그 표시가 '현재 무효'인지 판단은 LLM 이 근거와 함께 한다."),
        _cap("12", "사양 품질 점검", MODE_CODE_LLM, STATUS_NOT_PLANNED,
             note="사양서 자체의 모호성·누락 점검. 별도 스킬(anthropic-skills:spec-tc-extractor)이 이미 담당한다."),
        _cap("13", "TC 설계 기본 원칙", MODE_CODE_LLM, STATUS_PLANNED,
             note="프롬프트 규칙으로 주입하고, 결과는 코드가 검증한다(한 Step 한 조작, Expected 단일·관찰 가능)."),
        _cap("14", "Step–Expected Result 번호", MODE_CODE, STATUS_PLANNED,
             note="번호 일치를 코드가 검증한다. LLM 출력의 흔한 결함이라 결정적 검증이 필요하다."),
        _cap("15", "신규/전체 TC 형식", MODE_CODE, STATUS_IMPLEMENTED,
             where="app/core/document_schemas.py::TestCase",
             note="8열 형식을 스키마로 강제한다. Structured Output 이 형식을 벗어날 수 없다."),
        _cap("16", "기존 TC 첨삭", MODE_DRAFT_ONLY, STATUS_PLANNED,
             note="§55가 QA 승인 없는 기존 TC 덮어쓰기를 금지한다. 수정안 diff 까지만 만든다."),
        _cap("17", "Issue–TC 의미 추적성", MODE_CODE_LLM, STATUS_PLANNED,
             note="'Issue 번호가 있다고 관련 TC로 인정하지 않는다'는 코드 규칙으로 강제하고, 의미 일치는 LLM 이 판단한다."),
        _cap("18", "상태 전이 기반 설계", MODE_CODE_LLM, STATUS_PLANNED,
             note="전이 축(A→B→A, Error→정상, 연결→해제→재연결)은 코드가 고정하고 해당 여부만 LLM 이 판단한다."),
        _cap("19", "API / WebSocket Semantic Verification", MODE_CODE_LLM, STATUS_NOT_PLANNED,
             note="Command 표 추출은 코드로 가능하나 MVP 범위 밖. 2차 확장(S08)."),
        _cap("20", "API 입력값 구분", MODE_CODE, STATUS_NOT_PLANNED,
             note="omitted/null/\"\"/\" \"/0/negative/out-of-range/wrong type 조합 생성기. S08 과 함께 구현한다."),
        _cap("21", "API 상태 잔존 Regression", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S08 확장 시."),
        _cap("22", "Generator 작업 전 확인", MODE_QA_INPUT, STATUS_NOT_PLANNED,
             note="Generator 모델·연결 상태는 QA 가 아는 환경 사실이다. S09 확장 시 입력 폼으로."),
        _cap("23", "Generator Procedure Manager", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S09 확장 시."),
        _cap("24", "Generator Step / Size / Study", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S09 확장 시."),
        _cap("25", "Generator Dose Mode / Focal Spot", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S09 확장 시."),
        _cap("26", "Save Dose", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S09 확장 시."),
        _cap("27", "Dose Table 미제공", MODE_QA_INPUT, STATUS_NOT_PLANNED,
             note="Dose Table 유무는 QA 입력. 그 값에 따라 '확정 금지' 목록을 판정 제약으로 적용한다."),
        _cap("28", "원격 검증", MODE_QA_INPUT, STATUS_PLANNED,
             note="실제 Exposure 가능 여부는 환경 사실이다. 불가면 실제 X-ray 결과를 Expected 로 만들지 못하게 막는다(G4)."),
        _cap("29", "Issue 작성", MODE_DRAFT_ONLY, STATUS_NOT_PLANNED, note="S06 확장 시. 초안까지만."),
        _cap("30", "신규 Issue 관련 사양", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S06 확장 시."),
        _cap("31", "수정확인 중 신규 현상", MODE_CODE_LLM, STATUS_NOT_PLANNED,
             note="S04 확장 시. 동일 Root Cause 여부 판별이 핵심이라 근거 없이는 판정하지 않는다."),
        _cap("32", "Regression 영향 범위", MODE_CODE_LLM, STATUS_PLANNED,
             note="8개 축(직접/상태/데이터/저장/연동/권한/기존 Issue/Generator)을 코드가 고정하고, 축별 해당 여부와 근거를 LLM 이 채운다."),
        _cap("33", "Root Cause Coverage", MODE_CODE_LLM, STATUS_PLANNED,
             note="G3 의 핵심. 기존 TC 가 Root Cause 를 검출 가능한지 판단한다."),
        _cap("34", "DICOM 및 장치 연동", MODE_CODE_LLM, STATUS_NOT_PLANNED,
             note="Conformance Statement 의 Tag/VR/Type 표 추출은 코드로 가능. S08 확장 시."),
        _cap("35", "문서 개정 검토", MODE_CODE_LLM, STATUS_IMPLEMENTED,
             where="app/modules/manual_review/reviewer.py",
             note="이미 별도 기능으로 운영 중이다(매뉴얼 개정 검증). S07 은 이 모듈을 재사용한다."),
        _cap("36", "확인 필요 표준 표현", MODE_CODE, STATUS_PLANNED,
             note="15개 표준 표현을 enum 으로 두고 결과에 그 값만 쓴다. 표현이 담당자마다 달라지는 문제를 없앤다."),
        _cap("37", "근거 없는 추론 금지", MODE_CODE, STATUS_PLANNED,
             note="20개 금지 항목을 출력 검증 blocklist 로 구현한다. 근거 없이 이 주장이 나오면 confidence 를 내리고 사람 확인으로 돌린다."),
        _cap("38", "연구소 Review 재검토", MODE_CODE_LLM, STATUS_PLANNED,
             note="rndReviewResult 를 단독 근거로 쓰지 않는 것은 코드 규칙. 재검토 필요 여부는 LLM 이 사양 근거와 대조한다."),
        _cap("39", "Fixed Issue Checklist 작성 전 조사", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S04 확장 시."),
        _cap("40", "Test Step 상세도", MODE_CODE_LLM, STATUS_PLANNED, note="TC 초안 생성 프롬프트 규칙 + 코드 검증."),
        _cap("41", "Issue 종료 Comment", MODE_DRAFT_ONLY, STATUS_NOT_PLANNED,
             note="§55가 AI 분석만으로 Issue 자동 Close 를 금지한다. 초안 생성까지만 (S06 확장 시)."),
        _cap("42", "종료 금지 조건", MODE_CODE, STATUS_PLANNED,
             note="결정적으로 판정 가능하다. 하나라도 걸리면 종료 문구를 확정하지 않는다."),
        _cap("43", "Issue 종료 후 TC 정합성", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S06 확장 시."),
        _cap("44", "Agentic Workflow Gate (G1~G5)", MODE_CODE, STATUS_PLANNED,
             note="Gate 엔진. 판정 결과와 차단 이유를 데이터로 저장한다(로드맵 §11). Gate 가 막으면 LLM 을 부르지 않는다."),
        _cap("45", "표준 Skill Chain", MODE_CODE, STATUS_PLANNED,
             note="7개 Chain(수정확인/사양 확인/기존 TC 보강/신규 Issue/Issue 종료/Release/API·Generator)을 정의로 둔다."),
        _cap("46", "Skill Input/Output Contract", MODE_CODE, STATUS_PLANNED,
             note="Skill 간 입출력을 Pydantic 스키마로 고정한다. 앞 Skill 결과가 뒤 Skill 입력이 되는 구조의 전제다."),
        _cap("47", "Agent Skill 설계 자가진단", MODE_OUT_OF_SCOPE, STATUS_NOT_PLANNED,
             note="Skill 을 새로 설계할 때 사람이 쓰는 24점 체크리스트다. 런타임 동작이 아니다."),
        _cap("48", "AI × Engineering 운영 원칙", MODE_CODE, STATUS_PLANNED,
             note="'AI 는 조사·비교·초안, QA 는 확정'이라는 역할 경계를 코드 가드레일로 구현한다(§55와 같은 지점)."),
        _cap("49", "Technical / Cognitive / Intent Debt", MODE_OUT_OF_SCOPE, STATUS_NOT_PLANNED,
             note="운영 프로세스. 규칙 추가 이력은 규칙 문서 Rev 관리(qa_rules)로 대체한다."),
        _cap("50", "사용법 안내", MODE_CODE, STATUS_PLANNED,
             note="앱 안 /qa-agent/guide 화면. 이 저장소 규칙상 사용법은 문서가 아니라 화면에 둔다."),
        _cap("51", "전체 연계 Regression", MODE_CODE_LLM, STATUS_NOT_PLANNED, note="S10 확장 시."),
        _cap("52", "최종 자체검토", MODE_CODE, STATUS_PLANNED,
             note="체크리스트 항목 중 결정적으로 검증 가능한 것(번호 일치, Runtime TC 생성 여부, 근거 유무)을 자동 검증한다."),
        _cap("53", "최종 중단 조건", MODE_CODE, STATUS_PLANNED,
             note="9개 조건을 Gate 중단 조건으로 구현한다. 걸리면 완성형 산출물을 확정하지 않는다."),
        _cap("54", "자동화 Handoff 원칙", MODE_CODE, STATUS_PLANNED,
             note="이 시스템 자체가 이 절의 구현이다. 10단계 순서를 파이프라인이 지킨다."),
        _cap("55", "자동화 금지 원칙", MODE_CODE, STATUS_PLANNED,
             note="8개 금지 항목을 하드 가드레일로 구현한다. 코드 경로 자체가 없어야 하는 것들이다."),
        _cap("56", "Rev.1.12 핵심 강화 사항", MODE_OUT_OF_SCOPE, STATUS_NOT_PLANNED,
             note="규칙 문서의 변경 이력. 구현 대상이 아니다."),
    ]
)


def summary() -> dict:
    """구현 방식·상태별 집계. `/qa-agent/rules` 화면과 문서가 같은 숫자를 쓰게 한다."""
    by_mode: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for capability in RULE_CAPABILITY.values():
        by_mode[capability.mode] = by_mode.get(capability.mode, 0) + 1
        by_status[capability.status] = by_status.get(capability.status, 0) + 1
    return {"total": len(RULE_CAPABILITY), "by_mode": by_mode, "by_status": by_status}


def missing_paths(root: Path) -> list[tuple[str, str]]:
    """`status=IMPLEMENTED` 인데 `where` 파일이 실제로 없는 항목. 테스트가 이것을 비워야 한다."""
    missing: list[tuple[str, str]] = []
    for capability in RULE_CAPABILITY.values():
        if capability.status != STATUS_IMPLEMENTED:
            continue
        for path in capability.paths:
            if not (root / path).exists():
                missing.append((capability.section, path))
    return missing


def unclassified_sections(section_numbers: set[str]) -> list[str]:
    """규칙 문서에는 있는데 이 표에 없는 절. 규칙 Rev 가 올라가면 여기서 잡힌다."""
    return sorted((section_numbers - set(RULE_CAPABILITY)), key=lambda value: (len(value), value))


def stale_sections(section_numbers: set[str]) -> list[str]:
    """이 표에는 있는데 규칙 문서에서 사라진 절."""
    return sorted((set(RULE_CAPABILITY) - section_numbers), key=lambda value: (len(value), value))

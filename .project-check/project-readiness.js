#!/usr/bin/env node
'use strict';

// Strict, read-only completion gate for a root selected by the workspace registry.
// Unlike advisory spec-lint, missing maintained-project documents fail this gate.
// This proves document structure and local references, never behavioral correctness
// or that tests ran. It does not load source code, private configuration or knowledge.
const fs = require('fs');
const path = require('path');
// 사람이 읽는 docs/SPEC.html의 신선도는 그 파일을 만드는 렌더러의 해시 규칙으로 판정한다.
// 두 파일은 키트 tools/와 프로젝트 .project-check/에 나란히 배포된다. 렌더러가 없으면 조용히
// 건너뛰지 않고 checker 오류(CLI exit 2)로 멈춘다 — 빠진 bundle을 통과나 일반 실패(1)로 보이게
// 하지 않는다. 그래서 모듈 load가 아니라 검사 안에서 require한다.
const loadSpecHtml = () => require('./render-spec-html');
const KIT_TEMPLATE = path.join(__dirname, '../docs/templates/agents-spec-section.md');
const DEFAULT_TEMPLATE = fs.existsSync(KIT_TEMPLATE) ? KIT_TEMPLATE : path.join(__dirname, 'agents-spec-section.md');
// CHANGELOG 머리의 형식 예시 블록은 이력으로 읽히지 않는다(렌더러가 fenced 블록을 건너뛴다). 실제 항목이
// 그 안에 들어가면 조용히 사라지므로, 템플릿 예시와 비교해 경고한다. 기준은 키트 템플릿 한 벌이고,
// 프로젝트에는 migration이 같은 바이트의 사본(changelog-template.md)을 둔다.
const KIT_CHANGELOG = path.join(__dirname, '../docs/templates/CHANGELOG.md');
const DEFAULT_CHANGELOG = fs.existsSync(KIT_CHANGELOG) ? KIT_CHANGELOG : path.join(__dirname, 'changelog-template.md');
const REQUIRED = ['SPEC.md', 'CHANGELOG.md', 'progress.md', 'AGENTS.md', 'CLAUDE.md'];
const ID = /^(REQ|NFR|TEST)-[A-Z0-9]+-\d{3}$/;
const IDS = /\b(?:REQ|NFR|TEST)-[A-Za-z0-9]+-\d+\b/g;
// SPEC.md의 필수 절. 기능 사양 문서(specs/<기능>.md)는 3·4·6~10절을 SPEC.md에서 물려받으므로 1·2·5·11·12만 둔다.
// 번호는 일부러 SPEC.md와 같은 뜻이다 — 빠진 번호는 그 내용이 공통이라는 뜻이다.
const SPEC_SECTIONS = [1, 2, 5, 9, 11, 12];
const FEATURE_SECTIONS = [1, 2, 5, 11, 12];
const PLACEHOLDER = /\bTBD\b|\{\{[^}]+\}\}|확인 필요/;
const normalize = text => text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');

// Preserve line positions, masking comments and fenced examples. This avoids
// treating a quoted workflow, requirement or Claude import as an active rule.
// fenced 블록 판정은 렌더러의 규칙(fencedMask) 한 벌을 쓴다 — 두 파서가 다르면 HTML 카드 수와 검사한
// 요구사항 수가 어긋난다.
function visibleMarkdown(text) {
  const uncommented = text.replace(/<!--[\s\S]*?(?:-->|$)/g, x => x.replace(/[^\n]/g, ' '));
  const lines = uncommented.split('\n');
  const fenced = loadSpecHtml().fencedMask(lines);
  return lines.map((line, i) => (fenced[i] ? ' '.repeat(line.length) : line)).join('\n');
}

function sections(text) {
  const result = new Map();
  const headings = [...text.matchAll(/^##\s+(\d+)\.\s*[^\n]*$/gm)];
  for (let i = 0; i < headings.length; i++) {
    result.set(Number(headings[i][1]), text.slice(headings[i].index + headings[i][0].length, headings[i + 1]?.index ?? text.length).trim());
  }
  return result;
}
function ticks(text) { return [...text.matchAll(/`([^`\n]+)`/g)].map(m => m[1].trim()); }
function fileToken(token) {
  return !/[<>\n]|\{\{|:\/\//.test(token) && /\.[a-zA-Z0-9]+(?:#[^\s]+)?$/.test(token);
}
function testToken(token) {
  if (!fileToken(token)) return false;
  // `test-*.js` 이름(키트 방식)은 공백·와일드카드가 없는 경로일 때만 — "node tools\\test-x.js" 같은 명령이나 glob은 파일이 아니다.
  return /(?:^|[\\/])tests?[\\/]|(?:^|[\\/])test_[^\\/]+|[._-]test\.[^.]+$|\.(?:test|spec)\.[^.]+$/.test(token)
    || (/(?:^|[\\/])test-[^\\/]+$/.test(token) && !/[\s*?]/.test(token));
}
// A tool's own regression suite is referenced as `<tool> --self-test`; the file part must exist.
const SELF_TEST = /^(\S+\.[a-zA-Z0-9]+) --self-test$/;
function testRef(token) {
  if (testToken(token)) return { ref: token, file: token };
  const m = token.match(SELF_TEST);
  return m && fileToken(m[1]) ? { ref: token, file: m[1] } : null;
}
const STATUSES = ['draft', 'implemented', 'verified', 'deprecated'];
function inside(root, target) {
  const relative = path.relative(root, target);
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
}

// 첫 버전 제목(`## `) 앞에 나오는 첫 fenced 블록이 형식 예시다. 항목 안의 코드 블록은 예시가 아니다.
function formatExample(text) {
  const lines = normalize(text).split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (/^##\s/.test(lines[i])) return null;
    const open = lines[i].match(/^ {0,3}(`{3,}|~{3,})/);
    if (!open) continue;
    const body = [];
    for (let j = i + 1; j < lines.length; j++) {
      const close = lines[j].match(/^ {0,3}(`{3,}|~{3,})\s*$/);
      if (close && close[1][0] === open[1][0] && close[1].length >= open[1].length) return body;
      body.push({ line: j + 1, text: lines[j].replace(/\s+$/, '') });
    }
    return body;
  }
  return null;
}

function checkProject(projectRoot, options = {}) {
  if (typeof projectRoot !== 'string' || !projectRoot.trim()) throw new Error('Project root is required');
  const root = fs.realpathSync(path.resolve(projectRoot));
  if (!fs.statSync(root).isDirectory()) throw new Error('Project root must be a directory');
  const template = normalize(fs.readFileSync(options.templatePath || DEFAULT_TEMPLATE, 'utf8')).trim();
  const expectedVersion = template.match(/<!--\s*spec-workflow:\s*(\S+)\s*-->/)?.[1];
  if (!expectedVersion || !template.startsWith('## ') || template.length < 200) throw new Error('Invalid canonical workflow template');
  const result = { root, scope: 'project', errors: [], warnings: [], counts: { documents: 0, specDocuments: 0, requirements: 0, implementationPaths: 0, testPaths: 0, traceRows: 0 }, workflowVersion: expectedVersion };
  const error = (code, message) => result.errors.push({ code, message });
  const warning = (code, message) => result.warnings.push({ code, message });
  const docs = {};
  for (const name of REQUIRED) {
    const full = path.join(root, name);
    try {
      const stat = fs.statSync(full);
      if (!stat.isFile()) { error('DOCUMENT_NOT_FILE', `${name}: expected a file`); continue; }
      if (!inside(root, fs.realpathSync(full))) { error('PATH_OUTSIDE_PROJECT', `${name}: document resolves outside project`); continue; }
      if (stat.size > 4 * 1024 * 1024) throw new Error(`${name}: document exceeds 4 MiB inspection limit`);
      docs[name] = normalize(fs.readFileSync(full, 'utf8'));
      result.counts.documents++;
      if (!docs[name].trim()) error('DOCUMENT_EMPTY', `${name}: empty document`);
      if (/\bbootstrap stub\b/i.test(docs[name])) error('BOOTSTRAP_INCOMPLETE', `${name}: bootstrap stub remains`);
    } catch (err) {
      if (err.code === 'ENOENT') error('DOCUMENT_MISSING', `${name}: required document missing`);
      else throw err;
    }
  }

  const agents = docs['AGENTS.md'] || '';
  const version = agents.match(/<!--\s*spec-workflow:\s*(\S+)\s*-->/)?.[1];
  const visibleAgents = visibleMarkdown(agents);
  let canonicalPresent = false;
  for (const match of agents.matchAll(new RegExp('^' + template.split('\n')[0].replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$', 'gm'))) {
    if (visibleAgents.slice(match.index, match.index + match[0].length) === match[0] && agents.slice(match.index, match.index + template.length) === template) canonicalPresent = true;
  }
  if (canonicalPresent && version && version !== expectedVersion) warning('WORKFLOW_LEGACY_PRESERVED', `AGENTS.md: current canonical workflow present; legacy ${version} section preserved`);
  else if (!canonicalPresent && version && version !== expectedVersion) error('WORKFLOW_OUTDATED', `AGENTS.md: workflow ${version}; expected ${expectedVersion}`);
  else if (!canonicalPresent) error(version ? 'WORKFLOW_INCOMPLETE' : 'WORKFLOW_MISSING', 'AGENTS.md: full canonical SPEC workflow required; marker alone is insufficient');
  const claude = visibleMarkdown(docs['CLAUDE.md'] || '').replace(/`[^`\n]*`/g, '');
  if (!/^\s*@(?:\.\/)?AGENTS\.md\s*$/m.test(claude)) error('CLAUDE_IMPORT_MISSING', 'CLAUDE.md: active @AGENTS.md import missing');

  // 기준 템플릿은 CHANGELOG 내용과 무관하게 항상 읽는다 — 사본이 빠진 bundle을 통과로 보이게 하지 않는다.
  const expected = formatExample(fs.readFileSync(options.changelogTemplatePath || DEFAULT_CHANGELOG, 'utf8'));
  if (!expected) throw new Error('Invalid CHANGELOG template: no format example block');
  const example = docs['CHANGELOG.md'] ? formatExample(docs['CHANGELOG.md']) : null;
  if (example) {
    const known = new Set(expected.map(l => l.text));
    const extra = example.filter(l => l.text.trim() && !known.has(l.text));
    if (extra.length) warning('CHANGELOG_EXAMPLE_MODIFIED', `CHANGELOG.md:${extra[0].line}: the format example block has ${extra.length} line(s) not in the template (first: "${extra[0].text.trim().slice(0, 80)}"); a fenced block is not read as history, so move real entries under a version heading such as "## [Unreleased]"`);
  }

  const rawSpec = docs['SPEC.md'];
  if (rawSpec?.trim()) {
    const specHtml = loadSpecHtml();
    // 사양 묶음: SPEC.md, 그리고 인덱스 구역이 있으면 그 구역이 가리키는 specs/<기능>.md. 인덱스 해석은
    // 렌더러 한 벌(specSet)이다 — HTML에 들어가는 문서와 여기서 검사하는 문서가 같아야 카드 수가 맞는다.
    // 인덱스가 없으면 목록은 SPEC.md 하나이고 아래 규칙은 모두 예전과 똑같이 동작한다.
    const set = specHtml.specSet(root, rawSpec);
    for (const p of set.problems) (p.level === 'warning' ? warning : error)(p.code, p.message);
    const specDocuments = [{ label: 'SPEC.md', raw: rawSpec, required: SPEC_SECTIONS }];
    for (const d of set.documents) {
      const raw = normalize(d.text);
      if (!raw.trim()) { error('DOCUMENT_EMPTY', `${d.relative}: empty document`); continue; }
      if (/\bbootstrap stub\b/i.test(raw)) error('BOOTSTRAP_INCOMPLETE', `${d.relative}: bootstrap stub remains`);
      specDocuments.push({ label: d.relative, raw, required: FEATURE_SECTIONS });
    }
    result.counts.specDocuments = specDocuments.length;
    for (const doc of specDocuments) { doc.text = visibleMarkdown(doc.raw); doc.sec = sections(doc.text); }

    // 정의는 모든 문서에서 먼저 모은다: 기능 문서의 추적성 표가 SPEC.md의 NFR을 가리킬 수 있고, 두 번째
    // 문서에서 다시 쓴 ID는 두 문서를 다 읽은 뒤에야 충돌로 보인다.
    const defined = new Map();
    const untitled = [];
    for (const doc of specDocuments) {
      for (const number of doc.required) {
        if (!doc.sec.has(number) || !doc.sec.get(number)) error('SPEC_SECTION_MISSING', `${doc.label}: required section ${number} missing or empty`);
      }
      // Sections 13/14 explicitly hold open questions and future ideas. They remain
      // visible warnings; placeholders in the implemented contract fail completion.
      const core = doc.text.replace(/^##\s+(?:13|14)\.[\s\S]*?(?=^##\s+\d+\.|$(?![\s\S]))/gm, '');
      if (PLACEHOLDER.test(core)) error('SPEC_INCOMPLETE', `${doc.label}: unresolved placeholder in current specification`);
      if ([13, 14].some(n => PLACEHOLDER.test(doc.sec.get(n) || ''))) warning('SPEC_OPEN_QUESTIONS', `${doc.label}: open questions or future proposals remain; review their effect on the current scope`);
      doc.own = new Set();
      const headings = [...doc.text.matchAll(/^ {0,3}#{2,4}\s+((?:REQ|NFR|TEST)-\S+)[^\n]*$/gm)];
      for (let i = 0; i < headings.length; i++) {
        const id = headings[i][1].replace(/[.,:;]+$/, '');
        if (!ID.test(id)) { error('REQUIREMENT_ID_INVALID', `${doc.label}: invalid definition ${id}`); continue; }
        const body = doc.text.slice(headings[i].index + headings[i][0].length, headings[i + 1]?.index ?? doc.text.length).split(/^##\s/m)[0].trim();
        if (!body || !body.replace(/^#{1,6}[^\n]*$/gm, '').trim()) error('REQUIREMENT_EMPTY', `${doc.label}: ${id} has no description`);
        const previous = defined.get(id);
        if (previous) error('REQUIREMENT_ID_DUPLICATE', `${doc.label}: duplicate definition ${id}${previous.label === doc.label ? '' : `; already defined in ${previous.label}`}`);
        else defined.set(id, { label: doc.label, body });
        doc.own.add(id);
        result.counts.requirements++;
        // 번호만 보고 무슨 기능인지 알 수 있어야 한다: "### REQ-EXPORT-001 CSV 저장". 기존 SPEC을
        // 막지 않도록 경고로만 알린다. TEST 절차는 검증 대상 ID가 이름 역할을 하므로 제외한다.
        if (!id.startsWith('TEST-') && !headings[i][0].replace(/^ {0,3}#{2,4}\s+\S+/, '').replace(/^[.,:;\s]+/, '').trim()) untitled.push({ id, label: doc.label });
      }
    }
    const named = [...defined.keys()].filter(id => !id.startsWith('TEST-')).length;
    if (untitled.length) {
      const where = [...new Set(untitled.map(u => u.label))].join(', ');
      warning('REQUIREMENT_TITLE_MISSING', `${where}: ${untitled.length} of ${named} REQ/NFR headings have no name (e.g. ${untitled.slice(0, 3).map(u => u.id).join(', ')}); write "### ${untitled[0].id} <기능 이름>" so the ID alone tells what it does`);
    }
    if (![...defined.keys()].some(id => id.startsWith('REQ-'))) error('REQUIREMENT_MISSING', `SPEC.md: no functional REQ definition${specDocuments.length > 1 ? ` in ${specDocuments.length} specification documents` : ''}`);

    // 각 문서는 자기가 정의한 ID를 자기 12절에서 추적한다. 통합 표를 하나 더 두면 같은 행이 두 벌이 되고
    // 한쪽이 조용히 낡는다. 경로는 모든 문서에서 프로젝트 루트 기준이다(specs/ 안에서도).
    const implPaths = new Map();
    const testPaths = new Map();
    const remember = (map, token, label) => { if (!map.has(token)) map.set(token, label); };
    const manualVerified = []; const ungatedVerified = [];
    // 무엇이 게이트에서 실행되는가: 호출자가 목록을 주면(키트: check-kit.js가 돌리는 테스트) 그 목록,
    // 아니면 프로젝트의 botyard.json verify 명령이 하나라도 있을 때 그 프로젝트의 테스트 전부로 본다.
    const gated = options.gatedTests ? ref => options.gatedTests.has(ref.replace(/\\/g, '/')) : (() => {
      let verify = [];
      try { verify = JSON.parse(fs.readFileSync(path.join(root, 'botyard.json'), 'utf8')).verify; } catch { /* no gate */ }
      const on = Array.isArray(verify) && verify.some(v => typeof v === 'string' && v.trim());
      return () => on;
    })();
    for (const doc of specDocuments) {
      const label = doc.label;
      for (const token of ticks(doc.text).filter(testToken)) remember(testPaths, token, label);
      const traceIds = new Set();
      let header = null;
      for (const line of (doc.sec.get(12) || '').split('\n')) {
        if (!line.trim().startsWith('|')) continue;
        const cells = specHtml.cells(line);
        if (cells.some(c => /^(?:Requirement|요구사항)$/i.test(c))) { header = cells; continue; }
        if (!cells.some(c => /\b(?:REQ|NFR|TEST)-/.test(c))) continue;
        const reqCol = header?.findIndex(c => /^(?:Requirement|요구사항)$/i.test(c)) ?? 0;
        const implCol = header?.findIndex(c => /^(?:Implementation|구현)$/i.test(c)) ?? 1;
        const testCol = header?.findIndex(c => /^(?:Test|테스트)$/i.test(c)) ?? 2;
        const statusCol = header ? header.findIndex(c => /^(?:Status|상태)$/i.test(c)) : 3;
        if (implCol < 0 || testCol < 0) { error('TRACE_COLUMNS_MISSING', `${label}: traceability table requires Implementation and Test columns`); continue; }
        const rowIds = cells[reqCol]?.match(IDS) || [];
        if (!rowIds.length) continue;
        result.counts.traceRows++;
        for (const id of cells.join(' ').match(IDS) || []) if (!defined.has(id)) error('TRACE_UNDEFINED', `${label}: traceability references undefined ${id}`);
        rowIds.forEach(id => traceIds.add(id));
        const paths = ticks(cells[implCol] || '').filter(fileToken);
        paths.forEach(p => remember(implPaths, p, label));
        if (!paths.length) error('TRACE_IMPLEMENTATION_MISSING', `${label}: ${rowIds.join(', ')} lacks implementation file reference`);
        // Test 열은 테스트 경로(`tests/…`, `test-*.js` 등), `<도구> --self-test`, 정의된 TEST-ID만 받는다.
        // 존재하기만 하면 되는 규칙이면 README.md를 적어도 통과한다.
        const refs = [];
        for (const token of ticks(cells[testCol] || '')) {
          const r = testRef(token);
          if (r) { refs.push(r); remember(testPaths, r.file, label); } else error('TRACE_TEST_INVALID', `${label}: ${rowIds.join(', ')} Test column \`${token}\` is not a test file, "<tool> --self-test" or TEST-ID`);
        }
        const testIds = (cells[testCol] || '').match(/\bTEST-[A-Z0-9]+-\d{3}\b/g) || [];
        if (!refs.length && !testIds.some(id => defined.has(id))) error('TRACE_TEST_MISSING', `${label}: ${rowIds.join(', ')} lacks test file or defined TEST procedure`);
        if (statusCol >= 0) {
          const value = (cells[statusCol] || '').replace(/`/g, '').trim().toLowerCase();
          if (!STATUSES.includes(value)) error('TRACE_STATUS_INVALID', `${label}: ${rowIds.join(', ')} Status "${cells[statusCol] || ''}" must be one of ${STATUSES.join(' / ')}`);
          else if (value === 'verified') {
            // 자동 테스트 = 표에 적은 테스트 경로, 또는 11절 본문에 테스트 경로가 있는 TEST-ID.
            const automated = [...refs];
            for (const id of testIds) for (const token of ticks(defined.get(id)?.body || '')) { const r = testRef(token); if (r) automated.push(r); }
            if (!automated.length) manualVerified.push(...rowIds);
            else if (!automated.some(r => gated(r.ref))) ungatedVerified.push(...rowIds);
          }
        }
      }
      // 정의한 문서가 추적한다. 다른 문서의 표에 적어도 이 문서의 누락은 메워지지 않는다.
      for (const id of doc.own) if (!id.startsWith('TEST-') && !traceIds.has(id)) error('TRACE_REQUIREMENT_MISSING', `${label}: ${id} missing from traceability table`);
    }
    const labels = specDocuments.length > 1 ? 'specification documents' : 'SPEC.md';
    // 이번 workflow 판에서는 경고다. 다음 판에서 오류로 올린다(docs/project-readiness.md).
    if (manualVerified.length) warning('TRACE_VERIFIED_MANUAL', `${labels}: ${manualVerified.length} verified row(s) have only manual TEST procedures (${manualVerified.slice(0, 5).join(', ')}); set Status to implemented, or add an automated test that the gate runs`);
    if (ungatedVerified.length) warning('TRACE_VERIFIED_UNGATED', `${labels}: ${ungatedVerified.length} verified row(s) have automated tests that no gate runs (${ungatedVerified.slice(0, 5).join(', ')}); ${options.gatedTests ? 'register the test in the gate' : 'add the test command to botyard.json "verify"'}, or set Status to implemented`);
    for (const [kind, paths] of [['IMPLEMENTATION', implPaths], ['TEST', testPaths]]) {
      result.counts[kind === 'TEST' ? 'testPaths' : 'implementationPaths'] = paths.size;
      for (const [token, label] of paths) {
        const file = token.split('#')[0].replace(/\\/g, '/');
        const target = path.resolve(root, file);
        if (path.isAbsolute(file) || /^[A-Za-z]:/.test(file) || !inside(root, target)) { error('PATH_OUTSIDE_PROJECT', `${label}: ${token} must be project-relative`); continue; }
        try {
          if (!inside(root, fs.realpathSync(target))) error('PATH_OUTSIDE_PROJECT', `${label}: ${token} resolves outside project`);
          else if (!fs.statSync(target).isFile()) error(`${kind}_PATH_MISSING`, `${label}: ${token} is not a file`);
        } catch (err) {
          if (err.code === 'ENOENT' || err.code === 'ENOTDIR') error(`${kind}_PATH_MISSING`, `${label}: referenced file ${token} missing`);
          else throw err;
        }
      }
    }
    const html = specHtml.status(root);
    const regenerate = `run ${specHtml.REGENERATE}`;
    const sources = specDocuments.length > 1 ? `SPEC.md and ${specDocuments.length - 1} feature specification(s)` : 'SPEC.md';
    if (html.status === 'MISSING') error('SPEC_HTML_MISSING', `${html.output}: human-readable SPEC view missing; ${regenerate}`);
    else if (html.status === 'STALE') error('SPEC_HTML_STALE', `${html.output}: ${html.reason === 'content' ? `content differs from a fresh render of ${sources} (edited by hand?)` : `generated from an older ${sources}`}; ${regenerate}`);
    else if (html.status === 'UNMANAGED') error('SPEC_HTML_UNMANAGED', `${html.output}: not generated from SPEC.md (or a symlink); move it aside, then ${regenerate}`);
    else if (html.cards !== undefined && html.cards !== result.counts.requirements) error('SPEC_HTML_CARDS_MISMATCH', `${html.output}: ${html.cards} requirement cards but ${result.counts.requirements} definitions in ${sources}; the page would hide or invent requirements`);
    else if (html.version !== specHtml.RENDERER_VERSION) warning('SPEC_HTML_RENDERER_OUTDATED', `${html.output}: rendered by ${html.version}; ${regenerate} for the ${specHtml.RENDERER_VERSION} layout`);
    // 쉬운 말 기준(AGENTS.md "SPEC 문장 쓰기"). 판정이 아니라 다시 읽을 곳을 알린다. 목록은 렌더러 한 벌이다.
    result.counts.paragraphs = 0;
    for (const doc of specDocuments) {
      const plain = specHtml.plainLanguage(doc.raw);
      result.counts.paragraphs += plain.paragraphs;
      if (plain.words.length || plain.long.length) {
        const words = plain.words.map(w => `${w.word} ${w.count}(${doc.label}:${w.line})`).join(', ');
        const long = plain.long.length ? `${plain.long.length} paragraph(s) over ${plain.limit} chars (first ${doc.label}:${plain.long[0].line}, ${plain.long[0].chars} chars)` : '';
        warning('SPEC_PLAIN_LANGUAGE', `${doc.label}: hard-to-read wording for a first-time reader — ${[words && 'words: ' + words, long].filter(Boolean).join('; ')}; ${plain.paragraphs} paragraphs inspected; rewrite per AGENTS.md "SPEC 문장 쓰기"`);
      }
    }
    // 한눈에 보기(workflow v8): SPEC.md 1절에 프로젝트 전체의 흐름도가 있어야 처음 보는 사람이 무슨 프로젝트이고
    // 어떤 흐름으로 돌아가는지 먼저 본다. 그릴 수 있는지는 렌더러의 파서로 판정한다(HTML에 그려지는 것과 같다).
    // 경고다: 오류로 두면 자동 갱신된 다른 PC의 프로젝트가 흐름도를 쓰기 전까지 세션 준비에서 막힌다.
    const overview = specHtml.flowBlocks(rawSpec, 1);
    result.counts.overviewDiagrams = overview.filter(b => b.drawable).length;
    if (!overview.length) warning('SPEC_OVERVIEW_MISSING', 'SPEC.md: section 1 has no overview diagram; add "### 한눈에 보기" with a ```flow block showing what starts this project, its main steps, results and failure paths (AGENTS.md "한눈에 보기")');
    else if (!result.counts.overviewDiagrams) warning('SPEC_OVERVIEW_INVALID', `SPEC.md:${overview[0].line}: the section 1 flow block cannot be drawn; write each line as "A -> B -> C" (labelled: "A -(실패)-> B"), it is shown as plain code until then`);
    if (result.counts.testPaths === 0) warning('NO_TEST_FILE_REFERENCES', `${labels}: no automated test file references inspected; documented TEST procedures need actual execution evidence`);
  }
  return result;
}

function main(args) {
  try {
    const roots = [];
    let templatePath;
    for (let i = 0; i < args.length; i++) {
      if (args[i] === '--json') continue;
      if (args[i] === '--template') {
        if (templatePath || !args[i + 1] || args[i + 1].startsWith('-')) throw new Error('--template requires one file path');
        templatePath = args[++i];
      } else if (args[i].startsWith('-')) throw new Error(`Unknown option: ${args[i]}`);
      else roots.push(args[i]);
    }
    if (roots.length !== 1) throw new Error('Usage: node tools/project-readiness.js <project-root> [--json] [--template <file>]');
    const result = checkProject(roots[0], { templatePath });
    if (args.includes('--json')) console.log(JSON.stringify(result, null, 2));
    else {
      for (const finding of result.errors) console.log(`ERROR ${finding.code}: ${finding.message}`);
      for (const finding of result.warnings) console.log(`WARN ${finding.code}: ${finding.message}`);
      console.log(`TOTAL scope=project discovered=1 checked=1 excluded=0 failed=${result.errors.length ? 1 : 0} documents=${result.counts.documents} specDocuments=${result.counts.specDocuments} requirements=${result.counts.requirements} implementationPaths=${result.counts.implementationPaths} testPaths=${result.counts.testPaths}`);
    }
    return result.errors.length ? 1 : 0;
  } catch (err) {
    if (args.includes('--json')) console.log(JSON.stringify({ scope: 'project', checkerError: err.message }));
    else console.error(`CHECKER_ERROR: ${err.message}`);
    return 2;
  }
}
if (require.main === module) process.exitCode = main(process.argv.slice(2));
module.exports = { checkProject, visibleMarkdown, sections, testRef, STATUSES, SPEC_SECTIONS, FEATURE_SECTIONS };

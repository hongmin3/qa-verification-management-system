#!/usr/bin/env node
'use strict';

// Strict, read-only completion gate for a root selected by the workspace registry.
// Unlike advisory spec-lint, missing maintained-project documents fail this gate.
// This proves document structure and local references, never behavioral correctness
// or that tests ran. It does not load source code, private configuration or knowledge.
const fs = require('fs');
const path = require('path');
const KIT_TEMPLATE = path.join(__dirname, '../docs/templates/agents-spec-section.md');
const DEFAULT_TEMPLATE = fs.existsSync(KIT_TEMPLATE) ? KIT_TEMPLATE : path.join(__dirname, 'agents-spec-section.md');
const REQUIRED = ['SPEC.md', 'CHANGELOG.md', 'progress.md', 'AGENTS.md', 'CLAUDE.md'];
const ID = /^(REQ|NFR|TEST)-[A-Z0-9]+-\d{3}$/;
const IDS = /\b(?:REQ|NFR|TEST)-[A-Za-z0-9]+-\d+\b/g;
const PLACEHOLDER = /\bTBD\b|\{\{[^}]+\}\}|확인 필요/;
const normalize = text => text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');

// Preserve line positions, masking comments and fenced examples. This avoids
// treating a quoted workflow, requirement or Claude import as an active rule.
function visibleMarkdown(text) {
  const uncommented = text.replace(/<!--[\s\S]*?(?:-->|$)/g, x => x.replace(/[^\n]/g, ' '));
  let fence = null;
  return uncommented.split('\n').map(line => {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (fence) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && line.slice(line.indexOf(marker[1]) + marker[1].length).trim() === '') fence = null;
      return ' '.repeat(line.length);
    }
    if (marker) { fence = marker[1]; return ' '.repeat(line.length); }
    return line;
  }).join('\n');
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
  return fileToken(token) && /(?:^|[\\/])tests?[\\/]|(?:^|[\\/])test_[^\\/]+|[._-]test\.[^.]+$|\.(?:test|spec)\.[^.]+$/.test(token);
}
function inside(root, target) {
  const relative = path.relative(root, target);
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
}

function checkProject(projectRoot, options = {}) {
  if (typeof projectRoot !== 'string' || !projectRoot.trim()) throw new Error('Project root is required');
  const root = fs.realpathSync(path.resolve(projectRoot));
  if (!fs.statSync(root).isDirectory()) throw new Error('Project root must be a directory');
  const template = normalize(fs.readFileSync(options.templatePath || DEFAULT_TEMPLATE, 'utf8')).trim();
  const expectedVersion = template.match(/<!--\s*spec-workflow:\s*(\S+)\s*-->/)?.[1];
  if (!expectedVersion || !template.startsWith('## ') || template.length < 200) throw new Error('Invalid canonical workflow template');
  const result = { root, scope: 'project', errors: [], warnings: [], counts: { documents: 0, requirements: 0, implementationPaths: 0, testPaths: 0, traceRows: 0 }, workflowVersion: expectedVersion };
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

  const rawSpec = docs['SPEC.md'];
  if (rawSpec?.trim()) {
    const spec = visibleMarkdown(rawSpec);
    const sec = sections(spec);
    for (const number of [1, 2, 5, 9, 11, 12]) {
      if (!sec.has(number) || !sec.get(number)) error('SPEC_SECTION_MISSING', `SPEC.md: required section ${number} missing or empty`);
    }
    // Sections 13/14 explicitly hold open questions and future ideas. They remain
    // visible warnings; placeholders in the implemented contract fail completion.
    const core = spec.replace(/^##\s+(?:13|14)\.[\s\S]*?(?=^##\s+\d+\.|$(?![\s\S]))/gm, '');
    if (PLACEHOLDER.test(core)) error('SPEC_INCOMPLETE', 'SPEC.md: unresolved placeholder in current specification');
    if ([13, 14].some(n => PLACEHOLDER.test(sec.get(n) || ''))) warning('SPEC_OPEN_QUESTIONS', 'SPEC.md: open questions or future proposals remain; review their effect on the current scope');
    const defined = new Map();
    const headings = [...spec.matchAll(/^#{2,4}\s+((?:REQ|NFR|TEST)-\S+)[^\n]*$/gm)];
    for (let i = 0; i < headings.length; i++) {
      const id = headings[i][1].replace(/[.,:;]+$/, '');
      if (!ID.test(id)) { error('REQUIREMENT_ID_INVALID', `SPEC.md: invalid definition ${id}`); continue; }
      if (defined.has(id)) error('REQUIREMENT_ID_DUPLICATE', `SPEC.md: duplicate definition ${id}`);
      const body = spec.slice(headings[i].index + headings[i][0].length, headings[i + 1]?.index ?? spec.length).split(/^##\s/m)[0].trim();
      if (!body || !body.replace(/^#{1,6}[^\n]*$/gm, '').trim()) error('REQUIREMENT_EMPTY', `SPEC.md: ${id} has no description`);
      defined.set(id, body);
      result.counts.requirements++;
    }
    if (![...defined.keys()].some(id => id.startsWith('REQ-'))) error('REQUIREMENT_MISSING', 'SPEC.md: no functional REQ definition');
    const implPaths = new Set();
    const testPaths = new Set(ticks(spec).filter(testToken));
    const traceIds = new Set();
    let header = null;
    for (const line of (sec.get(12) || '').split('\n')) {
      if (!line.trim().startsWith('|')) continue;
      const cells = line.trim().replace(/^\||\|$/g, '').split('|').map(s => s.trim());
      if (cells.some(c => /^(?:Requirement|요구사항)$/i.test(c))) { header = cells; continue; }
      if (!cells.some(c => /\b(?:REQ|NFR|TEST)-/.test(c))) continue;
      const reqCol = header?.findIndex(c => /^(?:Requirement|요구사항)$/i.test(c)) ?? 0;
      const implCol = header?.findIndex(c => /^(?:Implementation|구현)$/i.test(c)) ?? 1;
      const testCol = header?.findIndex(c => /^(?:Test|테스트)$/i.test(c)) ?? 2;
      if (implCol < 0 || testCol < 0) { error('TRACE_COLUMNS_MISSING', 'SPEC.md: traceability table requires Implementation and Test columns'); continue; }
      const rowIds = cells[reqCol]?.match(IDS) || [];
      if (!rowIds.length) continue;
      result.counts.traceRows++;
      for (const id of cells.join(' ').match(IDS) || []) if (!defined.has(id)) error('TRACE_UNDEFINED', `SPEC.md: traceability references undefined ${id}`);
      rowIds.forEach(id => traceIds.add(id));
      const paths = ticks(cells[implCol] || '').filter(fileToken);
      paths.forEach(p => implPaths.add(p));
      if (!paths.length) error('TRACE_IMPLEMENTATION_MISSING', `SPEC.md: ${rowIds.join(', ')} lacks implementation file reference`);
      const tests = ticks(cells[testCol] || '').filter(fileToken);
      tests.forEach(p => testPaths.add(p));
      const testIds = (cells[testCol] || '').match(/\bTEST-[A-Z0-9]+-\d{3}\b/g) || [];
      if (!tests.length && !testIds.some(id => defined.has(id))) error('TRACE_TEST_MISSING', `SPEC.md: ${rowIds.join(', ')} lacks test file or defined TEST procedure`);
    }
    for (const id of defined.keys()) if (!id.startsWith('TEST-') && !traceIds.has(id)) error('TRACE_REQUIREMENT_MISSING', `SPEC.md: ${id} missing from traceability table`);
    for (const [kind, paths] of [['IMPLEMENTATION', implPaths], ['TEST', testPaths]]) {
      result.counts[kind === 'TEST' ? 'testPaths' : 'implementationPaths'] = paths.size;
      for (const token of paths) {
        const file = token.split('#')[0].replace(/\\/g, '/');
        const target = path.resolve(root, file);
        if (path.isAbsolute(file) || /^[A-Za-z]:/.test(file) || !inside(root, target)) { error('PATH_OUTSIDE_PROJECT', `SPEC.md: ${token} must be project-relative`); continue; }
        try {
          if (!inside(root, fs.realpathSync(target))) error('PATH_OUTSIDE_PROJECT', `SPEC.md: ${token} resolves outside project`);
          else if (!fs.statSync(target).isFile()) error(`${kind}_PATH_MISSING`, `SPEC.md: ${token} is not a file`);
        } catch (err) {
          if (err.code === 'ENOENT' || err.code === 'ENOTDIR') error(`${kind}_PATH_MISSING`, `SPEC.md: referenced file ${token} missing`);
          else throw err;
        }
      }
    }
    if (result.counts.testPaths === 0) warning('NO_TEST_FILE_REFERENCES', 'SPEC.md: no automated test file references inspected; documented TEST procedures need actual execution evidence');
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
      console.log(`TOTAL scope=project discovered=1 checked=1 excluded=0 failed=${result.errors.length ? 1 : 0} documents=${result.counts.documents} requirements=${result.counts.requirements} implementationPaths=${result.counts.implementationPaths} testPaths=${result.counts.testPaths}`);
    }
    return result.errors.length ? 1 : 0;
  } catch (err) {
    if (args.includes('--json')) console.log(JSON.stringify({ scope: 'project', checkerError: err.message }));
    else console.error(`CHECKER_ERROR: ${err.message}`);
    return 2;
  }
}
if (require.main === module) process.exitCode = main(process.argv.slice(2));
module.exports = { checkProject, visibleMarkdown };

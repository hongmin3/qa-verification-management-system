#!/usr/bin/env node
'use strict';
// 표현 개정용 읽기 전용 검사. 의미가 같은지는 사람이 대조해야 한다.
const fs = require('fs');
const { fencedMask, FENCE, closesFence } = require('./render-spec-html');
const HEAD = /^ {0,3}#{2,4}\s+((?:REQ|NFR|TEST)-[A-Z0-9]+-\d{3})\b/;
const IDS = /\b(?:REQ|NFR|TEST)-[A-Z0-9]+-\d{3}\b/g;
const norm = s => s.replace(/\s+/g, ' ').trim();
// Markdown code examples contain literal HTML comment delimiters.
function maskComments(original) {
  let fence = null, comment = false;
  return original.split('\n').map(line => {
    if (!comment && fence) {
      if (closesFence(line, fence)) fence = null;
      return line;
    }
    if (!comment) {
      const open = line.match(FENCE);
      if (open) { fence = open[1]; return line; }
      if (/^(?: {4}|\t)/.test(line)) return line;
    }
    let result = '', i = 0;
    while (i < line.length) {
      if (comment) {
        const end = line.indexOf('-->', i);
        const next = end < 0 ? line.length : end + 3;
        result += ' '.repeat(next - i); i = next;
        if (end >= 0) comment = false;
      } else if (line.startsWith('<!--', i)) {
        comment = true;
      } else if (line[i] === '`') {
        const delimiter = line.slice(i).match(/^`+/)[0];
        const end = line.indexOf(delimiter, i + delimiter.length);
        const next = end < 0 ? line.length : end + delimiter.length;
        result += line.slice(i, next); i = next;
      } else { result += line[i]; i++; }
    }
    return result;
  }).join('\n');
}
function parse(raw) {
  const original = raw.replace(/\r\n/g, '\n'), originalLines = original.split('\n');
  // Hide comments for heading discovery, but retain literal source inside cards and code.
  const text = maskComments(original);
  const lines = text.split('\n'), mask = fencedMask(lines);
  const order = [], blocks = new Map(), duplicates = [];
  let current = null, traceStart = -1, traceEnd = lines.length;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i], m = !mask[i] && line.match(HEAD);
    if (!mask[i] && /^ {0,3}##\s+12[.]\s/.test(line)) traceStart = i;
    else if (traceStart >= 0 && !mask[i] && /^ {0,3}##\s+/.test(line) && traceEnd === lines.length) traceEnd = i;
    if (m) {
      current = m[1]; order.push(current);
      if (blocks.has(current)) duplicates.push(current);
      else blocks.set(current, []);
    } else if (!mask[i] && /^ {0,3}#{1,3}\s/.test(line)) current = null;
    if (current) blocks.get(current).push(originalLines[i]);
  }
  const flows = []; let fence = null, flow = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (fence) {
      if (closesFence(line, fence)) fence = null;
      else if (flow && originalLines[i].trim()) flows.push(norm(originalLines[i]));
    } else {
      const m = line.match(FENCE);
      if (m) { fence = m[1]; flow = m[2] === 'flow'; }
    }
  }
  return { order, duplicates, blocks, flows, trace: traceStart < 0 ? '' : originalLines.slice(traceStart, traceEnd).join('\n').trim() };
}
function facts(text) {
  const values = new Set();
  for (const m of text.matchAll(/`([^`\n]+)`/g)) values.add('code:' + norm(m[1]));
  for (const m of text.matchAll(/"([^"\n]+)"|“([^”\n]+)”|'([^'\n]+)'|‘([^’\n]+)’/g)) values.add('quote:' + norm(m[1] || m[2] || m[3] || m[4]));
  for (const m of text.matchAll(IDS)) values.add('id:' + m[0]);
  // Remove list ordinals, not numbers embedded in prose such as 30건.
  const plain = text.replace(/^\s*\d+[.)]\s/gm, '').replace(/`[^`\n]*`/g, ' ');
  for (const m of plain.matchAll(/(?<![\w.])\d+(?:[.:,/~]\d+)*(?![\w.])/g)) values.add('number:' + m[0]);
  return values;
}
function compare(before, after) {
  const a = parse(before), b = parse(after), findings = [];
  const add = (code, detail) => findings.push({ code, detail });
  if (!a.order.length || !b.order.length) add('NO_IDS', '비교할 정의 ID가 없는 입력');
  if (JSON.stringify(a.order) !== JSON.stringify(b.order)) add('ID_ORDER', 'ID 집합 또는 순서 변경');
  for (const id of [...a.duplicates, ...b.duplicates]) add('ID_DUPLICATE', id);
  let inspected = 0;
  for (const [id, lines] of a.blocks) {
    const next = b.blocks.get(id); if (!next) continue;
    const targetFacts = facts(next.join('\n')), targetLines = new Set(next.map(norm));
    for (const fact of facts(lines.join('\n'))) {
      inspected++;
      const value = fact.slice(fact.indexOf(':') + 1);
      const present = targetFacts.has(fact) || (fact.startsWith('code:') && targetLines.has(value));
      if (!present) add('FACT_MISSING', `${id}: ${value}`);
    }
  }
  for (const line of a.flows) if (!b.flows.includes(line)) add('FLOW_MISSING', line);
  if (a.trace !== b.trace) add('TRACE_CHANGED', '12절 추적성 변경');
  return { ok: !findings.length, counts: { ids: a.order.length, comparedIds: [...a.blocks.keys()].filter(id => b.blocks.has(id)).length, facts: inspected, flows: a.flows.length }, findings };
}
function main(args) {
  const json = args.includes('--json'), files = args.filter(a => a !== '--json');
  if (files.length !== 2 || files.some(a => a.startsWith('--'))) throw new Error('사용법: spec-preservation.js <이전.md> <개정.md> [--json]');
  const result = compare(fs.readFileSync(files[0], 'utf8'), fs.readFileSync(files[1], 'utf8'));
  if (json) console.log(JSON.stringify(result));
  else {
    for (const f of result.findings) console.log(`${f.code}: ${f.detail}`);
    console.log(`SPEC_PRESERVATION ids=${result.counts.ids} compared=${result.counts.comparedIds} facts=${result.counts.facts} flows=${result.counts.flows} findings=${result.findings.length}`);
    console.log('문장의 의미 보존은 원문과 직접 대조해야 합니다.');
  }
  return result.ok ? 0 : 1;
}
module.exports = { compare };
if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); }
  catch (e) { console.error('SPEC_PRESERVATION_ERROR: ' + e.message); process.exitCode = 2; }
}

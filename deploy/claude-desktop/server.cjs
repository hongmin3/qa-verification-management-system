'use strict';
// Stable stdio bridge. No downloaded code, filesystem tools or local credentials.
const readline = require('node:readline');
const { isIP } = require('node:net');
const MAX_BYTES = 32 * 1024 * 1024;

function endpoint(value) {
  const url = new URL(value);
  if (url.username || url.password || url.search || url.hash ||
      !['http:', 'https:'].includes(url.protocol)) throw new Error('서버 주소 형식을 확인하세요.');
  const host = url.hostname;
  const local = host === 'localhost' || host === '[::1]' ||
    (isIP(host) === 4 && (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host) ||
      /^172\.(1[6-9]|2\d|3[01])\./.test(host)));
  if (url.protocol === 'http:' && !local) throw new Error('HTTP는 사내 IP에서만 사용하세요. 다른 주소는 HTTPS가 필요합니다.');
  url.pathname = url.pathname.replace(/\/$/, '') + '/vxvue-desktop/rpc';
  return url;
}

async function forward(message, url, token) {
  const response = await fetch(url, {
    method: 'POST', redirect: 'error',
    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
    body: JSON.stringify(message), signal: AbortSignal.timeout(180000)
  });
  if (!response.ok) throw new Error('QA 서버 연결 실패 HTTP ' + response.status + '. 주소·토큰·사내망 연결을 확인하세요.');
  if (response.status === 204) return null;
  const chunks = [];
  let bytes = 0;
  for await (const chunk of response.body) {
    bytes += chunk.length;
    if (bytes > MAX_BYTES) throw new Error('응답이 너무 큽니다. 페이지·조회 개수를 줄이세요.');
    chunks.push(chunk);
  }
  const reply = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (reply.jsonrpc !== '2.0' || reply.id !== message.id || (!reply.result && !reply.error)) {
    throw new Error('QA 서버 응답 형식이 맞지 않습니다.');
  }
  return reply;
}

async function main() {
  let url;
  const token = process.env.VXVUE_ACCESS_TOKEN || '';
  try {
    url = endpoint(process.env.VXVUE_SERVER_URL || '');
    if (token.length < 32 || /[\r\n]/.test(token)) throw new Error('팀 접근 토큰을 설정하세요.');
  } catch (error) {
    process.stderr.write('VXvue AI: ' + error.message + '\n');
    process.exitCode = 1;
    return;
  }
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  // Serialize to preserve initialize/notification ordering and bound concurrent work.
  for await (const line of input) {
    if (!line.trim()) continue;
    let message;
    try {
      if (Buffer.byteLength(line, 'utf8') > 1024 * 1024) throw new Error('요청이 너무 큽니다.');
      message = JSON.parse(line);
      const reply = await forward(message, url, token);
      if (reply) process.stdout.write(JSON.stringify(reply) + '\n');
    } catch (error) {
      if (message && Object.hasOwn(message, 'id')) {
        const detail = /^QA 서버|^응답|^요청/.test(error.message) ? error.message : 'QA 서버 연결에 실패했습니다. 사내망·서버 상태를 확인하세요.';
        process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: detail } }) + '\n');
      } else {
        process.stderr.write('VXvue AI: 요청 처리 실패\n');
      }
    }
  }
}

if (require.main === module) main();
module.exports = { endpoint, forward };

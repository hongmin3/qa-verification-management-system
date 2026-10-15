'use strict';
// VXvue AI stdio bridge (REQ-VXVUECHATBOT-011·013). No downloaded code, filesystem tools or Polarion credentials.
// First run trades the one-time ticket in enrollment.json for a per-PC key kept in ~/.vxvue-ai/credentials.json.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { isIP } = require('node:net');

const BRIDGE_VERSION = '1.1.0';
const MAX_BYTES = 32 * 1024 * 1024;
const RETRY_CODES = new Set(['device_revoked', 'device_expired', 'unknown_credential', 'missing_credential']);

class BridgeError extends Error {
  constructor(kind, message, code) {
    super(message);
    this.kind = kind;
    this.code = code || kind;
  }
}

function baseUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new BridgeError('config', '확장 설치 파일의 서버 주소가 올바르지 않습니다. 안내 화면에서 설치 파일을 다시 받으세요.');
  }
  if (url.username || url.password || url.search || url.hash || !['http:', 'https:'].includes(url.protocol)) {
    throw new BridgeError('config', '확장 설치 파일의 서버 주소 형식이 맞지 않습니다. 안내 화면에서 설치 파일을 다시 받으세요.');
  }
  const host = url.hostname;
  const local = host === 'localhost' || host === '[::1]' ||
    (isIP(host) === 4 && (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host) ||
      /^172\.(1[6-9]|2\d|3[01])\./.test(host)));
  if (url.protocol === 'http:' && !local) {
    throw new BridgeError('config', 'HTTP 연결은 사내 IP 주소에서만 허용됩니다. 관리자에게 HTTPS 주소를 확인하세요.');
  }
  return url.origin + url.pathname.replace(/\/+$/, '');
}

function endpoint(value, suffix = '/vxvue-desktop/rpc') {
  return new URL(baseUrl(value) + suffix);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = file + '.' + process.pid + '.tmp';
  fs.writeFileSync(temp, JSON.stringify(value, null, 2), { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(temp, file);
}

function networkKind(error) {
  const code = (error && error.cause && error.cause.code) || (error && error.code) || '';
  if (error && (error.name === 'TimeoutError' || error.name === 'AbortError')) return ['timeout', code || 'timeout'];
  if (code === 'ECONNREFUSED') return ['refused', code];
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return ['dns', code];
  if (['ETIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT', 'EHOSTUNREACH', 'ENETUNREACH', 'ENETDOWN'].includes(code)) return ['unreachable', code];
  return ['network', code || 'unknown'];
}

class Bridge {
  constructor(options) {
    this.serverUrl = options.serverUrl || '';
    this.legacyToken = options.legacyToken || '';
    this.enrollment = options.enrollment || null;
    this.home = options.home;
    this.fetch = options.fetchImpl || fetch;
    this.timeoutMs = options.timeoutMs || 180000;
    this.ticketRejected = false;  // set only when the server answered; a network failure keeps the ticket usable
    this.authFailure = null;      // the server's own reason, kept for later calls in this process
    try {
      this.base = baseUrl(this.serverUrl);
    } catch (error) {
      this.base = null;
      this.configError = error;
    }
  }

  get store() { return path.join(this.home, 'credentials.json'); }
  get toolCache() { return path.join(this.home, 'tools-cache.json'); }
  get guide() { return (this.base || '') + '/vxvue-desktop'; }

  stored() {
    const all = readJson(this.store) || {};
    const value = all[this.base];
    return value && typeof value.token === 'string' && value.token.length >= 32 ? value : null;
  }

  forget(token) {
    const all = readJson(this.store) || {};
    if (all[this.base] && all[this.base].token === token) {
      delete all[this.base];
      writeJson(this.store, all);
    }
  }

  async credential() {
    if (this.configError) throw this.configError;
    if (this.legacyToken && !this.enrollment) return { token: this.legacyToken, legacy: true };
    const saved = this.stored();
    if (saved) return saved;
    return this.enroll();
  }

  async enroll() {
    const ticket = this.enrollment && this.enrollment.ticket;
    if (!ticket || this.ticketRejected) {
      throw this.authFailure || new BridgeError('auth', '이 PC에 저장된 연결 키가 없고 설치 파일의 등록 번호도 쓸 수 없습니다. ' +
        `안내 화면(${this.guide})에서 설치 파일을 새로 받아 설치하세요.`, 'needs_reinstall');
    }
    const body = { ticket, client: 'desktop', hostname: os.hostname(), username: os.userInfo().username, bridge_version: BRIDGE_VERSION };
    let response;
    try {
      response = await this.fetch(endpoint(this.base, '/vxvue-desktop/enroll'), {
        method: 'POST', redirect: 'error', headers: { 'Content-Type': 'application/json', 'X-VXvue-Bridge': BRIDGE_VERSION },
        body: JSON.stringify(body), signal: AbortSignal.timeout(30000)
      });
    } catch (error) {
      const [kind, code] = networkKind(error);
      throw new BridgeError(kind, '', code);
    }
    const reply = await response.json().catch(() => ({}));
    if (!response.ok || typeof reply.token !== 'string') {
      if (reply.code === 'ticket_used') {
        const saved = this.stored();  // another bridge process on this PC may have just enrolled
        if (saved) return saved;
      }
      const failure = new BridgeError(response.status === 403 ? 'forbidden' : response.status >= 500 ? 'server' : 'auth',
        reply.detail || `등록 실패 HTTP ${response.status}`, reply.code || `http_${response.status}`);
      if (failure.kind === 'auth') {
        this.ticketRejected = true;
        this.authFailure = this.authFailure || failure;
      }
      throw failure;
    }
    const all = readJson(this.store) || {};
    all[this.base] = { device_id: reply.device_id, token: reply.token, client: 'desktop', enrolled_at: new Date().toISOString() };
    writeJson(this.store, all);
    process.stderr.write(`VXvue AI: 이 PC를 등록했습니다(device ${reply.device_id}).\n`);
    return all[this.base];
  }

  async post(message) {
    let credential = await this.credential();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      let response;
      try {
        response = await this.fetch(endpoint(this.base), {
          method: 'POST', redirect: 'error',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + credential.token, 'X-VXvue-Bridge': BRIDGE_VERSION },
          body: JSON.stringify(message), signal: AbortSignal.timeout(this.timeoutMs)
        });
      } catch (error) {
        const [kind, code] = networkKind(error);
        throw new BridgeError(kind, '', code);
      }
      if (response.status === 401 && !credential.legacy && attempt === 0) {
        const reply = await response.json().catch(() => ({}));
        if (RETRY_CODES.has(reply.code)) {
          // Keep the key's own reason (revoked, expired): it explains more than the spent ticket does.
          const rejected = new BridgeError('auth', reply.detail || '연결 키가 거절됐습니다.', reply.code);
          this.forget(credential.token);
          try {
            credential = await this.enroll();
            continue;
          } catch (enrollError) {
            if (enrollError.kind !== 'auth') throw enrollError;
            this.authFailure = rejected;
            throw rejected;
          }
        }
        throw new BridgeError('auth', reply.detail || '인증에 실패했습니다.', reply.code || 'http_401');
      }
      if (!response.ok) {
        const reply = await response.json().catch(() => ({}));
        const kind = response.status === 403 ? 'forbidden' : response.status === 401 ? 'auth' : response.status >= 500 ? 'server' : 'http';
        throw new BridgeError(kind, reply.detail || '', reply.code || `http_${response.status}`);
      }
      if (response.status === 204 || response.status === 202) return null;
      const chunks = [];
      let bytes = 0;
      for await (const chunk of response.body) {
        bytes += chunk.length;
        if (bytes > MAX_BYTES) throw new BridgeError('size', '응답이 너무 큽니다. 페이지·조회 개수를 줄이세요.');
        chunks.push(chunk);
      }
      let reply;
      try {
        reply = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch {
        throw new BridgeError('format', 'QA 서버 응답 형식이 맞지 않습니다.');
      }
      if (reply.jsonrpc !== '2.0' || reply.id !== message.id || (!reply.result && !reply.error)) {
        throw new BridgeError('format', 'QA 서버 응답 형식이 맞지 않습니다.');
      }
      return reply;
    }
    throw new BridgeError('auth', '연결 키 재등록 뒤에도 인증에 실패했습니다.', 'auth_retry');
  }

  explain(error) {
    const where = `서버: ${this.base || this.serverUrl || '(없음)'}`;
    const help = `설치 안내: ${this.guide}`;
    const text = {
      refused: 'QA 서버가 연결을 거부했습니다. 서버 프로그램이 멈췄거나 주소·포트가 바뀌었을 수 있습니다. 브라우저로 설치 안내 화면이 열리는지 확인하고, 열리지 않으면 QA 서버 관리자에게 알리세요.',
      unreachable: 'QA 서버에 닿지 않습니다. 사내망 또는 VPN 연결을 확인하세요. 연결 후 다시 질문하면 자동으로 이어집니다.',
      dns: 'QA 서버 이름을 찾지 못했습니다. 사내망·VPN과 DNS 연결을 확인하세요.',
      timeout: 'QA 서버 응답이 제한 시간 안에 오지 않았습니다. 잠시 뒤 다시 시도하거나 조회 범위를 줄이세요.',
      network: 'QA 서버와 통신하지 못했습니다. 사내망·VPN 연결을 확인하세요.',
      server: 'QA 서버에서 오류가 났습니다. 잠시 뒤 다시 시도하고, 반복되면 QA 서버 관리자에게 알리세요.',
    }[error.kind];
    const detail = text || error.message || '알 수 없는 오류입니다.';
    return `VXvue AI 연결 실패 [${error.code || error.kind}]: ${detail}\n${where}\n${help}`;
  }

  fallbackTools() {
    const cached = readJson(this.toolCache);
    if (cached && cached.base === this.base && Array.isArray(cached.tools) && cached.tools.length) return cached.tools;
    return [{
      name: 'get_status',
      description: 'VXvue 사양·매뉴얼·TC·Polarion 질문의 첫 단계. QA 서버 연결 상태와 실패 원인을 확인한다.',
      inputSchema: { type: 'object', properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }
    }];
  }

  async handle(message) {
    const hasId = Object.hasOwn(message, 'id');
    try {
      const reply = await this.post(message);
      if (message.method === 'tools/list' && reply && reply.result && Array.isArray(reply.result.tools)) {
        try { writeJson(this.toolCache, { base: this.base, tools: reply.result.tools }); } catch { /* cache is optional */ }
      }
      return reply;
    } catch (error) {
      if (!(error instanceof BridgeError)) error = new BridgeError('network', '', 'unexpected');
      const diagnosis = this.explain(error);
      process.stderr.write('VXvue AI: ' + diagnosis.split('\n')[0] + '\n');
      if (!hasId) return null;
      if (message.method === 'initialize') {
        const requested = message.params && message.params.protocolVersion;
        return { jsonrpc: '2.0', id: message.id, result: {
          protocolVersion: typeof requested === 'string' ? requested : '2025-06-18',
          capabilities: { tools: {} }, serverInfo: { name: 'vxvue-ai', version: BRIDGE_VERSION },
          instructions: 'VXvue AI 연결 확장입니다. 지금은 QA 서버에 연결하지 못했습니다. VXvue 질문을 받으면 get_status를 호출해 원인과 할 일을 사용자에게 그대로 안내하세요.\n' + diagnosis
        } };
      }
      if (message.method === 'tools/list') return { jsonrpc: '2.0', id: message.id, result: { tools: this.fallbackTools() } };
      if (message.method === 'tools/call') {
        return { jsonrpc: '2.0', id: message.id, result: { content: [{ type: 'text', text: diagnosis }], isError: true } };
      }
      if (message.method === 'ping') return { jsonrpc: '2.0', id: message.id, result: {} };
      return { jsonrpc: '2.0', id: message.id, error: { code: -32000, message: diagnosis } };
    }
  }
}

function fromEnvironment() {
  const enrollment = readJson(path.join(__dirname, 'enrollment.json'));
  return new Bridge({
    serverUrl: process.env.VXVUE_SERVER_URL || (enrollment && enrollment.server_url) || '',
    legacyToken: (process.env.VXVUE_ACCESS_TOKEN || '').length >= 32 ? process.env.VXVUE_ACCESS_TOKEN : '',
    enrollment,
    home: process.env.VXVUE_AI_HOME || path.join(os.homedir(), '.vxvue-ai')
  });
}

async function main() {
  const bridge = fromEnvironment();
  process.stderr.write(`VXvue AI bridge ${BRIDGE_VERSION} 시작: ${bridge.base || '(서버 주소 오류)'}\n`);
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  // Serialize to preserve initialize/notification ordering and bound concurrent work.
  for await (const line of input) {
    if (!line.trim()) continue;
    let message;
    try {
      if (Buffer.byteLength(line, 'utf8') > 1024 * 1024) throw new Error('요청이 너무 큽니다.');
      message = JSON.parse(line);
      if (!message || typeof message !== 'object' || Array.isArray(message)) throw new Error('JSON-RPC 객체가 아닙니다.');
    } catch (error) {
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: null, error: { code: -32700, message: '요청 형식 오류: ' + error.message } }) + '\n');
      continue;
    }
    const reply = await bridge.handle(message);
    if (reply) process.stdout.write(JSON.stringify(reply) + '\n');
  }
}

if (require.main === module) main();
module.exports = { Bridge, BridgeError, baseUrl, endpoint, BRIDGE_VERSION };

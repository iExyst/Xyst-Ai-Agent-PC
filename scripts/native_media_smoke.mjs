import { chromium, expect } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises';
import { randomUUID, createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import path from 'node:path';

if (process.platform !== 'win32') throw new Error('Native media acceptance requires Windows.');

const root = path.resolve('.acceptance', `native-media-${randomUUID()}`);
const localAppData = path.join(root, 'localappdata');
const appData = path.join(root, 'appdata');
const workspace = path.join(root, 'workspace');
const input = path.join(root, 'input');
const debugPort = Number(process.env.XAA_NATIVE_DEBUG_PORT || 19227);
const model = process.env.XAA_NATIVE_MEDIA_MODEL || 'qwen/qwen3.5-9b';
await Promise.all([root, localAppData, appData, workspace, input].map(directory => mkdir(directory, { recursive: true })));

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(name, payload) {
  const type = Buffer.from(name, 'ascii');
  const body = Buffer.concat([type, payload]);
  const header = Buffer.alloc(4);
  header.writeUInt32BE(payload.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(body));
  return Buffer.concat([header, body, checksum]);
}

function makeImage() {
  const width = 160, height = 100;
  const pixels = Buffer.alloc(height * (1 + width * 3));
  for (let y = 0; y < height; y++) {
    const row = y * (1 + width * 3);
    pixels[row] = 0;
    for (let x = 0; x < width; x++) {
      let color = [255, 255, 255];
      if ((x - 42) ** 2 + (y - 50) ** 2 <= 16 ** 2 || (x - 82) ** 2 + (y - 50) ** 2 <= 16 ** 2) color = [220, 35, 35];
      else if (x >= 116 && x < 146 && y >= 35 && y < 65) color = [35, 70, 220];
      const offset = row + 1 + x * 3;
      pixels.set(color, offset);
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 2;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk('IHDR', header), pngChunk('IDAT', deflateSync(pixels)), pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function makeTextPdf(text) {
  const escaped = text.replaceAll('\\', '\\\\').replaceAll('(', '\\(').replaceAll(')', '\\)');
  const stream = Buffer.from(`BT /F1 12 Tf 72 720 Td (${escaped}) Tj ET`, 'ascii');
  const objects = [
    Buffer.from('<< /Type /Catalog /Pages 2 0 R >>'),
    Buffer.from('<< /Type /Pages /Kids [3 0 R] /Count 1 >>'),
    Buffer.from('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'),
    Buffer.from('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'),
    Buffer.concat([Buffer.from(`<< /Length ${stream.length} >>\nstream\n`), stream, Buffer.from('\nendstream')]),
  ];
  const parts = [Buffer.from('%PDF-1.4\n%\xe2\xe3\xcf\xd3\n', 'latin1')];
  const offsets = [0];
  let position = parts[0].length;
  objects.forEach((object, index) => {
    offsets.push(position);
    const entry = Buffer.concat([Buffer.from(`${index + 1} 0 obj\n`), object, Buffer.from('\nendobj\n')]);
    parts.push(entry); position += entry.length;
  });
  const xref = position;
  const lines = [Buffer.from(`xref\n0 ${offsets.length}\n0000000000 65535 f \n`)];
  for (const offset of offsets.slice(1)) lines.push(Buffer.from(`${String(offset).padStart(10, '0')} 00000 n \n`));
  lines.push(Buffer.from(`trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`));
  return Buffer.concat([...parts, ...lines]);
}

function makeDocx(text) {
  const document = Buffer.from(
    '<?xml version="1.0" encoding="UTF-8"?>' +
      '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
      `<w:body><w:p><w:r><w:t>${text}</w:t></w:r></w:p></w:body></w:document>`,
  );
  const contentTypes = Buffer.from(
    '<?xml version="1.0" encoding="UTF-8"?>' +
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
      '</Types>',
  );
  const relationships = Buffer.from(
    '<?xml version="1.0" encoding="UTF-8"?>' +
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>' +
      '</Relationships>',
  );
  const entries = [
    ['[Content_Types].xml', contentTypes],
    ['_rels/.rels', relationships],
    ['word/document.xml', document],
  ];
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const [name, data] of entries) {
    const filename = Buffer.from(name);
    const checksum = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt32LE(checksum, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(filename.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, filename, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt32LE(checksum, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(filename.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, filename);
    offset += local.length + filename.length + data.length;
  }
  const centralDirectory = Buffer.concat(centralParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localParts, centralDirectory, end]);
}

const imageBytes = makeImage();
const imagePath = path.join(input, 'two-red-circles.png');
await writeFile(imagePath, imageBytes);
const marker = `DOC-${randomUUID().replaceAll('-', '').toUpperCase()}`;
const pdfBytes = makeTextPdf(`Document verification marker: ${marker}`);
const pdfPath = path.join(input, 'verification.pdf');
await writeFile(pdfPath, pdfBytes);
const docxMarker = `DOCX-${randomUUID().replaceAll('-', '').toUpperCase()}`;
const docxBytes = makeDocx(`Document verification marker: ${docxMarker}`);
const docxPath = path.join(input, 'verification.docx');
await writeFile(docxPath, docxBytes);

const manifest = JSON.parse(await readFile('build/native-dev/manifest.json', 'utf8'));
const nativeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !['PATH', 'PYTHONPATH', 'VIRTUAL_ENV'].includes(key.toUpperCase())));
nativeEnv.PATH = path.join(process.env.SystemRoot, 'System32');
const desktop = spawn(path.resolve('build/native-dev/xaa-desktop.exe'), [], {
  windowsHide: true, stdio: 'ignore',
  env: { ...nativeEnv, APPDATA: appData, LOCALAPPDATA: localAppData, XAA_TEST_HIDDEN: '1', XAA_DATA_DIR: root,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}` },
});
let browser, connection, instance, report;
const runs = [];

async function api(pathname, init = {}) {
  const response = await fetch(`${connection.base_url}${pathname}`, {
    ...init, headers: { Authorization: `Bearer ${connection.token}`, ...(init.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`Native media API ${pathname} returned ${response.status}: ${await response.text()}`);
  return response;
}

async function finishRun(
  page, objective, fileInput, filePath, attachedText, expectedAnswer, expectedArtifacts,
  sourceDocumentBytes = pdfBytes, expectedTextMarker = marker,
  screenshot = fileInput === '#run-image' ? 'image-run.png' : 'pdf-run.png',
) {
  await page.locator(fileInput).setInputFiles(filePath);
  await expect(page.getByText(attachedText, { exact: false })).toBeVisible({ timeout: 10000 });
  await page.getByLabel('Objective', { exact: true }).fill(objective);
  await page.getByRole('button', { name: 'Start objective', exact: true }).click();

  let run;
  for (let attempt = 0; attempt < 180; attempt++) {
    const history = await (await api('/runs?limit=1')).json();
    run = history.items?.[0];
    if (run && ['verifying', 'completed', 'failed', 'blocked'].includes(run.state)) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  if (!run || run.state !== 'verifying') throw new Error(`Media run did not reach review: ${run?.state ?? 'missing'}`);
  const events = await (await api(`/runs/${run.id}/events`)).json();
  const messages = events.filter(event => event.event_type === 'model.message').map(event => event.payload.message);
  const finalMessage = messages.at(-1);
  const answer = finalMessage?.content ?? '';
  const calls = messages.flatMap(message => message.tool_calls ?? []);
  const answerAccepted = expectedAnswer instanceof RegExp ? expectedAnswer.test(answer) : answer.includes(expectedAnswer);
  const result = { run_id: run.id, state: run.state, answer, tool_call_count: calls.length };
  runs.push(result);
  if (!answerAccepted) throw new Error(`Model answer omitted expected media evidence: ${answer}`);
  if (calls.length !== 0) throw new Error('Media-only acceptance unexpectedly proposed a tool call.');

  const artifacts = await (await api(`/runs/${run.id}/artifacts`)).json();
  if (JSON.stringify(artifacts.map(item => item.provenance).sort()) !== JSON.stringify(expectedArtifacts.slice().sort())) {
    throw new Error(`Unexpected artifact provenance: ${JSON.stringify(artifacts.map(item => item.provenance))}`);
  }
  const downloaded = {};
  for (const artifact of artifacts) {
    const bytes = Buffer.from(await (await api(`/artifacts/${artifact.id}`)).arrayBuffer());
    downloaded[artifact.provenance] = {
      bytes: bytes.length,
      sha256: createHash('sha256').update(bytes).digest('hex'),
    };
    if (artifact.provenance === 'user.input_image' && !bytes.equals(imageBytes)) throw new Error('Image artifact bytes changed.');
    if (artifact.provenance === 'user.input_document' && !bytes.equals(sourceDocumentBytes)) throw new Error('Document artifact bytes changed.');
    if (artifact.provenance === 'user.input_document_text' && !bytes.toString('utf8').includes(expectedTextMarker)) {
      throw new Error('Extracted-text artifact lost its verification marker.');
    }
  }
  await expect(page.getByText(expectedAnswer, { exact: false }).last()).toBeVisible({ timeout: 10000 });
  await page.screenshot({ path: path.join(root, screenshot), fullPage: true });
  const workspaceFiles = await readdir(workspace);
  if (workspaceFiles.length !== 0) throw new Error('Read-only media task changed the workspace.');
  Object.assign(result, {
    artifact_provenances: artifacts.map(item => item.provenance), artifacts: downloaded,
    workspace_unchanged: true,
  });
  return result;
}

try {
  let connected = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`); connected = true; break; }
    catch { if (desktop.exitCode !== null) throw new Error('Native app exited during media acceptance startup'); await new Promise(resolve => setTimeout(resolve, 250)); }
  }
  if (!connected || !browser) throw new Error('Native WebView did not expose its test connection');
  const page = browser.contexts()[0].pages()[0];
  await page.getByLabel('Local model').waitFor({ timeout: 20000 });
  connection = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('engine_connection'));
  const models = await (await api('/models')).json();
  const selected = models.find(item => item.id === model);
  if (!selected || selected.capabilities?.vision !== true) throw new Error(`Selected local model does not advertise vision: ${model}`);
  if (selected.loaded) throw new Error('Refusing media acceptance because the selected model was already loaded before this harness owned it.');
  const load = await api('/models/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model, context_length: 16384 }) });
  instance = (await load.json()).instance_id;
  await page.getByLabel('Local model').selectOption(model);
  await expect.poll(async () => (await (await api('/providers/lm-studio/config')).json()).selected_model).toBe(model);
  await page.getByText('Run limits', { exact: true }).click();
  await page.getByLabel('Output tokens per request (optional)').fill('512');
  await page.getByRole('button', { name: 'Run harmless tool-use diagnostic', exact: true }).click();
  await expect(page.getByText('The selected model completed a validated harmless tool round trip.', { exact: true })).toBeVisible({ timeout: 120000 });
  await page.getByLabel('Workspace path').fill(workspace);

  const imageResult = await finishRun(
    page,
    'Inspect the attached image. Count the red circles. Reply with a short line giving the count and color, with no explanation.',
    '#run-image', imagePath, 'Attached: two-red-circles.png', /(?:\b2\b|\btwo\b).*\bred\b|\bred\b.*(?:\b2\b|\btwo\b)/i, ['user.input_image'],
  );
  const documentResult = await finishRun(
    page,
    `Read the attached PDF and report the exact value after “Document verification marker:”. Reply with the value only.`,
    '#run-document', pdfPath, 'Attached: verification.pdf', marker,
    ['user.input_document', 'user.input_document_text'],
  );
  const docxResult = await finishRun(
    page,
    'Read the attached Word document and report the exact value after “Document verification marker:”. Reply with the value only.',
    '#run-document', docxPath, 'Attached: verification.docx', docxMarker,
    ['user.input_document', 'user.input_document_text'], docxBytes, docxMarker, 'docx-run.png',
  );

  const unloaded = await api('/models/unload', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ instance_id: instance }),
  });
  if (!unloaded.ok) throw new Error(`Could not unload acceptance-owned model instance ${instance}.`);
  instance = null;

  await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('plugin:window|close', { label: 'main' }))
    .catch(error => {
      if (!/target page, context or browser has been closed/i.test(String(error))) throw error;
    });
  for (let attempt = 0; attempt < 80 && desktop.exitCode === null; attempt++) await new Promise(resolve => setTimeout(resolve, 250));
  if (desktop.exitCode !== 0) throw new Error(`Native app did not close cleanly: ${desktop.exitCode}`);
  let engineStopped = false;
  for (let attempt = 0; attempt < 20; attempt++) {
    try { await fetch(`${connection.base_url}/health`, { signal: AbortSignal.timeout(500) }); }
    catch { engineStopped = true; break; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!engineStopped) throw new Error('Bundled engine stayed reachable after native desktop exit.');
  const graceful = desktop.exitCode === 0 && engineStopped;
  if (!graceful) throw new Error('Native desktop did not exit cleanly with its bundled engine stopped.');
  report = {
    passed: true,
    model,
    vision_advertised: true,
    native_tool_diagnostic: true,
    image: imageResult,
    document: documentResult,
    docx: docxResult,
    final_review_left_pending: true,
    engine_stopped: engineStopped,
    graceful_shutdown: graceful,
    build_sha256: manifest,
  };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  console.log(report, root);
} catch (error) {
  report = { passed: false, model, error: String(error), partial_runs: runs };
  await writeFile(path.join(root, 'report.json'), JSON.stringify(report, null, 2));
  if (browser) await browser.contexts()[0]?.pages()[0]?.screenshot({ path: path.join(root, 'failure.png'), fullPage: true }).catch(() => {});
  throw error;
} finally {
  if (instance && connection) {
    await fetch(`${connection.base_url}/models/unload`, {
      method: 'POST', headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ instance_id: instance }),
    }).catch(() => {});
  }
  await browser?.close().catch(() => {});
  if (desktop.exitCode === null) execFileSync('taskkill', ['/PID', String(desktop.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
}

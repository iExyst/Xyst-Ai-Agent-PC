import { readFile, writeFile, mkdir } from 'node:fs/promises';
import openapiTS, { astToString } from 'openapi-typescript';

const schema = new URL('../contracts/openapi.json', import.meta.url);
const target = new URL('../apps/desktop/src/generated/api.d.ts', import.meta.url);
const output = astToString(await openapiTS(schema));
if (process.argv.includes('--check')) {
  if (await readFile(target, 'utf8') !== output) {
    throw new Error('Generated API types are stale. Run npm run contracts.');
  }
  console.log('Generated API types are current.');
} else {
  await mkdir(new URL('.', target), { recursive: true });
  await writeFile(target, output);
  console.log('Generated desktop API types.');
}

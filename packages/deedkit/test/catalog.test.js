import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {DeedKitClient, toolCatalog, tools} from '../lib/index.js';

// Run from the repository checkout: the gateway catalog is the source of truth.
const catalogUrl = new URL('../../../services/gateway/src/agent_gateway/catalog.json', import.meta.url);

test('client tools equal the gateway catalog plus platform_capabilities', {skip: !existsSync(catalogUrl)}, () => {
  const catalog = JSON.parse(readFileSync(catalogUrl, 'utf8'));
  const expected = [...catalog.map(tool => ({name: tool.name, readOnly: tool.annotations?.readOnlyHint === true})),
    {name: 'platform_capabilities', readOnly: true}].sort((a, b) => a.name.localeCompare(b.name));
  assert.deepEqual(toolCatalog.map(row => ({...row})), expected,
    'Update packages/deedkit/lib/tools.json and index.d.ts for the changed gateway catalog');
  assert.equal(new Set(tools).size, tools.length);
});

test('TypeScript ToolName union lists exactly the client tools', () => {
  const declaration = readFileSync(new URL('../lib/index.d.ts', import.meta.url), 'utf8');
  const union = declaration.slice(declaration.indexOf('export type ToolName ='), declaration.indexOf(';'));
  assert.deepEqual([...union.matchAll(/'([a-z_]+)'/g)].map(match => match[1]).sort(), [...tools].sort());
});

test('read-only classification is exposed and unknown tools are refused', () => {
  assert.equal(DeedKitClient.isReadOnly('messages_search'), true);
  assert.equal(DeedKitClient.isReadOnly('messages_send_message'), false);
  assert.equal(DeedKitClient.isReadOnly('platform_capabilities'), true);
  assert.throws(() => DeedKitClient.isReadOnly('search'), TypeError);
});

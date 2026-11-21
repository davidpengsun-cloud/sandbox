import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('Ask setting docs describe the legacy brand field and direct app visibility to the app flags', () => {
  const demo = new URL('../../../', import.meta.url);
  const guide = readFileSync(new URL('kit/INSTALL.md', demo), 'utf8');
  const env = readFileSync(new URL('.env.example', demo), 'utf8');
  const descriptions = [
    guide.split('\n').find((line) => line.startsWith('| `PORTAL_ASK` |')),
    env.split('\n').find((line) => line.startsWith('PORTAL_ASK=')),
  ];
  for (const description of descriptions) {
    assert.match(description, /legacy.*kge_url/);
    assert.match(description, /GlobalBar/);
    assert.match(description, /Ask buttons, panels and routes.*stay available/);
    for (const app of ['CONTEXT', 'CERTIFY', 'DISCOVER']) assert.ok(description.includes(`PORTAL_APP_${app}`));
    assert.doesNotMatch(description, /Ask tab|terraform apply|outside Terraform|portal service yourself/);
  }
  for (const description of [
    guide.split('\n').find((line) => line.startsWith('| `KGE_PUBLIC_URL` |')),
    env.split('\n').find((line) => line.startsWith('KGE_PUBLIC_URL=')),
  ]) {
    assert.match(description, /legacy.*kge_url/);
    assert.match(description, /GlobalBar/);
    assert.match(description, /PORTAL_ASK=off/);
    assert.doesNotMatch(description, /Ask.*link/);
  }
});

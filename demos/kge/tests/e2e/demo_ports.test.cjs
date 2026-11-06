'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'demo_script.cjs'), 'utf8');
function ports(env) {
  const prefix = source.slice(0, source.indexOf('let playwright;'));
  return vm.runInNewContext(`${prefix}\nJSON.stringify({ PORT, PORT2, PORT3, PORT4, TEST_PORT, SIDE_BY_SIDE });`, {
    require, __dirname, process: { env },
  });
}

test('unset runner variables retain the walkthrough defaults', () => {
  assert.deepEqual(JSON.parse(ports({})), {
    PORT: 8000, PORT2: 8001, PORT3: 8002, PORT4: 8009, TEST_PORT: 8003, SIDE_BY_SIDE: true,
  });
});

test('adjacent lanes use only their assigned ports and reuse profile servers sequentially', () => {
  const lanes = [
    { PORT: '8210', PORTAL_PORT: '8310', TEST_PORT: '8215' },
    { PORT: '8220', PORTAL_PORT: '8320', TEST_PORT: '8225' },
  ].map((env) => JSON.parse(ports(env)));
  for (const lane of lanes) {
    assert.equal(lane.PORT2, lane.PORT);
    assert.equal(lane.PORT3, lane.PORT);
    assert.equal(lane.SIDE_BY_SIDE, false);
  }
  assert.equal(lanes[0].PORT4, 8310);
  assert.equal(lanes[1].PORT4, 8320);
  assert.equal(lanes[0].TEST_PORT, 8215);
  assert.equal(lanes[1].TEST_PORT, 8225);
  assert.equal(Object.values(lanes[0]).filter(Number.isInteger).some((port) =>
    Object.values(lanes[1]).filter(Number.isInteger).includes(port)), false);
});

test('legacy explicit profile ports remain available outside a lane', () => {
  const configured = JSON.parse(ports({ PORT2: '8101', PORT3: '8102', PORT4: '8109' }));
  assert.equal(configured.PORT2, 8101);
  assert.equal(configured.PORT3, 8102);
  assert.equal(configured.PORT4, 8109);
});

test('explicit gate lane ports stop the previous portal before starting another profile', async () => {
  const env = { PORT: '8210', PORT2: '8211', PORT3: '8212', PORTAL_PORT: '8310', TEST_PORT: '8215' };
  const lane = JSON.parse(ports(env));
  assert.equal(lane.PORT2, 8211);
  assert.equal(lane.PORT3, 8212);
  assert.equal(lane.PORT4, 8310);
  assert.equal(lane.TEST_PORT, 8215);
  assert.equal(lane.SIDE_BY_SIDE, false);

  const events = [];
  const meridian = { portal: 8310 };
  const thornbury = { portal: 8310 };
  let bound = meridian;
  const started = new Error('Thornbury start reached');
  const sandbox = {
    state: {
      meridian, thornbury, context: {}, page: { close: async () => {} },
      browser: { newContext: async () => ({}) },
    },
    SIDE_BY_SIDE: lane.SIDE_BY_SIDE, PORTAL_PORT: env.PORTAL_PORT,
    PORT: lane.PORT, PORT2: lane.PORT2, PORT3: lane.PORT3, TOY: 'fellmoor', HOST: '127.0.0.1', HOST2: '127.0.0.1', say: () => {},
    stop: async (server) => {
      assert.equal(bound, server);
      events.push('stop');
      server.exited = true;
      bound = null;
    },
    serve: async (args, port) => {
      assert.equal(bound, null, 'the previous portal must be stopped before serve can bind PORTAL_PORT');
      events.push(`serve:${port}`);
      if (port === lane.PORT2) {
        bound = thornbury;
        thornbury.exited = false;
        throw started;
      }
      bound = port === lane.PORT3 ? { portal: 8310 } : meridian;
      bound.exited = false;
      return bound;
    },
    open: async () => ({ close: async () => {} }),
  };
  const step = source.slice(source.indexOf('async function twoProfiles()'), source.indexOf('// The client onboarded from scratch'));
  vm.runInNewContext(step, sandbox);
  await assert.rejects(sandbox.twoProfiles(), (error) => error === started);
  await sandbox.resumeMeridian(thornbury);
  const fellmoor = source.slice(source.indexOf('  // Its page: on PORT3'), source.indexOf('  // Its page in dark mode'));
  await vm.runInNewContext(`(async () => { ${fellmoor} })()`, sandbox);
  await sandbox.resumeMeridian(sandbox.state.fellmoor);
  assert.deepEqual(events, ['stop', 'serve:8211', 'stop', 'serve:8210', 'stop', 'serve:8212', 'stop', 'serve:8210']);
});

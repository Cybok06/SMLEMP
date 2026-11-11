const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const esbuild = require('../Frontend/Customer_Support/node_modules/esbuild');

async function check(source, module = false) {
  const timers = new Map(), listeners = new Map(); let id = 0, calls = 0, release;
  const document = {hidden: false, addEventListener: (name, cb) => listeners.set(name, cb), removeEventListener: name => listeners.delete(name)};
  const context = {document, window: {}, exports: {}, module: {exports: {}},
    setTimeout: (cb, delay) => { timers.set(++id, {cb, delay}); return id; }, clearTimeout: key => timers.delete(key),
    fetch: () => { calls++; return new Promise(resolve => {release = resolve;}); }};
  context.exports = context.module.exports;
  vm.runInNewContext(source, context);
  const api = module ? context.module.exports : {poll: context.window.smartPoll};
  let pending, starts = 0;
  const job = api.poll(() => { starts++; return new Promise(resolve => pending = resolve); }, 100);
  await job.refresh(); // No second request while the first is unresolved.
  assert.equal(starts, 1);
  document.hidden = true; listeners.get('visibilitychange')();
  pending(); await new Promise(setImmediate);
  await job.refresh(); assert.equal(starts, 1);
  document.hidden = false; listeners.get('visibilitychange')(); assert.equal(starts, 2);
  pending(); await new Promise(setImmediate); job.stop(); assert.equal(timers.size, 0);
  const failed = api.poll(() => Promise.reject(Error('delayed provider failed')), 100);
  await new Promise(setImmediate);
  assert.equal([...timers.values()][0].delay, 200); failed.stop();
  if (api.snapshot) {
    const first = api.snapshot('/same'), second = api.snapshot('/same');
    assert.equal(calls, 1); assert.equal(first, second);
    release({ok: true, json: async () => ({ok: true, count: 3})});
    assert.equal((await first).count, 3);
    const fresh = api.snapshot('/same'); assert.equal(calls, 2);
    release({ok: true, json: async () => ({ok: true})}); await fresh;
  }
}
(async () => {
  await check(fs.readFileSync('static/js/smart_poll.js', 'utf8'));
  const built = esbuild.transformSync(fs.readFileSync('Frontend/Customer_Support/src/app/components/polling.ts', 'utf8'), {loader: 'ts', format: 'cjs'}).code;
  await check(built, true);
  console.log('Polling tests passed: overlap, hidden tabs, visibility return, backoff, cleanup, shared requests, freshness.');
})().catch(error => {console.error(error); process.exit(1);});

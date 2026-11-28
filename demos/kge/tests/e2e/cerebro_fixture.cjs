'use strict';
const fs = require('node:fs');
const path = require('node:path');
const build = path.resolve(__dirname, '../../portal/ui/build');

// The built portal page on /cerebro alone, with Cerebro the install's one app, and every other call answered from the
// scenario's resources by path (and query, where a resource names one); context_fixture.cjs is Context's.
module.exports = async function cerebroFixture(context, data) {
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    const resources = data();
    if (url.pathname === '/cerebro') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(build, 'index.html'), 'utf8') });
    if (url.pathname.startsWith('/assets/') && !url.pathname.includes('..')) {
      const file = path.join(build, url.pathname);
      return route.fulfill({ contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'font/woff2', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/api/config') return route.fulfill({ json: { knowledgeHome: false, apps: { context: false, discover: false, certify: false, cerebro: true }, kgeJoined: true, productName: 'Client ALTA' } });
    if (url.pathname === '/api/role') return route.fulfill({ json: { role: 'viewer', roles: ['viewer'] } });
    if (url.pathname === '/brand') return route.fulfill({ json: { logo: false } });
    const endpoint = Object.hasOwn(resources, url.pathname + url.search) ? url.pathname + url.search : url.pathname;
    if (Object.hasOwn(resources, endpoint)) return route.fulfill({ json: resources[endpoint] });
    return route.fulfill({ status: 404, json: { detail: 'Not needed by this isolated Cerebro scenario' } });
  });
};

// Curate on the walkthrough's live portal, which serves Context as its one app: the page's /api/config says Cerebro is on
// and joined to KGE, and each Cerebro steward call goes where the portal's own pass-through sends it
// (portal/server/src/cerebro/steward.js): KGE's steward queue, gaps and decide, its onboarding status, accept and certify,
// and a sign-off as the onboarding run's sign-off step, through the portal's Context proxy, as the signed-in person.
// Everything else, the page and KGE's answers among it, is the live portal's.
const STEWARD = {
  queue: '/steward/queue', decide: '/steward/decide', gaps: '/steward/gaps', candidate: '/onboard',
  accept: '/onboard/accept', certify: '/onboard/certify', 'sign-off': '/onboard/run',
};
module.exports.joined = async function curateJoined(page, portal) {
  await page.route(`${portal}/api/config`, async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, kgeJoined: true, apps: { ...body.apps, cerebro: true } } });
  });
  await page.route(`${portal}/api/cerebro/steward/**`, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const name = url.pathname.slice('/api/cerebro/steward/'.length);
    if (!Object.hasOwn(STEWARD, name)) return route.fulfill({ status: 404, json: { detail: 'Not found.' } });
    const postData = name === 'sign-off' ? JSON.stringify({ step: 'sign-off', ...(request.postDataJSON() || {}) }) : request.postData();
    const response = await route.fetch({ url: `${portal}/api/context${STEWARD[name]}${name === 'queue' ? url.search : ''}`, ...(postData == null ? {} : { postData }) });
    return route.fulfill({ response });
  });
};

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const build = path.resolve(__dirname, '../../portal/ui/build');

module.exports = async function portalFixture(context, data) {
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    const resources = data();
    if (url.pathname === '/context') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(build, 'index.html'), 'utf8') });
    if (url.pathname.startsWith('/assets/') && !url.pathname.includes('..')) {
      const file = path.join(build, url.pathname);
      return route.fulfill({ contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'font/woff2', body: fs.readFileSync(file) });
    }
    let endpoint = url.pathname.startsWith('/api/context') ? url.pathname.slice('/api/context'.length) : url.pathname;
    if (Object.hasOwn(resources, endpoint + url.search)) endpoint += url.search;
    if (endpoint === '/bootstrap') return route.fulfill({ json: { person: resources['/whoami'], profile: resources['/profile'], mode: resources['/mode'], presets: resources['/presets'], build: resources['/healthz'] } });
    if (url.pathname === '/api/config') return route.fulfill({ json: { knowledgeHome: false, apps: { context: true, discover: false, certify: false }, contextMcpUrl: 'https://context.test/mcp', productName: 'Client ALTA' } });
    if (url.pathname === '/api/role') return route.fulfill({ json: { role: 'viewer', roles: ['viewer'] } });
    if (url.pathname === '/brand') return route.fulfill({ json: { logo: false } });
    if (Object.hasOwn(resources, endpoint)) return route.fulfill({ json: resources[endpoint] });
    return route.fulfill({ status: 404, json: { detail: 'Not needed by this isolated Context scenario' } });
  });
};

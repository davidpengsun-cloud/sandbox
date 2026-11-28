// Offline model fixture; the agent, authenticated gateway and Meridian MCP are real.
import fs from 'node:fs';
import {kgeClient} from '../../../portal/server/src/kge.js';
import {GatewayChat} from '../../../portal/server/src/cerebro/gatewayModel.js';
import {cerebroAgent} from '../../../portal/server/src/cerebro/agent.js';
import {followRun, backgroundUsage} from '../../../portal/server/src/cerebro/runs.js';
import {openConversations} from '../../../portal/server/src/conversations/index.js';

const base = 'http://127.0.0.1:' + process.env.ENDPOINT_PORT;
const person = 'analyst_enterprise.meridian@example.com';
const login = await fetch(base + '/session/demo', {
  method: 'POST', headers: {'content-type': 'application/json'},
  body: JSON.stringify({principal: person}),
});
if (!login.ok) throw Error('login ' + login.status + ' ' + await login.text());
const cookie = login.headers.getSetCookie().map(c => c.split(';')[0]).join('; ');
const kge = kgeClient(base, undefined, process.env.PORTAL_KEY);
const conversations = openConversations();
const modelFor = req => new GatewayChat({kge, req});
const issueQuestion = (req, id, {signal} = {}) => kge.modelQuestion(req, id, 'agent', {signal});
const run = cerebroAgent({modelFor, issueQuestion, kge, conversations, retryAfterMs: 0});
const rows = JSON.parse(fs.readFileSync(process.env.GOLDEN_QUESTIONS, 'utf8'));
const results = [];
for (const {id, question} of rows) {
  const thread = crypto.randomUUID();
  const req = {headers: {cookie}, portalIdentity: {principal: person}};
  const events = [];
  try {
    for await (const event of run({thread, req, roles: ['business'], messages: [{type: 'human', content: question}]})) {
      events.push(event);
    }
    const values = events.filter(([name]) => name === 'values').at(-1)?.[1];
    const messages = values?.messages || [];
    const replies = messages.filter(m => m.type === 'tool' && m.name === 'ask').map(m => {
      try { return {id: m.id, ...JSON.parse(m.content)}; } catch { return {id: m.id}; }
    });
    for (const reply of replies.filter(x => x.run?.state === 'running')) {
      for (let i = 0; i < 150; i++) {
        const result = await followRun({conversations, kge, req, roles: ['business'], id: thread,
          messageId: reply.id, model: modelFor(req), issueQuestion});
        if (result.state === 'ended') {
          await backgroundUsage({conversations, kge, req, id: thread, messageId: reply.id, result});
          if (result.evidence_pending) {
            await new Promise(resolve => setTimeout(resolve, 100));
            continue;
          }
          Object.assign(reply, JSON.parse(result.message.content));
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    }
    const usage = await kge.modelUsage(req, {purpose: 'agent'});
    const outcome = events.filter(([name, value]) => name === 'custom' && value?.kind === 'outcome').at(-1)?.[1];
    results.push({id, question, replies, usage, outcome, interrupt: !!values?.__interrupt__});
  } catch (error) {
    results.push({id, question, error: error.message});
  }
  console.log(id, results.at(-1).error || results.at(-1).replies?.map(x => x.behaviour || x.run?.state).join(',') || 'NO_ASK');
  fs.writeFileSync(process.env.GOLDEN_RESULTS, JSON.stringify(results));
}

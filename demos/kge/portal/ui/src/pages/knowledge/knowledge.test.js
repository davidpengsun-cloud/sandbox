import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { normaliseConfig } from './knowledgeConfig';
import KnowledgeHome from './KnowledgeHome';
import DocsAtAGlance from './DocsAtAGlance';
import DocsStartHere from './DocsStartHere';
import * as barrel from './index';
import { SPRITE } from './sprite';
import { TECHNOLOGIES } from './content';

// The pages read /api/config through useKnowledgeConfig; each test sets what the portal answered.
let answer;
vi.mock('./useKnowledgeConfig', async () => {
  const { normaliseConfig: normalise } = await import('./knowledgeConfig');
  return { default: () => normalise(answer) };
});

const ALL_ON = { knowledgeHome: true, apps: { certify: true, context: true, discover: true }, certifyUrl: null, productName: 'Meridian ALTA', contextMcpUrl: "https://context.example/mcp" };

// A controllable prefers-reduced-motion query.
let reduceMotion;
let motionListeners;
function stubMatchMedia() {
  window.matchMedia = vi.fn((query) => ({
    matches: query.includes('reduce') ? reduceMotion : false,
    media: query,
    addEventListener: (_type, fn) => motionListeners.add(fn),
    removeEventListener: (_type, fn) => motionListeners.delete(fn),
  }));
}

beforeEach(() => {
  answer = { ...ALL_ON, apps: { ...ALL_ON.apps } };
  reduceMotion = false;
  motionListeners = new Set();
  stubMatchMedia();
});
afterEach(() => { delete window.matchMedia; });

const renderAt = (ui, path = '/') => render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
const doors = (container) => [...container.querySelectorAll('[data-door]')];
const door = (container, key) => container.querySelector(`[data-door="${key}"]`);

// Words the page must never use: superlatives, and claims about speed, scale or savings.
const BOASTS = /\b(best|fastest|faster|leading|world-class|unmatched|unrivalled|instantly|seamless(ly)?|revolutionary|guaranteed|savings?)\b/i;

function mustNots(text) {
  expect(text).not.toMatch(/KGE/);
  expect(text).not.toMatch(/get access/i);
  expect(text).not.toMatch(/%/);
  expect(text).not.toMatch(BOASTS);
}

function noApiReferenceLinks(container) {
  for (const a of container.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    expect(href === '/docs' || href.startsWith('/docs/') || href.startsWith('/docs#') || href.startsWith('/redoc')).toBe(false);
  }
}

describe('the ALTA home', () => {
  test('shows the three doors in order, Certify, Context, Discover, each with its roles and actions', () => {
    const { container } = renderAt(<KnowledgeHome />);
    expect(doors(container).map((d) => d.dataset.door)).toEqual(['certify', 'context', 'discover']);
    expect(doors(container).map((d) => within(d).getByRole('heading', { level: 2 }).textContent)).toEqual(['Certify', 'Context', 'Discover']);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Meridian ALTA');
    const context = within(door(container, 'context'));
    expect(context.getAllByRole('listitem').map((li) => li.textContent).slice(-4)[0]).toMatch(/^Draft/);
  });

  test('tells the story left to right: brought in, the three areas, then the business teams that use the result', () => {
    const { container } = renderAt(<KnowledgeHome />);
    const story = screen.getByTestId('kh-story');
    const order = [...story.querySelectorAll('section > h2, article h2')].map((h) => h.textContent);
    expect(order).toEqual(['Brought in', 'Certify', 'Context', 'Discover', 'Used by']);
    expect(container.querySelector('.kh-out')).toHaveTextContent('Business teams');
    expect(container.querySelector('.kh-out')).toHaveTextContent('Compliance');
  });

  test('Certify speaks of certification and sign-off only, and Context never of certification', () => {
    const { container } = renderAt(<KnowledgeHome />);
    const certify = door(container, 'certify').textContent;
    expect(certify).toMatch(/certif/i);
    expect(certify).toMatch(/sign off/i);
    expect(certify).not.toMatch(/onboard|release|roll back|run checks|draft/i);
    expect(door(container, 'context').textContent).not.toMatch(/certif/i);
    expect(door(container, 'discover').textContent).toMatch(/request access/i);
    expect(door(container, 'context').textContent).toMatch(/people, apps and agents, each within their own access/);
  });

  test('the Certify door shows the [Power App link] placeholder, not a link, until the link is set', () => {
    const { container } = renderAt(<KnowledgeHome />);
    expect(within(door(container, 'certify')).getByText('[Power App link]')).toBeInTheDocument();
    expect(within(door(container, 'certify')).queryByRole('link')).toBeNull();
  });

  test('the Certify door links out to the configured address, marked as external and opening in a new tab', () => {
    answer.certifyUrl = 'https://apps.powerapps.example/certify';
    const { container } = renderAt(<KnowledgeHome />);
    const link = within(door(container, 'certify')).getByRole('link', { name: /Open Certify.*outside the platform/ });
    expect(link).toHaveAttribute('href', 'https://apps.powerapps.example/certify');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(container).not.toHaveTextContent('[Power App link]');
  });

  test('Context opens inside the portal and Discover opens the discovery pages', () => {
    const { container } = renderAt(<KnowledgeHome />);
    expect(within(door(container, 'context')).getByRole('link', { name: 'Open Context' })).toHaveAttribute('href', '/context');
    expect(within(door(container, 'discover')).getByRole('link', { name: 'Open Discover' })).toHaveAttribute('href', '/home');
  });

  test.each(['certify', 'context', 'discover'])('an app switched off keeps its door, disabled as not set up here: %s', (key) => {
    answer.apps[key] = false;
    answer.certifyUrl = 'https://apps.powerapps.example/certify';
    const { container } = renderAt(<KnowledgeHome />);
    const name = { certify: 'Certify', context: 'Context', discover: 'Discover' }[key];
    expect(doors(container).map((d) => d.dataset.door)).toEqual(['certify', 'context', 'discover']);
    expect(within(door(container, key)).getByText(`${name} is not set up here`)).toBeInTheDocument();
    expect(within(door(container, key)).queryByRole('link')).toBeNull();
    for (const other of ['certify', 'context', 'discover'].filter((k) => k !== key)) {
      expect(within(door(container, other)).getByRole('link')).toBeInTheDocument();
    }
  });

  test('before the settings arrive no door offers a way in', () => {
    answer = null;
    const { container } = renderAt(<KnowledgeHome />);
    expect(doors(container)).toHaveLength(3);
    expect(container.querySelectorAll('.kh-station a')).toHaveLength(0);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Client ALTA');
  });

  test("the install's product name names the page and the client in Discover's roles", () => {
    answer.productName = 'Meridian ALTA';
    const { container } = renderAt(<KnowledgeHome />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Meridian ALTA');
    expect(door(container, 'discover')).toHaveTextContent('Everyone at Meridian');
  });

  test('the motion is decorative and can be paused and played again', () => {
    const { container } = renderAt(<KnowledgeHome />);
    expect(container.querySelector('.kh-rail')).toHaveAttribute('aria-hidden', 'true');
    container.querySelectorAll('.kh-stop').forEach((stop) => expect(stop).toHaveAttribute('aria-hidden', 'true'));
    const story = screen.getByTestId('kh-story');
    expect(story).toHaveClass('kh-row--moving');
    fireEvent.click(screen.getByRole('button', { name: 'Pause the animation' }));
    expect(story).toHaveClass('kh-row--paused');
    fireEvent.click(screen.getByRole('button', { name: 'Play the animation' }));
    expect(story).toHaveClass('kh-row--moving');
  });

  test('with reduced motion asked for, nothing moves and each area shows complete', () => {
    reduceMotion = true;
    renderAt(<KnowledgeHome />);
    expect(screen.getByTestId('kh-story')).toHaveClass('kh-row--still');
    expect(screen.queryByRole('button', { name: /animation/ })).toBeNull();
  });

  test('a change to the reduced-motion setting is followed while the page is open', () => {
    const { unmount } = renderAt(<KnowledgeHome />);
    expect(screen.getByTestId('kh-story')).toHaveClass('kh-row--moving');
    act(() => motionListeners.forEach((fn) => fn({ matches: true })));
    expect(screen.getByTestId('kh-story')).toHaveClass('kh-row--still');
    unmount();
    expect(motionListeners.size).toBe(0);
  });

  test('without matchMedia the motion simply runs', () => {
    delete window.matchMedia;
    renderAt(<KnowledgeHome />);
    expect(screen.getByTestId('kh-story')).toHaveClass('kh-row--moving');
  });

  test('the technologies show in the decided order, each named for screen readers, with the real logos shipped in the page', () => {
    const { container } = renderAt(<KnowledgeHome />);
    const section = screen.getByRole('region', { name: 'Technologies' });
    const items = within(section).getAllByRole('listitem');
    expect(items.map((li) => li.textContent)).toEqual([
      'Anthropic', 'BigQuery', 'BigQuery Graph', 'Collibra', 'Confluence', 'dbt', 'Gemini', 'GitHub Actions', 'GitHub Copilot', 'Google Cloud',
      'Informatica', 'MCP', 'Microsoft Azure', 'Microsoft Entra ID', 'Microsoft Fabric', 'Microsoft Purview', 'OpenAI', 'Power Platform',
      'SharePoint', 'Terraform', 'Databricks', 'Power BI', 'Snowflake', 'Tableau',
    ]);
    expect(section).not.toHaveTextContent(/in use|planned/i);
    for (const tech of TECHNOLOGIES) {
      const li = container.querySelector(`[data-tech="${tech.name}"]`);
      const svg = li.querySelector('svg');
      if (!tech.logo) { expect(svg).toBeNull(); continue; }
      expect(svg).toHaveAttribute('aria-hidden', 'true');
      const ref = svg.querySelector('use').getAttribute('href');
      expect(ref).toBe(`#kh-b-${tech.logo}`);
      expect(container.querySelector(`symbol[id="${ref.slice(1)}"]`)).not.toBeNull();
    }
    expect(container.querySelector('link[href^="http"], script[src], img[src^="http"]')).toBeNull();
    expect(SPRITE).not.toMatch(/https?:/);
  });

  test('"All technologies" is shown disabled and leads nowhere', () => {
    renderAt(<KnowledgeHome />);
    const all = screen.getByRole('link', { name: /All technologies/ });
    expect(all).toHaveAttribute('aria-disabled', 'true');
    expect(all).not.toHaveAttribute('href');
    expect(all).not.toHaveAttribute('tabindex');
  });

  test('says nothing it must not: no KGE, no "get access", no figures or superlatives, no live state', () => {
    answer.certifyUrl = 'https://apps.powerapps.example/certify';
    const { container } = renderAt(<KnowledgeHome />);
    const text = container.textContent;
    mustNots(text);
    expect(text).not.toMatch(/\d/);
    expect(text).not.toMatch(/\b(live|in progress|running now|releasing)\b/i);
    noApiReferenceLinks(container);
  });

  test('renders no header of its own', () => {
    const { container } = renderAt(<KnowledgeHome />);
    expect(container.querySelector('header')).toBeNull();
  });
});

describe('the ALTA docs', () => {
  const pages = [
    ['At a glance', DocsAtAGlance, '/knowledge/docs/at-a-glance', 'Meridian ALTA at a glance',
      ['What Meridian ALTA is', 'Why it matters', 'Three parts, one flow', 'How work flows', 'What makes it different', 'Who it serves', 'Where it runs']],
    ['Start here', DocsStartHere, '/knowledge/docs/start-here', 'Start here',
      ['Which door to open', 'What you will and will not see, and why', 'Asking for access or help', 'Terms you will meet', 'What 0.9.x does not do']],
  ];

  test.each(pages)('%s renders its sections, each listed on the page', (label, Page, path, title, sections) => {
    renderAt(<Page />, path);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(title);
    const article = screen.getByRole('article');
    expect(within(article).getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(sections);
    const toc = screen.getByRole('complementary', { name: 'On this page' });
    expect(within(toc).getAllByRole('link').map((a) => a.textContent)).toEqual(sections);
    const nav = screen.getByRole('navigation', { name: 'Docs' });
    expect(within(nav).getByRole('link', { name: label })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'At a glance' })).toHaveAttribute('href', '/knowledge/docs/at-a-glance');
    expect(within(nav).getByRole('link', { name: 'Start here' })).toHaveAttribute('href', '/knowledge/docs/start-here');
  });

  test.each(pages)('%s never links to the API reference and says nothing it must not', (_label, Page, path) => {
    const { container } = renderAt(<Page />, path);
    noApiReferenceLinks(container);
    mustNots(container.textContent);
    expect(container.textContent).not.toMatch(/Reference for the dev team/);
    expect(container.querySelector('header')).toBeNull();
  });

  test.each(pages)('%s moves to a section from "On this page" and focuses its heading', (_label, Page, path, _title, sections) => {
    renderAt(<Page />, path);
    const toc = screen.getByRole('complementary', { name: 'On this page' });
    const last = sections[sections.length - 1];
    const heading = screen.getByRole('heading', { level: 2, name: last });
    heading.scrollIntoView = vi.fn();
    const link = within(toc).getByRole('link', { name: last });
    expect(fireEvent.click(link)).toBe(false);
    expect(heading.scrollIntoView).toHaveBeenCalled();
    expect(document.activeElement).toBe(heading);
  });

  test('At a glance fills in the client name, links to Start here, and draws its flow for screen readers', () => {
    answer.productName = 'Meridian ALTA';
    renderAt(<DocsAtAGlance />, '/knowledge/docs/at-a-glance');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Meridian ALTA at a glance');
    expect(screen.getByText(/Business teams at Meridian ask/)).toBeInTheDocument();
    expect(within(screen.getByRole('article')).getByRole('link', { name: 'Start here' })).toHaveAttribute('href', '/knowledge/docs/start-here');
    expect(screen.getByRole('img', { name: /Business process: documents are written by content creators/ })).toBeInTheDocument();
    expect(screen.getByText('How do I request access?')).toBeInTheDocument();
  });

  test('Start here fills in the product and client names', () => {
    answer.productName = 'Meridian ALTA';
    renderAt(<DocsStartHere />, '/knowledge/docs/start-here');
    expect(screen.getByText(/Which part of Meridian ALTA to open/)).toBeInTheDocument();
    expect(screen.getByText(/A defined term Meridian uses/)).toBeInTheDocument();
  });

  test('before the settings arrive the docs use the generic product name', () => {
    answer = null;
    renderAt(<DocsStartHere />, '/knowledge/docs/start-here');
    expect(screen.getByText(/Which part of Client ALTA to open/)).toBeInTheDocument();
    renderAt(<DocsAtAGlance />, '/knowledge/docs/at-a-glance');
    expect(screen.getByRole('heading', { level: 1, name: 'Client ALTA at a glance' })).toBeInTheDocument();
  });
});

describe('the settings the pages read', () => {
  test('nothing known yet is null', () => {
    expect(normaliseConfig(null)).toBeNull();
    expect(normaliseConfig(undefined)).toBeNull();
    expect(normaliseConfig({ config: null, loading: true })).toBeNull();
    expect(normaliseConfig('x')).toBeNull();
  });

  test('takes the config directly or wrapped, and the client name from the product name', () => {
    const plain = normaliseConfig({ ...ALL_ON, productName: ' Meridian ALTA ', certifyUrl: 'https://x.example' });
    expect(plain).toEqual({ productName: 'Meridian ALTA', clientName: 'Meridian', certifyUrl: 'https://x.example', apps: { certify: true, context: true, discover: true } });
    expect(normaliseConfig({ config: ALL_ON })).toEqual({ productName: 'Meridian ALTA', clientName: 'Meridian', certifyUrl: null, apps: { certify: true, context: true, discover: true } });
  });

  test('a product name without "ALTA" keeps the generic client name; a blank one falls back', () => {
    expect(normaliseConfig({ productName: 'Data Portal' }).clientName).toBe('Client');
    expect(normaliseConfig({ productName: '  ' }).productName).toBe('Client ALTA');
    expect(normaliseConfig({ certifyUrl: '' }).certifyUrl).toBeNull();
  });

  test('an app is set up unless the config says it is off', () => {
    expect(normaliseConfig({ apps: { certify: false } }).apps).toEqual({ certify: false, context: false, discover: true });
    expect(normaliseConfig({}).apps).toEqual({ certify: true, context: false, discover: true });
  });

  test('the barrel exports the three pages P2 routes', () => {
    expect(barrel.KnowledgeHome).toBe(KnowledgeHome);
    expect(barrel.DocsAtAGlance).toBe(DocsAtAGlance);
    expect(barrel.DocsStartHere).toBe(DocsStartHere);
  });
});

test('an unconfigured Context door is disabled by the shared normaliser', () => {
  answer = { ...ALL_ON, contextMcpUrl: null };
  render(<MemoryRouter><KnowledgeHome /></MemoryRouter>);
  expect(screen.getByText('Context is not set up here')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Open Context' })).toBeNull();
});

for (const [Page, path] of [[KnowledgeHome, '/'], [DocsAtAGlance, '/knowledge/docs/at-a-glance'], [DocsStartHere, '/knowledge/docs/start-here']]) {
  test(`the configured display name replaces placeholders on ${path}`, () => {
    answer = { ...ALL_ON, productName: 'Meridian Group ALTA' };
    const { container } = renderAt(<Page />, path);
    expect(container).toHaveTextContent('Meridian Group ALTA');
    expect(container).not.toHaveTextContent('[Client name]');
    expect(container.textContent).not.toMatch(/Meridian Group Knowledge|governed dataset|Data Discovery Portal/i);
    if (Page === KnowledgeHome) expect(container).toHaveTextContent('Everyone at Meridian Group');
  });
}

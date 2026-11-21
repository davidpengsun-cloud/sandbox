import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, expect, test, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import DataOverviewV3 from './DataOverviewV3';
import UseCasesCard from './UseCasesCard';

vi.mock('../../services/user', () => ({ saveViewedDataProduct: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

const useCases = [
  { title: 'Month-end revenue review', description: 'Finance compares revenue with the plan.', url: 'https://example.com/revenue' },
  { title: 'Branch ranking', description: 'Regional leads rank branches by growth.' },
  { title: 'Churn early warning', description: 'Service teams spot accounts that stopped transacting.', url: 'https://example.com/churn' },
  { title: 'Fourth use case', description: 'Declared fourth, so not shown.', url: 'https://example.com/fourth' },
];

const card = () => screen.getByRole('heading', { name: 'Use cases' }).closest('.card');

test('the Overview shows a Use cases card directly under About this data asset', () => {
  render(<DataOverviewV3 data={{ PRODUCTID: 'retail.accounts', USE_CASES: useCases.slice(0, 2) }} />);
  const about = screen.getByRole('heading', { name: 'About this data asset' }).closest('.card');
  expect(about.nextElementSibling).toBe(card());
  expect(within(card()).getByText('Month-end revenue review')).toBeInTheDocument();
});

test('each entry shows its title, description and link in the declared order', () => {
  render(<UseCasesCard useCases={useCases.slice(0, 2)} />);
  const items = within(screen.getByRole('list', { name: 'Use cases' })).getAllByRole('listitem');
  expect(items.map((item) => within(item).getByRole('heading').textContent)).toEqual(['Month-end revenue review', 'Branch ranking']);
  expect(within(items[0]).getByText('Finance compares revenue with the plan.')).toBeInTheDocument();
  expect(within(items[1]).getByText('Regional leads rank branches by growth.')).toBeInTheDocument();
  expect(within(items[1]).queryByRole('link')).toBeNull();
});

test('a resolved use case shows its objective; an older record its description', () => {
  render(<UseCasesCard useCases={[{ id: 'uc-a', title: 'Resolved', objective: 'Its objective.', description: 'Not shown.' }, { title: 'Older record', description: 'Its description.' }]} />);
  expect(screen.getByText('Its objective.')).toBeInTheDocument();
  expect(screen.queryByText('Not shown.')).toBeNull();
  expect(screen.getByText('Its description.')).toBeInTheDocument();
});

test('a use case link opens in a new tab without giving the new page this one', () => {
  render(<UseCasesCard useCases={useCases.slice(0, 1)} />);
  const link = screen.getByRole('link', { name: 'https://example.com/revenue' });
  expect(link).toHaveAttribute('href', 'https://example.com/revenue');
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
});

test('with more than three, the first three in the declared order are shown', () => {
  render(<UseCasesCard useCases={useCases} />);
  const titles = within(card()).getAllByRole('heading', { level: 4 }).map((heading) => heading.textContent);
  expect(titles).toEqual(['Month-end revenue review', 'Branch ranking', 'Churn early warning']);
  expect(screen.queryByText('Fourth use case')).toBeNull();
  expect(screen.getAllByRole('link')).toHaveLength(2);
  expect(screen.queryByRole('button')).toBeNull();
});

test('with more than three and somewhere to go, the card links to every use case', () => {
  const onShowAll = vi.fn();
  render(<DataOverviewV3 data={{ PRODUCTID: 'retail.accounts', USE_CASES: useCases }} onShowAllUseCases={onShowAll} />);
  const all = within(card()).getByRole('button', { name: 'All 4 use cases' });
  expect(all).toHaveTextContent('All 4 use cases →');
  expect(within(all).getByText('→')).toHaveAttribute('aria-hidden', 'true');
  expect(all).toHaveAttribute('type', 'button');
  all.click();
  expect(onShowAll).toHaveBeenCalledTimes(1);
});

test.each([[undefined], [[]], ['not a list']])('with none recorded (%o) the card says so', (value) => {
  render(<DataOverviewV3 data={{ PRODUCTID: 'retail.accounts', USE_CASES: value }} />);
  expect(within(card()).getByText('No use cases recorded yet')).toBeInTheDocument();
  expect(within(card()).queryByRole('list')).toBeNull();
});

test('text is shown as text: no entry renders HTML', () => {
  const hostile = { title: '<b>Bold</b> title', description: '<img src=x onerror="alert(1)"> and <a href="https://evil.example">a link</a>', url: 'https://example.com/<script>' };
  const { container } = render(<UseCasesCard useCases={[hostile]} />);
  expect(screen.getByText('<b>Bold</b> title')).toBeInTheDocument();
  expect(screen.getByText(hostile.description)).toBeInTheDocument();
  expect(container.querySelector('b, img, script')).toBeNull();
  expect(screen.getAllByRole('link')).toHaveLength(1);
});

test.each(['javascript:alert(1)', 'http://example.com/plain', 'https://', '', 'https://example.com/with space'])('a url that is not an https address (%o) is not linked', (url) => {
  render(<UseCasesCard useCases={[{ title: 'Unsafe', description: 'Its url is not shown as a link.', url }]} />);
  expect(screen.getByText('Unsafe')).toBeInTheDocument();
  expect(screen.queryByRole('link')).toBeNull();
});

const css = readFileSync(join(process.cwd(), 'src/assets/styles/v3-detail.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const rule = (selector) => {
  const at = css.indexOf(`${selector}{`);
  expect(at).toBeGreaterThanOrEqual(0);
  return css.slice(at, css.indexOf('}', at));
};

test('at phone width the card wraps its text and links rather than overflowing, using the design tokens', () => {
  for (const selector of ['.v3-detail .use-case h4', '.v3-detail .use-case p', '.v3-detail .use-case a']) {
    const declarations = rule(selector);
    expect(declarations).toContain('overflow-wrap:anywhere');
    expect(declarations).not.toMatch(/white-space:\s*nowrap|(^|[;{\s])width:\s*\d/);
    expect(declarations).not.toMatch(/#[0-9a-f]{3,6}\b/i);
    expect(declarations).toMatch(/color:var\(--v3-/);
  }
  expect(rule('.v3-detail .use-case a')).toContain('max-width:100%');
  expect(rule('.v3-detail .use-case')).toContain('min-width:0');
  expect(css).toMatch(/@container \(max-width: 1080px\)\{\s*\.v3-detail \.two-col\{grid-template-columns:minmax\(0,1fr\)\}/);
});

test('the link to every use case uses the design tokens', () => {
  const declarations = rule('.v3-detail .use-cases-all');
  expect(declarations).toMatch(/color:var\(--v3-link\)/);
  expect(declarations).not.toMatch(/#[0-9a-f]{3,6}\b/i);
  expect(rule('.v3-detail .use-cases-all:hover')).toMatch(/color:var\(--v3-link-hover\)/);
});

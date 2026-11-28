import React from 'react';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { useState } from 'react';
import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom';
import V3Card from '../shared/V3ProductCard/V3Card';
import UseCaseDetail from './UseCaseDetail';
import { normaliseUseCase, useCaseCard, useCaseSearchUrl, useCaseSlug } from './useCase';
import { exportUseCaseOdcs } from '../../services/exports';

vi.mock('../../services/exports', () => ({ exportUseCaseOdcs: vi.fn(() => Promise.resolve()) }));
beforeEach(() => vi.clearAllMocks());

const full = {
  id: 'uc-revenue-review',
  title: 'Month-end revenue review',
  objective: 'Finance compares revenue with the plan.',
  domain: 'Finance',
  owner: 'Group Finance',
  url: 'https://example.com/revenue',
  kpi: { name: 'Revenue vs plan', definition: 'Actual revenue over planned revenue.', grain: 'Month by branch', unit: 'percent' },
  example_questions: ['What was revenue against plan last month?', 'Which branches missed plan?'],
  products: [{ id: 'retail/accounts', name: 'Retail accounts' }, { id: 'sme.loans', name: 'SME loans' }],
  count: 2,
};
const bare = { id: 'uc-bare', title: 'Bare use case', objective: '', domain: '', owner: '', url: '', kpi: null, example_questions: [], products: [{ id: 'p1', name: '' }], count: 1 };

function Card({ useCase }) {
  const [open, setOpen] = useState(null);
  return (
    <>
      <V3Card {...useCaseCard(useCase, setOpen)} />
      {open ? <UseCaseDetail useCase={open} onClose={() => setOpen(null)} /> : null}
    </>
  );
}

function SearchPage() {
  return <output aria-label="Search">{useLocation().search}</output>;
}

function ProductPage() {
  return <p>Product {useParams().id}</p>;
}

const renderCard = (useCase) => render(
  <MemoryRouter initialEntries={['/home']}>
    <Routes>
      <Route path="/home" element={<Card useCase={useCase} />} />
      <Route path="/search" element={<SearchPage />} />
      <Route path="/productDetails/:id" element={<ProductPage />} />
    </Routes>
  </MemoryRouter>,
);
const cardOf = (title) => screen.getByRole('button', { name: `Explore use case ${title}` });

test('on an asset\'s tab a use case is the product card: eyebrow, title, owner, objective, KPI chip and readable asset count, on the no-tier rail', () => {
  renderCard(full);
  const card = cardOf('Month-end revenue review');
  expect(card.tagName).toBe('ARTICLE');
  expect(card).toHaveClass('mp-card', 'mp-card--n');
  expect(card).toHaveAttribute('tabindex', '0');
  expect(card.querySelector('.mp-card__eyebrow .mp-card__kind')).toHaveTextContent('Use case · Finance');
  expect(card.querySelector('.mp-tier-img')).toBeNull();
  expect(within(card).getByRole('heading', { level: 3 })).toHaveClass('mp-card__title');
  expect(within(card).getByRole('heading', { level: 3 })).toHaveTextContent('Month-end revenue review');
  expect(card.querySelector('.mp-card__body > .mp-card__meta')).toHaveTextContent('Group Finance');
  expect(card.querySelector('.mp-card__desc')).toHaveTextContent('Finance compares revenue with the plan.');
  expect([...card.querySelectorAll('.mp-card__facts .mp-chip')].map((chip) => chip.textContent)).toEqual(['Revenue vs plan']);
  expect(card.querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent('2 assets');
  expect(within(card).queryAllByRole('button')).toEqual([]);
  expect(within(card.parentElement).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual(['Explore use case Month-end revenue review', 'Export as ODCS: Month-end revenue review']);
  expect(screen.queryByText(/View details/)).toBeNull();
});

test('a use case without a domain, owner, objective or KPI shows none of them, and one asset reads "1 asset"', () => {
  renderCard(bare);
  const card = cardOf('Bare use case');
  expect(card.querySelector('.mp-card__kind')).toHaveTextContent(/^Use case$/);
  expect(card.querySelector('.mp-card__body > .mp-card__meta')).toBeNull();
  expect(card.querySelector('.mp-card__desc')).toBeNull();
  expect(card.querySelector('.mp-chip')).toBeNull();
  expect(card.querySelector('.mp-card__foot > .mp-card__meta')).toHaveTextContent(/^1 asset$/);
});

test.each([
  ['a click', (card) => fireEvent.click(card)],
  ['Enter', (card) => fireEvent.keyDown(card, { key: 'Enter' })],
  ['Space', (card) => fireEvent.keyDown(card, { key: ' ' })],
])('%s on the tab\'s card opens the use case\'s detail modal, not the search', (_how, press) => {
  renderCard(full);
  press(cardOf('Month-end revenue review'));
  expect(screen.getByRole('dialog', { name: 'Month-end revenue review' })).toHaveAttribute('aria-modal', 'true');
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
});

test('another key does not open the use case', () => {
  renderCard(full);
  fireEvent.keyDown(cardOf('Month-end revenue review'), { key: 'a' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the modal shows the use case\'s id, facts, objective, KPI, example questions and linked assets', () => {
  renderCard(full);
  fireEvent.click(cardOf('Month-end revenue review'));
  const dialog = screen.getByRole('dialog', { name: 'Month-end revenue review' });
  expect(within(dialog).getByRole('heading', { level: 2 })).toHaveTextContent('Month-end revenue review');
  expect(within(dialog).getByText('ID uc-revenue-review')).toBeInTheDocument();
  const facts = (root) => [...root.querySelectorAll('.uc-fact')].map((fact) => [fact.querySelector('dt').textContent, fact.querySelector('dd').textContent]);
  expect(facts(dialog.querySelector('.uc-dialog-head'))).toEqual([['Domain', 'Finance'], ['Owner', 'Group Finance']]);
  expect(within(dialog).getByText('Finance compares revenue with the plan.')).toHaveClass('uc-dialog-text');
  const link = within(dialog).getByRole('link', { name: 'https://example.com/revenue' });
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  const kpi = within(dialog).getByRole('heading', { level: 3, name: 'KPI' }).closest('section');
  expect(facts(kpi)).toEqual([['Name', 'Revenue vs plan'], ['Definition', 'Actual revenue over planned revenue.'], ['Grain', 'Month by branch'], ['Unit', 'percent']]);
  expect(within(within(dialog).getByRole('list', { name: 'Example questions' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual(full.example_questions);
  const products = within(within(dialog).getByRole('list', { name: 'Linked data products' })).getAllByRole('listitem');
  expect(products.map((item) => item.querySelector('.uc-product-name').textContent)).toEqual(['Retail accounts', 'SME loans']);
  expect(within(products[0]).getByRole('link', { name: 'View product' })).toHaveAttribute('href', '/productDetails/retail%2Faccounts');
  expect(within(products[0]).getByRole('link', { name: 'View product' })).toHaveAccessibleDescription('Retail accounts');
  expect(within(dialog).getAllByRole('button').map((button) => button.textContent)).toEqual(['Export as ODCS', 'Close']);
  expect(within(dialog).queryByText(/ODPS/)).toBeNull();
});

test('a bare use case\'s modal leaves out what it does not have', () => {
  renderCard(bare);
  fireEvent.click(cardOf('Bare use case'));
  const dialog = screen.getByRole('dialog', { name: 'Bare use case' });
  expect(dialog.querySelector('.uc-facts')).toBeNull();
  expect(within(dialog).queryByRole('heading', { level: 3, name: 'Objective' })).toBeNull();
  expect(within(dialog).queryByRole('heading', { level: 3, name: 'KPI' })).toBeNull();
  expect(within(dialog).queryByRole('list', { name: 'Example questions' })).toBeNull();
  expect(within(within(dialog).getByRole('list', { name: 'Linked data products' })).getAllByRole('listitem')).toHaveLength(1);
});

test('the modal lists only the linked assets the data gives for the person', () => {
  renderCard({ ...full, products: [{ id: 'retail/accounts', name: 'Retail accounts' }] });
  fireEvent.click(cardOf('Month-end revenue review'));
  const products = within(screen.getByRole('list', { name: 'Linked data products' })).getAllByRole('listitem');
  expect(products.map((item) => item.querySelector('.uc-product-name').textContent)).toEqual(['Retail accounts']);
  expect(screen.queryByText('SME loans')).toBeNull();
});

test('the modal\'s "Export as ODCS" downloads the contract and keeps the modal open', () => {
  renderCard(full);
  fireEvent.click(cardOf('Month-end revenue review'));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Export as ODCS' }));
  expect(exportUseCaseOdcs).toHaveBeenCalledTimes(1);
  expect(exportUseCaseOdcs.mock.calls[0][0]).toMatchObject({ id: 'uc-revenue-review', title: 'Month-end revenue review' });
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});

test('the modal\'s "Show assets in Discover" opens the search filtered to the use case and closes the modal', () => {
  renderCard(full);
  fireEvent.click(cardOf('Month-end revenue review'));
  const show = within(screen.getByRole('dialog')).getByRole('link', { name: 'Show assets in Discover' });
  expect(show).toHaveAttribute('href', '/search?useCase=Month-end+revenue+review');
  fireEvent.click(show);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(new URLSearchParams(screen.getByRole('status', { name: 'Search' }).textContent).get('useCase')).toBe('Month-end revenue review');
});

test('a linked asset\'s "View product" opens its page and closes the modal', () => {
  renderCard(full);
  fireEvent.click(cardOf('Month-end revenue review'));
  fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('link', { name: 'View product' })[1]);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByText('Product sme.loans')).toBeInTheDocument();
});

test.each([
  ['Escape', (dialog) => fireEvent.keyDown(dialog, { key: 'Escape' })],
  ['Close', (dialog) => fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))],
  ['a click on the backdrop', (dialog) => fireEvent.mouseDown(dialog.parentElement)],
])('%s closes the modal and returns focus to the card that opened it', (_how, close) => {
  renderCard(full);
  const card = cardOf('Month-end revenue review');
  card.focus();
  fireEvent.click(card);
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveFocus();
  close(dialog);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(card).toHaveFocus();
});

test('a click inside the modal does not close it', () => {
  renderCard(full);
  fireEvent.click(cardOf('Month-end revenue review'));
  fireEvent.mouseDown(within(screen.getByRole('dialog')).getByText('Finance compares revenue with the plan.'));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});

test('Tab and Shift+Tab stay inside the modal', () => {
  renderCard(full);
  fireEvent.click(cardOf('Month-end revenue review'));
  const dialog = screen.getByRole('dialog');
  const controls = [...dialog.querySelectorAll('a[href], button')];
  controls.at(-1).focus();
  fireEvent.keyDown(dialog, { key: 'Tab' });
  expect(controls[0]).toHaveFocus();
  fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
  expect(controls.at(-1)).toHaveFocus();
});

test('the modal\'s values are text, never HTML', () => {
  renderCard({ ...full, title: '<b>Bold</b>', objective: '<img src=x>', example_questions: ['<i>q</i>'], products: [{ id: 'p', name: '<u>P</u>' }] });
  fireEvent.click(cardOf('<b>Bold</b>'));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByRole('heading', { level: 2 })).toHaveTextContent('<b>Bold</b>');
  expect(within(dialog).getByText('<img src=x>')).toBeInTheDocument();
  expect(within(dialog).getByText('<i>q</i>')).toBeInTheDocument();
  expect(dialog.querySelector('b, i, u, img')).toBeNull();
});

test('the modal\'s styles use the design tokens and cover its focus rings', () => {
  const css = readFileSync(join(process.cwd(), 'src/components/UseCases/UseCaseDetail.css'), 'utf8');
  expect(css).toMatch(/\.uc-dialog\{[^}]*background:var\(--v3-surface\)/);
  expect(css).toMatch(/\.uc-btn:focus-visible[^{]*\{outline:2px solid var\(--v3-focus\)/);
  expect(css).not.toMatch(/\.uc-card/);
});

test('the search a use case opens is the existing search-by-use-case URL', () => {
  expect(useCaseSearchUrl(normaliseUseCase(full))).toBe('/search?useCase=Month-end+revenue+review');
  expect(useCaseSearchUrl({ id: 'uc-x', title: '' })).toBe('/search?useCase=uc-x');
});

test('every value is shown as text, never as HTML', () => {
  const { container } = renderCard({ ...bare, title: '<b>Bold</b>', objective: '<img src=x onerror="alert(1)">', owner: '<i>me</i>', domain: '<u>D</u>', kpi: { name: '<s>K</s>' } });
  expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('<b>Bold</b>');
  expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  expect(screen.getByText('<s>K</s>')).toBeInTheDocument();
  expect(container.querySelector('b, i, u, s, img')).toBeNull();
});

test('the one use case detail modal is back; the ODPS draft export and the old card files stay removed', () => {
  expect(existsSync(join(process.cwd(), 'src/components/UseCases/UseCaseDetail.js'))).toBe(true);
  for (const file of ['UseCaseCard.js', 'odps.js', 'UseCases.css']) {
    expect(existsSync(join(process.cwd(), 'src/components/UseCases', file))).toBe(false);
  }
  const modal = readFileSync(join(process.cwd(), 'src/components/UseCases/UseCaseDetail.js'), 'utf8');
  expect(modal).not.toMatch(/odps|ODPS/);
});

test('a legacy entry reads as a use case whose id comes from its title', () => {
  expect(useCaseSlug('  Month-end & Revenue: review! ')).toBe('month-end-revenue-review');
  expect(useCaseSlug('Риск')).toBe('420-438-441-43a');
  expect(normaliseUseCase({ title: 'Branch ranking', description: 'Leads rank branches.', url: 'http://plain.example' })).toEqual({
    id: 'uc-branch-ranking', title: 'Branch ranking', objective: 'Leads rank branches.', domain: '', owner: '', url: '', kpi: null, exampleQuestions: [], products: [],
  });
  expect(normaliseUseCase(null)).toMatchObject({ id: '', title: '', products: [] });
  expect(normaliseUseCase({ title: 'T', kpi: { definition: 'no name' }, example_questions: ['', 'ok', 3], products: [null, { name: 'no id' }, { id: 'a' }] })).toMatchObject({ kpi: null, exampleQuestions: ['ok'], products: [{ id: 'a', name: 'a' }] });
});

import React from 'react';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import V3Card from '../shared/V3ProductCard/V3Card';
import { normaliseUseCase, useCaseCard, useCaseSearchUrl, useCaseSlug } from './useCase';

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
  return <V3Card {...useCaseCard(useCase, useNavigate())} />;
}

function SearchPage() {
  return <output aria-label="Search">{useLocation().search}</output>;
}

const renderCard = (useCase) => render(
  <MemoryRouter initialEntries={['/home']}>
    <Routes>
      <Route path="/home" element={<Card useCase={useCase} />} />
      <Route path="/search" element={<SearchPage />} />
    </Routes>
  </MemoryRouter>,
);

test('a use case is the product card: eyebrow, title, owner, objective, KPI chip and readable asset count, on the no-tier rail', () => {
  renderCard(full);
  const card = screen.getByRole('button', { name: 'Show assets for use case Month-end revenue review' });
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
  expect(within(card.parentElement).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual(['Show assets for use case Month-end revenue review', 'Export as ODCS: Month-end revenue review']);
  expect(screen.queryByText(/View details/)).toBeNull();
});

test('a use case without a domain, owner, objective or KPI shows none of them, and one asset reads "1 asset"', () => {
  renderCard(bare);
  const card = screen.getByRole('button', { name: 'Show assets for use case Bare use case' });
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
])('%s opens Discover\'s search filtered to the use case, with no detail dialog', (_how, press) => {
  renderCard(full);
  press(screen.getByRole('button', { name: 'Show assets for use case Month-end revenue review' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(new URLSearchParams(screen.getByRole('status', { name: 'Search' }).textContent).get('useCase')).toBe('Month-end revenue review');
});

test('another key does not open the use case', () => {
  renderCard(full);
  fireEvent.keyDown(screen.getByRole('button', { name: 'Show assets for use case Month-end revenue review' }), { key: 'a' });
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
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

test('no use-case-specific card, detail dialog, ODPS export or stylesheet remains', () => {
  for (const file of ['UseCaseCard.js', 'UseCaseDetail.js', 'odps.js', 'UseCases.css']) {
    expect(existsSync(join(process.cwd(), 'src/components/UseCases', file))).toBe(false);
  }
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

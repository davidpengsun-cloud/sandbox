import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import V3Card from '../shared/V3ProductCard/V3Card';
import { useCaseCard } from './useCase';
import { exportUseCaseOdcs } from '../../services/exports';
import { showToast } from '../shared/V3Toast/V3Toast';

vi.mock('../../services/exports', () => ({ exportUseCaseOdcs: vi.fn() }));
vi.mock('../shared/V3Toast/V3Toast', () => ({ showToast: vi.fn() }));

const useCase = { id: 'uc-lantern-count', title: 'Lantern count', objective: 'Harbour staff count lit lanterns.', products: [{ id: 'harbour-lanterns', name: 'Harbour lanterns' }] };

function Card() {
  return <V3Card {...useCaseCard(useCase, useNavigate())} />;
}
function SearchPage() {
  return <output aria-label="Search">{new URLSearchParams(useLocation().search).get('useCase')}</output>;
}
const renderCard = () => render(
  <MemoryRouter initialEntries={['/home']}>
    <Routes>
      <Route path="/home" element={<Card />} />
      <Route path="/search" element={<SearchPage />} />
    </Routes>
  </MemoryRouter>,
);
const card = () => screen.getByRole('button', { name: 'Show assets for use case Lantern count' });
const action = () => screen.getByRole('button', { name: 'Export as ODCS: Lantern count' });

beforeEach(() => {
  vi.clearAllMocks();
  exportUseCaseOdcs.mockResolvedValue();
});

test('the card\'s foot shows the "Export as ODCS" action, a keyboard-reachable button beside the role="button" card, not inside it', () => {
  renderCard();
  expect(action()).toHaveTextContent('Export as ODCS');
  expect(action()).toHaveAttribute('type', 'button');
  expect(card().contains(action())).toBe(false);
  expect(action().closest('.mp-card-wrap')).toBe(card().parentElement);
  expect(screen.getAllByRole('button', { name: /^Export as ODCS/ })).toEqual([action()]);
  expect(within(card()).queryAllByRole('button')).toEqual([]);
  const room = card().querySelector('.mp-card__foot > .mp-card__action-room');
  expect(room).toHaveAttribute('aria-hidden', 'true');
  expect(room).toHaveStyle({ visibility: 'hidden' });
  expect(room).toHaveTextContent('Export as ODCS');
});

test.each([
  ['a click', (button) => fireEvent.click(button)],
  ['Enter', (button) => { fireEvent.keyDown(button, { key: 'Enter' }); fireEvent.click(button); }],
  ['Space', (button) => { fireEvent.keyDown(button, { key: ' ' }); fireEvent.click(button); }],
])('%s on the action downloads the use case\'s contract and does not open search', (_how, press) => {
  renderCard();
  press(action());
  expect(exportUseCaseOdcs).toHaveBeenCalledTimes(1);
  expect(exportUseCaseOdcs.mock.calls[0][0]).toMatchObject({ id: 'uc-lantern-count', title: 'Lantern count' });
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
  expect(card()).toBeInTheDocument();
});

test('the card\'s own click still opens search and downloads nothing', () => {
  renderCard();
  fireEvent.click(card());
  expect(screen.getByRole('status', { name: 'Search' })).toHaveTextContent('Lantern count');
  expect(exportUseCaseOdcs).not.toHaveBeenCalled();
});

test('a failed export says so and stays on the card', async () => {
  exportUseCaseOdcs.mockRejectedValue(new Error('404'));
  renderCard();
  fireEvent.click(action());
  await vi.waitFor(() => expect(showToast).toHaveBeenCalledWith('The ODCS export could not be downloaded'));
  expect(screen.queryByRole('status', { name: 'Search' })).toBeNull();
});

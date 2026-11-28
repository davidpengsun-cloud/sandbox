import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import V3Card from './V3Card';
import V3ProductCard from './V3ProductCard';

const navigate = vi.fn();
vi.mock('react-redux', () => ({ useDispatch: () => vi.fn() }));
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));
vi.mock('./V3Card', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: vi.fn(actual.default) };
});

test('a data product is drawn by the shared card, on its tier rail, and opens its own page', () => {
  render(<V3ProductCard product={{ PRODUCTID: 'retail.customer', PRODUCTNAME: 'Customer', BADGE_NAME: 'SILVER', PRODUCTTYPE: 'TABLE', DOMAIN: 'Retail', COMMUNITY: 'Deposits' }} />);
  expect(V3Card).toHaveBeenCalledTimes(1);
  expect(V3Card.mock.calls[0][0]).toMatchObject({ title: 'Customer', rail: 'mp-card--s', eyebrow: 'TABLE · Retail', chips: [{ key: 'community', tone: 'plum', label: 'Deposits' }] });
  const card = screen.getByRole('button', { name: 'Open Customer' });
  expect(card).toHaveClass('mp-card', 'mp-card--s');
  fireEvent.keyDown(card, { key: 'Enter' });
  expect(navigate).toHaveBeenCalledWith('/productDetails/retail.customer');
});

test('a product with no tier keeps the no-tier rail the use case cards use', () => {
  render(<V3ProductCard product={{ PRODUCTID: 'p', PRODUCTNAME: 'Plain' }} />);
  expect(screen.getByRole('button', { name: 'Open Plain' })).toHaveClass('mp-card--n');
  expect(document.querySelector('.mp-tier-img')).toBeNull();
});

test('a card without an action is drawn as before: the role="button" card is the outermost element', () => {
  const { container } = render(<V3Card title="Plain" footEnd={<span>end</span>} />);
  expect(container.firstChild).toBe(screen.getByRole('button', { name: 'Open Plain' }));
  expect(document.querySelector('.mp-card-wrap, .mp-card__float, .mp-card__action-room')).toBeNull();
});

test('a card\'s action sits beside the card, not in it; each click stays its own', () => {
  const onOpen = vi.fn();
  const act = vi.fn();
  render(<V3Card title="Lantern" onOpen={onOpen} action={<button type="button" onClick={act}>Act</button>} />);
  const card = screen.getByRole('button', { name: 'Open Lantern' });
  const action = screen.getByRole('button', { name: 'Act' });
  expect(card.contains(action)).toBe(false);
  expect(action.parentElement).toHaveClass('mp-card__float');
  expect(card.parentElement).toHaveClass('mp-card-wrap');
  expect(card.querySelector('.mp-card__foot > .mp-card__action-room')).toHaveAttribute('aria-hidden', 'true');
  fireEvent.click(action);
  fireEvent.keyDown(action, { key: 'Enter' });
  expect(act).toHaveBeenCalledTimes(1);
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.click(card);
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(act).toHaveBeenCalledTimes(1);
});

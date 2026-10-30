import { expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import V3ProductCard from './V3ProductCard';

vi.mock('react-redux', () => ({ useDispatch: () => vi.fn() }));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

test('product cards show the catalogue quality tier without a table certification badge', () => {
  render(<V3ProductCard product={{ PRODUCTID: 'retail.customer', PRODUCTNAME: 'Customer', BADGE_NAME: 'GOLD', BADGES: ['GOLD'] }} />);
  expect(screen.getByAltText('Gold quality tier')).toBeInTheDocument();
  expect(screen.queryByText('CERTIFIED', { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByText('NOT CERTIFIED', { exact: true })).not.toBeInTheDocument();
});

test('the plum chip names the community, not the source application', () => {
  render(<V3ProductCard product={{ PRODUCTID: 'retail.customer', PRODUCTNAME: 'Customer', PRODUCTTYPE: 'TABLE', DOMAIN: 'Retail Banking', APPLICATIONNAME: 'Core ledger', COMMUNITY: 'Deposits, cards and lending' }} />);
  expect(document.querySelector('.mp-chip-plum')).toHaveTextContent('Deposits, cards and lending');
  expect(screen.queryByText('Core ledger')).toBeNull();
});

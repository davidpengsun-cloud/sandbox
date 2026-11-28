import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataOverviewV3 from './DataOverviewV3';

vi.mock('../../services/user', () => ({ saveViewedDataProduct: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

const useCases = [
  { title: 'Month-end revenue review', description: 'Finance compares revenue with the plan.', url: 'https://example.com/revenue' },
  { title: 'Branch ranking', description: 'Regional leads rank branches by growth.' },
  { title: 'Churn early warning', description: 'Service teams spot accounts that stopped transacting.' },
  { title: 'Fourth use case', description: 'Declared fourth.' },
];

test.each([[useCases], [useCases.slice(0, 1)], [undefined], [[]]])('the Overview has no Use cases box under About this data asset (%o)', (value) => {
  render(<DataOverviewV3 data={{ PRODUCTID: 'retail.accounts', USE_CASES: value }} />);
  expect(screen.getByRole('heading', { name: 'About this data asset' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Use cases' })).toBeNull();
  expect(screen.queryByRole('list', { name: 'Use cases' })).toBeNull();
  expect(screen.queryByRole('button', { name: /^All \d+ use cases/ })).toBeNull();
  expect(screen.queryByText('No use cases recorded yet')).toBeNull();
  expect(screen.queryByText('Month-end revenue review')).toBeNull();
});

test('the Overview box and its "All n use cases" link are gone from the code and the styles', () => {
  const read = (path) => readFileSync(join(process.cwd(), 'src', path), 'utf8');
  expect(read('components/DataOverview/UseCasesCard.js')).not.toMatch(/export default|USE_CASES_SHOWN|UseCaseList|use-cases-all/);
  expect(read('components/DataOverview/DataOverviewV3.js')).not.toMatch(/UseCasesCard|onShowAllUseCases/);
  expect(read('pages/DataProductDetails/DataProductDetails.js')).not.toMatch(/showAllUseCases|useCasesTabRef|focusUseCasesTab/);
  expect(read('assets/styles/v3-detail.css')).not.toMatch(/\.use-cases?\b|\.use-cases-all/);
});

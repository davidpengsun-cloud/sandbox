import { beforeEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataOverview from './DataOverview';
import DataOverviewV3 from './DataOverviewV3';
import UseCases from '../UseCases/UseCases';
import { qualityOf } from './quality';
import { saveViewedDataProduct } from '../../services/user';

vi.mock('../../services/user', () => ({ saveViewedDataProduct: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

const product = {
  PRODUCTID: 'retail.orders', TECHNICALNAME: 'orders', SCHEMANAME: 'retail', BUSINESSOWNER: 'Dana Smith', PRODUCTOWNER: 'lee',
  COUNTOFRECORDS: 1200, PRODUCT_SCORE: 95, DATA_FORMAT: 'Parquet', PRODUCTTYPE: 'Table', SIZE: '3 MB', COUNTOFCOLUMNS: 8,
  PARENT_DATASET_COUNT_OF_COLUMNS: 12, LAST_REFRESH: '2026-10-01', DQRUNDATE: '2026-10-02', VIEWTIMES: 4, ISPREVIEW: 'Yes',
  PATH: 'Root>Retail>Orders', DESCRIPTION: 'Every order', DATA_DOMAIN_NAME: 'Retail,Sales', COMMUNITY: 'Commerce', DATA_PRODUCT_SOURCE: 'BigQuery',
};

test('the overview tab shows ownership and technical metadata and records the visit', () => {
  render(<DataOverview data={product} config={{ Owner: 'owns it', Steward: 'stewards it', DQ: '<b>score</b>' }} />);
  expect(screen.getByText('Dana Smith')).toBeInTheDocument();
  expect(screen.getByText('95 %')).toBeInTheDocument();
  expect(screen.getByText('8 out of 12 (original dataset)')).toBeInTheDocument();
  expect(screen.getByText('Retail>Orders')).toBeInTheDocument();
  expect(saveViewedDataProduct).toHaveBeenCalledWith({ productId: 'retail.orders', productName: 'orders', searchWord: null, category: 'Details', tab: 'DETAILS-OVERVIEW' });
});

test.each([[50, '50 %'], [5, '5 %']])('the overview tab shows a score of %s', (score, text) => {
  render(<DataOverview data={{ ...product, PRODUCT_SCORE: score }} />);
  expect(screen.getByText(text)).toBeInTheDocument();
});

test('the overview tab fills gaps with N/A and zero', () => {
  render(<DataOverview data={{ ...product, PRODUCT_SCORE: 'N/A', SIZE: '', DQRUNDATE: null, VIEWTIMES: null, ISPREVIEW: null, PARENT_DATASET_COUNT_OF_COLUMNS: null }} />);
  expect(screen.getAllByText('N/A').length).toBeGreaterThanOrEqual(4);
  expect(screen.getByText('0')).toBeInTheDocument();
});

test('the v3 overview shows a recorded score, owners as initials, and the tags', () => {
  render(<DataOverviewV3 data={product} />);
  expect(screen.getByText('DQ 95%')).toBeInTheDocument();
  expect(screen.getByText('Every order')).toBeInTheDocument();
  expect(screen.getByText('Retail Sales')).toBeInTheDocument();
  expect(screen.getByText('DS')).toBeInTheDocument();
  expect(screen.getByText('L')).toBeInTheDocument();
  expect(screen.getByText('8 of 12')).toBeInTheDocument();
  expect(screen.getByText('Retail>Orders')).toBeInTheDocument();
  expect(screen.getByText('Last DQ run')).toBeInTheDocument();
  expect(screen.getByAltText('Gold')).toBeInTheDocument();
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ productId: 'retail.orders', tab: 'DETAILS-OVERVIEW' }));
});

test.each([[50, 'mt-chip--warn'], [5, 'mt-chip--bad'], [95, 'mt-chip--good']])('the v3 overview tones a score of %s', (score, tone) => {
  render(<DataOverviewV3 data={{ ...product, PRODUCT_SCORE: score }} />);
  expect(screen.getByText(`${score}%`).className).toContain(tone);
});

test('the v3 overview says when nothing is recorded', () => {
  render(<DataOverviewV3 data={{ PRODUCT_SCORE: 0 }} />);
  expect(screen.getByText('NO DQ RUN')).toBeInTheDocument();
  expect(screen.getByText('Not assessed')).toBeInTheDocument();
  expect(screen.getByText('No description provided.')).toBeInTheDocument();
  expect(screen.getAllByText('Unassigned')).toHaveLength(2);
  expect(screen.queryByText('Last DQ run')).toBeNull();
});

test('the v3 overview copes with no product at all', () => {
  render(<DataOverviewV3 />);
  expect(screen.getByRole('heading', { name: 'About this data asset' })).toBeInTheDocument();
});

test('qualityOf treats missing, N/A and zero as not recorded', () => {
  for (const raw of [undefined, null, '', 'N/A', 0]) expect(qualityOf({ PRODUCT_SCORE: raw }).recorded).toBe(false);
  expect(qualityOf({ PRODUCT_SCORE: 72 })).toEqual({ recorded: true, score: 72, text: '72%' });
  expect(qualityOf(undefined).text).toBe('—');
});

test('the use-cases tab links each report to Excel, Power BI and GraphQL', () => {
  render(<UseCases data={product} />);
  expect(screen.getByText('EOD Positions')).toBeInTheDocument();
  expect(screen.getByText('EOM Positions')).toBeInTheDocument();
  expect(screen.getAllByAltText('Link to GraphQL')).toHaveLength(2);
  expect(screen.getAllByAltText('Link to GraphQL')[0].closest('a')).toHaveAttribute('href', 'https://graphql.org/');
  expect(saveViewedDataProduct).toHaveBeenCalledWith(expect.objectContaining({ tab: 'DETAILS-OVERVIEW' }));
});

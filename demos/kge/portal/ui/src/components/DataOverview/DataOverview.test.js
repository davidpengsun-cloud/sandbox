import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
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
  expect(screen.getByText('Root>Retail>Orders')).toBeInTheDocument();
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
  expect(screen.getByText('Data steward', { exact: true })).toBeInTheDocument();
  expect(screen.queryByText(/approves access/i)).not.toBeInTheDocument();
  expect(screen.getByText('DQ 95%')).toBeInTheDocument();
  expect(screen.getByText('Every order')).toBeInTheDocument();
  expect(screen.getByText('Retail Sales')).toBeInTheDocument();
  expect(screen.getByText('DS')).toBeInTheDocument();
  expect(screen.getByText('L')).toBeInTheDocument();
  expect(screen.getByText('8 of 12')).toBeInTheDocument();
  expect(screen.getByText('Root>Retail>Orders')).toBeInTheDocument();
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


test.each([DataOverview, DataOverviewV3])('the overview shows all catalogue asset-type metadata values without renaming the existing type label', (Overview) => {
  render(<Overview data={{ ...product, ASSETTYPE: 'Unstructured', USAGEPURPOSE: 'Operational', DATASETTYPE: 'Customer correspondence', APM_ID: 'APM-CRM-017' }} />);
  for (const [label, value] of [['Data structure', 'Unstructured'], ['Usage purpose', 'Operational'], ['Dataset type', 'Customer correspondence'], ['APM ID', 'APM-CRM-017']]) {
    expect(screen.getByText(label).parentElement).toHaveTextContent(value);
  }
  expect(screen.getByText(Overview === DataOverviewV3 ? 'Object type' : 'Data Asset Type').parentElement).toHaveTextContent('Table');
});

test('the v3 overview shows Project only where set', () => {
  const { rerender } = render(<DataOverviewV3 data={product} />);
  expect(screen.queryByText('Project')).toBeNull();
  rerender(<DataOverviewV3 data={{ ...product, PROJECT: 'Meridian Core Ledger' }} />);
  expect(screen.getByText('Project').parentElement).toHaveTextContent('Meridian Core Ledger');
});

test('the v3 ownership card opens contact details on hover or focus with mailto and Teams links, and closes on Escape', () => {
  render(<DataOverviewV3 data={{ ...product, BUSINESSOWNEREMAIL: 'dana.smith@meridian.example', PRODUCTOWNEREMAIL: 'lee+steward@meridian.example' }} />);
  const danaTrigger = screen.getByRole('button', { name: 'Dana Smith' });
  fireEvent.mouseEnter(danaTrigger.parentElement);
  const dialog = screen.getByRole('dialog', { name: 'Contact Dana Smith' });
  expect(dialog).toHaveTextContent('Dana Smith');
  expect(dialog).toHaveTextContent('Data domain owner');
  expect(screen.getByRole('link', { name: 'dana.smith@meridian.example' })).toHaveAttribute('href', 'mailto:dana.smith@meridian.example');
  expect(screen.getByRole('link', { name: 'Chat in Teams' })).toHaveAttribute('href', 'https://teams.microsoft.com/l/chat/0/0?users=dana.smith%40meridian.example');
  fireEvent.mouseLeave(danaTrigger.parentElement);
  expect(screen.queryByRole('dialog')).toBeNull();

  const leeTrigger = screen.getByRole('button', { name: 'lee' });
  fireEvent.focus(leeTrigger);
  expect(screen.getByRole('dialog', { name: 'Contact lee' })).toHaveTextContent('Data steward');
  expect(screen.getByRole('link', { name: 'Chat in Teams' })).toHaveAttribute('href', 'https://teams.microsoft.com/l/chat/0/0?users=lee%2Bsteward%40meridian.example');
  fireEvent.keyDown(leeTrigger.parentElement, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the v3 ownership contact card shows No email on record and omits mailto and Teams when no email is set', () => {
  render(<DataOverviewV3 data={product} />);
  const trigger = screen.getByRole('button', { name: 'Dana Smith' });
  fireEvent.click(trigger);
  const dialog = screen.getByRole('dialog', { name: 'Contact Dana Smith' });
  expect(dialog).toHaveTextContent('No email on record');
  expect(screen.queryByRole('link', { name: /@/ })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Chat in Teams' })).toBeNull();
  fireEvent.blur(trigger.parentElement, { relatedTarget: document.body });
  expect(screen.queryByRole('dialog')).toBeNull();
});


test.each([['Table', 'Daily', 'Daily'], ['View', 'Daily', 'N/A'], ['View', undefined, 'N/A'], ['Documents', 'Weekly', 'Weekly'], ['API/MCP', 'On demand', 'On demand']])('cadence for %s is %s', (type, cadence, expected) => {
  render(<DataOverviewV3 data={{ ...product, PRODUCTTYPE: type, REFRESH_CADENCE: cadence }} />);
  const label = screen.getByText('Refresh cadence');
  expect(label.parentElement).toHaveTextContent(expected);
  expect(label.parentElement.previousElementSibling).toHaveTextContent('Last refresh');
});

test.each(['Table', 'Documents', 'API/MCP'])('no cadence adds no row on %s', (type) => {
  render(<DataOverviewV3 data={{ ...product, PRODUCTTYPE: type }} />);
  expect(screen.queryByText('Refresh cadence')).toBeNull();
});

test('Documents hides only the four specified technical rows', () => {
  render(<DataOverviewV3 data={{ ...product, PRODUCTTYPE: 'Documents' }} />);
  for (const label of ['Technical name', 'Source records (before read filters)', 'Columns', 'Portal views']) expect(screen.queryByText(label)).toBeNull();
  for (const [label, value] of [['Schema', 'retail'], ['Storage', '3 MB'], ['Last refresh', '2026-10-01']]) expect(screen.getByText(label).parentElement).toHaveTextContent(value);
});

test('API/MCP is labelled Connector and has N/A for the four specified metrics', () => {
  render(<DataOverviewV3 data={{ ...product, PRODUCTTYPE: 'API/MCP' }} />);
  expect(screen.queryByText('API/MCP')).toBeNull();
  expect(screen.getByText('Object type').parentElement).toHaveTextContent('Connector');
  for (const label of ['Schema', 'Source records (before read filters)', 'Storage', 'Columns']) expect(screen.getByText(label).parentElement).toHaveTextContent('N/A');
  expect(screen.getByText('Technical name').parentElement).toHaveTextContent('orders');
  expect(screen.getByText('Portal views').parentElement).toHaveTextContent('4');
});

test.each(['Table', 'View'])('%s retains its existing technical metrics', (type) => {
  render(<DataOverviewV3 data={{ ...product, PRODUCTTYPE: type }} />);
  for (const [label, value] of [['Technical name', 'orders'], ['Schema', 'retail'], ['Source records (before read filters)', '1200'], ['Storage', '3 MB'], ['Columns', '8 of 12'], ['Portal views', '4']]) expect(screen.getByText(label).parentElement).toHaveTextContent(value);
});


test.each([['API/MCP', 'policies', 'policies'], ['API/MCP', '', 'N/A'], ['Documents', 'policies', 'policies'], ['Table', 'policies', 'policies']])('the %s schema row shows a supplied schema %j as %s', (type, schema, expected) => {
  render(<DataOverviewV3 data={{ ...product, PRODUCTTYPE: type, SCHEMA: schema, SCHEMANAME: schema || product.SCHEMANAME, PATH: 'gs://meridian-docs/policies', DATA_FORMAT: 'PDF', APM_ID: 'APM-DOC-7' }} />);
  expect(screen.getByText('Schema').parentElement).toHaveTextContent(expected);
  for (const [label, value] of [['Path', 'gs://meridian-docs/policies'], ['Data format', 'PDF'], ['APM ID', 'APM-DOC-7']]) expect(screen.getByText(label).parentElement).toHaveTextContent(value);
});


test.each([DataOverview, DataOverviewV3])('a supplied path containing > shows whole, and a derived asset-id path shows as before', (Overview) => {
  const { unmount } = render(<Overview data={{ ...product, PATH: 'Policies > Lending > Current' }} />);
  expect(screen.getByText('Policies > Lending > Current')).toBeInTheDocument();
  expect(screen.queryByText('Lending > Current')).toBeNull();
  unmount();
  render(<Overview data={{ ...product, PATH: 'retail.orders' }} />);
  expect(screen.getByText('retail.orders')).toBeInTheDocument();
});

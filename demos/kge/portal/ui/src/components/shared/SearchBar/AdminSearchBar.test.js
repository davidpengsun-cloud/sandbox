import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AdminSearchBar from './AdminSearchBar';
import { getPublishSearchData, getPublishedSearchResult } from '../../../services/admin';

vi.mock('../../../services/admin', () => ({ getPublishSearchData: vi.fn(), getPublishedSearchResult: vi.fn() }));

const pick = async (text) => {
  await waitFor(() => expect(document.querySelector(`.ant-select-item-option[title="${text}"]`)).not.toBeNull());
  return document.querySelector(`.ant-select-item-option[title="${text}"]`);
};
const type = (input, value) => {
  fireEvent.change(input, { target: { value } });
  fireEvent.keyUp(input, { key: 'Enter', target: { value } });
};

const setup = () => {
  const searchResult = vi.fn();
  const view = render(<AdminSearchBar searchBarStyle={['bar', 'input']} iconWidth="2rem" searchResult={searchResult} />);
  return { searchResult, input: screen.getByRole('combobox'), icon: view.container.querySelector('svg.MuiSvgIcon-root'), ...view };
};

beforeEach(() => {
  vi.clearAllMocks();
  getPublishedSearchResult.mockResolvedValue({ data: [{ TECHNICAL_NAME: 'retail.orders' }] });
  getPublishSearchData.mockResolvedValue({ data: [{ value: 'retail.orders' }] });
});
afterEach(() => vi.restoreAllMocks());

test('Enter searches the publish list and hands back the rows', async () => {
  const { input, searchResult } = setup();
  fireEvent.keyUp(input, { key: 'Enter', target: { value: 'retail' } });
  expect(getPublishedSearchResult).toHaveBeenCalledWith('retail');
  await waitFor(() => expect(searchResult).toHaveBeenCalledWith([{ TECHNICAL_NAME: 'retail.orders' }]));
});

test('Enter on an empty box searches everything', async () => {
  const { input, searchResult } = setup();
  fireEvent.keyUp(input, { key: 'Enter', target: { value: '' } });
  expect(getPublishedSearchResult).toHaveBeenCalledWith('0');
  await waitFor(() => expect(searchResult).toHaveBeenCalled());
});

test('the icon with nothing typed searches everything; after a pick it searches the pick', async () => {
  const { input, icon, searchResult } = setup();
  fireEvent.click(icon);
  expect(getPublishedSearchResult).toHaveBeenCalledWith('0');
  fireEvent.change(input, { target: { value: 'ret' } });
  expect(getPublishSearchData).toHaveBeenCalledWith('ret');
  fireEvent.click(await pick('retail.orders'));
  fireEvent.click(icon);
  expect(getPublishedSearchResult).toHaveBeenLastCalledWith('retail.orders');
  await waitFor(() => expect(searchResult).toHaveBeenCalledTimes(2));
});

test('clearing the box resets without a lookup', () => {
  const { input } = setup();
  fireEvent.change(input, { target: { value: '' } });
  expect(getPublishSearchData).not.toHaveBeenCalled();
});

test.each(['enter', 'icon'])('no rows via %s hands nothing back', async (how) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getPublishedSearchResult.mockResolvedValue({});
  const { input, icon, searchResult } = setup();
  if (how === 'enter') fireEvent.keyUp(input, { key: 'Enter', target: { value: 'x' } });
  else fireEvent.click(icon);
  await waitFor(() => expect(console.log).toHaveBeenCalledWith('No records found'));
  expect(searchResult).not.toHaveBeenCalled();
});

test.each(['enter', 'icon'])('a failed search via %s is logged', async (how) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  getPublishedSearchResult.mockRejectedValue(new Error('down'));
  const { input, icon } = setup();
  if (how === 'enter') fireEvent.keyUp(input, { key: 'Enter', target: { value: 'x' } });
  else fireEvent.click(icon);
  await waitFor(() => expect(console.log).toHaveBeenCalledWith('Search Failed!', expect.any(Error)));
});

test.each([
  ['an empty', () => getPublishSearchData.mockResolvedValue(undefined)],
  ['a failed', () => getPublishSearchData.mockRejectedValue(new Error('down'))],
])('%s suggestion lookup shows no suggestions', async (_, arrange) => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  arrange();
  const { input } = setup();
  fireEvent.change(input, { target: { value: 'ret' } });
  await waitFor(() => expect(console.log).toHaveBeenCalled());
  expect(screen.queryByText('retail.orders')).toBeNull();
});

import { afterEach, beforeEach, expect, test, vi } from 'vitest';
const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('axios', () => ({ default: { create: () => ({ get, post: vi.fn(), put: vi.fn() }) } }));
import { exportAssetOdps, exportUseCaseOdcs } from './exports';

let clicked;
let blobs;
beforeEach(() => {
  clicked = [];
  blobs = [];
  get.mockReset();
  get.mockResolvedValue({ headers: { 'content-type': 'application/yaml; charset=utf-8' }, data: 'kind: DataContract\n' });
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => { blobs.push(blob); return 'blob:export'; });
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() { clicked.push({ href: this.getAttribute('href'), download: this.download, attached: this.isConnected }); });
  window.history.replaceState({}, '', '/home');
});
afterEach(() => { vi.restoreAllMocks(); });

test('an asset\'s ODPS export is requested through the shared API helper and saved as <asset id>.odps.yaml', async () => {
  await exportAssetOdps('harbour/lanterns');
  expect(get).toHaveBeenCalledWith('/product/harbour%2Flanterns/odps', { params: null });
  expect(clicked).toEqual([{ href: 'blob:export', download: 'harbour/lanterns.odps.yaml', attached: true }]);
  expect(await blobs[0].text()).toBe('kind: DataContract\n');
  expect(blobs[0].type).toBe('application/yaml; charset=utf-8');
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:export');
  expect(document.querySelector('a[download]')).toBeNull();
});

test('a use case\'s ODCS export names its id and title and is saved as <use case id>.odcs.yaml', async () => {
  await exportUseCaseOdcs({ id: 'uc-lantern-count', title: 'Lantern & count' });
  expect(get).toHaveBeenCalledWith('/use-cases/uc-lantern-count/odcs', { params: { title: 'Lantern & count' } });
  expect(clicked.map((link) => link.download)).toEqual(['uc-lantern-count.odcs.yaml']);
  get.mockResolvedValue({ headers: {}, data: 'kind: DataContract\n' });
  await exportUseCaseOdcs({ id: 'uc-untitled', title: '' });
  expect(get).toHaveBeenLastCalledWith('/use-cases/uc-untitled/odcs', { params: null });
  expect(blobs[1].type).toBe('application/yaml');
});

test('a refused export saves nothing and rejects, without leaving the page', async () => {
  const error = { response: { status: 404 } };
  get.mockRejectedValue(error);
  await expect(exportAssetOdps('orchard-pears')).rejects.toBe(error);
  expect(clicked).toEqual([]);
  expect(window.location.pathname).toBe('/home');
});

test('an export with an expired session (401) sends the person back to sign in, as every other call does', async () => {
  const error = { response: { status: 401 } };
  get.mockRejectedValue(error);
  await expect(exportUseCaseOdcs({ id: 'uc-lantern-count', title: '' })).rejects.toBe(error);
  expect(clicked).toEqual([]);
  expect(window.location.pathname).toBe('/');
});

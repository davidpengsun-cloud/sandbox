import { beforeEach, expect, test, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import UserManagement from './UserManagement';
import { getPortalUsers, getPortalRoleAudit, grantPortalRole, removePortalRole } from '../../services/admin';

vi.mock('../../services/admin', () => ({
  getPortalUsers: vi.fn(), getPortalRoleAudit: vi.fn(), grantPortalRole: vi.fn(), removePortalRole: vi.fn(),
}));

const availableRoles = ['viewer', 'admin', 'publisher', 'approver'];
const grant = { id: 'change-1', at: '2026-10-31T12:00:00.000Z', by: 'operator@example.com', principal: 'ada@example.com', before: [], after: ['publisher'] };

beforeEach(() => {
  vi.resetAllMocks();
  getPortalUsers.mockResolvedValue({ data: { users: [], roles: availableRoles } });
  getPortalRoleAudit.mockResolvedValue({ data: { audit: [] } });
});

test('a fresh install shows the default and inherited admin access and all grantable roles', async () => {
  render(<UserManagement />);
  expect(await screen.findByText(/No portal roles have been granted/)).toBeInTheDocument();
  expect(screen.getByText(/Access admins always have portal admin access/)).toBeInTheDocument();
  expect(screen.getAllByRole('option').map((option) => option.value)).toEqual(availableRoles);
  expect(screen.getByRole('button', { name: 'Grant role' })).toBeDisabled();
});

test('an admin grants a role and sees the refreshed membership and who/when/before/after audit', async () => {
  grantPortalRole.mockResolvedValue({ data: grant });
  render(<UserManagement />);
  await screen.findByText(/No portal roles have been granted/);
  getPortalUsers.mockResolvedValue({ data: { users: [{ principal: 'ada@example.com', roles: ['publisher'] }], roles: availableRoles } });
  getPortalRoleAudit.mockResolvedValue({ data: { audit: [grant] } });
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'ada@example.com' } });
  fireEvent.change(screen.getByLabelText('Portal role'), { target: { value: 'publisher' } });
  fireEvent.click(screen.getByRole('button', { name: 'Grant role' }));
  expect(await screen.findByRole('status')).toHaveTextContent('publisher granted to ada@example.com');
  expect(grantPortalRole).toHaveBeenCalledWith('ada@example.com', 'publisher');
  expect(await screen.findByRole('button', { name: 'Remove publisher from ada@example.com' })).toBeEnabled();
  const audit = screen.getByRole('table', { name: 'Role change audit' });
  expect(within(audit).getByText('operator@example.com')).toBeInTheDocument();
  expect(within(audit).getByText('2026-10-31T12:00:00.000Z')).toHaveAttribute('dateTime', grant.at);
  expect(within(audit).getByText('None')).toBeInTheDocument();
  expect(within(audit).getByText('publisher')).toBeInTheDocument();
});

test('removing one membership retains the other and refreshes the audit', async () => {
  getPortalUsers.mockResolvedValue({ data: { users: [{ principal: 'ada@example.com', roles: ['publisher', 'approver'] }], roles: availableRoles } });
  removePortalRole.mockResolvedValue({ data: { ...grant, before: ['publisher', 'approver'], after: ['approver'] } });
  render(<UserManagement />);
  const remove = await screen.findByRole('button', { name: 'Remove publisher from ada@example.com' });
  await waitFor(() => expect(remove).toBeEnabled());
  getPortalUsers.mockResolvedValue({ data: { users: [{ principal: 'ada@example.com', roles: ['approver'] }], roles: availableRoles } });
  fireEvent.click(remove);
  expect(await screen.findByRole('status')).toHaveTextContent('publisher removed from ada@example.com');
  expect(removePortalRole).toHaveBeenCalledWith('ada@example.com', 'publisher');
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove publisher from ada@example.com' })).toBeNull());
  expect(screen.getByRole('button', { name: 'Remove approver from ada@example.com' })).toBeEnabled();
});

test.each(['Portal role required.', 'Unknown portal role.'])('a refusal is shown without claiming success: %s', async (message) => {
  grantPortalRole.mockRejectedValue({ response: { status: 403, data: { message } } });
  render(<UserManagement />);
  await screen.findByText(/No portal roles have been granted/);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'ada@example.com' } });
  fireEvent.click(screen.getByRole('button', { name: 'Grant role' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.queryByRole('status')).toBeNull();
  expect(screen.getByRole('button', { name: 'Grant role' })).toBeEnabled();
});

test('a failed load is not mistaken for an empty table and can be refreshed', async () => {
  getPortalUsers.mockRejectedValueOnce({ response: { data: { detail: 'Portal role required.' } } });
  render(<UserManagement />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal role required.');
  expect(screen.queryByText(/No portal roles have been granted/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh roles and audit' }));
  expect(await screen.findByText(/No portal roles have been granted/)).toBeInTheDocument();
  expect(screen.queryByRole('alert')).toBeNull();
});

test('a free-form username is passed to the server for shared normalization and never interpreted as markup', async () => {
  const username = '<b>person</b>';
  getPortalUsers.mockResolvedValue({ data: { users: [{ principal: username, roles: ['viewer'] }], roles: availableRoles } });
  render(<UserManagement />);
  expect(await screen.findByRole('rowheader', { name: username })).toBeInTheDocument();
  expect(document.querySelector('.portal-users tbody b')).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', { name: `Remove viewer from ${username}` })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: '  Ａda@example.com  ' } });
  grantPortalRole.mockResolvedValue({ data: grant });
  fireEvent.click(screen.getByRole('button', { name: 'Grant role' }));
  await waitFor(() => expect(grantPortalRole).toHaveBeenCalledWith('  Ａda@example.com  ', 'publisher'));
});

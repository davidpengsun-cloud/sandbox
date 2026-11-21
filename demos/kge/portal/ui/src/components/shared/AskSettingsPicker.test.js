import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import AskSettingsPicker from './AskSettingsPicker';
import * as picker from './AskSettingsPicker';

// A /health `ask` object; the picker reads only its model. It carries the fallback fields too, so the tests show
// the picker ignores them.
const health = { ai_fallback: 'on', default: 'off', modes: ['off', 'strict', 'open'], model: 'vertex:gemini-flash' };
const open = () => fireEvent.click(screen.getByRole('button', { name: /^Answer settings:/ }));
const read = (name) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8');

test('the picker and its styles use the portal tokens only: no hex colour, no new token', () => {
  const css = read('./Controls.css');
  const start = css.indexOf('/* Ask settings picker');
  expect(start).toBeGreaterThan(0);
  const own = css.slice(start) + read('./AskSettingsPicker.js');
  expect(own).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  expect(own).not.toMatch(/--v3-[\w-]+\s*:/);
  expect(own).toMatch(/var\(--v3-surface\)/);
});

test('the trigger and the panel show the model and no AI-fallback control: no fieldset, no radio, no fallback text', () => {
  const onChange = vi.fn();
  render(<AskSettingsPicker health={health} value="open" onChange={onChange} />);
  const trigger = screen.getByRole('button', { name: /^Answer settings:/ });
  expect(trigger).toHaveTextContent('vertex:gemini-flash');
  expect(trigger).not.toHaveTextContent(/fallback|strict|open/i);
  open();
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  expect(within(panel).getByText('vertex:gemini-flash')).toBeInTheDocument();
  expect(panel).not.toHaveTextContent(/fallback/i);
  expect(panel.querySelector('fieldset')).toBeNull();
  expect(within(panel).queryAllByRole('radio')).toHaveLength(0);
  expect(screen.queryByRole('group', { name: /fallback/i })).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  expect(Object.keys(picker).sort()).toEqual(['default', 'runtimeSummary']);
});

test('with no health the trigger shows "none" for the model', () => {
  render(<AskSettingsPicker health={null} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveTextContent('none');
});

test('keyboard: aria-expanded follows the panel, focus moves in, Escape closes and returns focus', () => {
  const onOpen = vi.fn();
  render(<AskSettingsPicker health={health} onOpen={onOpen} />);
  const trigger = screen.getByRole('button', { name: /^Answer settings:/ });
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');
  trigger.focus();
  fireEvent.click(trigger);
  expect(onOpen).toHaveBeenCalledTimes(1);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  expect(trigger).toHaveAttribute('aria-controls', panel.id);
  expect(document.activeElement).toBe(panel);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(trigger);
});

test('the panel opens upward: it is placed above the trigger', () => {
  render(<AskSettingsPicker health={health} />);
  open();
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  expect(panel).toHaveAttribute('data-placement', 'top');
  expect(panel).toHaveClass('ask-settings-panel');
  expect(read('./Controls.css')).toMatch(/\.ask-settings-panel \{ position:absolute; bottom:calc\(100% \+ 6px\)/);
});

test('without arms or runtimes the panel holds only the model', () => {
  render(<AskSettingsPicker health={health} />);
  open();
  expect(screen.queryByLabelText('Answered by')).not.toBeInTheDocument();
  expect(screen.queryByRole('group', { name: 'Runtimes' })).not.toBeInTheDocument();
});

test('it absorbs the Answered by select: grouped arms, the current arm on the trigger, onArmChange on a pick', () => {
  const onArmChange = vi.fn();
  const arms = [
    { label: 'The control: presets', options: [{ value: 'C' }, { value: 'B' }] },
    { label: 'The reference agent, the A/B', options: [{ value: 'R1', label: 'agent R1, left (tools)' }] },
  ];
  render(<AskSettingsPicker health={health} arms={arms} arm="B" onArmChange={onArmChange} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveTextContent('B');
  open();
  const select = screen.getByLabelText('Answered by');
  expect(select).toHaveValue('B');
  expect(within(select).getByRole('option', { name: 'agent R1, left (tools)' })).toBeInTheDocument();
  fireEvent.change(select, { target: { value: 'R1' } });
  expect(onArmChange).toHaveBeenCalledWith('R1');
});

test('it absorbs the Runtimes chip: loading, empty, and an ordered selection with toggles', () => {
  const onRuntimeToggle = vi.fn();
  const props = { health, selectedRuntimes: [], onRuntimeToggle };
  const { rerender } = render(<AskSettingsPicker {...props} runtimes={null} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveTextContent('certified default');
  open();
  expect(screen.getByText('Loading runtimes…')).toBeInTheDocument();
  rerender(<AskSettingsPicker {...props} runtimes={[]} />);
  expect(screen.getByText('Default certified runtime active.')).toBeInTheDocument();
  const runtimes = [{ id: 'rules', label: 'Rules', certified: true }, { id: 'model' }];
  rerender(<AskSettingsPicker {...props} runtimes={runtimes} selectedRuntimes={['model', 'rules']} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveTextContent('model → rules');
  const group = screen.getByRole('group', { name: 'Runtimes' });
  expect(within(group).getByText('#1')).toBeInTheDocument();
  expect(within(group).getByText('Certified')).toBeInTheDocument();
  fireEvent.click(within(group).getByRole('checkbox', { name: /Rules/ }));
  expect(onRuntimeToggle).toHaveBeenCalledWith('rules');
});

test('a click outside closes the panel', () => {
  render(<div><AskSettingsPicker health={health} /><p>outside</p></div>);
  open();
  fireEvent.mouseDown(screen.getByText('outside'));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('no page code sends ai_fallback: outside tests and fixtures, no source under ui/src names it as a key', () => {
  const root = join(process.cwd(), 'src');
  const sources = readdirSync(root, { recursive: true })
    .map(String)
    .filter((file) => /\.(js|jsx|ts|tsx)$/.test(file) && !/\.test\.[jt]sx?$/.test(file) && !/(^|[\\/])fixtures[\\/]/.test(file));
  expect(sources.length).toBeGreaterThan(50);
  const sending = sources.filter((file) => /ai_fallback\s*:|["'`]ai_fallback["'`]|\.ai_fallback\s*=(?!=)/.test(readFileSync(join(root, file), 'utf8')));
  expect(sending).toEqual([]);
});

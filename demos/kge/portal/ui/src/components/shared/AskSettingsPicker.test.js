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
// The smallest props that make the picker a drop-up rather than a static chip.
const settable = { selectedRuntimes: [], onRuntimeToggle: () => {}, runtimes: [] };
const purposes = ['analysis', 'assistant', 'supervision'];
const runtimes = [{ id: 'rt_shadow', label: 'Shadow runtime' }, { id: 'rt_cert', label: 'Certified runtime', certified: true }];
const arms = [
  { label: 'The control: presets', options: [{ value: 'C' }, { value: 'B' }] },
  { label: 'The reference agent, the A/B', options: [{ value: 'R1', label: 'agent R1, left (tools)' }] },
];

test('the picker and its styles use the portal tokens only: no hex colour, no new token', () => {
  const own = read('./AskSettingsPicker.css') + read('./AskSettingsPicker.js');
  expect(own).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  expect(own).not.toMatch(/--v3-[\w-]+\s*:/);
  expect(own).toMatch(/var\(--v3-surface\)/);
});

test('the picker imports its own styles, and no shared stylesheet holds picker rules', () => {
  expect(read('./AskSettingsPicker.js')).toMatch(/^import "\.\/AskSettingsPicker\.css";$/m);
  expect(read('./Controls.css')).not.toMatch(/\.ask-settings/);
});

test('with the model alone it is a static chip: the model, no caret, no button, no drop-up (Cerebro)', () => {
  const { container } = render(<AskSettingsPicker id="agentchat-settings" model="vertex:gemini-flash" />);
  const chip = container.querySelector('#agentchat-settings');
  expect(chip).toHaveClass('ask-settings', 'ask-settings-static');
  expect(chip).toHaveTextContent('vertex:gemini-flash');
  expect(chip.tagName).toBe('SPAN');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(container.querySelector('.ask-settings-caret')).toBeNull();
  expect(container.querySelector('[aria-haspopup]')).toBeNull();
  fireEvent.click(chip);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('the static chip keeps the older health prop, the model prop wins, and with neither it says "none"', () => {
  const { container, rerender } = render(<AskSettingsPicker health={health} />);
  expect(container.querySelector('#ask-settings')).toHaveTextContent('vertex:gemini-flash');
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  rerender(<AskSettingsPicker health={health} model="claude:sonnet" />);
  expect(container.querySelector('#ask-settings')).toHaveTextContent('claude:sonnet');
  expect(container.querySelector('#ask-settings')).not.toHaveTextContent('gemini');
  rerender(<AskSettingsPicker health={null} />);
  expect(container.querySelector('#ask-settings')).toHaveTextContent('none');
});

test('the trigger and the panel show the model and no AI-fallback control: no fieldset, no radio, no fallback text', () => {
  const onChange = vi.fn();
  render(<AskSettingsPicker health={health} value="open" onChange={onChange} {...settable} />);
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

test('the chip reads "model · settings" with a caret', () => {
  render(<AskSettingsPicker model="vertex:gemini-flash" arms={arms} arm="B" onArmChange={vi.fn()} />);
  const trigger = screen.getByRole('button', { name: /^Answer settings:/ });
  const now = trigger.querySelector('.ask-settings-now');
  expect([...now.children].map((part) => part.textContent)).toEqual(['vertex:gemini-flash', 'B']);
  expect(now.firstElementChild).toHaveClass('ask-settings-model');
  expect(trigger.querySelector('.ask-settings-caret')).toBeInTheDocument();
});

test('without tester props the picker holds no tester settings: no Purpose, no Rich answers', () => {
  render(<AskSettingsPicker model="m" runtimes={runtimes} selectedRuntimes={[]} onRuntimeToggle={vi.fn()} />);
  open();
  expect(document.querySelector('#settings, #settings-now, #purpose, #rich')).toBeNull();
  expect(screen.queryByLabelText('Purpose')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Rich answers')).not.toBeInTheDocument();
  expect(screen.queryByText(/Why you ask/)).not.toBeInTheDocument();
});

test('given them, the picker holds the tester settings: Purpose as #purpose with its hint, Rich answers as #rich', () => {
  render(<AskSettingsPicker model="m" purposes={purposes} purpose="analysis" onPurposeChange={vi.fn()} rich={false} onRichChange={vi.fn()} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveAttribute('aria-haspopup', 'dialog');
  open();
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  const select = within(panel).getByLabelText('Purpose');
  expect(select).toHaveAttribute('id', 'purpose');
  expect(select).toHaveValue('analysis');
  expect([...select.options].map((option) => option.value)).toEqual(purposes);
  const box = within(panel).getByLabelText('Rich answers');
  expect(box).toHaveAttribute('id', 'rich');
  expect(box).toHaveAttribute('type', 'checkbox');
  expect(box).not.toBeChecked();
  expect(within(panel).getByText('Why you ask: the access rules are checked for this purpose. Analysis suits most questions.')).toBeInTheDocument();
  expect(document.querySelector('#settings, #settings-now')).toBeNull();
});

test('each tester row appears only when given: Purpose alone, or Rich answers alone', () => {
  const { rerender } = render(<AskSettingsPicker model="m" purposes={purposes} purpose="analysis" onPurposeChange={vi.fn()} />);
  open();
  expect(screen.getByLabelText('Purpose')).toBeInTheDocument();
  expect(screen.queryByLabelText('Rich answers')).not.toBeInTheDocument();
  rerender(<AskSettingsPicker model="m" rich onRichChange={vi.fn()} />);
  expect(screen.queryByLabelText('Purpose')).not.toBeInTheDocument();
  expect(screen.queryByText(/Why you ask/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Rich answers')).toBeChecked();
  rerender(<AskSettingsPicker model="m" purposes={purposes} purpose="analysis" />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(document.querySelector('#ask-settings')).toHaveClass('ask-settings-static');
});

test('changing the purpose or Rich answers calls back with the new value', () => {
  const onPurposeChange = vi.fn();
  const onRichChange = vi.fn();
  render(<AskSettingsPicker model="m" purposes={purposes} purpose="analysis" onPurposeChange={onPurposeChange} rich={false} onRichChange={onRichChange} />);
  open();
  fireEvent.change(screen.getByLabelText('Purpose'), { target: { value: 'supervision' } });
  expect(onPurposeChange).toHaveBeenCalledTimes(1);
  expect(onPurposeChange).toHaveBeenCalledWith('supervision');
  fireEvent.click(screen.getByLabelText('Rich answers'));
  expect(onRichChange).toHaveBeenCalledWith(true);
  expect(screen.getByRole('dialog', { name: 'Answer settings' })).toBeInTheDocument();
});

test('keyboard: aria-expanded follows the panel, focus moves in, Escape closes and returns focus', () => {
  const onOpen = vi.fn();
  render(<AskSettingsPicker health={health} onOpen={onOpen} {...settable} />);
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

test('the panel opens upward, stays inside the viewport and scrolls when it is taller than its cap', () => {
  render(<AskSettingsPicker health={health} {...settable} />);
  open();
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  expect(panel).toHaveAttribute('data-placement', 'top');
  expect(panel).toHaveClass('ask-settings-panel');
  const rule = read('./AskSettingsPicker.css').match(/\.ask-settings \.ask-settings-panel \{([^}]*)\}/)[1];
  expect(rule).toMatch(/position:absolute; bottom:calc\(100% \+ 6px\)/);
  expect(rule).toMatch(/max-width:calc\(100vw - 32px\)/);
  expect(rule).toMatch(/max-height:min\(70vh, 480px\); overflow:auto/);
});

test('one setting per line, in order: Model, Answered by, then Runtimes', () => {
  render(<AskSettingsPicker model="m" arms={arms} arm="B" onArmChange={vi.fn()} runtimes={runtimes} selectedRuntimes={[]} onRuntimeToggle={vi.fn()} />);
  open();
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  const keys = [...panel.querySelectorAll('.ask-settings-line > .ask-settings-key, .ask-settings-runtimes > .ask-settings-key')].map((key) => key.firstChild.textContent.trim());
  expect(keys).toEqual(['Model', 'Answered by', 'Runtimes']);
  expect(panel.querySelectorAll('select')).toHaveLength(1);
});

test('for a tester, one setting per line, in order: Model, Answered by, Purpose, Rich answers, then Runtimes', () => {
  render(<AskSettingsPicker model="m" arms={arms} arm="B" onArmChange={vi.fn()} purposes={purposes} purpose="analysis" onPurposeChange={vi.fn()}
    rich={false} onRichChange={vi.fn()} runtimes={runtimes} selectedRuntimes={[]} onRuntimeToggle={vi.fn()} />);
  open();
  const panel = screen.getByRole('dialog', { name: 'Answer settings' });
  const keys = [...panel.querySelectorAll('.ask-settings-line > .ask-settings-key, .ask-settings-runtimes > .ask-settings-key')].map((key) => key.firstChild.textContent.trim());
  expect(keys).toEqual(['Model', 'Answered by', 'Purpose', 'Rich answers', 'Runtimes']);
  const order = [...panel.querySelectorAll('#ask-settings-arm, #purpose, .ask-settings-hint, #rich, .runtime-option input')].map((node) => node.id || node.className || node.closest('[data-runtime]').dataset.runtime);
  expect(order).toEqual(['ask-settings-arm', 'purpose', 'ask-settings-note ask-settings-hint', 'rich', 'rt_shadow', 'rt_cert']);
  expect(panel.querySelectorAll('select')).toHaveLength(2);
});

test('it absorbs the Answered by select: grouped arms, the current arm on the trigger, onArmChange on a pick', () => {
  const onArmChange = vi.fn();
  render(<AskSettingsPicker health={health} arms={arms} arm="B" onArmChange={onArmChange} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveTextContent('B');
  open();
  const select = screen.getByLabelText('Answered by');
  expect(select).toHaveAttribute('id', 'ask-settings-arm');
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
  const listed = [{ id: 'rules', label: 'Rules', certified: true }, { id: 'model' }];
  rerender(<AskSettingsPicker {...props} runtimes={listed} selectedRuntimes={['model', 'rules']} />);
  expect(screen.getByRole('button', { name: /^Answer settings:/ })).toHaveTextContent('model → rules');
  const group = screen.getByRole('group', { name: 'Runtimes' });
  expect(within(group).getByText('#1')).toBeInTheDocument();
  expect(within(group).getByText('Certified')).toBeInTheDocument();
  fireEvent.click(within(group).getByRole('checkbox', { name: /Rules/ }));
  expect(onRuntimeToggle).toHaveBeenCalledWith('rules');
});

test('each runtime row is one label wrapping its own checkbox, name and badges: the Certified pill sits on its runtime', () => {
  render(<AskSettingsPicker model="m" runtimes={runtimes} selectedRuntimes={['rt_cert']} onRuntimeToggle={vi.fn()} />);
  open();
  const rows = [...screen.getByRole('group', { name: 'Runtimes' }).querySelectorAll('label.runtime-option')];
  expect(rows.map((row) => row.dataset.runtime)).toEqual(['rt_shadow', 'rt_cert']);
  for (const row of rows) {
    expect(row.querySelectorAll('input[type="checkbox"]')).toHaveLength(1);
    expect(row.firstElementChild).toBe(row.querySelector('input[type="checkbox"]'));
    expect(row.children[1]).toHaveClass('runtime-option-label');
  }
  expect(screen.getByRole('checkbox', { name: 'Shadow runtime' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: /^Certified runtime/ })).toBeChecked();
  expect(rows[0].querySelector('.ask-settings-badge')).toBeNull();
  expect([...rows[1].querySelectorAll('.ask-settings-badge')].map((badge) => badge.textContent)).toEqual(['Certified', '#1']);
  for (const box of screen.getAllByRole('checkbox')) expect(box).toHaveAccessibleName(/\S/);
});

// The popover bug (#651 C2): the Context page's own `label { flex-direction:column }`, select height and checkbox
// size, and the shared `select { flex:1 1 200px }`, reached into the picker. jsdom does no layout but does cascade
// declared styles by specificity, so the page's rules, taken from their files, are applied around the picker here.
test('the page rules that leaked into the picker no longer reach it: rows stay on one line and the select at chip height', () => {
  const rule = (file, selector) => {
    const found = read(file).split('}').map((block) => block.split('{')).find(([head]) => head.split('\n').at(-1).trim() === selector);
    expect(found, `${file} ${selector}`).toBeDefined();
    return found[1];
  };
  const leaks = [
    `.context-app label {${rule('../../pages/Context/context.css', 'label')}}`,
    `.context-app select {${rule('../../pages/Context/context.css', 'select')}}`,
    `.context-app input[type=text], .context-app select {${rule('../../pages/Context/context.css', 'input[type=text], select')}}`,
    `.context-app input[type=checkbox] {${rule('../../pages/Context/context.css', 'input[type=checkbox]')}}`,
    `.v3-controls select {${rule('./Controls.css', 'select')}}`,
  ].join('\n');
  expect(leaks).toMatch(/flex-direction:column/);
  const style = document.createElement('style');
  document.head.appendChild(style);
  try {
    const host = document.createElement('div');
    host.className = 'context-app';
    document.body.appendChild(host);
    render(<AskSettingsPicker model="m" arms={arms} arm="B" onArmChange={vi.fn()} purposes={purposes} purpose="analysis" onPurposeChange={vi.fn()}
      onRichChange={vi.fn()} runtimes={runtimes} selectedRuntimes={['rt_cert']} onRuntimeToggle={vi.fn()} />, { container: host });
    open();
    const row = document.querySelector('label.runtime-option');
    const select = document.querySelector('#ask-settings-arm');
    const box = row.querySelector('input[type="checkbox"]');
    style.textContent = leaks;
    expect(getComputedStyle(row).flexDirection).toBe('column');
    style.textContent = `${leaks}\n${read('./AskSettingsPicker.css')}`;
    expect(getComputedStyle(row).display).toBe('flex');
    expect(getComputedStyle(row).flexDirection).toBe('row');
    expect(getComputedStyle(row).flexWrap).toBe('nowrap');
    for (const control of ['ask-settings-arm', 'purpose', 'rich']) expect(getComputedStyle(document.querySelector(`label[for="${control}"]`)).flexDirection).toBe('row');
    expect(getComputedStyle(document.querySelector('#purpose')).height).toBe('32px');
    expect(getComputedStyle(document.querySelector('#rich')).width).toBe('16px');
    expect(getComputedStyle(select).height).toBe('32px');
    expect(getComputedStyle(select).flexGrow).toBe('0');
    expect(getComputedStyle(select).flexBasis).toBe('auto');
    expect(getComputedStyle(box).width).toBe('16px');
    const panel = document.querySelector('.ask-settings-panel');
    expect(getComputedStyle(panel).overflow).toBe('auto');
  } finally {
    style.remove();
  }
});

test('a click outside closes the panel', () => {
  render(<div><AskSettingsPicker health={health} {...settable} /><p>outside</p></div>);
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

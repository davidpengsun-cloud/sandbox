import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { act } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import WaitingStatus, { REDUCED_MOTION } from './WaitingStatus';

const read = (name) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8');
const ownCss = () => {
  const css = read('./Controls.css');
  const start = css.indexOf('/* Waiting status');
  expect(start).toBeGreaterThan(0);
  return css.slice(start);
};

// A controllable prefers-reduced-motion query.
let reduceMotion;
let motionListeners;
beforeEach(() => {
  reduceMotion = false;
  motionListeners = new Set();
  window.matchMedia = vi.fn((query) => ({
    get matches() { return query.includes('reduce') ? reduceMotion : false; },
    media: query,
    addEventListener: (_type, fn) => motionListeners.add(fn),
    removeEventListener: (_type, fn) => motionListeners.delete(fn),
  }));
});
afterEach(() => {
  delete window.matchMedia;
  vi.useRealTimers();
});

test('its styles animate through @keyframes on the portal tokens only: no hex colour, no new token', () => {
  const css = ownCss() + read('./WaitingStatus.js');
  expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  expect(css).not.toMatch(/--v3-[\w-]+\s*:/);
  expect(css).toMatch(/@keyframes waiting-status-spin/);
  expect(css).toMatch(/@keyframes waiting-status-pulse\s*\{[^}]*var\(--v3-ink-3\)[^}]*\}[^}]*var\(--v3-ink\)/);
  expect(css).toMatch(/\.is-animated \.waiting-status-spinner \{ animation:waiting-status-spin/);
  expect(css).toMatch(/\.is-animated \.waiting-status-text \{ animation:waiting-status-pulse/);
});

test('it shows a spinning glyph in front of the pulsing text', () => {
  const { container } = render(<WaitingStatus />);
  const root = container.firstChild;
  expect(root).toHaveClass('waiting-status', 'is-animated');
  const [spinner, text] = root.children;
  expect(spinner).toHaveClass('waiting-status-spinner');
  expect(spinner).toHaveAttribute('aria-hidden', 'true');
  expect(text).toHaveClass('waiting-status-text');
  expect(text).toHaveTextContent('Working out the answer…');
  expect(window.matchMedia).toHaveBeenCalledWith(REDUCED_MOTION);
});

test('with reduced motion the animation class is left off, and the CSS stops both animations', () => {
  reduceMotion = true;
  const { container } = render(<WaitingStatus text="Thinking" />);
  expect(container.firstChild).toHaveClass('waiting-status');
  expect(container.firstChild).not.toHaveClass('is-animated');
  expect(container.querySelector('.is-animated')).toBeNull();
  expect(screen.getByRole('status')).toHaveTextContent('Thinking');
  // The reduce rule must match the animating selectors exactly (same specificity, later in the file), or it never wins.
  expect(ownCss()).toMatch(/@media \(prefers-reduced-motion:reduce\)\{\.waiting-status\.is-animated \.waiting-status-spinner, \.waiting-status\.is-animated \.waiting-status-text\{animation:none\}\}/);
});

test('it follows a change of the reduced-motion setting while shown', () => {
  const { container } = render(<WaitingStatus />);
  expect(container.firstChild).toHaveClass('is-animated');
  reduceMotion = true;
  act(() => motionListeners.forEach((fn) => fn()));
  expect(container.firstChild).not.toHaveClass('is-animated');
});

test('without matchMedia it still renders, animated', () => {
  delete window.matchMedia;
  const { container } = render(<WaitingStatus className="extra" />);
  expect(container.firstChild).toHaveClass('waiting-status', 'is-animated', 'extra');
  expect(screen.getByRole('status')).toHaveTextContent('Working out the answer…');
});

test('it is announced once: one live region; the pulse is CSS only, so no timer changes its text, nor do step changes', () => {
  vi.useFakeTimers();
  const { rerender, container } = render(<WaitingStatus />);
  expect(screen.getAllByRole('status')).toHaveLength(1);
  expect(container.querySelectorAll('[aria-live], [role="status"], [role="alert"]')).toHaveLength(1);
  const status = screen.getByRole('status');
  act(() => { vi.advanceTimersByTime(10000); });
  expect(screen.getByRole('status')).toBe(status);
  expect(status.textContent).toBe('Working out the answer…');

  rerender(<WaitingStatus detail="Planning" />);
  rerender(<WaitingStatus detail="Reading the warehouse" />);
  expect(screen.getAllByRole('status')).toHaveLength(1);
  expect(screen.getByRole('status')).toBe(status);
  expect(status.textContent).toBe('Working out the answer…');
  const detail = container.querySelector('.waiting-status-detail');
  expect(detail).toHaveTextContent('Reading the warehouse');
  expect(status.contains(detail)).toBe(false);
});

test('without a detail no detail is shown', () => {
  const { container } = render(<WaitingStatus />);
  expect(container.querySelector('.waiting-status-detail')).toBeNull();
});

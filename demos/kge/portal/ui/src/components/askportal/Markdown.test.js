import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import Markdown from './Markdown';

test('renders headings, lists, paragraphs and inline emphasis from an answer', () => {
  const answer = [
    '# Summary',
    '#### Deep heading',
    'Balances are **up** for `FUND_MASTER` and *steady* elsewhere.',
    'Same paragraph line.',
    '',
    '- first point',
    '* second point',
    '1. step one',
    '2. step two',
    '- back to bullets',
    'Closing line.',
  ].join('\r\n');
  const { container } = render(<Markdown>{answer}</Markdown>);
  expect(screen.getByText('Summary')).toHaveClass('md-h1');
  expect(screen.getByText('Deep heading')).toHaveClass('md-h3');
  expect(screen.getByText('up').tagName).toBe('STRONG');
  expect(screen.getByText('FUND_MASTER').tagName).toBe('CODE');
  expect(screen.getByText('steady').tagName).toBe('EM');
  expect(container.querySelector('p').textContent).toBe('Balances are up for FUND_MASTER and steady elsewhere. Same paragraph line.');
  const lists = container.querySelectorAll('.md-list');
  expect([...lists].map((list) => list.tagName)).toEqual(['UL', 'OL', 'UL']);
  expect(lists[0]).toHaveTextContent('first pointsecond point');
  expect(lists[1]).toHaveTextContent('step onestep two');
  expect(screen.getByText('Closing line.').tagName).toBe('P');
});

test('underscores in identifiers are left alone', () => {
  render(<Markdown>{'Use account_balance_daily.'}</Markdown>);
  expect(screen.getByText('Use account_balance_daily.')).toBeInTheDocument();
});

test('non-string children pass through and nothing renders for no children', () => {
  const { container, rerender } = render(<Markdown><span>Greeting</span></Markdown>);
  expect(screen.getByText('Greeting').tagName).toBe('SPAN');
  rerender(<Markdown />);
  expect(container).toBeEmptyDOMElement();
});

test('links only to a same-site path and unescapes its label', () => {
  const { container } = render(<Markdown>{'[1] [A \\[b\\]](/productDetails/retail.accounts), [x](javascript:alert(1)), [y](//evil.example/p), [z](https://evil.example), [w](/\\outside.example/asset), [v](/a\\b)'}</Markdown>);
  const links = container.querySelectorAll('a');
  expect(links).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'A [b]' })).toHaveAttribute('href', '/productDetails/retail.accounts');
  expect(container).toHaveTextContent('[x](javascript:alert(1))');
});

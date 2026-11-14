import { expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import useKnowledgeConfig from './useKnowledgeConfig';

// Through the portal's real useConfig: null first, then the settings /api/config answered (setupTests serves them).
function Probe() {
  const config = useKnowledgeConfig();
  return <p>{config ? `${config.productName} | ${config.clientName} | ${String(config.certifyUrl)} | ${Object.values(config.apps).join(',')}` : 'waiting'}</p>;
}

test('reads the settings through the portal config hook', async () => {
  render(<Probe />);
  expect(screen.getByText('waiting')).toBeInTheDocument();
  expect(await screen.findByText('Meridian ALTA | Meridian | null | true,true,true')).toBeInTheDocument();
});

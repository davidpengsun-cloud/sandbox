import { expect, test } from 'vitest';
import { nextStep } from './contextState';

const signedOff = (can) => ({ can, candidate: { hash: 'c0ffee', signed_off: { principal: 'steward' } },
  units: [{ unit: 'leasing', label: 'Asset Finance', items: [{ id: 'one', accepted_by: 'business', certified_by: 'steward' }],
    history: [{ step: 'onboard', detail: { passed: true } }] }] });

test('a signed-off candidate waits on a knowledge engineer to release it, never a steward (#466: release is the engineer\'s)', () => {
  const step = nextStep(signedOff({}));
  expect(step.waiting).toBe(true);
  expect(step.text).toBe('a knowledge engineer to release the signed-off candidate: Asset Finance goes live.');
  expect(step.text).not.toContain('steward');
});

test('the engineer who may release is told to release it', () => {
  const step = nextStep(signedOff({ release: true }));
  expect(step.waiting).toBe(false);
  expect(step.text).toBe('Release the signed-off candidate: Asset Finance goes live.');
});

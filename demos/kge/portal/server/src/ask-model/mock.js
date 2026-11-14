export function mockAskModel(reply = { statements: [] }) {
  return { complete: async (input) => typeof reply === "function" ? reply(input) : structuredClone(reply) };
}

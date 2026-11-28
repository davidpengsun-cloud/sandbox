import { layoutGraph } from "./layout";

/* The layout worker: one graph in, its positions out, off the page's main thread. */
self.onmessage = (event) => {
  const { id, graph } = event.data || {};
  try {
    self.postMessage({ id, positions: layoutGraph(graph) });
  } catch (error) {
    self.postMessage({ id, error: String(error?.message || error) });
  }
};

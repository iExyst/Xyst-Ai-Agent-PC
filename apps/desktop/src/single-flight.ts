/** Concurrent callers share one operation; settled results are never cached. */
export function singleFlight<T>(operation: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | undefined;
  return () => {
    pending ??= Promise.resolve().then(operation).finally(() => { pending = undefined; });
    return pending;
  };
}

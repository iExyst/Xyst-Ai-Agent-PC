export function createCoalescedPersist(persist: () => void, intervalMs: number) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastPersistAt = 0;

  return {
    schedule() {
      if (timer) return;
      const delay = Math.max(0, intervalMs - (Date.now() - lastPersistAt));
      timer = setTimeout(() => {
        timer = undefined;
        lastPersistAt = Date.now();
        persist();
      }, delay);
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = undefined;
    },
  };
}

(function (root) {
  function createConversation() {
    let generation = 0;
    let controller = null;
    return {
      begin() {
        controller?.abort();
        controller = new AbortController();
        const mine = { generation, controller };
        return {
          signal: controller.signal,
          isCurrent: () => mine.generation === generation && !mine.controller.signal.aborted,
          finish: () => {
            if (controller === mine.controller) controller = null;
          },
        };
      },
      reset() {
        generation += 1;
        controller?.abort();
        controller = null;
      },
    };
  }

  root.createConversation = createConversation;
  if (typeof module !== "undefined" && module.exports) module.exports = { createConversation };
})(typeof window !== "undefined" ? window : globalThis);

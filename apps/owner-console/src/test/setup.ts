import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Newer Node versions define a global localStorage that is unusable without
// --localstorage-file and hides jsdom's; give tests a working one.
if (typeof globalThis.localStorage?.clear !== "function") {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => store.get(key) ?? null,
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => {
      store.delete(key);
    },
    setItem: (key, value) => {
      store.set(key, String(value));
    }
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    configurable: true
  });
  Object.defineProperty(window, "localStorage", {
    value: storage,
    configurable: true
  });
}

afterEach(() => {
  cleanup();
});

import "@testing-library/jest-dom/vitest";

class ResizeObserverStub {
  private observed = new Set<Element>();

  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    this.observed.add(target);
    queueMicrotask(() => {
      if (!this.observed.has(target)) return;
      this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry], this as unknown as ResizeObserver);
    });
  }

  unobserve(target: Element) {
    this.observed.delete(target);
  }

  disconnect() {
    this.observed.clear();
  }
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

class DOMMatrixReadOnlyStub {
  readonly m22 = 1;
}

globalThis.DOMMatrixReadOnly = DOMMatrixReadOnlyStub as unknown as typeof DOMMatrixReadOnly;

Object.defineProperties(HTMLElement.prototype, {
  offsetWidth: {
    configurable: true,
    get() {
      if (this.classList.contains("react-flow__node")) return 168;
      if (this.classList.contains("react-flow__handle")) return 7;
      if (this.classList.contains("react-flow") || this.classList.contains("projection-flow")) return 1000;
      return 100;
    },
  },
  offsetHeight: {
    configurable: true,
    get() {
      if (this.classList.contains("react-flow__node")) return 100;
      if (this.classList.contains("react-flow__handle")) return 7;
      if (this.classList.contains("react-flow") || this.classList.contains("projection-flow")) return 340;
      return 30;
    },
  },
});

HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  const width = this.offsetWidth;
  const height = this.offsetHeight;
  return {
    x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height,
    toJSON: () => ({ x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height }),
  };
};

Object.defineProperty(SVGElement.prototype, "getBBox", {
  configurable: true,
  value: () => ({ x: 0, y: 0, width: 80, height: 16 } as DOMRect),
});

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type HTMLAttributes,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

function readStoredWidth(storageKey: string, fallback: number) {
  try {
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? stored : fallback;
  } catch {
    return fallback;
  }
}

function storeWidth(storageKey: string, width: number) {
  try {
    window.localStorage.setItem(storageKey, String(Math.round(width)));
  } catch {
    // 禁用浏览器存储时仍保留当前页面内的调节结果。
  }
}

interface PanelResizeOptions {
  defaultSize: number;
  minSize: number;
  getMaxSize: () => number;
  storageKey: string;
  edge: "left" | "right";
  ariaLabel: string;
  keyboardStep?: number;
}

interface DragState {
  startX: number;
  startSize: number;
  lastSize: number;
}

export function usePanelResize({
  defaultSize,
  minSize,
  getMaxSize,
  storageKey,
  edge,
  ariaLabel,
  keyboardStep = 16,
}: PanelResizeOptions) {
  const getMaxSizeRef = useRef(getMaxSize);
  getMaxSizeRef.current = getMaxSize;
  const [size, setSize] = useState(() =>
    clamp(readStoredWidth(storageKey, defaultSize), minSize, getMaxSize()),
  );
  const dragRef = useRef<DragState | null>(null);

  const resizeTo = useCallback(
    (nextSize: number, persist = false) => {
      const clamped = clamp(nextSize, minSize, getMaxSizeRef.current());
      setSize(clamped);
      if (persist) storeWidth(storageKey, clamped);
      return clamped;
    },
    [minSize, storageKey],
  );

  useEffect(() => {
    const fitViewport = () =>
      setSize((current) =>
        clamp(current, minSize, getMaxSizeRef.current()),
      );
    window.addEventListener("resize", fitViewport);
    return () => {
      window.removeEventListener("resize", fitViewport);
      document.body.classList.remove("app-panel-is-resizing");
    };
  }, [minSize]);

  const onMouseDown = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      dragRef.current = {
        startX: event.clientX,
        startSize: size,
        lastSize: size,
      };
      document.body.classList.add("app-panel-is-resizing");
    },
    [size],
  );

  useEffect(() => {
    const onMouseMove = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const pointerDelta = event.clientX - drag.startX;
      const sizeDelta = edge === "right" ? pointerDelta : -pointerDelta;
      drag.lastSize = resizeTo(drag.startSize + sizeDelta);
    };
    const onMouseUp = () => {
      const drag = dragRef.current;
      if (!drag) return;
      storeWidth(storageKey, drag.lastSize);
      dragRef.current = null;
      document.body.classList.remove("app-panel-is-resizing");
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("blur", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      window.removeEventListener("blur", onMouseUp);
    };
  }, [edge, resizeTo, storageKey]);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      let nextSize: number | null = null;
      if (event.key === "Home") nextSize = minSize;
      if (event.key === "End") nextSize = getMaxSizeRef.current();
      if (event.key === "ArrowLeft") {
        nextSize = size + (edge === "left" ? keyboardStep : -keyboardStep);
      }
      if (event.key === "ArrowRight") {
        nextSize = size + (edge === "right" ? keyboardStep : -keyboardStep);
      }
      if (nextSize === null) return;
      event.preventDefault();
      resizeTo(nextSize, true);
    },
    [edge, keyboardStep, minSize, resizeTo, size],
  );

  const separatorProps = useMemo<HTMLAttributes<HTMLDivElement>>(
    () => ({
      role: "separator",
      tabIndex: 0,
      "aria-label": ariaLabel,
      "aria-orientation": "vertical",
      "aria-valuemin": minSize,
      "aria-valuemax": Math.round(getMaxSize()),
      "aria-valuenow": Math.round(size),
      onMouseDown,
      onKeyDown,
    }),
    [
      ariaLabel,
      getMaxSize,
      minSize,
      onKeyDown,
      onMouseDown,
      size,
    ],
  );

  return { size, separatorProps };
}

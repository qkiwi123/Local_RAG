import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Drawer } from "antd";
import type { DrawerProps } from "antd";

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

interface ResizableDrawerProps
  extends Omit<
    DrawerProps,
    "size" | "defaultSize" | "maxSize" | "resizable" | "classNames"
  > {
  initialSize: number;
  minSize?: number;
  storageKey: string;
  resizeHint?: ReactNode;
}

export function ResizableDrawer({
  initialSize,
  minSize = 360,
  storageKey,
  resizeHint = "拖动左边界调整宽度",
  extra,
  ...props
}: ResizableDrawerProps) {
  const viewportMax = Math.max(minSize, window.innerWidth - 24);
  const [size, setSize] = useState(() =>
    clamp(readStoredWidth(storageKey, initialSize), minSize, viewportMax),
  );
  const sizeRef = useRef(size);

  useEffect(() => {
    sizeRef.current = size;
  }, [size]);

  const resize = useMemo(
    () => ({
      onResize: (nextSize: number) => {
        const clamped = clamp(
          nextSize,
          minSize,
          Math.max(minSize, window.innerWidth - 24),
        );
        sizeRef.current = clamped;
        setSize(clamped);
      },
      onResizeEnd: () => {
        storeWidth(storageKey, sizeRef.current);
      },
    }),
    [minSize, storageKey],
  );

  return (
    <Drawer
      {...props}
      size={size}
      maxSize={viewportMax}
      resizable={resize}
      extra={
        extra ?? <span className="app-drawer-resize-hint">{resizeHint}</span>
      }
      classNames={{ dragger: "app-drawer-resizer" }}
    />
  );
}

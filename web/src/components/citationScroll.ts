const CITATION_TARGET_SELECTOR =
  '[data-citation-preview-target="true"]';

/** 在弹窗布局稳定后调用；定位高亮首行，过高的整表或整页则定位顶部。 */
export function centerCitationTarget(container: HTMLElement): boolean {
  const targetSection = container.querySelector<HTMLElement>(
    CITATION_TARGET_SELECTOR,
  );
  const target =
    targetSection?.querySelector<HTMLElement>("mark, .preview-table-hit") ??
    targetSection;
  if (!target || typeof container.scrollTo !== "function") {
    return false;
  }

  const containerRect = container.getBoundingClientRect();
  // 一个 mark 可能横跨很多行，整体包围盒的中心会跳过引用开头。
  const targetRect =
    (target.tagName === "MARK" ? target.getClientRects()[0] : undefined) ??
    target.getBoundingClientRect();
  const viewportOffset = targetRect.height > container.clientHeight
    ? 24
    : (container.clientHeight - targetRect.height) / 2;
  const centeredTop =
    container.scrollTop +
    targetRect.top -
    containerRect.top -
    container.clientTop -
    viewportOffset;
  container.scrollTo({ top: Math.max(0, centeredTop), behavior: "auto" });
  return true;
}

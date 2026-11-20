/**
 * Shown the moment a page inside the shell is tapped, while the server builds
 * it. Without it the old page stayed up, unchanged, until the new one was
 * complete, so a tap looked ignored on a phone (web polish audit,
 * 2026-11-20). It also gives <Link> a boundary to prefetch up to, so the shell
 * part of a navigation is already on the device.
 *
 * Shaped like PageHeader and a list of rows, so the real page lands without a
 * jump.
 */
export default function Loading() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" data-testid="page-loading">
      <div className="mb-8 animate-pulse lg:mb-12">
        <div className="h-3 w-24 rounded-pill bg-surface-2" />
        <div className="mt-4 h-9 w-3/4 max-w-measure rounded-inner bg-surface-2 lg:h-12" />
        <div className="mt-5 h-4 w-full max-w-measure rounded-pill bg-surface-2" />
        <div className="mt-2 h-4 w-2/3 max-w-measure rounded-pill bg-surface-2" />
      </div>
      <ul className="animate-pulse border-t border-border lg:max-w-3xl">
        {[0, 1, 2, 3, 4].map((i) => (
          <li key={i} className="border-b border-border py-5">
            <div className="h-4 w-5/6 rounded-pill bg-surface-2" />
            <div className="mt-3 h-3 w-1/3 rounded-pill bg-surface-2" />
          </li>
        ))}
      </ul>
    </div>
  );
}

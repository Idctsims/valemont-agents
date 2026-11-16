import { Slip, SlipSpacer } from "@/components/betting/slip";

/** A betting view beside its slip: side by side on desktop; on the phone the slip docks above the tab bar. */
export function BettingFrame({ children }: { children: React.ReactNode }) {
  return (
    <>
      <div className="lg:grid lg:grid-betting lg:items-start lg:gap-8">
        <div className="min-w-0">{children}</div>
        <Slip />
      </div>
      <SlipSpacer />
    </>
  );
}
